# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
import socket
from os import getenv
from typing import List, Optional

from celery import Task
from celery.signals import after_setup_logger
from ogr import __version__ as ogr_version
from sqlalchemy import __version__ as sqlal_version
from syslog_rfc5424_formatter import RFC5424Formatter

from packit import __version__ as packit_version
from packit.exceptions import PackitException
from packit_service import __version__ as ps_version
from packit_service.celerizer import celery_app
from packit_service.constants import (
    DEFAULT_RETRY_LIMIT,
    DEFAULT_RETRY_BACKOFF,
    CELERY_DEFAULT_MAIN_TASK_NAME,
)
from packit_service.models import VMImageBuildTargetModel
from packit_service.utils import (
    load_job_config,
    load_package_config,
    log_package_versions,
)
from packit_service.worker.database import discard_old_srpm_build_logs, backup
from packit_service.worker.handlers import (
    CoprBuildEndHandler,
    CoprBuildStartHandler,
    KojiTaskReportHandler,
    SyncFromDownstream,
    CoprBuildHandler,
    GithubAppInstallationHandler,
    KojiBuildHandler,
    ProposeDownstreamHandler,
    TestingFarmHandler,
    TestingFarmResultsHandler,
    VMImageBuildHandler,
    VMImageBuildResultHandler,
)
from packit_service.worker.handlers.abstract import TaskName
from packit_service.worker.handlers.bodhi import (
    CreateBodhiUpdateHandler,
    RetriggerBodhiUpdateHandler,
    IssueCommentRetriggerBodhiUpdateHandler,
)
from packit_service.worker.handlers.distgit import (
    DownstreamKojiBuildHandler,
    RetriggerDownstreamKojiBuildHandler,
    PullFromUpstreamHandler,
)
from packit_service.worker.handlers.forges import GithubFasVerificationHandler
from packit_service.worker.handlers.koji import KojiBuildReportHandler
from packit_service.worker.helpers.build.babysit import (
    check_copr_build,
    check_pending_copr_builds,
    check_pending_testing_farm_runs,
    update_vm_image_build,
    check_pending_vm_image_builds,
)
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.result import TaskResults

logger = logging.getLogger(__name__)


class PackitCoprBuildTimeoutException(PackitException):
    """Copr build has timed out"""


class PackitVMImageBuildTimeoutException(PackitException):
    """VM image build has timed out"""


@after_setup_logger.connect
def setup_loggers(logger, *args, **kwargs):
    # debug logs of these are super-duper verbose
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("github").setLevel(logging.WARNING)
    logging.getLogger("kubernetes").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("s3transfer").setLevel(logging.WARNING)
    # info is just enough
    logging.getLogger("ogr").setLevel(logging.INFO)
    logging.getLogger("sandcastle").setLevel(logging.INFO)
    # easier debugging
    logging.getLogger("packit").setLevel(logging.DEBUG)

    syslog_host = getenv("SYSLOG_HOST", "fluentd")
    syslog_port = int(getenv("SYSLOG_PORT", 5140))
    logger.info(f"Setup logging to syslog -> {syslog_host}:{syslog_port}")
    try:
        handler = logging.handlers.SysLogHandler(address=(syslog_host, syslog_port))
    except (ConnectionRefusedError, socket.gaierror):
        logger.info(f"{syslog_host}:{syslog_port} not available")
    else:
        handler.setLevel(logging.DEBUG)
        project = getenv("PROJECT", "packit")
        handler.setFormatter(RFC5424Formatter(msgid=project))
        logger.addHandler(handler)

    package_versions = [
        ("OGR", ogr_version),
        ("Packit", packit_version),
        ("Packit Service", ps_version),
        ("SQL Alchemy", sqlal_version),
    ]
    log_package_versions(package_versions)


class HandlerTaskWithRetry(Task):
    autoretry_for = (Exception,)
    max_retries = int(getenv("CELERY_RETRY_LIMIT", DEFAULT_RETRY_LIMIT))
    retry_kwargs = {"max_retries": max_retries}
    retry_backoff = int(getenv("CELERY_RETRY_BACKOFF", DEFAULT_RETRY_BACKOFF))
    # https://docs.celeryq.dev/en/stable/userguide/tasks.html#Task.acks_late
    # retry if worker gets obliterated during execution
    acks_late = True


