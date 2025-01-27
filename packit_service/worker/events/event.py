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
from ogr.parsing import RepoUrl
from packit.config import JobConfigTriggerType, PackageConfig

from packit_service.config import ServiceConfig
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


class EventData:
    """
    Class to represent the data which are common for handlers and comes from the original event
    """

    def __init__(
        self,
        event_type: str,
        actor: str,
        event_id: int,
        project_url: str,
        tag_name: Optional[str],
        git_ref: Optional[str],
        pr_id: Optional[int],
        commit_sha: Optional[str],
        identifier: Optional[str],
        event_dict: Optional[dict],
        issue_id: Optional[int],
        task_accepted_time: Optional[datetime],
        build_targets_override: Optional[set[tuple[str, str]]],
        tests_targets_override: Optional[set[tuple[str, str]]],
        branches_override: Optional[list[str]],
    ):
        self.event_type = event_type
        self.actor = actor
        self.event_id = event_id
        self.project_url = project_url
        self.tag_name = tag_name
        self.git_ref = git_ref
        self.pr_id = pr_id
        self.commit_sha = commit_sha
        self.identifier = identifier
        self.event_dict = event_dict
        self.issue_id = issue_id
        self.task_accepted_time = task_accepted_time
        self.build_targets_override = (
            set(build_targets_override) if build_targets_override else None
        )
        self.tests_targets_override = (
            set(tests_targets_override) if tests_targets_override else None
        )
        self.branches_override = set(branches_override) if branches_override else None

        # lazy attributes
        self._project = None
        self._db_project_object: Optional[AbstractProjectObjectDbType] = None
        self._db_project_event: Optional[ProjectEventModel] = None

    @classmethod
    def from_event_dict(cls, event: dict):
        event_type = event.get("event_type")
        # We used `user_login` in the past.
        actor = event.get("user_login") or event.get("actor")
        event_id = event.get("event_id")
        project_url = event.get("project_url")
        tag_name = event.get("tag_name")
        git_ref = event.get("git_ref")
        # event has _pr_id as the attribute while pr_id is a getter property
        pr_id = event.get("_pr_id") or event.get("pr_id")
        commit_sha = event.get("commit_sha")
        identifier = event.get("identifier")
        issue_id = event.get("issue_id")

        time = event.get("task_accepted_time")
        task_accepted_time = datetime.fromtimestamp(time, timezone.utc) if time else None

        build_targets_override = (
            {(target, identifier_) for [target, identifier_] in event.get("build_targets_override")}
            if event.get("build_targets_override")
            else set()
        )
        tests_targets_override = (
            {(target, identifier_) for [target, identifier_] in event.get("tests_targets_override")}
            if event.get("tests_targets_override")
            else set()
        )
        branches_override = event.get("branches_override")

        return EventData(
            event_type=event_type,
            actor=actor,
            event_id=event_id,
            project_url=project_url,
            tag_name=tag_name,
            git_ref=git_ref,
            pr_id=pr_id,
            commit_sha=commit_sha,
            identifier=identifier,
            event_dict=event,
            issue_id=issue_id,
            task_accepted_time=task_accepted_time,
            build_targets_override=build_targets_override,
            tests_targets_override=tests_targets_override,
            branches_override=branches_override,
        )

    def to_event(self) -> "Event":
        """
        Create an instance of Event class from the data in this class.
        """
        # Import the event class
        event_submodule, event_kls_member = self.event_type.rsplit(".", maxsplit=1)
        mod = __import__(
            f"packit_service.worker.events.{event_submodule}", fromlist=[event_kls_member]
        )
        event_kls = getattr(mod, event_kls_member)

        # Process the arguments for the event class' constructor
        kwargs = copy.copy(self.event_dict)
        # The following data should be reconstructed by the Event instance (when needed)
        kwargs.pop("event_type", None)
        kwargs.pop("event_id", None)
        kwargs.pop("task_accepted_time", None)
        kwargs.pop("build_targets_override", None)
        kwargs.pop("tests_targets_override", None)
        kwargs.pop("branches_override", None)
        pr_id = kwargs.pop("_pr_id", None)
        kwargs["pr_id"] = pr_id

        # Construct the event
        return event_kls(**kwargs)

    @property
    def project(self):
        if not self._project:
            self._project = self.get_project()
        return self._project

    def _add_project_object_and_event(self):
        # TODO, do a better job
        # Probably, try to recreate original classes.
        if self.event_type in {
            "github.pr.Synchronize",
            "pagure.pr.Synchronize",
            "gitlab.mr.Synchronize",
            "github.pr.Comment",
            "pagure.pr.Comment",
            "gitlab.mr.Comment",
            "pagure.pr.Flag",
            "github.check.PullRequest",
        }:
            (
                self._db_project_object,
                self._db_project_event,
            ) = ProjectEventModel.add_pull_request_event(
                pr_id=self.pr_id,
                namespace=self.project.namespace,
                repo_name=self.project.repo,
                project_url=self.project_url,
                commit_sha=self.commit_sha,
            )
        elif self.event_type in {
            "github.push.Push",
            "gitlab.push.Push",
            "pagure.push.Push",
            "github.check.Commit",
        }:
            (
                self._db_project_object,
                self._db_project_event,
            ) = ProjectEventModel.add_branch_push_event(
                branch_name=self.git_ref,
                namespace=self.project.namespace,
                repo_name=self.project.repo,
                project_url=self.project_url,
                commit_sha=self.commit_sha,
            )

        elif self.event_type in {
            "github.release.Release",
            "gitlab.release.Release",
            "github.check.Release",
        }:
            (
                self._db_project_object,
                self._db_project_event,
            ) = ProjectEventModel.add_release_event(
                tag_name=self.tag_name,
                namespace=self.project.namespace,
                repo_name=self.project.repo,
                project_url=self.project_url,
                commit_hash=self.commit_sha,
            )
        elif self.event_type in {
            "anitya.NewHotness",
        }:
            if not self.project_url:
                (
                    self._db_project_object,
                    self._db_project_event,
                ) = ProjectEventModel.add_anitya_version_event(
                    version=self.event_dict.get("version"),
                    project_name=self.event_dict.get("anitya_project_name"),
                    project_id=self.event_dict.get("anitya_project_id"),
                    package=self.event_dict.get("package_name"),
                )
                return

            if self.project:
                namespace = self.project.namespace
                repo_name = self.project.repo
            else:
                repo_url = RepoUrl.parse(self.project_url)
                namespace = repo_url.namespace
                repo_name = repo_url.repo
            (
                self._db_project_object,
                self._db_project_event,
            ) = ProjectEventModel.add_release_event(
                tag_name=self.tag_name,
                namespace=namespace,
                repo_name=repo_name,
                project_url=self.project_url,
                commit_hash=self.commit_sha,
            )
        elif self.event_type in {
            "github.issue.Comment",
            "gitlab.issue.Comment",
        }:
            (
                self._db_project_object,
                self._db_project_event,
            ) = ProjectEventModel.add_issue_event(
                issue_id=self.issue_id,
                namespace=self.project.namespace,
                repo_name=self.project.repo,
                project_url=self.project_url,
            )
        elif self.event_type in {
            "koji.Tag",
        }:
            (
                self._db_project_object,
                self._db_project_event,
            ) = ProjectEventModel.add_koji_build_tag_event(
                task_id=str(self.event_dict.get("task_id")),
                koji_tag_name=self.tag_name,
                namespace=self.project.namespace,
                repo_name=self.project.repo,
                project_url=self.project_url,
            )
        elif self.event_type in {
            "koji.Build",
        }:
            (
                self._db_project_object,
                self._db_project_event,
            ) = ProjectEventModel.add_branch_push_event(
                branch_name=self.event_dict.get("branch_name"),
                namespace=self.project.namespace,
                repo_name=self.project.repo,
                project_url=self.project_url,
                commit_sha=self.event_dict.get("commit_sha"),
            )
        elif self.event_type in {
            "github.commit.Comment",
            "gitlab.commit.Comment",
        }:
            if self.tag_name:
                (
                    self._db_project_object,
                    self._db_project_event,
                ) = ProjectEventModel.add_release_event(
                    tag_name=self.tag_name,
                    namespace=self.project.namespace,
                    repo_name=self.project.repo,
                    project_url=self.project_url,
                    commit_hash=self.commit_sha,
                )
            else:
                (
                    self._db_project_object,
                    self._db_project_event,
                ) = ProjectEventModel.add_branch_push_event(
                    branch_name=self.git_ref,
                    namespace=self.project.namespace,
                    repo_name=self.project.repo,
                    project_url=self.project_url,
                    commit_sha=self.commit_sha,
                )

        else:
            logger.warning(
                "We don't know, what to search in the database for this event data.",
            )

    @property
    def db_project_object(self) -> Optional[AbstractProjectObjectDbType]:
        if not self._db_project_object:
            self._add_project_object_and_event()
        return self._db_project_object

    @property
    def db_project_event(self) -> Optional[ProjectEventModel]:
        if not self._db_project_event:
            self._add_project_object_and_event()
        return self._db_project_event

    def get_dict(self) -> dict:
        d = self.__dict__
        d = copy.deepcopy(d)
        task_accepted_time = d.get("task_accepted_time")
        d["task_accepted_time"] = (
            int(task_accepted_time.timestamp()) if task_accepted_time else None
        )
        if self.build_targets_override:
            d["build_targets_override"] = list(self.build_targets_override)
        if self.tests_targets_override:
            d["tests_targets_override"] = list(self.tests_targets_override)
        if self.branches_override:
            d["branches_override"] = list(self.branches_override)
        d.pop("_project", None)
        d.pop("_db_project_object", None)
        d.pop("_db_project_event", None)
        return d

    def get_project(self) -> Optional[GitProject]:
        if not self.project_url:
            return None
        return ServiceConfig.get_service_config().get_project(
            url=self.project_url or self.db_project_object.project.project_url,
            required=self.event_type not in ("anitya.NewHotness",),
        )


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
        # [TODO] check correctness, removed ‹__class__›
        d["event_type"] = self.__class__.event_type()

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
        return True

    @abstractmethod
    def get_packages_config(self): ...

    @abstractmethod
    def get_project(self) -> GitProject: ...

    def __str__(self):
        return str(self.get_dict())

    def __repr__(self):
        return f"{self.__class__.__name__}({self.get_dict()})"
