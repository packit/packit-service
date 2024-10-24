# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import enum
from logging import getLogger
from typing import Optional

from ogr.abstract import GitProject

from packit_service.config import ServiceConfig
from packit_service.models import (
    AbstractProjectObjectDbType,
    OSHScanModel,
    ProjectEventModel,
)
from packit_service.worker.events.event import AbstractResultEvent

logger = getLogger(__name__)


class OpenScanHubTaskAbstractEvent(AbstractResultEvent):
    def __init__(
        self,
        task_id: int,
        commit_sha: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)

        self.task_id = task_id
        self.commit_sha = commit_sha

        self.scan = OSHScanModel.get_by_task_id(task_id)
        self.build = None
        if not self.scan:
            logger.warning(
                f"Scan with id {task_id} not found in the database."
                " It should have been created when receiving the CoprBuildEndEvent"
                " and should have been associated with the copr build.",
            )
        else:
            self.build = self.scan.copr_build_target
            project_event = self.build.get_project_event_model()
            # commit_sha is needed by the StatusReporter
            # and have to be serialized to be later found in the
            # event metadata
            self.commit_sha = project_event.commit_sha if not self.commit_sha else self.commit_sha

    def get_db_project_object(self) -> Optional[AbstractProjectObjectDbType]:
        return self.build.get_project_event_object()

    def get_db_project_event(self) -> Optional[ProjectEventModel]:
        return self.build.get_project_event_model()

    def get_project(self) -> GitProject:
        return ServiceConfig.get_service_config().get_project(
            self.db_project_object.project.project_url,
        )

    def get_non_serializable_attributes(self):
        # build and scan are not serializable
        return [*super().get_non_serializable_attributes(), "build", "scan"]


class OpenScanHubTaskFinishedEvent(OpenScanHubTaskAbstractEvent):
    class Status(str, enum.Enum):
        success = "success"
        cancel = "cancel"
        interrupt = "interrupt"
        fail = "fail"

    def __init__(
        self,
        status: Status,
        issues_added_url: str,
        issues_fixed_url: str,
        scan_results_url: str,
        **kwargs,
    ):
        super().__init__(**kwargs)

        self.status = status
        self.issues_added_url = issues_added_url
        self.issues_fixed_url = issues_fixed_url
        self.scan_results_url = scan_results_url


class OpenScanHubTaskStartedEvent(OpenScanHubTaskAbstractEvent): ...