class BodhiHandlerTaskWithRetry(HandlerTaskWithRetry):
    # hardcode for creating bodhi updates to account for the tagging race condition
    max_retries = 5
    # also disable jitter for the same reason
    retry_jitter = False
    retry_kwargs = {"max_retries": max_retries, "retry_jitter": retry_jitter}


@celery_app.task(
    name=getenv("CELERY_MAIN_TASK_NAME") or CELERY_DEFAULT_MAIN_TASK_NAME, bind=True
)
def process_message(
    self, event: dict, source: Optional[str] = None, event_type: Optional[str] = None
) -> List[TaskResults]:
    """
    Main celery task for processing messages.

    For values of 'source' and 'event_type' see Parser.MAPPING.

    Args:
        event: event data
        source: Source of the event, for example: "github"
        event_type: Type of the event, for example: "pull_request"

    Returns:
        task results
    """
    return SteveJobs.process_message(event=event, source=source, event_type=event_type)


@celery_app.task(
    bind=True,
    name="task.babysit_copr_build",
    autoretry_for=(PackitCoprBuildTimeoutException,),
    retry_backoff=30,  # retry again in 30s, 60s, 120s, 240s...
    retry_backoff_max=3600,  # at most, wait for an hour between retries
    max_retries=14,  # retry 14 times; with the backoff values above this is ~8 hours
    retry_jitter=False,  # do not jitter, as it might considerably reduce the total wait time
)
def babysit_copr_build(self, build_id: int):
    """check status of a copr build and update it in DB"""
    if not check_copr_build(build_id=build_id):
        raise PackitCoprBuildTimeoutException(
            f"No feedback for copr build id={build_id} yet"
        )


# tasks for running the handlers
@celery_app.task(name=TaskName.copr_build_start, base=HandlerTaskWithRetry)
def run_copr_build_start_handler(event: dict, package_config: dict, job_config: dict):
    handler = CoprBuildStartHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(name=TaskName.copr_build_end, base=HandlerTaskWithRetry)
def run_copr_build_end_handler(event: dict, package_config: dict, job_config: dict):
    handler = CoprBuildEndHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(
    bind=True, name=TaskName.copr_build, base=HandlerTaskWithRetry, queue="long-running"
)
def run_copr_build_handler(
    self,
    event: dict,
    package_config: dict,
    job_config: dict,
    copr_build_group_id: Optional[int] = None,
):
    handler = CoprBuildHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
        celery_task=self,
        copr_build_group_id=copr_build_group_id,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(name=TaskName.installation, base=HandlerTaskWithRetry)
