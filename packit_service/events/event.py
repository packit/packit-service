# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
Generic/abstract event classes.
"""

import copy
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from logging import getLogger
from typing import Optional, Union

from ogr.abstract import GitProject
from packit.config import JobConfigTriggerType, PackageConfig

from packit_service.models import (
    AbstractProjectObjectDbType,
    AnityaProjectModel,
    ProjectEventModel,
)

logger = getLogger(__name__)


MAP_EVENT_TO_JOB_CONFIG_TRIGGER_TYPE: dict[type["Event"], JobConfigTriggerType] = {}


def use_for_job_config_trigger(trigger_type: JobConfigTriggerType):
    """
    [class decorator]
    Specify a trigger_type which this event class matches
    so we don't need to search database to get that information.

    In other words, what job-config in the configuration file
    is compatible with this event.

    Example:
    ```
    @use_for_job_config_trigger(trigger_type=JobConfigTriggerType.commit)
    class KojiBuildEvent(AbstractKojiEvent):
    ```
    """

    def _add_to_mapping(kls: type["Event"]):
        MAP_EVENT_TO_JOB_CONFIG_TRIGGER_TYPE[kls] = trigger_type
        return kls

    return _add_to_mapping


class Event(ABC):
    task_accepted_time: Optional[datetime] = None
    actor: Optional[str]

    def __init__(self, created_at: Optional[Union[int, float, str]] = None):
        self.created_at: datetime
        if created_at:
            if isinstance(created_at, (int, float)):
                self.created_at = datetime.fromtimestamp(created_at, timezone.utc)
            elif isinstance(created_at, str):
                # https://stackoverflow.com/questions/127803/how-do-i-parse-an-iso-8601-formatted-date/49784038
                created_at = created_at.replace("Z", "+00:00")
                self.created_at = datetime.fromisoformat(created_at)
        else:
            self.created_at = datetime.now(timezone.utc)

        # lazy properties:
        self._project: Optional[GitProject] = None
        self._base_project: Optional[GitProject] = None
        self._package_config: Optional[PackageConfig] = None
        self._package_config_searched: bool = False
        self._db_project_object: Optional[AbstractProjectObjectDbType] = None
        self._db_project_event: Optional[ProjectEventModel] = None

    @classmethod
    @abstractmethod
    def event_type(cls) -> str:
        """Represents a string representation of the event type that's used for
        Celery representation and deserialization from the Celery event.

        For abstract classes also checks that it's being called »only« during
        test runs.

        Returns:
            “Topic” or type of the event as a string.
        """
        ...

    @staticmethod
    def make_serializable(d: dict, skip: list) -> dict:
        """We need a JSON serializable dict (because of redis and celery tasks)
        This method will copy everything from dict except the specified
        non serializable keys.
        """
        return {k: copy.deepcopy(v) for k, v in d.items() if k not in skip}

    def store_packages_config(self):
        """
        For events starting pipeline for Koji/Copr builds/tests, we
        want to store the packages config to limit
        getting it via API (reduce API calls).
        """
        if not self.db_project_event:
            return

        package_config_dict = (
            self.packages_config.get_raw_dict_with_defaults() if self.packages_config else None
        )
        if package_config_dict:
            logger.debug("Storing packages config in DB.")
            self.db_project_event.set_packages_config(package_config_dict)

    def get_non_serializable_attributes(self):
        """List here both non serializable attributes and attributes that
        we want to skip from the dict because are not needed to re-create
        the event.
        """
        return [
            "_db_project_object",
            "_db_project_event",
            "_project",
            "_base_project",
            "_package_config",
            "_package_config_searched",
        ]

    def get_dict(self, default_dict: Optional[dict] = None) -> dict:
        d = default_dict or self.__dict__
        # whole dict has to be JSON serializable because of redis
        d = self.make_serializable(d, self.get_non_serializable_attributes())
        d["event_type"] = self.event_type()

        # we are trying to be lazy => don't touch database if it is not needed
        d["event_id"] = self._db_project_object.id if self._db_project_object else None

        d["created_at"] = int(d["created_at"].timestamp())
        task_accepted_time = d.get("task_accepted_time")
        d["task_accepted_time"] = (
            int(task_accepted_time.timestamp()) if task_accepted_time else None
        )
        d["project_url"] = d.get("project_url") or (
            self.db_project_object.project.project_url
            if (
                self.db_project_object
                and not isinstance(self.db_project_object.project, AnityaProjectModel)
            )
            else None
        )
        if self.build_targets_override:
            d["build_targets_override"] = list(self.build_targets_override)
        if self.tests_targets_override:
            d["tests_targets_override"] = list(self.tests_targets_override)
        if self.branches_override:
            d["branches_override"] = list(self.branches_override)

        return d

    def get_db_project_object(self) -> Optional[AbstractProjectObjectDbType]:
        return None

    def get_db_project_event(self) -> Optional[ProjectEventModel]:
        return None

    @property
    def db_project_object(self) -> Optional[AbstractProjectObjectDbType]:
        if not self._db_project_object:
            self._db_project_object = self.get_db_project_object()
        return self._db_project_object

    @property
    def db_project_event(self) -> Optional[ProjectEventModel]:
        if not self._db_project_event:
            self._db_project_event = self.get_db_project_event()
        return self._db_project_event

    @property
    def job_config_trigger_type(self) -> Optional[JobConfigTriggerType]:
        """
        By default, we can use a database model related to this to get the config trigger type.

        Set this for an event subclass if it is clear and
        can be determined without any database connections
        by using a `@use_for_job_config_trigger` decorator.
        """
        for (
            event_cls,
            job_config_trigger_type,
        ) in MAP_EVENT_TO_JOB_CONFIG_TRIGGER_TYPE.items():
            if isinstance(self, event_cls):
                return job_config_trigger_type
        if not self.db_project_object:
            logger.warning(
                f"Event {self} does not have a matching object in the database.",
            )
            return None
        return self.db_project_object.job_config_trigger_type

    @property
    def build_targets_override(self) -> Optional[set[tuple[str, str]]]:
        """
        Return the targets and identifiers to use for building
        of the all targets from config
        for the relevant events (e.g.rerunning of a single check).
        """
        return None

    @property
    def tests_targets_override(self) -> Optional[set[tuple[str, str]]]:
        """
        Return the targets and identifiers to use for testing
        of the all targets from config
        for the relevant events (e.g.rerunning of a single check).
        """
        return None

    @property
    def branches_override(self) -> Optional[set[str]]:
        """
        Return the branches to use for propose-downstream of the all branches from config
        for the relevant events (e.g.rerunning of a single check).
        """
        return None

    @property
    @abstractmethod
    def project(self): ...

    @property
    @abstractmethod
    def base_project(self): ...

    @property
    @abstractmethod
    def packages_config(self): ...

    def pre_check(self) -> bool:
        """
        Implement this method for those events, where you want to check if event properties are
        correct. If this method returns False during runtime, execution of service code is skipped.

        Returns:
            `False` when we can ignore the event, `True` otherwise (for handling).
        """
        # [SAFETY] Since majority of processed events do not require any
        # validation, e.g., results from Copr/Koji, we default to the `True`
        # here.
        return True

    @abstractmethod
    def get_packages_config(self): ...

    @abstractmethod
    def get_project(self) -> GitProject: ...

    def __str__(self):
        return str(self.get_dict())

    def __repr__(self):
        return f"{self.__class__.__name__}({self.get_dict()})"
