# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from abc import abstractmethod
from typing import Optional

from packit.config import JobConfig
from packit.config.package_config import PackageConfig

from packit_service.events.event_data import EventData
from packit_service.worker.mixin import (
    ConfigFromEventMixin,
    PackitAPIWithDownstreamMixin,
)

logger = logging.getLogger(__name__)


class Checker(ConfigFromEventMixin, PackitAPIWithDownstreamMixin):
    def __init__(
        self,
        package_config: PackageConfig,
        job_config: JobConfig,
        event: dict,
        task_name: Optional[str] = None,
    ):
        self.package_config = package_config
        self.job_config = job_config
        self.data = EventData.from_event_dict(event)
        self.task_name = task_name
        self._mismatch_data: Optional[dict] = None

    @abstractmethod
    def pre_check(self) -> bool: ...

    def get_failure_message(self) -> Optional[dict]:
        """
        Get the failure message/mismatch data if the check failed.
        This is used to aggregate failure messages into a single comment.

        Returns:
            Failure message/mismatch data if check failed, None otherwise.
            Dict with structured data:
            {
                "type": str, # type of the matcher failure
                "job_value": str | list[str],  # job's identifier or labels
                "comment_value": str | list[str],  # what was specified in command
                "targets": list[str],  # all targets for this job
            }
        """
        return self._mismatch_data


class ActorChecker(Checker):
    @property
    def actor(self) -> Optional[str]:
        return self.data.actor

    @abstractmethod
    def _pre_check(self) -> bool: ...

    def pre_check(self) -> bool:
        if not self.actor:
            logger.debug("Actor not set for this event, skipping the actor check.")
            return True
        return self._pre_check()
