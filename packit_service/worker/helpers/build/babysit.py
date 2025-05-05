# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import collections
import logging
from collections.abc import Iterable
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import celery
import copr.v3
import requests
from celery.canvas import Signature
from copr.v3 import Client as CoprClient
from requests import HTTPError

from packit_service.constants import (
    COPR_API_FAIL_STATE,
    COPR_API_SUCC_STATE,
    COPR_FAIL_STATE,
    COPR_SRPM_CHROOT,
    COPR_SUCC_STATE,
    DEFAULT_JOB_TIMEOUT,
    TESTING_FARM_API_URL,
)
from packit_service.events import copr as copr_events
from packit_service.events import (
    testing_farm,
    vm_image,
)
from packit_service.events.enums import FedmsgTopic
from packit_service.models import (
    BuildStatus,
    CoprBuildTargetModel,
    SRPMBuildModel,
    TestingFarmResult,
    TFTTestRunTargetModel,
    VMImageBuildStatus,
    VMImageBuildTargetModel,
)
from packit_service.utils import elapsed_seconds
from packit_service.worker.handlers import (
    CoprBuildEndHandler,
    CoprBuildStartHandler,
    TestingFarmResultsHandler,
    VMImageBuildResultHandler,
)
from packit_service.worker.handlers.copr import AbstractCoprBuildReportHandler
from packit_service.worker.handlers.mixin import GetVMImageBuilderMixin
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.mixin import ConfigFromUrlMixin
from packit_service.worker.parser import Parser

logger = logging.getLogger(__name__)


def celery_run_async(signatures: list[Signature]) -> None:
    logger.debug("Signatures are going to be sent to Celery (from babysit task).")
    # https://docs.celeryq.dev/en/stable/userguide/canvas.html#groups
    celery.group(signatures).apply_async()
    logger.debug("Signatures were sent to Celery.")


def check_pending_testing_farm_runs() -> None:
    """Checks the status of pending TFT runs and updates it if needed."""
    logger.info("Getting pending TFT runs from DB")
    current_time = datetime.now(timezone.utc)
    not_completed = (
        TestingFarmResult.new,
        TestingFarmResult.queued,
        TestingFarmResult.running,
        TestingFarmResult.cancel_requested,
    )
    pending_test_runs = TFTTestRunTargetModel.get_all_by_status(*not_completed)
    for run in pending_test_runs:
        logger.debug(f"Checking status of TF pipeline {run.pipeline_id}")
        # .submitted_time can be None, we'll set it later
        if run.submitted_time:
            elapsed = elapsed_seconds(begin=run.submitted_time, end=current_time)
            if elapsed > DEFAULT_JOB_TIMEOUT:
                logger.info(
                    f"TF pipeline {run.pipeline_id} has been running for "
                    f"{elapsed}s, probably an internal error occurred. "
                    "Not checking it anymore.",
                )
                run.set_status(TestingFarmResult.error)
                continue
        run_url = f"{TESTING_FARM_API_URL}requests/{run.pipeline_id}"
        response = requests.get(run_url)
        if not response.ok:
            logger.info(
                f"Failed to obtain state of TF pipeline {run.pipeline_id}. "
                f"Status code {response.status_code}. Reason: {response.reason}. "
                "Let's try again later.",
            )
            continue

        details = response.json()
        data = Parser.parse_data_from_testing_farm(run, details)

        logger.debug(f"Result for the TF pipeline {run.pipeline_id} is {data.result}.")
        if data.result in not_completed:
            logger.debug("Skip updating a pipeline which is not yet completed.")
            continue
        event = testing_farm.Result(
            pipeline_id=details["id"],
            result=data.result,
            compose=data.compose,
            summary=data.summary,
            log_url=data.log_url,
            copr_build_id=data.copr_build_id,
            copr_chroot=data.copr_chroot,
            commit_sha=data.ref,
            project_url=data.project_url,
            created=data.created,
            identifier=data.identifier,
        )
        try:
            update_testing_farm_run(event, run)
        except Exception as ex:
            logger.debug(
                f"There was an exception when updating the Testing farm run "
                f"with pipeline ID {run.pipeline_id}: {ex}",
            )


