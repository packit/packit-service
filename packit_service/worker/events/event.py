# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
Generic/abstract event classes.
"""
import copy
from datetime import datetime, timezone
from logging import getLogger
from typing import Dict, Optional, Type, Union, Set, List

from ogr.abstract import GitProject, PullRequest
from ogr.parsing import RepoUrl

from packit.config import JobConfigTriggerType, PackageConfig
from packit_service.config import PackageConfigGetter, ServiceConfig
from packit_service.models import (
    AbstractProjectObjectDbType,
    ProjectEventModel,
    CoprBuildTargetModel,
    TFTTestRunTargetModel,
    filter_most_recent_target_names_by_status,
)
from packit_service.utils import dump_package_config

logger = getLogger(__name__)


MAP_EVENT_TO_JOB_CONFIG_TRIGGER_TYPE: Dict[Type["Event"], JobConfigTriggerType] = {}


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

    def _add_to_mapping(kls: Type["Event"]):
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
        build_targets_override: Optional[List[str]],
        tests_targets_override: Optional[List[str]],
        branches_override: Optional[List[str]],
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
        task_accepted_time = (
            datetime.fromtimestamp(time, timezone.utc) if time else None
        )

        build_targets_override = event.get("build_targets_override")
        tests_targets_override = event.get("tests_targets_override")
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

    @property
    def project(self):
        if not self._project:
            self._project = self.get_project()
        return self._project

    def _add_project_object_and_event(self):
        packages_config = self.event_dict.get("packages_config")
        # TODO, do a better job
        # Probably, try to recreate original classes.
        if self.event_type in {
            "PullRequestGithubEvent",
            "PullRequestPagureEvent",
            "MergeRequestGitlabEvent",
            "PullRequestCommentGithubEvent",
            "MergeRequestCommentGitlabEvent",
            "PullRequestCommentPagureEvent",
            "PullRequestFlagPagureEvent",
            "CheckRerunPullRequestEvent",
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
                packages_config=packages_config,
            )
        elif self.event_type in {
            "PushGitHubEvent",
            "PushGitlabEvent",
            "PushPagureEvent",
            "CheckRerunCommitEvent",
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
                packages_config=packages_config,
            )

        elif self.event_type in {
            "ReleaseEvent",
            "ReleaseGitlabEvent",
            "CheckRerunReleaseEvent",
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
                packages_config=packages_config,
            )
        elif self.event_type in {
            "NewHotnessUpdateEvent",
        }:
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
                packages_config=packages_config,
            )
        elif self.event_type in {
            "IssueCommentEvent",
            "IssueCommentGitlabEvent",
        }:
            (
                self._db_project_object,
                self._db_project_event,
            ) = ProjectEventModel.add_issue_event(
                issue_id=self.issue_id,
                namespace=self.project.namespace,
                repo_name=self.project.repo,
                project_url=self.project_url,
                packages_config=packages_config,
            )
        else:
            logger.warning(
                "We don't know, what to search in the database for this event data."
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
            required=self.event_type not in ("NewHotnessUpdateEvent",),
        )


class Event:
    task_accepted_time: Optional[datetime] = None
    actor: Optional[str]

    def __init__(self, created_at: Union[int, float, str] = None):
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

    @staticmethod
    def make_serializable(d: dict, skip: List) -> dict:
        """We need a JSON serializable dict (because of redis and celery tasks)
        This method will copy everything from dict except the specified
        non serializable keys.
        """
        return {k: copy.deepcopy(v) for k, v in d.items() if k not in skip}

    def get_non_serializable_attributes(self):
        return [
            "_db_project_object",
            "_db_project_event",
            "_project",
            "_base_project",
            "_package_config",
        ]

    def get_dict(
        self, default_dict: Optional[Dict] = None, store_event: bool = False
    ) -> dict:
        d = default_dict or self.__dict__
        # whole dict has to be JSON serializable because of redis
        d = self.make_serializable(d, self.get_non_serializable_attributes())
        d["event_type"] = self.__class__.__name__
        d["packages_config"] = dump_package_config(self.packages_config)

        if store_event:
            db_project_object = self.db_project_object
        else:
            # we are trying to be lazy => don't touch database if it is not needed
            db_project_object = self._db_project_object

        d["event_id"] = db_project_object.id if db_project_object else None

        d["created_at"] = int(d["created_at"].timestamp())
        task_accepted_time = d.get("task_accepted_time")
        d["task_accepted_time"] = (
            int(task_accepted_time.timestamp()) if task_accepted_time else None
        )
        d["project_url"] = d.get("project_url") or (
            self.db_project_object.project.project_url
            if self.db_project_object
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
                f"Event {self} does not have a matching object in the database."
            )
            return None
        return self.db_project_object.job_config_trigger_type

    @property
    def project(self):
        raise NotImplementedError("Please implement me!")

    @property
    def base_project(self):
        raise NotImplementedError("Please implement me!")

    @property
    def packages_config(self):
        raise NotImplementedError("Please implement me!")

    @property
    def build_targets_override(self) -> Optional[Set[str]]:
        """
        Return the targets to use for building of the all targets from config
        for the relevant events (e.g.rerunning of a single check).
        """
        return None

    @property
    def tests_targets_override(self) -> Optional[Set[str]]:
        """
        Return the targets to use for testing of the all targets from config
        for the relevant events (e.g.rerunning of a single check).
        """
        return None

    @property
    def branches_override(self) -> Optional[Set[str]]:
        """
        Return the branches to use for propose-downstream of the all branches from config
        for the relevant events (e.g.rerunning of a single check).
        """
        return None

    def get_packages_config(self):
        raise NotImplementedError("Please implement me!")

    def get_project(self) -> GitProject:
        raise NotImplementedError("Please implement me!")

    def pre_check(self) -> bool:
        """
        Implement this method for those events, where you want to check if event properties are
        correct. If this method returns False during runtime, execution of service code is skipped.

        :return: False if we can ignore the event
        """
        return True

    def __str__(self):
        return str(self.get_dict())

    def __repr__(self):
        return f"{self.__class__.__name__}({self.get_dict()})"


class AbstractForgeIndependentEvent(Event):
    commit_sha: Optional[str]
    project_url: str

    def __init__(
        self,
        created_at: Union[int, float, str] = None,
        project_url=None,
        pr_id: Optional[int] = None,
        actor: Optional[str] = None,
    ):
        super().__init__(created_at)
        self.project_url = project_url
        self._pr_id = pr_id
        self.fail_when_config_file_missing = False
        self.actor = actor
        self._pull_request_object = None

    @property
    def project(self):
        if not self._project:
            self._project = self.get_project()
        return self._project

    @property
    def base_project(self):
        if not self._base_project:
            self._base_project = self.get_base_project()
        return self._base_project

    @property
    def packages_config(self):
        if not self._package_config_searched and not self._package_config:
            self._package_config = self.get_packages_config()
            self._package_config_searched = True
        return self._package_config

    def get_db_project_object(self) -> Optional[AbstractProjectObjectDbType]:
        raise NotImplementedError()

    def get_db_project_event(self) -> Optional[ProjectEventModel]:
        raise NotImplementedError()

    @property
    def pr_id(self) -> Optional[int]:
        return self._pr_id

    @property
    def pull_request_object(self) -> Optional[PullRequest]:
        if not self._pull_request_object and self.pr_id:
            self._pull_request_object = self.project.get_pr(self.pr_id)
        return self._pull_request_object

    def get_project(self) -> Optional[GitProject]:
        if not (self.project_url or self.db_project_object):
            return None

        return ServiceConfig.get_service_config().get_project(
            url=self.project_url or self.db_project_object.project.project_url
        )

    def get_base_project(self) -> Optional[GitProject]:
        """Reimplement in the PR events."""
        return None

    def get_packages_config(self) -> Optional[PackageConfig]:
        logger.debug(
            f"Getting packages_config:\n"
            f"\tproject: {self.project}\n"
            f"\tbase_project: {self.base_project}\n"
            f"\treference: {self.commit_sha}\n"
            f"\tpr_id: {self.pr_id}"
        )

        packages_config = PackageConfigGetter.get_package_config_from_repo(
            base_project=self.base_project,
            project=self.project,
            reference=self.commit_sha,
            pr_id=self.pr_id,
            fail_when_missing=self.fail_when_config_file_missing,
        )

        return packages_config

    def get_all_tf_targets_by_status(
        self, statuses_to_filter_with: List[str]
    ) -> Optional[Set[str]]:
        if self.commit_sha is None:
            return None

        logger.debug(
            f"Getting failed Testing Farm targets for commit sha: {self.commit_sha}"
        )
        return filter_most_recent_target_names_by_status(
            models=TFTTestRunTargetModel.get_all_by_commit_target(
                commit_sha=self.commit_sha
            ),
            statuses_to_filter_with=statuses_to_filter_with,
        )

    def get_all_build_targets_by_status(
        self, statuses_to_filter_with: List[str]
    ) -> Optional[Set[str]]:
        if self.commit_sha is None or self.project.repo is None:
            return None

        logger.debug(
            f"Getting failed COPR build targets for commit sha: {self.commit_sha}"
        )
        return filter_most_recent_target_names_by_status(
            models=CoprBuildTargetModel.get_all_by_commit(commit_sha=self.commit_sha),
            statuses_to_filter_with=statuses_to_filter_with,
        )

    def get_dict(
        self, default_dict: Optional[Dict] = None, store_event: bool = False
    ) -> dict:
        result = super().get_dict()
        result.pop("_pull_request_object")
        return result


class AbstractResultEvent(AbstractForgeIndependentEvent):
    """
    This class is used only as an Abstract for result events to
    allow Steve properly filter jobs with manual trigger.
    """

    pass
