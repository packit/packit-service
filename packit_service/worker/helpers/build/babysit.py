# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import collections
import datetime
import logging
from typing import Iterable, Type, Any

import copr.v3
import requests
from copr.v3 import Client as CoprClient

from packit_service.constants import (
    COPR_API_FAIL_STATE,
    COPR_API_SUCC_STATE,
    COPR_SUCC_STATE,
    TESTING_FARM_API_URL,
    DEFAULT_JOB_TIMEOUT,
)
from packit_service.worker.parser import Parser
from packit_service.models import (
    CoprBuildTargetModel,
    TFTTestRunTargetModel,
    TestingFarmResult,
    BuildStatus,
)
from packit_service.worker.events import (
    AbstractCoprBuildEvent,
    CoprBuildStartEvent,
    CoprBuildEndEvent,
    TestingFarmResultsEvent,
)
from packit_service.worker.events.enums import FedmsgTopic
from packit_service.worker.handlers import (
    CoprBuildEndHandler,
    CoprBuildStartHandler,
    TestingFarmResultsHandler,
)
from packit_service.worker.handlers.copr import AbstractCoprBuildReportHandler
from packit_service.worker.jobs import SteveJobs

logger = logging.getLogger(__name__)


def check_pending_testing_farm_runs() -> None:
    """Checks the status of pending TFT runs and updates it if needed."""
    logger.info("Getting pending TFT runs from DB")
    current_time = datetime.datetime.utcnow()
    not_completed = (
        TestingFarmResult.new,
        TestingFarmResult.queued,
        TestingFarmResult.running,
    )
    pending_test_runs = TFTTestRunTargetModel.get_all_by_status(*not_completed)
    for run in pending_test_runs:
        logger.debug(f"Checking status of TF pipeline {run.pipeline_id}")
        # .submitted_time can be None, we'll set it later
        if run.submitted_time:
            elapsed = current_time - run.submitted_time
            if elapsed.total_seconds() > DEFAULT_JOB_TIMEOUT:
                logger.info(
                    f"TF pipeline {run.pipeline_id} has been running for "
                    f"{elapsed.total_seconds()}, probably an internal error occurred. "
                    "Not checking it anymore."
                )
                run.set_status(TestingFarmResult.error)
                continue
        run_url = f"{TESTING_FARM_API_URL}requests/{run.pipeline_id}"
        response = requests.get(run_url)
        if not response.ok:
            logger.info(
                f"Failed to obtain state of TF pipeline {run.pipeline_id}. "
                f"Status code {response.status_code}. Reason: {response.reason}."
            )
            run.set_status(TestingFarmResult.error)
            continue

        details = response.json()
        (
            project_url,
            ref,
            result,
            summary,
            copr_build_id,
            copr_chroot,
            compose,
            log_url,
            created,
            identifier,
        ) = Parser.parse_data_from_testing_farm(run, details)

        logger.debug(f"Result for the TF pipeline {run.pipeline_id} is {result}.")
        if result in not_completed:
            logger.debug("Skip updating a pipeline which is not yet completed.")
            continue

        event = TestingFarmResultsEvent(
            pipeline_id=details["id"],
            result=result,
            compose=compose,
            summary=summary,
            log_url=log_url,
            copr_build_id=copr_build_id,
            copr_chroot=copr_chroot,
            commit_sha=ref,
            project_url=project_url,
            created=created,
            identifier=identifier,
        )

        package_config = event.get_package_config()
        if not package_config:
            logger.info(f"No config found for {run.pipeline_id}. Skipping.")
            continue

        job_configs = SteveJobs(event).get_config_for_handler_kls(
            handler_kls=TestingFarmResultsHandler,
        )

        event_dict = event.get_dict()
        for job_config in job_configs:
            handler = TestingFarmResultsHandler(
                package_config=event.package_config,
                job_config=job_config,
                event=event_dict,
            )
            # check for identifiers equality
            if handler.pre_check(package_config, job_config, event_dict):
                handler.run()


def check_pending_copr_builds() -> None:
    """Checks the status of pending copr builds and updates it if needed."""
    pending_copr_builds = CoprBuildTargetModel.get_all_by_status(BuildStatus.pending)
    builds_grouped_by_id = collections.defaultdict(list)
    for build in pending_copr_builds:
        builds_grouped_by_id[build.build_id].append(build)

    for build_id, builds in builds_grouped_by_id.items():
        update_copr_builds(build_id, builds)


