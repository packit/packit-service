# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from typing import Tuple, Type

from packit.config import (
    JobType,
    aliases,
)
from packit_service.worker.checker.abstract import Checker
from packit_service.worker.events import (
    OpenScanHubTaskFinishEvent,
)
from packit_service.worker.handlers.abstract import (
    RetriableJobHandler,
    TaskName,
    configured_as,
    reacts_to,
)
from packit_service.worker.helpers.build import CoprBuildJobHelper
from packit_service.worker.helpers.scan import ScanHelper

from packit_service.worker.handlers.mixin import (
    ConfigFromEventMixin,
)
from packit_service.worker.result import TaskResults
from packit_service.worker.mixin import (
    LocalProjectMixin,
    PackitAPIWithUpstreamMixin,
)

from packit_service.worker.reporting import BaseCommitStatus

logger = logging.getLogger(__name__)


@configured_as(job_type=JobType.copr_build)
@reacts_to(OpenScanHubTaskFinishEvent)
class OpenScanHubTaskFinishHandler(
    RetriableJobHandler,
    LocalProjectMixin,
    ConfigFromEventMixin,
    PackitAPIWithUpstreamMixin,
):
    task_name = TaskName.openscanhub_task_finish

    @staticmethod
    def get_checkers() -> Tuple[Type[Checker], ...]:
        return ()

    def run(self) -> TaskResults:
        task_id = self.data.event_dict["task_id"]
        event = self.data.to_event()
        if not event.scan or not event.build:
            return TaskResults(
                success=False,
                details={
                    "msg": f"Scan {task_id} not found or not associated with a Copr build"
                },
            )
        elif not self.job_config:
            return TaskResults(
                success=False,
                details={
                    "msg": (
                        "No job configuration found for "
                        f"openscanhub_task_finish in {self.project.repo}"
                    )
                },
            )

        branches = aliases.get_build_targets(
            *self.job_config.targets,
        )
        if "fedora-rawhide-x86_64" not in branches:
            return TaskResults(
                success=False,
                details={
                    "msg": "Skipping job configuration with no fedora-rawhide-x86_64 target."
                },
            )

        build_helper = CoprBuildJobHelper(
            service_config=self.service_config,
            package_config=self.package_config,
            project=self.project,
            metadata=self.data,
            db_project_event=self.data.db_project_event,
            job_config=self.job_config,
            celery_task=self.celery_task,
        )

        scan_helper = ScanHelper(copr_build_helper=build_helper, build=event.build)

        external_links = {
            "Added issues": event.issues_added_url,
            "Fixed issues": event.issues_fixed_url,
            "Scan results": event.scan_results_url,
        }

        # TODO: probably we need a babysit task for when the build is not finished yet
        if event.build.status == "success":
            state = BaseCommitStatus.success
            description = (
                "Scan in OpenScanHub is finished. Check the URL for more details."
            )
        else:
            state = BaseCommitStatus.neutral
            description = (
                "Scan in OpenScanHub is finished but the build did not "
                "finish yet or did not succeed."
            )

        scan_helper.report(
            state=state,
            description=description,
            url=event.scan_results_url,
            links_to_external_services=external_links,
        )

        return TaskResults(
            success=True,
            details={},
        )
