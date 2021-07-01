# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from typing import Dict, Optional

from ogr.abstract import GitProject

from packit_service.service.db_triggers import (
    AddIssueDbTrigger,
    AddPullRequestDbTrigger,
    AddBranchPushDbTrigger,
)
from packit_service.worker.events.enums import GitlabEventAction
from packit_service.worker.events.event import AbstractForgeIndependentEvent


class AbstractGitlabEvent(AbstractForgeIndependentEvent):
    def __init__(
        self,
        project_url: str,
        pr_id: Optional[int] = None,
    ):
        super().__init__(pr_id=pr_id)
        self.project_url: str = project_url
        self.git_ref: Optional[str] = None
        self.identifier: Optional[
            str
        ] = None  # will be shown to users -- e.g. in logs or in the copr-project name


class PushGitlabEvent(AddBranchPushDbTrigger, AbstractGitlabEvent):
    def __init__(
        self,
        repo_namespace: str,
        repo_name: str,
        git_ref: str,
        project_url: str,
        commit_sha: str,
    ):
        super().__init__(project_url=project_url)
        self.repo_namespace = repo_namespace
        self.repo_name = repo_name
        self.git_ref = git_ref
        self.commit_sha = commit_sha
        self.identifier = git_ref


class MergeRequestGitlabEvent(AddPullRequestDbTrigger, AbstractGitlabEvent):
    def __init__(
        self,
        action: GitlabEventAction,
        username: str,
        object_id: int,
        object_iid: int,
        source_repo_namespace: str,
        source_repo_name: str,
        source_repo_branch: str,
        target_repo_namespace: str,
        target_repo_name: str,
        target_repo_branch: str,
        project_url: str,
        commit_sha: str,
    ):
        super().__init__(
            project_url=project_url,
            pr_id=object_iid,
        )
        self.action = action
        self.user_login = username
        self.object_id = object_id
        self.identifier = str(object_iid)
        self.source_repo_namespace = source_repo_namespace
        self.source_repo_name = source_repo_name
        self.source_repo_branch = source_repo_branch
        self.target_repo_namespace = target_repo_namespace
        self.target_repo_name = target_repo_name
        self.target_repo_branch = target_repo_branch
        self.project_url = project_url
        self.commit_sha = commit_sha

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        result = super().get_dict()
        result["action"] = result["action"].value
        return result

    def get_base_project(self) -> GitProject:
        return self.project.service.get_project(
            namespace=self.source_repo_namespace,
            repo=self.source_repo_name,
        )


class MergeRequestCommentGitlabEvent(AddPullRequestDbTrigger, AbstractGitlabEvent):
    def __init__(
        self,
        action: GitlabEventAction,
        object_id: int,
        object_iid: int,
        source_repo_namespace: str,
        source_repo_name: Optional[str],
        target_repo_namespace: str,
        target_repo_name: str,
        project_url: str,
        username: str,
        comment: str,
        commit_sha: str,
    ):
        super().__init__(
            project_url=project_url,
            pr_id=object_iid,
        )
        self.action = action
        self.object_id = object_id
        self.object_iid = object_iid
        self.source_repo_namespace = source_repo_namespace
        self.source_repo_name = source_repo_name
        self.target_repo_namespace = target_repo_namespace
        self.target_repo_name = target_repo_name
        self.project_url = project_url
        self.user_login = username
        self.comment = comment
        self.commit_sha = commit_sha
        self.identifier = str(object_iid)

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        result = super().get_dict()
        result["action"] = result["action"].value
        return result

    def get_base_project(self) -> GitProject:
        return self.project.service.get_project(
            namespace=self.source_repo_namespace,
            repo=self.source_repo_name,
        )


class IssueCommentGitlabEvent(AddIssueDbTrigger, AbstractGitlabEvent):
    def __init__(
        self,
        action: GitlabEventAction,
        issue_id: int,
        repo_namespace: str,
        repo_name: str,
        project_url: str,
        username: str,
        comment: str,
    ):
        super().__init__(project_url=project_url)
        self.action = action
        self.issue_id = issue_id
        self.repo_namespace = repo_namespace
        self.repo_name = repo_name
        self.project_url = project_url
        self.user_login = username
        self.comment = comment
        self._tag_name = None

    @property
    def tag_name(self):
        if not self._tag_name:
            self._tag_name = ""
            if latest_release := self.project.get_latest_release():
                self._tag_name = latest_release.tag_name
        return self._tag_name

    @property
    def commit_sha(self):
        return self.tag_name

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        result = super().get_dict()
        result["action"] = result["action"].value
        result["tag_name"] = self.tag_name
        result["issue_id"] = self.issue_id
        return result
