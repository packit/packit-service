# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from typing import Union

from packit.config import (
    JobType,
)

from packit_service.models import OSHScanStatus
from packit_service.worker.checker.abstract import Checker
from packit_service.worker.checker.open_scan_hub import RawhideX86Target
from packit_service.worker.events import (
    OpenScanHubTaskFinishedEvent,
    OpenScanHubTaskStartedEvent,
)
from packit_service.worker.handlers.abstract import (
    RetriableJobHandler,
    TaskName,
    configured_as,
    reacts_to,
)
from packit_service.worker.handlers.mixin import (
    ConfigFromEventMixin,
)
from packit_service.worker.helpers.build import CoprBuildJobHelper
from packit_service.worker.helpers.open_scan_hub import OpenScanHubHelper
from packit_service.worker.mixin import (
    LocalProjectMixin,
    PackitAPIWithUpstreamMixin,
)
from packit_service.worker.reporting import BaseCommitStatus
from packit_service.worker.result import TaskResults

logger = logging.getLogger(__name__)


class OpenScanHubAbstractHandler(
    RetriableJobHandler,
    LocalProjectMixin,
    ConfigFromEventMixin,
    PackitAPIWithUpstreamMixin,
):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.event: Union[OpenScanHubTaskFinishedEvent | OpenScanHubTaskStartedEvent] = (
            self.data.to_event()
        )

    @staticmethod
    def get_checkers() -> tuple[type[Checker], ...]:
        return (RawhideX86Target,)

    def get_helper(self) -> OpenScanHubHelper:
        build_helper = CoprBuildJobHelper(
            service_config=self.service_config,
            package_config=self.package_config,
            project=self.project,
            metadata=self.data,
            db_project_event=self.data.db_project_event,
            job_config=self.job_config,
            celery_task=self.celery_task,
        )

        return OpenScanHubHelper(
            copr_build_helper=build_helper,
            build=self.event.build,
        )

    def check_scan_and_build(self):
        task_id = self.data.event_dict["task_id"]
        if not self.event.scan or not self.event.build:
            return TaskResults(
                success=True,
                details={
                    "msg": f"Scan {task_id} not found or not associated with a Copr build",
                },
            )

        if not self.job_config:
            return TaskResults(
                success=True,
                details={
                    "msg": (
                        "No job configuration found for OpenScanHub task" f" in {self.project.repo}"
                    ),
                },
            )

        return None


@configured_as(job_type=JobType.copr_build)
@reacts_to(OpenScanHubTaskFinishedEvent)
class OpenScanHubTaskFinishedHandler(
    OpenScanHubAbstractHandler,
):
    event: OpenScanHubTaskFinishedEvent
    task_name = TaskName.openscanhub_task_finished

    def run(self) -> TaskResults:
        self.check_scan_and_build()

        if self.event.status == OpenScanHubTaskFinishedEvent.Status.success:
            state = BaseCommitStatus.success
            description = "Scan in OpenScanHub is finished. Check the URL for more details."
            external_links = {
                "Added issues": self.event.issues_added_url,
                "Fixed issues": self.event.issues_fixed_url,
                "Scan results": self.event.scan_results_url,
            }
            self.event.scan.set_status(OSHScanStatus.succeeded)
            self.event.scan.set_issues_added_url(self.event.issues_added_url)
            self.event.scan.set_issues_fixed_url(self.event.issues_fixed_url)
            self.event.scan.set_scan_results_url(self.event.scan_results_url)
        else:
            state = BaseCommitStatus.neutral
            description = f"Scan in OpenScanHub is finished in a {self.event.status} state."
            external_links = {}
            if self.event.status == OpenScanHubTaskFinishedEvent.Status.cancel:
                self.event.scan.set_status(OSHScanStatus.canceled)
            else:
                self.event.scan.set_status(OSHScanStatus.failed)

        self.get_helper().report(
            state=state,
            description=description,
            url=self.event.scan.url,
            links_to_external_services=external_links,
        )

        return TaskResults(
            success=True,
            details={},
        )


@configured_as(job_type=JobType.copr_build)
@reacts_to(OpenScanHubTaskStartedEvent)
class OpenScanHubTaskStartedHandler(
    OpenScanHubAbstractHandler,
):
    task_name = TaskName.openscanhub_task_started

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.event: OpenScanHubTaskStartedEvent = self.data.to_event()

    def run(self) -> TaskResults:
        self.check_scan_and_build()

        state = BaseCommitStatus.running
        description = "Scan in OpenScanHub has started."
        self.event.scan.set_status(OSHScanStatus.running)

        self.get_helper().report(
            state=state,
            description=description,
            url=self.event.scan.url,
        )

        return TaskResults(
            success=True,
            details={},
        )
