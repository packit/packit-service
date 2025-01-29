# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
abstract-comment event classes.
"""

import os
import re
from logging import getLogger
from typing import Optional, Union

from ogr.abstract import Comment
from ogr.abstract import Issue as OgrIssue

from packit_service.models import (
    BuildStatus,
    GitBranchModel,
    ProjectEventModel,
    ProjectReleaseModel,
    TestingFarmResult,
)
from packit_service.service.db_project_events import (
    AddIssueEventToDb,
    AddPullRequestEventToDb,
)

from .base import ForgeIndependent

logger = getLogger(__name__)


class CommentEvent(ForgeIndependent):
    def __init__(
        self,
        project_url: str,
        comment: str,
        comment_id: int,
        pr_id: Optional[int] = None,
        comment_object: Optional[Comment] = None,
    ) -> None:
        super().__init__(project_url=project_url, pr_id=pr_id)
        self.comment = comment
        self.comment_id = comment_id

        # Lazy properties
        self._comment_object = comment_object

    @property
    def comment_object(self) -> Optional[Comment]:
        raise NotImplementedError("Use subclass instead.")

    def get_dict(self, default_dict: Optional[dict] = None) -> dict:
        result = super().get_dict()
        result.pop("_comment_object")
        return result


class PullRequest(AddPullRequestEventToDb, CommentEvent):
    def __init__(
        self,
        pr_id: int,
        project_url: str,
        comment: str,
        comment_id: int,
        commit_sha: str = "",
        comment_object: Optional[Comment] = None,
        build_targets_override: Optional[set[tuple[str, str]]] = None,
        tests_targets_override: Optional[set[tuple[str, str]]] = None,
    ) -> None:
        super().__init__(
            pr_id=pr_id,
            project_url=project_url,
            comment=comment,
            comment_id=comment_id,
            comment_object=comment_object,
        )

        # Lazy properties
        self._commit_sha = commit_sha
        self._comment_object = comment_object
        self._build_targets_override = build_targets_override
        self._tests_targets_override = tests_targets_override

    @classmethod
    def event_type(cls) -> str:
        assert os.environ.get("PYTEST_VERSION"), "Should be initialized only during tests"
        return "test.abstract.comment.PullRequest"

    @property
    def commit_sha(self) -> str:  # type:ignore
        # mypy does not like properties
        if not self._commit_sha:
            self._commit_sha = self.pull_request_object.head_commit
        return self._commit_sha

    @property
    def comment_object(self) -> Optional[Comment]:
        if not self._comment_object:
            self._comment_object = self.pull_request_object.get_comment(self.comment_id)
        return self._comment_object

    @property
    def build_targets_override(self) -> Optional[set[tuple[str, str]]]:
        # If we do not override the failing builds for the retest-failed comment
        # we will later submit all tests.
        # Overriding builds for the retest-failed comment will let the test jobs
        # see that something has failed and only for those targets the
        # tests will be submitted.
        if (
            not self._build_targets_override and "rebuild-failed" in self.comment
        ) or "retest-failed" in self.comment:
            self._build_targets_override = (
                super().get_all_build_targets_by_status(
                    statuses_to_filter_with=[BuildStatus.failure],
                )
                or None
            )
        return self._build_targets_override

    @property
    def tests_targets_override(self) -> Optional[set[tuple[str, str]]]:
        if not self._tests_targets_override and "retest-failed" in self.comment:
            self._tests_targets_override = (
                super().get_all_tf_targets_by_status(
                    statuses_to_filter_with=[
                        TestingFarmResult.failed,
                        TestingFarmResult.error,
                    ],
                )
                or None
            )
        return self._tests_targets_override

    def get_dict(self, default_dict: Optional[dict] = None) -> dict:
        result = super().get_dict()
        result["commit_sha"] = self.commit_sha
        result.pop("_build_targets_override")
        result.pop("_tests_targets_override")
        return result


class Issue(AddIssueEventToDb, CommentEvent):
    def __init__(
        self,
        issue_id: int,
        repo_namespace: str,
        repo_name: str,
        project_url: str,
        comment: str,
        comment_id: int,
        tag_name: str = "",
        comment_object: Optional[Comment] = None,
        dist_git_project_url=None,
    ) -> None:
        super().__init__(
            project_url=project_url,
            comment=comment,
            comment_id=comment_id,
            comment_object=comment_object,
        )
        self.issue_id = issue_id
        self.repo_namespace = repo_namespace
        self.repo_name = repo_name

        # issue description link to dist-git
        self.dist_git_project_url = dist_git_project_url

        # Lazy properties
        self._tag_name = tag_name
        self._commit_sha: Optional[str] = None
        self._comment_object = comment_object
        self._issue_object: Optional[OgrIssue] = None

    @classmethod
    def event_type(cls) -> str:
        assert os.environ.get("PYTEST_VERSION"), "Should be initialized only during tests"
        return "test.abstract.comment.Issue"

    @property
    def tag_name(self):
        if not self._tag_name:
            self._tag_name = ""
            if releases := self.project.get_releases():
                self._tag_name = releases[0].tag_name
        return self._tag_name

    @property
    def commit_sha(self) -> Optional[str]:  # type:ignore
        # mypy does not like properties
        if not self._commit_sha and self.tag_name:
            self._commit_sha = self.project.get_sha_from_tag(tag_name=self.tag_name)
        return self._commit_sha

    @property
    def issue_object(self) -> Optional[OgrIssue]:
        if not self._issue_object:
            self._issue_object = self.project.get_issue(self.issue_id)
        return self._issue_object

    @property
    def comment_object(self) -> Optional[Comment]:
        if not self._comment_object:
            self._comment_object = self.issue_object.get_comment(self.comment_id)
        return self._comment_object

    def get_dict(self, default_dict: Optional[dict] = None) -> dict:
        result = super().get_dict()
        result["tag_name"] = self.tag_name
        result["commit_sha"] = self.commit_sha
        result["issue_id"] = self.issue_id
        result.pop("_issue_object")
        return result


class Commit(CommentEvent):
    _trigger: Union[GitBranchModel, ProjectReleaseModel] = None
    _event: ProjectEventModel = None

    def __init__(
        self,
        project_url: str,
        comment: str,
        comment_id: int,
        commit_sha: str,
        actor: str,
        repo_name: str,
        repo_namespace: str,
    ) -> None:
        super().__init__(
            project_url=project_url,
            comment=comment,
            comment_id=comment_id,
        )
        self.repo_name = repo_name
        self.repo_namespace = repo_namespace
        self.actor = actor
        self.commit_sha = commit_sha
        self._tag_name: Optional[str] = None
        self._branch: Optional[str] = None

    @classmethod
    def event_type(cls) -> str:
        assert os.environ.get("PYTEST_VERSION"), "Should be initialized only during tests"
        return "test.abstract.comment.Commit"

    @property
    def identifier(self) -> Optional[str]:
        return self.tag_name or self.branch

    @property
    def git_ref(self) -> Optional[str]:
        return self.tag_name or self.branch

    @property
    def tag_name(self) -> Optional[str]:
        if not self._tag_name and "--release" in self.comment:
            release_match = re.search(r"--release[\s=](?P<release>\S+)", self.comment)
            if release_match:
                self._tag_name = release_match.group("release")
        return self._tag_name

    @property
    def branch(self) -> Optional[str]:
        if not self._branch and "--release" not in self.comment:
            if "--commit" in self.comment:
                commit_match = re.search(r"--commit[\s=](?P<commit>\S+)", self.comment)
                if commit_match:
                    self._branch = commit_match.group("commit")
            else:
                self._branch = self.project.default_branch
        return self._branch

    @property
    def comment_object(self) -> Optional[Comment]:
        if not self._comment_object:
            self._comment_object = self.project.get_commit_comment(
                self.commit_sha,
                self.comment_id,
            )
        return self._comment_object

    def _add_release_trigger(self):
        try:
            release = self.project.get_release(tag_name=self.tag_name)
        except Exception:
            logger.debug(f"Release with tag name {self.tag_name} not found.")
            return

        if not release or release.git_tag.commit_sha != self.commit_sha:
            logger.debug(
                "Release with tag name from comment doesn't exist or doesn't match the commit SHA.",
            )
            return

        self._trigger, self._event = ProjectEventModel.add_release_event(
            tag_name=self.tag_name,
            namespace=self.repo_namespace,
            repo_name=self.repo_name,
            project_url=self.project_url,
            commit_hash=self.commit_sha,
        )

    def _add_commit_trigger(self):
        try:
            commits = self.project.get_commits(self.branch)
        except Exception:
            commits = []
        if self.commit_sha not in commits:
            logger.debug(
                f"Branch {self.branch} doesn't exist or doesn't "
                f"contain the commit where the comment was triggered on.",
            )
            return

        self._trigger, self._event = ProjectEventModel.add_branch_push_event(
            branch_name=self.branch,
            namespace=self.repo_namespace,
            repo_name=self.repo_name,
            project_url=self.project_url,
            commit_sha=self.commit_sha,
        )

    def _add_trigger_and_event(self):
        if not self._event or not self._trigger:
            if self.tag_name:
                self._add_release_trigger()
            elif self.branch:
                self._add_commit_trigger()
        return self._trigger, self._event

    @property
    def db_project_object(self) -> Union[GitBranchModel, ProjectReleaseModel]:
        (trigger, _) = self._add_trigger_and_event()
        return trigger

    @property
    def db_project_event(self) -> ProjectEventModel:
        (_, event) = self._add_trigger_and_event()
        return event

    def get_dict(self, default_dict: Optional[dict] = None) -> dict:
        result = super().get_dict()  # type: ignore
        result.pop("_trigger", None)
        result.pop("_event", None)
        result["git_ref"] = self.git_ref
        result["identifier"] = self.identifier
        result["tag_name"] = self.tag_name
        return result
