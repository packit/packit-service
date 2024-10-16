# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from typing import Optional
from logging import getLogger

from ogr.abstract import GitProject
from packit_service.config import ServiceConfig
from packit_service.worker.events.event import AbstractResultEvent
from packit_service.models import (
    AbstractProjectObjectDbType,
    ProjectEventModel,
    ScanModel,
)

logger = getLogger(__name__)


class OpenScanHubTaskFinishEvent(AbstractResultEvent):
    def __init__(
        self,
        task_id: int,
        issues_added_url: str,
        issues_fixed_url: str,
        scan_results_url: str,
        **kwargs,
    ):
        super().__init__(**kwargs)

        self.task_id = task_id
        self.issues_added_url = issues_added_url
        self.issues_fixed_url = issues_fixed_url
        self.scan_results_url = scan_results_url

        self.scan = ScanModel.get_by_task_id(task_id)
        self.build = self.scan.copr_build_target

    def get_db_project_object(self) -> Optional[AbstractProjectObjectDbType]:
        return self.build.get_project_event_object()

    def get_db_project_event(self) -> Optional[ProjectEventModel]:
        return self.build.get_project_event_model()

    def get_project(self) -> GitProject:
        return ServiceConfig.get_service_config().get_project(
            self.db_project_object.project.project_url
        )

    def get_non_serializable_attributes(self):
        return super().get_non_serializable_attributes() + ["build", "scan"]
