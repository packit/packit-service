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
from typing import Optional

from copr.v3 import Client as CoprClient

from packit_service.celerizer import celery_app
from packit_service.config import ServiceConfig
from packit_service.constants import (
    COPR_SUCC_STATE,
    COPR_API_SUCC_STATE,
    COPR_API_FAIL_STATE,
)
from packit_service.models import CoprBuildModel, TaskResultModel
from packit_service.service.events import CoprBuildEvent, FedmsgTopic
from packit_service.worker.handlers import CoprBuildEndHandler
from packit_service.worker.jobs import SteveJobs

logger = logging.getLogger(__name__)

# debug logs of these are super-duper verbose
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("github").setLevel(logging.WARNING)
logging.getLogger("kubernetes").setLevel(logging.WARNING)
# info is just enough
logging.getLogger("ogr").setLevel(logging.INFO)
# easier debugging
logging.getLogger("packit").setLevel(logging.DEBUG)
logging.getLogger("sandcastle").setLevel(logging.DEBUG)


@celery_app.task(name="task.steve_jobs.process_message", bind=True)
def process_message(self, event: dict, topic: str = None) -> Optional[dict]:
    task_results: dict = SteveJobs().process_message(event=event, topic=topic)
    if task_results:
        TaskResultModel.add_task_result(
            task_id=self.request.id, task_result_dict=task_results
        )
    return task_results


@celery_app.task(
    bind=True,
    name="task.babysit_copr_build",
    retry_backoff=60,  # retry again in 60s, 120s, 240s, 480s...
    retry_backoff_max=60 * 60 * 8,  # is 8 hours okay? gcc/kernel build really long
    max_retries=7,
)
def babysit_copr_build(self, build_id: int):
    """ check status of a copr build and update it in DB """
    logger.debug(f"getting copr build ID {build_id} from DB")
    builds = CoprBuildModel.get_all_by_build_id(build_id)
    if builds:
        copr_client = CoprClient.create_from_config_file()
        build_copr = copr_client.build_proxy.get(build_id)

        if not build_copr.ended_on:
            logger.info("The copr build is still in progress")
            self.retry()
        logger.info(f"The status is {build_copr.state}")

        # copr doesn't tell status of how a build in the chroot went:
        #   https://bugzilla.redhat.com/show_bug.cgi?id=1813227
        for build in builds:
            if build.status != "pending":
                logger.info(
                    f"DB state says {build.status}, "
                    "things were taken care of already, skipping."
                )
                continue
            event = CoprBuildEvent(
                topic=FedmsgTopic.copr_build_finished.value,
                build_id=build_id,
                build={},
                chroot=build.target,
                status=(
                    COPR_API_SUCC_STATE
                    if build_copr.state == COPR_SUCC_STATE
                    else COPR_API_FAIL_STATE
                ),
                owner=build.owner,
                project_name=build.project_name,
                pkg=build_copr.source_package.get(
                    "name", ""
                ),  # this seems to be the SRPM name
                build_pg=build,
            )
            CoprBuildEndHandler(
                ServiceConfig.get_service_config(), job_config=None, event=event
            ).run()
    else:
        logger.warning(f"Copr build {build_id} not in DB.")
