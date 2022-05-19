# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from logging import getLogger
from typing import Dict, Optional

from ogr.abstract import Comment, GitProject

from packit_service.service.db_triggers import (
    AddBranchPushDbTrigger,
    AddPullRequestDbTrigger,
)
from packit_service.worker.events.enums import (
    PullRequestAction,
    PullRequestCommentAction,
)
from packit_service.worker.events.comment import AbstractPRCommentEvent
from packit_service.worker.events.event import AbstractForgeIndependentEvent

logger = getLogger(__name__)


class AbstractPagureEvent(AbstractForgeIndependentEvent):
    def __init__(self, project_url: str, pr_id: Optional[int] = None, **kwargs):
        super().__init__(pr_id=pr_id)
        self.project_url: str = project_url
        self.git_ref: Optional[str] = None  # git ref that can be 'git checkout'-ed
        self.identifier: Optional[
            str
        ] = None  # will be shown to users -- e.g. in logs or in the copr-project name


class PushPagureEvent(AddBranchPushDbTrigger, AbstractPagureEvent):
    def __init__(
        self,
        repo_namespace: str,
        repo_name: str,
        git_ref: str,
        project_url: str,
        commit_sha: str,
        name: str,
        email: str,
    ):
        super().__init__(project_url=project_url)
        self.repo_namespace = repo_namespace
        self.repo_name = repo_name
        self.git_ref = git_ref
        self.commit_sha = commit_sha
        self.identifier = git_ref
        self.name = name
        self.email = email


class PullRequestCommentPagureEvent(AbstractPRCommentEvent, AbstractPagureEvent):
    def __init__(
        self,
        action: PullRequestCommentAction,
        pr_id: int,
        base_repo_namespace: str,
        base_repo_name: str,
        base_repo_owner: str,
        base_ref: Optional[str],
        target_repo: str,
        project_url: str,
        user_login: str,
        comment: str,
        comment_id: int,
        commit_sha: str = "",
        comment_object: Optional[Comment] = None,
    ):
        super().__init__(
            pr_id=pr_id,
            project_url=project_url,
            comment=comment,
            comment_id=comment_id,
            commit_sha=commit_sha,
            comment_object=comment_object,
        )
        self.action = action
        self.base_repo_namespace = base_repo_namespace
        self.base_repo_name = base_repo_name
        self.base_repo_owner = base_repo_owner
        self.base_ref = base_ref
        self.target_repo = target_repo
        self.user_login = user_login
        self.identifier = str(pr_id)
        self.git_ref = None  # pr_id will be used for checkout

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        result = super().get_dict()
        result["action"] = result["action"].value
        return result

    def get_base_project(self) -> GitProject:
        fork = self.project.service.get_project(
            namespace=self.base_repo_namespace,
            repo=self.base_repo_name,
            username=self.base_repo_owner,
            is_fork=True,
        )
        logger.debug(f"Base project: {fork} owned by {self.base_repo_owner}")
        return fork


class PullRequestPagureEvent(AddPullRequestDbTrigger, AbstractPagureEvent):
    def __init__(
        self,
        action: PullRequestAction,
        pr_id: int,
        base_repo_namespace: str,
        base_repo_name: str,
        base_repo_owner: str,
        base_ref: str,
        target_repo: str,
        project_url: str,
        commit_sha: str,
        user_login: str,
    ):
        super().__init__(project_url=project_url, pr_id=pr_id)
        self.action = action
        self.base_repo_namespace = base_repo_namespace
        self.base_repo_name = base_repo_name
        self.base_repo_owner = base_repo_owner
        self.base_ref = base_ref
        self.target_repo = target_repo
        self.commit_sha = commit_sha
        self.user_login = user_login
        self.identifier = str(pr_id)
        self.git_ref = None  # pr_id will be used for checkout
        self.project_url = project_url

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        result = super().get_dict()
        result["action"] = result["action"].value
        return result

    def get_base_project(self) -> GitProject:
        fork = self.project.service.get_project(
            namespace=self.base_repo_namespace,
            repo=self.base_repo_name,
            username=self.base_repo_owner,
            is_fork=True,
        )
        logger.debug(f"Base project: {fork} owned by {self.base_repo_owner}")
        return fork


class PullRequestMergedPagureEvent(AddBranchPushDbTrigger, AbstractPagureEvent):
    def __init__(
        self,
        pr_id: int,
        repo_namespace: str,
        repo_name: str,
        base_repo_owner: str,
        git_ref: str,
        target_repo: str,
        project_url: str,
        commit_sha: str,
        pr_author: str,
        committer: str,
    ):
        super().__init__(project_url=project_url, pr_id=pr_id)
        self.repo_namespace = repo_namespace
        self.repo_name = repo_name
        self.base_repo_owner = base_repo_owner
        self.git_ref = git_ref
        self.target_repo = target_repo
        self.commit_sha = commit_sha
        self.pr_author = pr_author
        self.committer = committer
        self.identifier = str(pr_id)
        self.project_url = project_url

    def get_base_project(self) -> GitProject:
        fork = self.project.service.get_project(
            namespace=self.repo_namespace,
            repo=self.repo_name,
            username=self.base_repo_owner,
            is_fork=True,
        )
        logger.debug(f"Base project: {fork} owned by {self.base_repo_owner}")
        return fork


class PullRequestFlagPagureEvent(AbstractPagureEvent):
    def __init__(
        self,
        username: str,
        comment: str,
        status: str,
        date_updated: int,
        url: str,
        commit_sha: str,
        pr_id: int,
        pr_url: str,
        pr_source_branch: str,
        project_url: str,
        project_name: str,
        project_namespace: str,
    ):
        super().__init__(project_url=project_url, pr_id=pr_id)
        self.username = username
        self.comment = comment
        self.status = status
        self.date_updated = date_updated
        self.url = url
        self.commit_sha = commit_sha
        self.pr_url = pr_url
        self.pr_source_branch = pr_source_branch
        self.project_name = project_name
        self.project_namespace = project_namespace
