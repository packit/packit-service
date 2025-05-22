# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import copy
from datetime import datetime, timezone
from logging import getLogger
from typing import Optional

from ogr.abstract import GitProject
from ogr.parsing import RepoUrl

from packit_service.config import ServiceConfig
from packit_service.models import (
    AbstractProjectObjectDbType,
    ProjectEventModel,
)

from .event import Event

logger = getLogger(__name__)


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
        commit_sha_before: Optional[str],
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
        self.commit_sha_before = commit_sha_before
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
        commit_sha_before = event.get("commit_sha_before")
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
            commit_sha_before=commit_sha_before,
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
        mod = __import__(f"packit_service.events.{event_submodule}", fromlist=[event_kls_member])
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
        # [TODO] Improve handling of this matching against hard-coded values.
        # 1. Switching from hard-coded values to using ‹.event_type()› on the
        #    classes themselves would be possible, but that would introduce
        #    circular imports.
        # 2. Better approach would be importing based on the ‹self.event_type›
        #    and inheriting a “mixin” within the event class that could handle
        #    this logic. However ‹self.project› (used below) is a property, and
        #    the return values are assigned to ‹self›. Also the type signature
        #    for the methods introduced by a mixin would be complicated, as the
        #    least amount of copy-paste basically leads to having both named
        #    arguments and ‹**kwargs› to catch any additional unused parameters.
        #    We would also lose silent fail on some of the currently ignored
        #    events (right now we log a warning).
        if self.event_type in {
            "github.pr.Action",
            "pagure.pr.Action",
            "gitlab.mr.Action",
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
            "github.push.Commit",
            "gitlab.push.Commit",
            "pagure.push.Commit",
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
            "koji.tag.Build",
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
            "koji.result.Build",
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
