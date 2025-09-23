# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
import socket
from datetime import timedelta
from os import getenv
from typing import ClassVar, Optional

from celery import Task
from celery._state import get_current_task
from celery.signals import after_setup_logger
from ogr import __version__ as ogr_version
from ogr.exceptions import OgrException
from packit import __version__ as packit_version
from packit.exceptions import PackitException
from sqlalchemy import __version__ as sqlal_version
from syslog_rfc5424_formatter import RFC5424Formatter

from packit_service import __version__ as ps_version
from packit_service.celerizer import celery_app
from packit_service.constants import (
    CELERY_DEFAULT_MAIN_TASK_NAME,
    DEFAULT_RETRY_BACKOFF,
    DEFAULT_RETRY_LIMIT,
    USAGE_CURRENT_DATE,
    USAGE_DATE_IN_THE_PAST,
    USAGE_DATE_IN_THE_PAST_STR,
    USAGE_PAST_DAY_DATE_STR,
    USAGE_PAST_MONTH_DATE_STR,
    USAGE_PAST_WEEK_DATE_STR,
    USAGE_PAST_YEAR_DATE_STR,
)
from packit_service.models import (
    GitProjectModel,
    SyncReleaseTargetModel,
    VMImageBuildTargetModel,
    get_usage_data,
)
from packit_service.utils import (
    load_job_config,
    load_package_config,
    log_package_versions,
)
from packit_service.worker.database import (
    backup,
    discard_old_package_configs,
    discard_old_srpm_build_logs,
)
from packit_service.worker.handlers import (
    CoprBuildEndHandler,
    CoprBuildHandler,
    CoprBuildStartHandler,
    CoprOpenScanHubTaskFinishedHandler,
    CoprOpenScanHubTaskStartedHandler,
    DownstreamLogDetectiveResultsHandler,
    DownstreamTestingFarmELNHandler,
    DownstreamTestingFarmHandler,
    DownstreamTestingFarmResultsHandler,
    GithubAppInstallationHandler,
    GitPullRequestHelpHandler,
    KojiBuildHandler,
    KojiTaskReportHandler,
    ProposeDownstreamHandler,
    SyncFromDownstream,
    TestingFarmHandler,
    TestingFarmResultsHandler,
    VMImageBuildHandler,
    VMImageBuildResultHandler,
)
from packit_service.worker.handlers.abstract import TaskName
from packit_service.worker.handlers.bodhi import (
    BodhiUpdateFromSidetagHandler,
    CreateBodhiUpdateHandler,
    IssueCommentRetriggerBodhiUpdateHandler,
    RetriggerBodhiUpdateHandler,
)
from packit_service.worker.handlers.distgit import (
    DownstreamKojiBuildHandler,
    DownstreamKojiELNScratchBuildHandler,
    DownstreamKojiScratchBuildHandler,
    PullFromUpstreamHandler,
    RetriggerDownstreamKojiBuildHandler,
    TagIntoSidetagHandler,
)
from packit_service.worker.handlers.forges import GithubFasVerificationHandler
from packit_service.worker.handlers.koji import (
    KojiBuildReportHandler,
    KojiBuildTagHandler,
    KojiTaskReportDownstreamHandler,
)
from packit_service.worker.handlers.usage import check_onboarded_projects
from packit_service.worker.helpers.build.babysit import (
    check_copr_build,
    check_pending_copr_builds,
    check_pending_testing_farm_runs,
    check_pending_vm_image_builds,
    update_vm_image_build,
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
    logging.getLogger("packit_service").setLevel(logging.DEBUG)

    class CustomFormatter(logging.Formatter):
        def format(self, record):
            task = get_current_task()
            if task and task.request:
                record.__dict__["task_info"] = f" {task.name}[{task.request.id}]"
            else:
                record.__dict__["task_info"] = ""
            return super().format(record)

    # add task name and id to log messages from tasks
    logger.handlers[0].setFormatter(
        CustomFormatter(
            "[%(asctime)s: %(levelname)s/%(processName)s]%(task_info)s %(message)s",
        ),
    )

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
        # Also add to root logger to ensure all loggers (including child loggers) inherit it
        root_logger = logging.getLogger()
        # Check if handler already exists to avoid duplicates
        if not any(isinstance(h, logging.handlers.SysLogHandler) for h in root_logger.handlers):
            root_logger.addHandler(handler)

    package_versions = [
        ("OGR", ogr_version),
        ("Packit", packit_version),
        ("Packit Service", ps_version),
        ("SQL Alchemy", sqlal_version),
    ]
    log_package_versions(package_versions)


class TaskWithRetry(Task):
    # Only retry on specific exceptions that are likely to be transient:
    # - PackitException: Packit-specific errors that might be retryable
    # - OgrException: OGR library errors (API, network, authentication issues)
    # - ConnectionError: Network connection problems
    # - TimeoutError: Timeout issues
    # - OSError: File system/OS errors that might be transient
    # Note: RateLimitRequeueException is NOT in this list, so it won't trigger autoretry
    autoretry_for = (
        PackitException,
        OgrException,
        ConnectionError,
        TimeoutError,
        OSError,
    )
    max_retries = int(getenv("CELERY_RETRY_LIMIT", DEFAULT_RETRY_LIMIT))
    retry_kwargs: ClassVar[dict] = {"max_retries": max_retries}
    retry_backoff = int(getenv("CELERY_RETRY_BACKOFF", DEFAULT_RETRY_BACKOFF))
    # https://docs.celeryq.dev/en/stable/userguide/tasks.html#Task.acks_late
    # retry if worker gets obliterated during execution
    acks_late = True


class BodhiTaskWithRetry(TaskWithRetry):
    # hardcode for creating bodhi updates to account for the tagging race condition
    max_retries = 5
    # also disable jitter for the same reason
    retry_jitter = False
    retry_kwargs: ClassVar[dict] = {
        "max_retries": max_retries,
        "retry_jitter": retry_jitter,
    }


@celery_app.task(
    name=getenv("CELERY_MAIN_TASK_NAME") or CELERY_DEFAULT_MAIN_TASK_NAME,
    bind=True,
    # set a lower time limit for process message as for other tasks
    # https://docs.celeryq.dev/en/stable/reference/celery.app.task.html#celery.app.task.Task.time_limit
    time_limit=300,
    base=TaskWithRetry,
)
def process_message(
    self,
    event: dict,
    source: Optional[str] = None,
    event_type: Optional[str] = None,
) -> list[TaskResults]:
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
            f"No feedback for copr build id={build_id} yet",
        )


