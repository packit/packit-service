# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
from re import fullmatch
from typing import Dict, Optional

from ogr.abstract import GitProject, PRComment, IssueComment

from packit_service.config import ServiceConfig
from packit_service.models import AbstractTriggerDbType, PullRequestModel
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
        source_project_url: str,
        target_repo_namespace: str,
        target_repo_name: str,
        target_repo_branch: str,
        project_url: str,
        commit_sha: str,
        title: str,
        description: str,
        url: str,
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
        self.source_project_url = source_project_url
        self.target_repo_namespace = target_repo_namespace
        self.target_repo_name = target_repo_name
        self.target_repo_branch = target_repo_branch
        self.project_url = project_url
        self.commit_sha = commit_sha
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
        comment_id: int,
        comment_object: Optional[PRComment] = None,
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
        self.comment_id = comment_id

        # Lazy properties
        self._comment_object = comment_object

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        result = super().get_dict()
        result["action"] = result["action"].value
        result.pop("_comment_object")
        return result

    def get_base_project(self) -> GitProject:
        return self.project.service.get_project(
            namespace=self.source_repo_namespace,
            repo=self.source_repo_name,
        )

    @property
    def comment_object(self) -> Optional[PRComment]:
        if not self._comment_object:
            self._comment_object = self.project.get_pr(self.object_id).get_comment(
                self.comment_id
            )
        return self._comment_object


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
        comment_id: int,
        comment_object: Optional[IssueComment] = None,
    ):
        super().__init__(project_url=project_url)
        self.action = action
        self.issue_id = issue_id
        self.repo_namespace = repo_namespace
        self.repo_name = repo_name
        self.project_url = project_url
        self.user_login = username
        self.comment = comment
        self.comment_id = comment_id
        self._tag_name = None

        # Lazy properties
        self._comment_object = comment_object

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
        result.pop("_comment_object")
        return result

    @property
    def comment_object(self) -> Optional[IssueComment]:
        if not self._comment_object:
            self._comment_object = self.project.get_issue(self.issue_id).get_comment(
                self.comment_id
            )
        return self._comment_object


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

        self._db_trigger: Optional[AbstractTriggerDbType] = None

    @property
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        if not self._db_trigger and self.source == "merge_request_event":
            # Can't use self.project because that can be either source or target project.
            # We need target project here. Let's derive it from self.merge_request_url
            m = fullmatch(r"(\S+)/-/merge_requests/(\d+)", self.merge_request_url)
            if m:
                project = ServiceConfig.get_service_config().get_project(url=m[1])
                self._db_trigger = PullRequestModel.get_or_create(
                    pr_id=int(m[2]),
                    namespace=project.namespace,
                    repo_name=project.repo,
                    project_url=m[1],
                )
        return self._db_trigger
