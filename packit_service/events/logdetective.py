# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from datetime import datetime
from typing import Optional

from ogr.abstract import GitProject
from ogr.services.pagure import PagureProject

from packit_service.models import (
    AbstractProjectObjectDbType,
    LogDetectiveBuildSystem,
    LogDetectiveResult,
    LogDetectiveRunModel,
    ProjectEventModel,
)

from .abstract.base import Result as AbstractResult

logger = logging.getLogger(__name__)


class Result(AbstractResult):
    """Result of Log Detective analysis"""

    @classmethod
    def event_type(cls) -> str:
        return "logdetective.result"

    def __init__(
        self,
        target_build: str,
        log_detective_response: dict,
        status: LogDetectiveResult,
        build_system: LogDetectiveBuildSystem,
        identifier: str,
        log_detective_analysis_start: datetime,
    ):
        super().__init__()
        self.target_build = target_build
        self.log_detective_response = log_detective_response
        self.status = status
        self.build_system = build_system
        self.identifier = identifier
        self.log_detective_analysis_start = log_detective_analysis_start

    def get_dict(self, default_dict: Optional[dict] = None) -> dict:
        """Return Log Detective result as a dictionary,
        serializable as json."""
        result = super().get_dict()
        result["status"] = self.status.value
        result["build_system"] = self.build_system.value
        result["log_detective_analysis_start"] = str(self.log_detective_analysis_start)
        return result

    def get_db_project_event(self) -> Optional[ProjectEventModel]:
        """Get ProjectEventModel describing event that triggered Log Detective
        analysis run. If no such model exists, return None."""
        if run_model := LogDetectiveRunModel.get_by_identifier(self.identifier):
            return run_model.get_project_event_model()
        return None

    def get_db_project_object(self) -> Optional[AbstractProjectObjectDbType]:
        """Get AbstractProjectObjectDbType, one of possible areas where the Log Detective
        run may have been triggered, such as pull request or an issue.
        Only objects related to builds are expected here.
        """

        if run_model := LogDetectiveRunModel.get_by_identifier(self.identifier):
            return run_model.get_project_event_object()
        return None

    def get_base_project(self) -> Optional[GitProject]:
        """Get project the Log Detective analysis was executed for.
        For GitHub, return only original repository and disregard forks."""
        if self.pr_id is not None:
            if isinstance(self.project, PagureProject):
                return self.project.service.get_project(
                    namespace=self.project.namespace,
                    username=self.pull_request_object.author,
                    repo=self.project.repo,
                    is_fork=True,
                )
            return None  # With Github app, we cannot work with fork repo
        return self.project
