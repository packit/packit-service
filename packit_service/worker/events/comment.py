# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
abstract-comment event classes.
"""
from logging import getLogger
from typing import Dict, Optional, Set

from ogr.abstract import Comment

from packit_service.models import TestingFarmResult, BuildStatus
from packit_service.service.db_project_events import (
    AddIssueEventToDb,
    AddPullRequestEventToDb,
)
from packit_service.worker.events.event import AbstractForgeIndependentEvent

logger = getLogger(__name__)


class AbstractCommentEvent(AbstractForgeIndependentEvent):
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

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        result = super().get_dict()
        result.pop("_comment_object")
        return result


class AbstractPRCommentEvent(AddPullRequestEventToDb, AbstractCommentEvent):
    def __init__(
        self,
        pr_id: int,
        project_url: str,
        comment: str,
        comment_id: int,
        commit_sha: str = "",
        comment_object: Optional[Comment] = None,
        build_targets_override: Optional[Set[str]] = None,
        tests_targets_override: Optional[Set[str]] = None,
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

    @property
    def commit_sha(self) -> str:  # type:ignore
        # mypy does not like properties
        if not self._commit_sha:
            self._commit_sha = self.project.get_pr(pr_id=self.pr_id).head_commit
        return self._commit_sha

    @property
    def comment_object(self) -> Optional[Comment]:
        if not self._comment_object:
            self._comment_object = self.project.get_pr(self.pr_id).get_comment(
                self.comment_id
            )
        return self._comment_object

    @property
    def build_targets_override(self) -> Optional[Set[str]]:
        if not self._build_targets_override and "rebuild-failed" in self.comment:
            self._build_targets_override = (
                super().get_all_build_targets_by_status(
                    statuses_to_filter_with=[BuildStatus.failure]
                )
                or None
            )
        return self._build_targets_override

    @property
    def tests_targets_override(self) -> Optional[Set[str]]:
        if not self._tests_targets_override and "retest-failed" in self.comment:
            self._tests_targets_override = (
                super().get_all_tf_targets_by_status(
                    statuses_to_filter_with=[
                        TestingFarmResult.failed,
                        TestingFarmResult.error,
                    ]
                )
                or None
            )
        return self._tests_targets_override

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        result = super().get_dict()
        result["commit_sha"] = self.commit_sha
        result.pop("_build_targets_override")
        result.pop("_tests_targets_override")
        return result


class AbstractIssueCommentEvent(AddIssueEventToDb, AbstractCommentEvent):
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

    @property
    def tag_name(self):
        if not self._tag_name:
            self._tag_name = ""
            if latest_release := self.project.get_latest_release():
                self._tag_name = latest_release.tag_name
        return self._tag_name

    @property
    def commit_sha(self) -> Optional[str]:  # type:ignore
        # mypy does not like properties
        if not self._commit_sha and self.tag_name:
            self._commit_sha = self.project.get_sha_from_tag(tag_name=self.tag_name)
        return self._commit_sha

    @property
    def comment_object(self) -> Optional[Comment]:
        if not self._comment_object:
            self._comment_object = self.project.get_issue(self.issue_id).get_comment(
                self.comment_id
            )
        return self._comment_object

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        result = super().get_dict()
        result["tag_name"] = self.tag_name
        result["commit_sha"] = self.commit_sha
        result["issue_id"] = self.issue_id
        return result