def check_copr_build(build_id: int) -> bool:
    """
    Check the copr_build with given id and refresh the status if needed.

    Used in the babysit task.

    Args:
        build_id: ID of the copr build to check.

    Returns:
        Whether the run was successful, False signals the need to retry.
    """
    logger.debug(f"Getting copr build ID {build_id} from DB.")
    builds = list(CoprBuildTargetModel.get_all_by_build_id(build_id))
    if not builds:
        logger.warning(f"Copr build {build_id} not in DB.")
        return True
    return update_copr_builds(build_id, builds)


def update_copr_builds(build_id: int, builds: Iterable["CoprBuildTargetModel"]) -> bool:
    """
    Updates the state of copr builds.

    Builds which have ended will be updated into success/fail state.
    Builds which have been pending for too long will be updated to error (timeout).
    Builds which have started and are waiting for SRPM will get their
        CoprBuildTargetModel and SRPMBuildModel updated (to cover the case
        where we do not correctly react to fedmsg).

    Args:
        build_id: ID of the copr build to update.
        builds: List of builds corresponding to the given ``build_id``.

    Returns:
        Whether the run was successful and the build has ended,
        False signals the need to retry again.
    """
    copr_client = CoprClient.create_from_config_file()
    try:
        build_copr = copr_client.build_proxy.get(build_id)
    except copr.v3.CoprNoResultException:
        logger.info(
            f"Copr build {build_id} no longer available. Setting it to error status and "
            f"not checking it anymore."
        )
        for build in builds:
            build.set_status(BuildStatus.error)
        return True

    if not build_copr.ended_on and not build_copr.started_on:
        logger.info("The copr build has not started yet.")
        return False

    logger.info(f"The status is {build_copr.state!r}.")

    current_time = datetime.datetime.utcnow()
    for build in builds:
        elapsed = current_time - build.build_submitted_time
        if elapsed.total_seconds() > DEFAULT_JOB_TIMEOUT:
            logger.info(
                f"The build {build_id} has been running for "
                f"{elapsed.total_seconds()}, probably an internal error"
                f"occurred. Not checking it anymore."
            )
            build.set_status(BuildStatus.error)
            continue
        if build.status not in (BuildStatus.pending, BuildStatus.waiting_for_srpm):
            logger.info(
                f"DB state says {build.status!r}, "
                "things were taken care of already, skipping."
            )
            continue
        chroot_build = copr_client.build_chroot_proxy.get(build_id, build.target)
        update_copr_build_state(build, build_copr, chroot_build)
    # Builds which we ran CoprBuildStartHandler for still need to be monitored.
    return bool(build_copr.ended_on)


def update_copr_build_state(
    build: CoprBuildTargetModel, build_copr: Any, chroot_build_copr: Any
) -> None:
    """
    Updates the state of the given copr build chroot.

    If the build ended, its state will be updated using CoprBuildEndHandler.
    If the build is waiting for SRPM and only started (not ended), it will
        be initialized using CoprBuildStartHandler.

    Args:
        build: Model of the copr build to update.
        build_copr: Data of the whole copr build from the copr API.
        chroot_build_copr: Data of the single build chroot from the copr API.

    """
    event_kls: Type[AbstractCoprBuildEvent]
    handler_kls: Type[AbstractCoprBuildReportHandler]
    if build_copr.ended_on:
        event_kls = CoprBuildEndEvent
        handler_kls = CoprBuildEndHandler
        timestamp = chroot_build_copr.ended_on
    elif build_copr.started_on and build.status == BuildStatus.waiting_for_srpm:
        event_kls = CoprBuildStartEvent
        handler_kls = CoprBuildStartHandler
        timestamp = chroot_build_copr.started_on
    else:
        # Nothing to do
        return
    event = event_kls(
        topic=FedmsgTopic.copr_build_finished.value,
        build_id=build.build_id,
        build=build,
        chroot=build.target,
        status=(
            # This is fine even for CoprBuildStartHandler (it ignores the
            # status value).
            COPR_API_SUCC_STATE
            if chroot_build_copr.state == COPR_SUCC_STATE
            else COPR_API_FAIL_STATE
        ),
        owner=build.owner,
        project_name=build.project_name,
        pkg=build_copr.source_package.get("name", ""),  # this seems to be the SRPM name
        timestamp=timestamp,
    )

    package_config = event.get_package_config()
    if not package_config:
        logger.info(f"No config found for {build.build_id}. Skipping.")
        return

    job_configs = SteveJobs(event).get_config_for_handler_kls(
        handler_kls=handler_kls,
    )

    for job_config in job_configs:
        event_dict = event.get_dict()
        handler = handler_kls(
            package_config=event.package_config,
            job_config=job_config,
            event=event_dict,
        )
        if handler.pre_check(package_config, job_config, event_dict):
            handler.run()
