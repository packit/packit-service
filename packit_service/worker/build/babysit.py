# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging

from copr.v3 import Client as CoprClient

from packit_service.constants import (
    COPR_API_FAIL_STATE,
    COPR_API_SUCC_STATE,
    COPR_SUCC_STATE,
)
from packit_service.models import CoprBuildModel
from packit_service.service.events import AbstractCoprBuildEvent
from packit_service.service.events.enums import FedmsgTopic
from packit_service.worker.handlers import CoprBuildEndHandler
from packit_service.worker.jobs import get_config_for_handler_kls

logger = logging.getLogger(__name__)


def check_copr_build(build_id: int) -> bool:
    """
    Check the copr_build with given id and refresh the status if needed.

    Used in the babysit task.

    :param build_id: id of the copr_build (CoprBuildModel.build.id)
    :return: True if in case of successful run, False when we need to retry
    """
    logger.debug(f"Getting copr build ID {build_id} from DB.")
    builds = CoprBuildModel.get_all_by_build_id(build_id)
    if not builds:
        logger.warning(f"Copr build {build_id} not in DB.")
        return True

    copr_client = CoprClient.create_from_config_file()
    build_copr = copr_client.build_proxy.get(build_id)

    if not build_copr.ended_on:
        logger.info("The copr build is still in progress.")
        return False

    logger.info(f"The status is {build_copr.state!r}.")

    for build in builds:
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

        job_configs = get_config_for_handler_kls(
            handler_kls=CoprBuildEndHandler,
            event=event,
            package_config=event.get_package_config(),
        )

        for job_config in job_configs:
            CoprBuildEndHandler(
                package_config=event.package_config,
                job_config=job_config,
                event=event.get_dict(),
            ).run()
    return True