def run_installation_handler(event: dict, package_config: dict, job_config: dict):
    handler = GithubAppInstallationHandler(
        package_config=None, job_config=None, event=event
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(name=TaskName.github_fas_verification, base=HandlerTaskWithRetry)
def run_github_fas_verification_handler(
    event: dict, package_config: dict, job_config: dict
):
    handler = GithubFasVerificationHandler(
        package_config=None, job_config=None, event=event
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(bind=True, name=TaskName.testing_farm, base=HandlerTaskWithRetry)
def run_testing_farm_handler(
    self,
    event: dict,
    package_config: dict,
    job_config: dict,
    build_id: Optional[int] = None,
    testing_farm_target_id: Optional[int] = None,
):
    handler = TestingFarmHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
        build_id=build_id,
        celery_task=self,
        testing_farm_target_id=testing_farm_target_id,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(name=TaskName.testing_farm_results, base=HandlerTaskWithRetry)
def run_testing_farm_results_handler(
    event: dict, package_config: dict, job_config: dict
):
    handler = TestingFarmResultsHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(
    bind=True,
    name=TaskName.propose_downstream,
    base=HandlerTaskWithRetry,
    queue="long-running",
)
def run_propose_downstream_handler(
    self,
    event: dict,
    package_config: dict,
    job_config: dict,
    sync_release_run_id: Optional[int] = None,
):
    handler = ProposeDownstreamHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
        sync_release_run_id=sync_release_run_id,
        celery_task=self,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(
    bind=True,
    name=TaskName.pull_from_upstream,
    base=HandlerTaskWithRetry,
    queue="long-running",
)
def run_pull_from_upstream_handler(
    self,
    event: dict,
    package_config: dict,
    job_config: dict,
    sync_release_run_id: Optional[int] = None,
):
    handler = PullFromUpstreamHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
        sync_release_run_id=sync_release_run_id,
        celery_task=self,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(
    name=TaskName.upstream_koji_build, base=HandlerTaskWithRetry, queue="long-running"
)
def run_koji_build_handler(event: dict, package_config: dict, job_config: dict):
    handler = KojiBuildHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(name=TaskName.upstream_koji_build_report, base=HandlerTaskWithRetry)
def run_koji_build_report_handler(event: dict, package_config: dict, job_config: dict):
    handler = KojiTaskReportHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(
    name=TaskName.sync_from_downstream, base=HandlerTaskWithRetry, queue="long-running"
)
def run_sync_from_downstream_handler(
    event: dict, package_config: dict, job_config: dict
):
    handler = SyncFromDownstream(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(
    bind=True,
    name=TaskName.downstream_koji_build,
    base=HandlerTaskWithRetry,
    queue="long-running",
)
def run_downstream_koji_build(
    self,
    event: dict,
    package_config: dict,
    job_config: dict,
    koji_group_model_id: Optional[int] = None,
):
    handler = DownstreamKojiBuildHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
        celery_task=self,
        koji_group_model_id=koji_group_model_id,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(
    bind=True,
    name=TaskName.retrigger_downstream_koji_build,
    base=HandlerTaskWithRetry,
    queue="long-running",
)
def run_retrigger_downstream_koji_build(
    self,
    event: dict,
    package_config: dict,
    job_config: dict,
    koji_group_model_id: Optional[int] = None,
):
    handler = RetriggerDownstreamKojiBuildHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
        celery_task=self,
        koji_group_model_id=koji_group_model_id,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(name=TaskName.downstream_koji_build_report, base=HandlerTaskWithRetry)
def run_downstream_koji_build_report(
    event: dict, package_config: dict, job_config: dict
):
    handler = KojiBuildReportHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(
    bind=True,
    name=TaskName.bodhi_update,
    base=BodhiHandlerTaskWithRetry,
    queue="long-running",
)
def run_bodhi_update(self, event: dict, package_config: dict, job_config: dict):
    handler = CreateBodhiUpdateHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
        celery_task=self,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(
    bind=True,
    name=TaskName.retrigger_bodhi_update,
    base=HandlerTaskWithRetry,
    queue="long-running",
)
def run_retrigger_bodhi_update(
    self, event: dict, package_config: dict, job_config: dict
):
    handler = RetriggerBodhiUpdateHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
        celery_task=self,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(
    bind=True,
    name=TaskName.issue_comment_retrigger_bodhi_update,
    base=HandlerTaskWithRetry,
    queue="long-running",
)
def run_issue_comment_retrigger_bodhi_update(
    self, event: dict, package_config: dict, job_config: dict
):
    handler = IssueCommentRetriggerBodhiUpdateHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
        celery_task=self,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(
    bind=True,
    name=TaskName.vm_image_build,
    base=HandlerTaskWithRetry,
    queue="short-running",
)
def run_vm_image_build(self, event: dict, package_config: dict, job_config: dict):
    handler = VMImageBuildHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
        celery_task=self,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(name=TaskName.vm_image_build_result, base=HandlerTaskWithRetry)
def run_vm_image_build_result(
    self, event: dict, package_config: dict, job_config: dict
):
    handler = VMImageBuildResultHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(
    bind=True,
    name="task.babysit_vm_image_build",
    autoretry_for=(PackitVMImageBuildTimeoutException,),
    retry_backoff=30,  # retry again in 30s, 60s, 120s, 240s...
    retry_backoff_max=3600,  # at most, wait for an hour between retries
    max_retries=14,  # retry 14 times; with the backoff values above this is ~8 hours
    retry_jitter=False,  # do not jitter, as it might considerably reduce the total wait time
)
def babysit_vm_image_build(self, build_id: int):
    """check status of a vm image build and update it in DB"""
    model = VMImageBuildTargetModel.get_by_build_id(build_id)
    if not update_vm_image_build(build_id, model):
        raise PackitVMImageBuildTimeoutException(
            f"No feedback for vm image build id={build_id} yet"
        )


def get_handlers_task_results(results: dict, event: dict) -> dict:
    # include original event to provide more info
    return {"job": results, "event": event}


# Periodic tasks


@celery_app.task
def babysit_pending_copr_builds() -> None:
    check_pending_copr_builds()


@celery_app.task
def babysit_pending_tft_runs() -> None:
    check_pending_testing_farm_runs()


@celery_app.task
def database_maintenance() -> None:
    discard_old_srpm_build_logs()
    backup()


@celery_app.task
def babysit_pending_vm_image_builds() -> None:
    check_pending_vm_image_builds()
