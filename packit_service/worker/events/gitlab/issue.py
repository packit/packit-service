# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
from typing import Optional

from ogr.abstract import Comment as OgrComment

from packit_service.worker.events.abstract.comment import (
    Issue as AbstractIssueCommentEvent,
)
from packit_service.worker.events.enums import GitlabEventAction
from packit_service.worker.events.gitlab.abstract import GitlabEvent


class Comment(AbstractIssueCommentEvent, GitlabEvent):
    def __init__(
        self,
        action: GitlabEventAction,
        issue_id: int,
        repo_namespace: str,
        repo_name: str,
        project_url: str,
        actor: str,
        comment: str,
        comment_id: int,
        tag_name: str = "",
        comment_object: Optional[OgrComment] = None,
        dist_git_project_url=None,
    ):
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

    def get_dict(self, default_dict: Optional[dict] = None) -> dict:
        result = super().get_dict()
        result["action"] = result["action"].value
        return result