def update_testing_farm_run(event: testing_farm.Result, run: TFTTestRunTargetModel):
    """
    Updates the state of the Testing Farm run.
    """
    packages_config = event.get_packages_config()
    if not packages_config:
        logger.info(f"No config found for {run.pipeline_id}. Skipping.")
        return

    job_configs = SteveJobs(event).get_config_for_handler_kls(
        handler_kls=TestingFarmResultsHandler,
    )

    event_dict = event.get_dict()
    signatures = []
    for job_config in job_configs:
        package_config = (
            event.packages_config.get_package_config_for(job_config)
            if event.packages_config
            else None
        )
        handler = TestingFarmResultsHandler(
            package_config=package_config,
            job_config=job_config,
            event=event_dict,
        )
        # check for identifiers equality
        if handler.pre_check(package_config, job_config, event_dict):
            signatures.append(handler.get_signature(event=event, job=job_config))

    celery_run_async(signatures=signatures)


def check_pending_copr_builds() -> None:
    """Checks the status of pending copr builds and updates it if needed."""
    pending_copr_builds = CoprBuildTargetModel.get_all_by_status(BuildStatus.pending)
    builds_grouped_by_id = collections.defaultdict(list)
    for build in pending_copr_builds:
        # our DB uses str(build_id) but our code expects int(build_id)
        builds_grouped_by_id[int(build.build_id)].append(build)

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
            f"not checking it anymore.",
        )
        for build in builds:
            build.set_status(BuildStatus.error)
        return True

    if not build_copr.ended_on and not build_copr.started_on:
        logger.info(f"The copr build {build_id} has not started yet.")
        return False

    logger.info(f"The status of {build_id} is {build_copr.state!r}.")

    if (
        srpm_build := SRPMBuildModel.get_by_copr_build_id(build_id)
    ) and srpm_build.status == BuildStatus.pending:
        try:
            build_copr_srpm = copr_client.build_proxy.get_source_chroot(build_id)
        except copr.v3.CoprNoResultException:
            logger.info(
                f"SRPM build of Copr build {build_id} no longer available. "
                "Setting it to error status and not checking it anymore.",
            )
            srpm_build.set_status(BuildStatus.error)
        else:
            try:
                update_srpm_build_state(srpm_build, build_copr, build_copr_srpm)
            except Exception as ex:
                logger.debug(
                    f"There was an exception when updating the SRPM build of"
                    f" Copr build {build_id}: {ex}",
                )
                return False

    current_time = datetime.now(timezone.utc)
    for build in builds:
        elapsed = elapsed_seconds(begin=build.build_submitted_time, end=current_time)
        if elapsed > DEFAULT_JOB_TIMEOUT:
            logger.info(
                f"The build {build_id} has been running for "
                f"{elapsed}s, probably an internal error"
                f"occurred. Not checking it anymore.",
            )
            build.set_status(BuildStatus.error)
            continue
        if build.status not in (BuildStatus.pending, BuildStatus.waiting_for_srpm):
            logger.info(
                f"DB state of {build_id} says {build.status!r}, "
                "things were taken care of already, skipping.",
            )
            continue
        try:
            chroot_build = copr_client.build_chroot_proxy.get(build_id, build.target)
        except copr.v3.CoprNoResultException:
            logger.info(
                f"Copr build {build_id} for {build.target} no longer available. "
                "Setting it to error status and not checking it anymore.",
            )
            build.set_status(BuildStatus.error)
            continue
        try:
            update_copr_build_state(build, build_copr, chroot_build)
        except Exception as ex:
            logger.debug(
                f"There was an exception when updating the Copr build {build_id} for"
                f" {build.target}: {ex}",
            )
            return False
    # Builds which we ran CoprBuildStartHandler for still need to be monitored.
    return bool(build_copr.ended_on)


