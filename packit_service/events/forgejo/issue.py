# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

# SPDX-License-Identifier: MIT

from typing import Optional

from ogr.abstract import Comment as OgrComment

from ..abstract.comment import Issue as AbstractIssueCommentEvent
from ..enums import IssueCommentAction
from .abstract import ForgejoEvent


class Comment(AbstractIssueCommentEvent, ForgejoEvent):
    def __init__(
        self,
        action: IssueCommentAction,
        issue_id: int,
        repo_namespace: str,
        repo_name: str,
        target_repo: str,
        project_url: str,
        actor: str,
        comment: str,
        comment_id: int,
        tag_name: str = "",
        base_ref: Optional[str] = "main",
        comment_object: Optional[OgrComment] = None,
        dist_git_project_url=None,
    ) -> None:
        super().__init__(
            issue_id=issue_id,
            repo_namespace=repo_namespace,
            repo_name=repo_name,
            project_url=project_url,
            comment=comment,
            comment_id=comment_id,
            tag_name=tag_name,
            comment_object=comment_object,
            dist_git_project_url=dist_git_project_url,
        )
        self.action = action
        self.actor = actor
        self.base_ref = base_ref
        self.target_repo = target_repo
        self.identifier = str(issue_id)

    @classmethod
    def event_type(cls) -> str:
        return "forgejo.issue.Comment"

    @property
    def tag_name(self):
        """
        For Forgejo issue comments, return the tag_name passed in constructor
        without making API calls to avoid authentication issues.
        """
        return self._tag_name

    @tag_name.setter
    def tag_name(self, value: str) -> None:
        self._tag_name = value

    @property
    def commit_sha(self) -> Optional[str]:
        """
        For Forgejo issue comments, return the commit_sha passed in constructor
        without making API calls to avoid authentication issues.
        """
        return self._commit_sha

    @commit_sha.setter
    def commit_sha(self, value: Optional[str]) -> None:
        self._commit_sha = value

    def get_dict(self, default_dict: Optional[dict] = None) -> dict:
        """
        Override get_dict to avoid accessing properties that make API calls.
        """
        # Get the basic dict from CommentEvent, not from Issue to avoid tag_name access
        from ..abstract.comment import CommentEvent

        result = CommentEvent.get_dict(self, default_dict=default_dict)

        # Add the specific fields we need without triggering API calls
        result["action"] = self.action.value
        result["issue_id"] = self.issue_id
        result["tag_name"] = self._tag_name  # Use the private attribute directly
        result["commit_sha"] = self._commit_sha  # Use the private attribute directly

        return result
