# MIT License
#
# Copyright (c) 2018-2019 Red Hat, Inc.

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
import logging

from copr.v3 import Client as CoprClient

from packit_service.constants import (
    COPR_API_FAIL_STATE,
    COPR_API_SUCC_STATE,
    COPR_SUCC_STATE,
)
from packit_service.models import CoprBuildModel
from packit_service.service.events import CoprBuildEvent, EventData, FedmsgTopic
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
        event = CoprBuildEvent(
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
                data=EventData.from_event_dict(event.get_dict()),
                copr_event=event,
            ).run()
    return True
