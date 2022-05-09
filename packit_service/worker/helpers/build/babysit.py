# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import collections
import datetime
import logging
from typing import Iterable

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
)
from packit_service.worker.events import AbstractCoprBuildEvent, TestingFarmResultsEvent
from packit_service.worker.events.enums import FedmsgTopic
from packit_service.worker.handlers import (
    CoprBuildEndHandler,
    TestingFarmResultsHandler,
)
from packit_service.worker.jobs import get_config_for_handler_kls

logger = logging.getLogger(__name__)


def check_pending_testing_farm_runs() -> None:
    """Checks the status of pending TFT runs and updates it if needed."""
    logger.debug("Getting pending TFT runs from DB")
    current_time = datetime.datetime.utcnow()
    not_completed = (
        TestingFarmResult.new,
        TestingFarmResult.queued,
        TestingFarmResult.running,
    )
    pending_test_runs = TFTTestRunTargetModel.get_all_by_status(*not_completed)
    for run in pending_test_runs:
        logger.info(f"Checking status of pipeline with id {run.pipeline_id}")
        # .submitted_time can be None, we'll set it later
        if run.submitted_time:
            elapsed = current_time - run.submitted_time
            if elapsed.total_seconds() > DEFAULT_JOB_TIMEOUT:
                logger.info(
                    f"Pipeline has been running for {elapsed.total_seconds()},"
                    f"probably an internal error occurred. Not checking it anymore."
                )
                run.set_status(TestingFarmResult.error)
                continue
        endpoint = "requests/"
        run_url = f"{TESTING_FARM_API_URL}{endpoint}{run.pipeline_id}"
        response = requests.get(run_url)
        if not response.ok:
            logger.info(
                f"Failed to obtain state of testing farm pipeline "
                f"id {run.pipeline_id} (status code {response.status_code}. "
                f"Reason: {response.reason}."
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
        ) = Parser.parse_data_from_testing_farm(run, details)

        logger.info(
            f"Result for the testing farm pipeline {run.pipeline_id} is {result}."
        )
        if result in not_completed:
            logger.info("Skip updating a pipeline which is not yet completed.")
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
        )

        package_config = event.get_package_config()
        if not package_config:
            logger.info(f"No config found for {run.pipeline_id}. Skipping.")
            continue

        job_configs = get_config_for_handler_kls(
            handler_kls=TestingFarmResultsHandler,
            event=event,
            package_config=package_config,
        )

        for job_config in job_configs:
            TestingFarmResultsHandler(
                package_config=event.package_config,
                job_config=job_config,
                event=event.get_dict(),
            ).run()


def check_pending_copr_builds() -> None:
    """Checks the status of pending copr builds and updates it if needed."""
    pending_copr_builds = CoprBuildTargetModel.get_all_by_status("pending")
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
        build_id (int): ID of the copr build to check.

    Returns:
        bool: Whether the run was successful, False signals the need to retry.
    """
    logger.debug(f"Getting copr build ID {build_id} from DB.")
    builds = CoprBuildTargetModel.get_all_by_build_id(build_id)
    if not builds:
        logger.warning(f"Copr build {build_id} not in DB.")
        return True
    return update_copr_builds(build_id, builds)


def update_copr_builds(build_id: int, builds: Iterable["CoprBuildTargetModel"]) -> bool:
    """
    Updates the state of copr builds if they have ended.

    Args:
        build_id (int): ID of the copr build to update.
        builds (Iterable[CoprBuildTargetModel]): List of builds corresponding to
            the given ``build_id``.

    Returns:
        bool: Whether the run was successful, False signals the need to retry.
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
            build.set_status("error")
        return True

    if not build_copr.ended_on:
        logger.info("The copr build is still in progress.")
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
            build.set_status("error")
            continue
        if build.status != "pending":
            logger.info(
                f"DB state says {build.status!r}, "
                "things were taken care of already, skipping."
            )
            continue
        chroot_build = copr_client.build_chroot_proxy.get(build_id, build.target)
        event = AbstractCoprBuildEvent(
            topic=FedmsgTopic.copr_build_finished.value,
            build_id=build_id,
            build=build,
            chroot=build.target,
            status=(
                COPR_API_SUCC_STATE
                if chroot_build.state == COPR_SUCC_STATE
                else COPR_API_FAIL_STATE
            ),
            owner=build.owner,
            project_name=build.project_name,
            pkg=build_copr.source_package.get(
                "name", ""
            ),  # this seems to be the SRPM name
            timestamp=chroot_build.ended_on,
        )

        package_config = event.get_package_config()
        if not package_config:
            logger.info(f"No config found for {build_id}. Skipping.")
            continue

        job_configs = get_config_for_handler_kls(
            handler_kls=CoprBuildEndHandler,
            event=event,
            package_config=package_config,
        )

        for job_config in job_configs:
            CoprBuildEndHandler(
                package_config=event.package_config,
                job_config=job_config,
                event=event.get_dict(),
            ).run()
    return True
