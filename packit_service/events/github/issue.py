# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from typing import Optional

from ogr.abstract import Comment as OgrComment

from ..abstract.comment import Issue as AbstractIssueCommentEvent
from ..enums import IssueCommentAction
from .abstract import GithubEvent


class Comment(AbstractIssueCommentEvent, GithubEvent):
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
        base_ref: Optional[str] = "master",  # default is master when working with issues
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
        return "github.issue.Comment"

    def get_dict(self, default_dict: Optional[dict] = None) -> dict:
        result = super().get_dict()
        result["action"] = result["action"].value
        return result
