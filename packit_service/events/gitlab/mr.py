# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from typing import Optional

from ogr.abstract import Comment as OgrComment
from ogr.abstract import GitProject

from packit_service.service.db_project_events import (
    AddPullRequestEventToDb,
)

from ..abstract.comment import (
    PullRequest as AbstractPRCommentEvent,
)
from .abstract import GitlabEvent
from .enums import Action as GitlabAction


class Action(AddPullRequestEventToDb, GitlabEvent):
    def __init__(
        self,
        action: GitlabAction,
        actor: str,
        object_id: int,
        object_iid: int,
        source_repo_namespace: str,
        source_repo_name: str,
        source_repo_branch: str,
        source_project_url: str,
        target_repo_namespace: str,
        target_repo_name: str,
        target_repo_branch: str,
        project_url: str,
        commit_sha: str,
        commit_sha_before: Optional[str],
        title: str,
        description: str,
        url: str,
    ):
        super().__init__(
            project_url=project_url,
            pr_id=object_iid,
        )
        self.action = action
        self.actor = actor
        self.object_id = object_id
        self.identifier = str(object_iid)
        self.source_repo_namespace = source_repo_namespace
        self.source_repo_name = source_repo_name
        self.source_repo_branch = source_repo_branch
        self.source_project_url = source_project_url
        self.target_repo_namespace = target_repo_namespace
        self.target_repo_name = target_repo_name
        self.target_repo_branch = target_repo_branch
        self.project_url = project_url
        self.commit_sha = commit_sha
        self.commit_sha_before = commit_sha_before
        self.title = title
        self.description = description
        self.url = url

    @classmethod
    def event_type(cls) -> str:
        return "gitlab.mr.Action"

    def get_dict(self, default_dict: Optional[dict] = None) -> dict:
        result = super().get_dict()
        result["action"] = result["action"].value
        return result

    def get_base_project(self) -> GitProject:
        return self.project.service.get_project(
            namespace=self.source_repo_namespace,
            repo=self.source_repo_name,
        )


class Comment(AbstractPRCommentEvent, GitlabEvent):
    def __init__(
        self,
        action: GitlabAction,
        object_id: int,
        object_iid: int,
        source_repo_namespace: str,
        source_repo_name: Optional[str],
        target_repo_namespace: str,
        target_repo_name: str,
        project_url: str,
        actor: str,
        comment: str,
        comment_id: int,
        commit_sha: str,
        comment_object: Optional[OgrComment] = None,
    ):
        super().__init__(
            project_url=project_url,
            pr_id=object_iid,
            comment=comment,
            comment_id=comment_id,
            commit_sha=commit_sha,
            comment_object=comment_object,
        )
        self.action = action
        self.object_id = object_id
        self.source_repo_name = source_repo_name
        self.source_repo_namespace = source_repo_namespace
        self.target_repo_namespace = target_repo_namespace
        self.target_repo_name = target_repo_name
        self.actor = actor
        self.identifier = str(object_iid)

    @classmethod
    def event_type(cls) -> str:
        return "gitlab.mr.Comment"

    def get_dict(self, default_dict: Optional[dict] = None) -> dict:
        result = super().get_dict()
        result["action"] = result["action"].value
        return result

    def get_base_project(self) -> GitProject:
        return self.project.service.get_project(
            namespace=self.source_repo_namespace,
            repo=self.source_repo_name,
        )
