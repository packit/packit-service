# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from datetime import datetime
from typing import Optional

from packit_service.models import (
    LogDetectiveBuildSystem,
    LogDetectiveResult,
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