def update_srpm_build_state(
    build: SRPMBuildModel,
    build_copr: Any,
    build_copr_srpm: Any,
) -> None:
    """
    Updates the state of the given SRPM build.

    If the build ended, its state will be updated using CoprBuildEndHandler.

    Args:
        build: Model of the SRPM build to update.
        build_copr: Data of the whole copr build from the copr API.
        build_copr_srpm: Data of the associated SRPM build from the copr API.

    """
    if build_copr_srpm.state not in (COPR_SUCC_STATE, COPR_FAIL_STATE):
        # Nothing to do
        return

    event = copr_events.End(
        topic=FedmsgTopic.copr_build_finished.value,
        build_id=int(
            build.copr_build_id,
        ),  # we expect int there even though we have str in DB
        build=build,
        chroot=COPR_SRPM_CHROOT,
        status=(
            COPR_API_SUCC_STATE if build_copr_srpm.state == COPR_SUCC_STATE else COPR_API_FAIL_STATE
        ),
        owner=build_copr.ownername,
        project_name=build_copr.projectname,
        pkg=build_copr.source_package.get("name", ""),  # this seems to be the SRPM name
        timestamp=build_copr.ended_on,
    )

    packages_config = event.get_packages_config()
    if not packages_config:
        logger.info(f"No config found for {build.copr_build_id}. Skipping.")
        return

    job_configs = SteveJobs(event).get_config_for_handler_kls(
        handler_kls=CoprBuildEndHandler,
    )

    signatures = []
    for job_config in job_configs:
        event_dict = event.get_dict()
        package_config = (
            event.packages_config.get_package_config_for(job_config)
            if event.packages_config
            else None
        )
        handler = CoprBuildEndHandler(
            package_config=package_config,
            job_config=job_config,
            event=event_dict,
        )
        if handler.pre_check(package_config, job_config, event_dict):
            signatures.append(handler.get_signature(event=event, job=job_config))

    celery_run_async(signatures=signatures)


