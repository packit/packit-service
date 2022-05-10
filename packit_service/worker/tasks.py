# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from os import getenv
from typing import List, Optional

from celery import Task

from packit_service.celerizer import celery_app
from packit_service.constants import (
    DEFAULT_RETRY_LIMIT,
    DEFAULT_RETRY_BACKOFF,
    CELERY_DEFAULT_MAIN_TASK_NAME,
)
from packit_service.utils import load_job_config, load_package_config
from packit_service.worker.handlers.forges import GithubFasVerificationHandler
from packit_service.worker.helpers.build.babysit import (
    check_copr_build,
    check_pending_copr_builds,
    check_pending_testing_farm_runs,
)
from packit_service.worker.database import discard_old_srpm_build_logs, backup
from packit_service.worker.handlers import (
    BugzillaHandler,
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
)
from packit_service.worker.handlers.abstract import TaskName
from packit_service.worker.handlers.bodhi import CreateBodhiUpdateHandler
from packit_service.worker.handlers.distgit import DownstreamKojiBuildHandler
from packit_service.worker.handlers.koji import KojiBuildReportHandler
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.result import TaskResults

logger = logging.getLogger(__name__)

# debug logs of these are super-duper verbose
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("github").setLevel(logging.WARNING)
logging.getLogger("kubernetes").setLevel(logging.WARNING)
logging.getLogger("botocore").setLevel(logging.WARNING)
logging.getLogger("s3transfer").setLevel(logging.WARNING)
# info is just enough
logging.getLogger("ogr").setLevel(logging.INFO)
# easier debugging
logging.getLogger("packit").setLevel(logging.DEBUG)
logging.getLogger("sandcastle").setLevel(logging.DEBUG)


class HandlerTaskWithRetry(Task):
    autoretry_for = (Exception,)
    retry_kwargs = {
        "max_retries": int(getenv("CELERY_RETRY_LIMIT", DEFAULT_RETRY_LIMIT))
    }
    retry_backoff = int(getenv("CELERY_RETRY_BACKOFF", DEFAULT_RETRY_BACKOFF))


@celery_app.task(
    name=getenv("CELERY_MAIN_TASK_NAME") or CELERY_DEFAULT_MAIN_TASK_NAME, bind=True
)
def process_message(
    self, event: dict, topic: str = None, source: str = None
) -> List[TaskResults]:
    """
    Main celery task for processing messages.

    :param event: event data
    :param topic: event topic
    :param source: event source
    :return: dictionary containing task results
    """
    return SteveJobs().process_message(event=event, topic=topic, source=source)


@celery_app.task(
    bind=True,
    name="task.babysit_copr_build",
    retry_backoff=30,  # retry again in 30s, 60s, 120s, 240s...
    retry_backoff_max=3600,  # at most, wait for an hour between retries
    max_retries=14,  # retry 14 times; with the backoff values above this is ~8 hours
    retry_jitter=False,  # do not jitter, as it might considerably reduce the total wait time
)
def babysit_copr_build(self, build_id: int):
    """check status of a copr build and update it in DB"""
    if not check_copr_build(build_id=build_id):
        self.retry()


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
    name=TaskName.copr_build, base=HandlerTaskWithRetry, queue="long-running"
)
def run_copr_build_handler(event: dict, package_config: dict, job_config: dict):
    handler = CoprBuildHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
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


@celery_app.task(name=TaskName.testing_farm, base=HandlerTaskWithRetry)
def run_testing_farm_handler(
    event: dict,
    package_config: dict,
    job_config: dict,
    build_id: Optional[int] = None,
):
    handler = TestingFarmHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
        build_id=build_id,
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
    propose_downstream_run_id: Optional[int] = None,
):
    handler = ProposeDownstreamHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
        propose_downstream_run_id=propose_downstream_run_id,
        task=self,
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
    self, event: dict, package_config: dict, job_config: dict
):
    handler = DownstreamKojiBuildHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
        task=self,
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
    base=HandlerTaskWithRetry,
    queue="long-running",
)
def run_bodhi_update(self, event: dict, package_config: dict, job_config: dict):
    handler = CreateBodhiUpdateHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event,
        task=self,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(name=TaskName.bugzilla, base=HandlerTaskWithRetry)
def run_bugzilla_handler(event: dict, package_config: dict, job_config: dict):
    handler = BugzillaHandler(
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
    discard_old_srpm_build_logs()
    backup()
