# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
import os
from typing import Optional, Union

from packit_service.models import (
    AbstractProjectObjectDbType,
    KojiBuildTargetModel,
    ProjectEventModel,
)

from ..abstract.base import Result

logger = logging.getLogger(__name__)


class KojiEvent(Result):
    def __init__(
        self,
        task_id: int,
        rpm_build_task_ids: Optional[dict[str, int]] = None,
        start_time: Optional[Union[int, float, str]] = None,
        completion_time: Optional[Union[int, float, str]] = None,
    ):
        super().__init__()
        self.task_id = task_id
        # dictionary with archs and IDs, e.g. {"x86_64": 123}
        self.rpm_build_task_ids = rpm_build_task_ids
        self.start_time: Optional[Union[int, float, str]] = start_time
        self.completion_time: Optional[Union[int, float, str]] = completion_time

        # Lazy properties
        self._target: Optional[str] = None
        self._build_model: Optional[KojiBuildTargetModel] = None
        self._build_model_searched = False

    @classmethod
    def event_type(cls) -> str:
        assert os.environ.get("PYTEST_VERSION"), "Should be initialized only during tests"
        return "test.koji.Event"

    @property
    def build_model(self) -> Optional[KojiBuildTargetModel]:
        if not self._build_model_searched and not self._build_model:
            self._build_model = KojiBuildTargetModel.get_by_task_id(
                task_id=self.task_id,
            )
            self._build_model_searched = True
        return self._build_model

    def get_db_project_object(self) -> Optional[AbstractProjectObjectDbType]:
        return self.build_model.get_project_event_object() if self.build_model else None

    def get_db_project_event(self) -> Optional[ProjectEventModel]:
        return self.build_model.get_project_event_model() if self.build_model else None

    @property
    def target(self) -> Optional[str]:
        if not self._target and self.build_model:
            self._target = self.build_model.target
        return self._target

    @staticmethod
    def get_koji_rpm_build_web_url(
        rpm_build_task_id: int,
        koji_web_url: str = "https://koji.fedoraproject.org",
    ) -> str:
        """
        Constructs the web URL for the given Koji task.
        You can redefine the Koji instance using the one defined in the service config.
        """
        return f"{koji_web_url}/koji/taskinfo?taskID={rpm_build_task_id}"

    @staticmethod
    def get_koji_build_logs_url(
        rpm_build_task_id: int,
        koji_logs_url: str = "https://kojipkgs.fedoraproject.org",
    ) -> str:
        """
        Constructs the log URL for the given Koji task.
        You can redefine the Koji instance using the one defined in the service config.
        """
        return (
            f"{koji_logs_url}//work/tasks/{rpm_build_task_id % 10000}/{rpm_build_task_id}/build.log"
        )

    def get_koji_build_rpm_tasks_logs_urls(
        self,
        koji_logs_url: str = "https://kojipkgs.fedoraproject.org",
    ) -> dict[str, str]:
        """
        Constructs the log URLs for all RPM subtasks of the Koji task.
        """
        return {
            arch: KojiEvent.get_koji_build_logs_url(
                rpm_build_task_id=rpm_build_task_id,
                koji_logs_url=koji_logs_url,
            )
            for arch, rpm_build_task_id in self.rpm_build_task_ids.items()
        }

    def get_dict(self, default_dict: Optional[dict] = None) -> dict:
        result = super().get_dict()
        result.pop("_build_model")
        result.pop("_build_model_searched")
        return result