def update_copr_build_state(
    build: CoprBuildTargetModel,
    build_copr: Any,
    chroot_build_copr: Any,
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
    event_kls: type[copr.CoprBuild]
    handler_kls: type[AbstractCoprBuildReportHandler]
    if chroot_build_copr.ended_on:
        event_kls = copr_events.End
        handler_kls = CoprBuildEndHandler
        timestamp = chroot_build_copr.ended_on
    elif build_copr.started_on and build.status == BuildStatus.waiting_for_srpm:
        event_kls = copr_events.Start
        handler_kls = CoprBuildStartHandler
        timestamp = chroot_build_copr.started_on
    else:
        # Nothing to do
        return
    event = event_kls(
        topic=FedmsgTopic.copr_build_finished.value,
        build_id=int(
            build.build_id,
        ),  # we expect int there even though we have str in DB
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

    packages_config = event.get_packages_config()
    if not packages_config:
        logger.info(f"No config found for {build.build_id}. Skipping.")
        return

    job_configs = SteveJobs(event).get_config_for_handler_kls(
        handler_kls=handler_kls,
    )

    signatures = []
    for job_config in job_configs:
        event_dict = event.get_dict()
        package_config = (
            event.packages_config.get_package_config_for(job_config)
            if event.packages_config
            else None
        )
        handler = handler_kls(
            package_config=package_config,
            job_config=job_config,
            event=event_dict,
        )
        if handler.pre_check(package_config, job_config, event_dict):
            signatures.append(handler.get_signature(event=event, job=job_config))

    celery_run_async(signatures=signatures)


class UpdateImageBuildHelper(ConfigFromUrlMixin, GetVMImageBuilderMixin):
    def __init__(self, project_url) -> None:
        self._project_url = project_url


class ImageBuildUploadType(str, Enum):
    aws = "aws"
    awss3 = "aws.s3"
    azure = "azure"
    gcp = "gcp"


def get_message_for_successful_build(image_status_body):
    message = (
        "Congratulations! Your image was successfully built.\n"
        "You will find it shared with your cloud provider account.\n"
    )
    upload_status = image_status_body["upload_status"]
    upload_status_type = upload_status["type"]
    if upload_status_type == ImageBuildUploadType.aws:
        region = upload_status["options"]["region"]
        ami = upload_status["options"]["ami"]
        message += (
            f"https://console.aws.amazon.com/ec2/v2/home?"
            f"region={region}"
            f"#LaunchInstanceWizard:ami={ami}\n\n"
        )
    if upload_status_type == ImageBuildUploadType.awss3:
        message += upload_status["options"]["url"]
    if upload_status_type == ImageBuildUploadType.azure:
        image_name = upload_status["options"]["image_name"]
        message += f"The image name is {image_name}"
    if upload_status_type == ImageBuildUploadType.gcp:
        image_name = upload_status["options"]["image_name"]
        project_id = upload_status["options"]["project_id"]
        message += f"The image name is {image_name} for project id {project_id}"
    return message


def update_vm_image_build(build_id: int, build: "VMImageBuildTargetModel"):
    """
    Updates the state of a vm image build if ended.

    Args:
        build_id (int): ID of the built image to update.
        build VMImageBuildTargetModel: build data for ``build_id``.

    Returns:
        bool: Whether the run was successful, False signals the need to retry.
    """
    helper = UpdateImageBuildHelper(build.project_url)

    status = None
    response = None
    try:
        response = helper.vm_image_builder.image_builder_request(
            "GET",
            f"composes/{build_id}",
        )
        body = response.json()
        status = body["image_status"]["status"]
    except HTTPError as ex:
        message = f"No response for VM Image Build {build_id}: {ex}"
        logger.debug(message)
        status = VMImageBuildStatus.error
    except Exception as ex:
        message = (
            f"There was an exception when getting status of the VM Image Build {build_id}: {ex}"
        )
        logger.error(message)
        # keep polling
        return False

    if status in (
        VMImageBuildStatus.pending,
        VMImageBuildStatus.building,
        VMImageBuildStatus.uploading,
        VMImageBuildStatus.registering,
    ):
        return False  # keep polling; build not complete yet

    if status == VMImageBuildStatus.failure:
        error = body["image_status"]["error"]
        message = f"VM image build {build.build_id} failed: {error}"
        logger.debug(message)

    if status == VMImageBuildStatus.success:
        message = get_message_for_successful_build(body["image_status"])
        logger.debug(message)

    event = vm_image.Result(
        build.build_id,
        build.target,
        build.get_pr_id(),
        build.owner,
        build.commit_sha,
        build.project_url,
        status,
        message,
        str(datetime.utcnow()),
    )

    packages_config = event.get_packages_config()
    if not packages_config:
        build.set_status(status)
        logger.debug(
            f"No package config found for {build.build_id}. No feedback can be given to the user.",
        )
        return True

    job_configs = SteveJobs(event).get_config_for_handler_kls(
        handler_kls=VMImageBuildResultHandler,
    )

    event_dict = event.get_dict()
    results = []
    for job_config in job_configs:
        package_config = (
            event.packages_config.get_package_config_for(job_config)
            if event.packages_config
            else None
        )
        handler = VMImageBuildResultHandler(
            package_config=package_config,
            job_config=job_config,
            event=event_dict,
        )

        if handler.pre_check(package_config, job_config, event_dict):
            results.extend(handler.run_job())

    if results:
        return True

    build.set_status(status)
    logger.debug(
        f"Something went wrong retrieving job configs for {build.build_id}. "
        "No feedback can be given to the user.",
    )
    return True


def check_pending_vm_image_builds() -> None:
    """Checks the status of pending vm image builds and updates it if needed.

    Inside our db all builds are just pending but if you check the
    VM Image Build server you may find out that an image build could be:
    - pending
    - building
    - uploading
    - registering
    """
    pending_vm_image_builds = VMImageBuildTargetModel.get_all_by_status(
        VMImageBuildStatus.pending,
    )
    current_time = datetime.now(timezone.utc)
    for build in pending_vm_image_builds:
        logger.debug(f"Checking status of VM image build {build.build_id}")
        if build.build_submitted_time:
            elapsed = elapsed_seconds(
                begin=build.build_submitted_time,
                end=current_time,
            )
            if elapsed > DEFAULT_JOB_TIMEOUT:
                logger.info(
                    f"VM image build {build.build_id} has been running for "
                    f"{elapsed}s, probably an internal error occurred. "
                    "Not checking it anymore.",
                )
                build.set_status(VMImageBuildStatus.error)
                continue
        update_vm_image_build(build.build_id, build)
