# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
from typing import Dict, Optional

from ogr.abstract import GitProject, Comment
from packit_service.service.db_triggers import (
    AddPullRequestDbTrigger,
    AddBranchPushDbTrigger,
    AddReleaseDbTrigger,
)
from packit_service.worker.events.comment import (
    AbstractIssueCommentEvent,
    AbstractPRCommentEvent,
)
from packit_service.worker.events.enums import GitlabEventAction
from packit_service.worker.events.event import AbstractForgeIndependentEvent


class AbstractGitlabEvent(AbstractForgeIndependentEvent):
    def __init__(self, project_url: str, pr_id: Optional[int] = None, **kwargs):
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
        oldrev: Optional[str],
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
        self.oldrev = oldrev
        self.title = title
        self.description = description
        self.url = url

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        result = super().get_dict()
        result["action"] = result["action"].value
        return result

    def get_base_project(self) -> GitProject:
        return self.project.service.get_project(
            namespace=self.source_repo_namespace,
            repo=self.source_repo_name,
        )


class MergeRequestCommentGitlabEvent(AbstractPRCommentEvent, AbstractGitlabEvent):
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
        actor: str,
        comment: str,
        comment_id: int,
        commit_sha: str,
        comment_object: Optional[Comment] = None,
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

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        result = super().get_dict()
        result["action"] = result["action"].value
        return result

    def get_base_project(self) -> GitProject:
        return self.project.service.get_project(
            namespace=self.source_repo_namespace,
            repo=self.source_repo_name,
        )


class IssueCommentGitlabEvent(AbstractIssueCommentEvent, AbstractGitlabEvent):
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
        comment_object: Optional[Comment] = None,
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
        )
        self.action = action
        self.actor = actor

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        result = super().get_dict()
        result["action"] = result["action"].value
        return result


class PipelineGitlabEvent(AbstractGitlabEvent):
    def __init__(
        self,
        project_url: str,
        project_name: str,
        pipeline_id: int,
        git_ref: str,
        status: str,
        detailed_status: str,
        commit_sha: str,
        source: str,
        merge_request_url: Optional[str],
    ):
        super().__init__(project_url=project_url)
        self.project_name = project_name
        self.pipeline_id = pipeline_id
        self.git_ref = git_ref
        self.status = status
        self.detailed_status = detailed_status
        self.commit_sha = commit_sha
        self.source = source
        self.merge_request_url = merge_request_url


class ReleaseGitlabEvent(AddReleaseDbTrigger, AbstractGitlabEvent):
    def __init__(
        self,
        repo_namespace: str,
        repo_name: str,
        git_ref: str,
        tag_name: str,
        project_url: str,
        commit_sha: str,
    ):
        super().__init__(project_url=project_url)
        self.repo_namespace = repo_namespace
        self.repo_name = repo_name
        self.git_ref = git_ref
        self.tag_name = tag_name
        self._commit_sha = commit_sha

    @property
    def commit_sha(self):
        return self._commit_sha


class TagPushGitlabEvent(AddBranchPushDbTrigger, AbstractGitlabEvent):
    def __init__(
        self,
        repo_namespace: str,
        repo_name: str,
        actor: str,
        git_ref: str,
        project_url: str,
        commit_sha: str,
        title: str,
        message: str,
    ):
        super().__init__(project_url=project_url)
        self.repo_namespace = repo_namespace
        self.repo_name = repo_name
        self.actor = actor
        self.git_ref = git_ref
        self.commit_sha = commit_sha
        self.title = title
        self.message = message