# tasks for running the handlers
@celery_app.task(name=TaskName.copr_build_start, base=TaskWithRetry)
def run_copr_build_start_handler(event: dict, package_config: dict, job_config: dict):
    handler = CoprBuildStartHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(name=TaskName.copr_build_end, base=TaskWithRetry)
def run_copr_build_end_handler(event: dict, package_config: dict, job_config: dict):
    handler = CoprBuildEndHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(bind=True, name=TaskName.copr_build, base=TaskWithRetry, queue="long-running")
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


@celery_app.task(name=TaskName.installation, base=TaskWithRetry)
def run_installation_handler(event: dict, package_config: dict, job_config: dict):
    handler = GithubAppInstallationHandler(
        package_config=None,
        job_config=None,
        event=event,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(name=TaskName.github_fas_verification, base=TaskWithRetry)
def run_github_fas_verification_handler(
    event: dict,
    package_config: dict,
    job_config: dict,
):
    handler = GithubFasVerificationHandler(
        package_config=None,
        job_config=None,
        event=event,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(name=TaskName.help, base=TaskWithRetry)
def run_pr_help_handler(
    event: dict,
    package_config: dict,
    job_config: dict,
):
    handler = GitPullRequestHelpHandler(
        package_config=None,
        job_config=None,
        event=event,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(bind=True, name=TaskName.testing_farm, base=TaskWithRetry)
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


@celery_app.task(name=TaskName.testing_farm_results, base=TaskWithRetry)
def run_testing_farm_results_handler(
    event: dict,
    package_config: dict,
    job_config: dict,
):
    handler = TestingFarmResultsHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(bind=True, name=TaskName.downstream_testing_farm, base=TaskWithRetry)
def run_downstream_testing_farm_handler(
    self,
    event: dict,
    package_config: dict,
    job_config: dict,
    testing_farm_target_id: Optional[int] = None,
):
    handler = DownstreamTestingFarmHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
        celery_task=self,
        testing_farm_target_id=testing_farm_target_id,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(bind=True, name=TaskName.downstream_testing_farm_eln, base=TaskWithRetry)
def run_downstream_testing_farm_eln_handler(
    self,
    event: dict,
    package_config: dict,
    job_config: dict,
    testing_farm_target_id: Optional[int] = None,
):
    handler = DownstreamTestingFarmELNHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
        celery_task=self,
        testing_farm_target_id=testing_farm_target_id,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(name=TaskName.downstream_testing_farm_results, base=TaskWithRetry)
def run_downstream_testing_farm_results_handler(
    event: dict,
    package_config: dict,
    job_config: dict,
):
    handler = DownstreamTestingFarmResultsHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(
    bind=True,
    name=TaskName.propose_downstream,
    # longer time limit for sync release tasks (30 minutes)
    time_limit=1800,
    base=TaskWithRetry,
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
    # longer time limit for sync release tasks (30 minutes)
    time_limit=1800,
    base=TaskWithRetry,
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
    name=TaskName.upstream_koji_build,
    base=TaskWithRetry,
    queue="long-running",
)
def run_koji_build_handler(event: dict, package_config: dict, job_config: dict):
    handler = KojiBuildHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(name=TaskName.upstream_koji_build_report, base=TaskWithRetry)
def run_koji_build_report_handler(event: dict, package_config: dict, job_config: dict):
    handler = KojiTaskReportHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(name=TaskName.downstream_koji_scratch_build_report, base=TaskWithRetry)
def run_downstream_koji_scratch_build_report_handler(
    event: dict, package_config: dict, job_config: dict
):
    handler = KojiTaskReportDownstreamHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(
    bind=True, name=TaskName.downstream_koji_scratch_build, base=TaskWithRetry, queue="long-running"
)
def run_downstream_koji_scratch_build_handler(
    self, event: dict, package_config: dict, job_config: dict
):
    handler = DownstreamKojiScratchBuildHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
        celery_task=self,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(
    bind=True,
    name=TaskName.downstream_koji_eln_scratch_build,
    base=TaskWithRetry,
    queue="long-running",
)
def run_downstream_koji_eln_scratch_build_handler(
    self, event: dict, package_config: dict, job_config: dict
):
    handler = DownstreamKojiELNScratchBuildHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
        celery_task=self,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(
    name=TaskName.sync_from_downstream,
    base=TaskWithRetry,
    queue="long-running",
)
def run_sync_from_downstream_handler(
    event: dict,
    package_config: dict,
    job_config: dict,
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
    base=TaskWithRetry,
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
    base=TaskWithRetry,
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


@celery_app.task(name=TaskName.downstream_koji_build_report, base=TaskWithRetry)
def run_downstream_koji_build_report(
    event: dict,
    package_config: dict,
    job_config: dict,
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
    base=BodhiTaskWithRetry,
    queue="long-running",
)
def run_bodhi_update(
    self,
    event: dict,
    package_config: dict,
    job_config: dict,
    bodhi_update_group_model_id: Optional[int] = None,
):
    handler = CreateBodhiUpdateHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
        celery_task=self,
        bodhi_update_group_model_id=bodhi_update_group_model_id,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(
    bind=True,
    name=TaskName.bodhi_update_from_sidetag,
    base=BodhiTaskWithRetry,
    queue="long-running",
)
def run_bodhi_update_from_sidetag(
    self,
    event: dict,
    package_config: dict,
    job_config: dict,
    bodhi_update_group_model_id: Optional[int] = None,
):
    handler = BodhiUpdateFromSidetagHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
        celery_task=self,
        bodhi_update_group_model_id=bodhi_update_group_model_id,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(
    bind=True,
    name=TaskName.retrigger_bodhi_update,
    base=TaskWithRetry,
    queue="long-running",
)
def run_retrigger_bodhi_update(
    self,
    event: dict,
    package_config: dict,
    job_config: dict,
    bodhi_update_group_model_id: Optional[int] = None,
):
    handler = RetriggerBodhiUpdateHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
        celery_task=self,
        bodhi_update_group_model_id=bodhi_update_group_model_id,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(
    bind=True,
    name=TaskName.issue_comment_retrigger_bodhi_update,
    base=TaskWithRetry,
    queue="long-running",
)
def run_issue_comment_retrigger_bodhi_update(
    self,
    event: dict,
    package_config: dict,
    job_config: dict,
    bodhi_update_group_model_id: Optional[int] = None,
):
    handler = IssueCommentRetriggerBodhiUpdateHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
        celery_task=self,
        bodhi_update_group_model_id=bodhi_update_group_model_id,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(
    bind=True,
    name=TaskName.vm_image_build,
    base=TaskWithRetry,
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


@celery_app.task(name=TaskName.vm_image_build_result, base=TaskWithRetry)
def run_vm_image_build_result(
    self,
    event: dict,
    package_config: dict,
    job_config: dict,
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
            f"No feedback for vm image build id={build_id} yet",
        )


@celery_app.task(name=TaskName.koji_build_tag, base=TaskWithRetry)
def run_koji_build_tag_handler(event: dict, package_config: dict, job_config: dict):
    handler = KojiBuildTagHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(bind=True, name=TaskName.tag_into_sidetag, base=TaskWithRetry)
def run_tag_into_sidetag_handler(
    self,
    event: dict,
    package_config: dict,
    job_config: dict,
):
    handler = TagIntoSidetagHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
        celery_task=self,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(bind=True, name=TaskName.openscanhub_task_finished, base=TaskWithRetry)
def run_openscanhub_task_finished_handler(
    self,
    event: dict,
    package_config: dict,
    job_config: dict,
):
    handler = CoprOpenScanHubTaskFinishedHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
        celery_task=self,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(bind=True, name=TaskName.openscanhub_task_started, base=TaskWithRetry)
def run_openscanhub_task_started_handler(
    self,
    event: dict,
    package_config: dict,
    job_config: dict,
):
    handler = CoprOpenScanHubTaskStartedHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
        celery_task=self,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(name=TaskName.downstream_log_detective_results, base=TaskWithRetry)
def run_downstream_log_detective_results_handler(
    event: dict,
    package_config: dict,
    job_config: dict,
):
    handler = DownstreamLogDetectiveResultsHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
    )
    return get_handlers_task_results(handler.run_job(), event)


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
    backup()
    # TODO: uncomment once we did the first manual cleanup
    # delete_old_data()
    discard_old_srpm_build_logs()
    discard_old_package_configs()


@celery_app.task
def babysit_pending_vm_image_builds() -> None:
    check_pending_vm_image_builds()


# Usage / statistics tasks


@celery_app.task
def run_check_onboarded_projects() -> None:
    known_onboarded_projects = GitProjectModel.get_known_onboarded_downstream_projects()
    downstream_synced_projects = SyncReleaseTargetModel.get_all_downstream_projects()
    almost_onboarded_projects = downstream_synced_projects.difference(
        known_onboarded_projects,
    )
    check_onboarded_projects(almost_onboarded_projects)


def _get_usage_interval_data(days, hours, count) -> None:
    """Call functions collecting usage statistics and **cache** results
    to be used quicker later.

    :param days: number of days for the interval length
    :param hours: number of days for the interval length
    :param count: number of intervals
    """
    logger.debug(
        f"Starting collecting statistics for days={days}, hours={hours}, count={count}",
    )

    delta = timedelta(days=days, hours=hours)
    current_date = USAGE_CURRENT_DATE
    days_legend = []
    for _ in range(count):
        days_legend.append(current_date)
        current_date -= delta

    logger.debug(
        f"Getting usage data datetime_from {USAGE_DATE_IN_THE_PAST} datetime_to {days_legend[-1]}",
    )
    get_usage_data(
        datetime_from=USAGE_DATE_IN_THE_PAST,
        datetime_to=days_legend[-1],
        top=100000,
    )
    logger.debug("Got usage data.")

    for day in reversed(days_legend):
        day_from = (day - delta).isoformat()
        day_to = day.isoformat()

        logger.warn(f"Getting usage data datetime_from {day_from} datetime_to {day_to}")
        get_usage_data(datetime_from=day_from, datetime_to=day_to, top=100000)
        logger.warn("Got usage data.")

    logger.debug(
        f"Done collecting statistics for days={days}, hours={hours}, count={count}",
    )


@celery_app.task
def get_usage_statistics() -> None:
    """Call functions collecting usage statistics and **cache** results
    to be used later.

    We need to do the very same calls made by the dashboard! Keep it in sync.
    """
    _get_usage_interval_data(days=0, hours=1, count=24)  # past day hourly statistics
    _get_usage_interval_data(days=1, hours=0, count=7)  # past week daily statistics
    _get_usage_interval_data(days=1, hours=0, count=30)  # past month daily statistics
    _get_usage_interval_data(days=7, hours=0, count=52)  # past year weekly statistics

    for day in (
        USAGE_PAST_DAY_DATE_STR,
        USAGE_PAST_WEEK_DATE_STR,
        USAGE_PAST_MONTH_DATE_STR,
        USAGE_PAST_YEAR_DATE_STR,
        USAGE_DATE_IN_THE_PAST_STR,
    ):
        logger.debug(f"Getting usage data from datetime_from {day}.")
        get_usage_data(datetime_from=day)
        logger.debug("Got usage data.")
