# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from typing import Optional

from ogr.abstract import Comment as OgrComment
from ogr.abstract import GitProject

from packit_service.service.db_project_events import AddPullRequestEventToDb

from ..abstract.comment import PullRequest as AbstractPRCommentEvent
from ..enums import PullRequestAction, PullRequestCommentAction
from .abstract import ForgejoEvent


class Action(AddPullRequestEventToDb, ForgejoEvent):
    def __init__(
        self,
        action: PullRequestAction,
        pr_id: int,
        base_repo_namespace: str,
        base_repo_name: str,
        base_ref: str,
        target_repo_namespace: str,
        target_repo_name: str,
        project_url: str,
        commit_sha: str,
        commit_sha_before: str,
        actor: str,
        body: str,
    ):
        super().__init__(project_url=project_url, pr_id=pr_id)
        self.action = action
        self.base_repo_namespace = base_repo_namespace
        self.base_repo_name = base_repo_name
        self.base_ref = base_ref
        self.target_repo_namespace = target_repo_namespace
        self.target_repo_name = target_repo_name
        self.commit_sha = commit_sha
        self.commit_sha_before = commit_sha_before
        self.actor = actor
        self.identifier = str(pr_id)
        self._pr_id = pr_id
        self.git_ref = None  # use pr_id for checkout
        self.body = body

    def get_dict(self, default_dict: Optional[dict] = None) -> dict:
        result = super().get_dict()
        result["action"] = result["action"].value
        return result

    @classmethod
    def event_type(cls) -> str:
        return "forgejo.pr.Action"

    def get_base_project(self) -> GitProject:
        return self.project.service.get_project(
            namespace=self.target_repo_namespace,
            repo=self.target_repo_name,
        )


class Comment(AbstractPRCommentEvent, ForgejoEvent):
    def __init__(
        self,
        action: PullRequestCommentAction,
        pr_id: int,
        base_repo_namespace: str,
        base_repo_name: Optional[str],
        base_ref: Optional[str],
        target_repo_namespace: str,
        target_repo_name: str,
        project_url: str,
        actor: str,
        comment: str,
        comment_id: int,
        commit_sha: Optional[str] = None,
        comment_object: Optional[OgrComment] = None,
    ) -> None:
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
        self.base_ref = base_ref
        self.target_repo_namespace = target_repo_namespace
        self.target_repo_name = target_repo_name
        self.actor = actor
        self.identifier = str(pr_id)
        self.git_ref = None
        self.pr_id = pr_id

    @classmethod
    def event_type(cls) -> str:
        return "forgejo.pr.Comment"

    def get_dict(self, default_dict: Optional[dict] = None) -> dict:
        """
        Override get_dict to avoid accessing properties that make API calls.
        Use private attributes directly, similar to forgejo/issue.py.
        """
        from ..abstract.comment import CommentEvent

        result = CommentEvent.get_dict(self, default_dict=default_dict)
        result.pop("_comment_object")
        result["action"] = self.action.value
        result["pr_id"] = self.pr_id
        result["commit_sha"] = self._commit_sha
        return result

    def get_base_project(self) -> GitProject:
        return self.project.service.get_project(
            namespace=self.target_repo_namespace,
            repo=self.target_repo_name,
        )
