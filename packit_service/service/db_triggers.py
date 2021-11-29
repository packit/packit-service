# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
This file contains helper classes for events.
"""
from typing import Optional

from ogr.abstract import GitProject
from packit_service.models import (
    AbstractTriggerDbType,
    GitBranchModel,
    IssueModel,
    ProjectReleaseModel,
    PullRequestModel,
)


class _AddDbTrigger:
    # This is a private common supertype for Add*DbTrigger classes. Use it only as a superclass in
    # this file as a superclass of Add*DbTrigger classes.
    pass


class AddReleaseDbTrigger(_AddDbTrigger):
    tag_name: str
    repo_namespace: str
    repo_name: str
    project_url: str
    commit_sha: Optional[str]

    @staticmethod
    def get_or_create(
        tag_name: str,
        repo_namespace: str,
        repo_name: str,
        project_url: str,
        commit_sha: Optional[str],
    ) -> Optional[AbstractTriggerDbType]:
        return ProjectReleaseModel.get_or_create(
            tag_name=tag_name,
            namespace=repo_namespace,
            repo_name=repo_name,
            project_url=project_url,
            commit_hash=commit_sha,
        )

    @property
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        return self.get_or_create(
            tag_name=self.tag_name,
            repo_namespace=self.repo_namespace,
            repo_name=self.repo_name,
            project_url=self.project_url,
            commit_sha=self.commit_sha,
        )


class AddPullRequestDbTrigger(_AddDbTrigger):
    pr_id: int
    project: GitProject
    project_url: str

    @staticmethod
    def get_or_create(
        pr_id: int,
        repo_namespace: str,
        repo_name: str,
        project_url: str,
    ) -> Optional[AbstractTriggerDbType]:
        return PullRequestModel.get_or_create(
            pr_id=pr_id,
            namespace=repo_namespace,
            repo_name=repo_name,
            project_url=project_url,
        )

    @property
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        return self.get_or_create(
            pr_id=self.pr_id,
            repo_namespace=self.project.namespace,
            repo_name=self.project.repo,
            project_url=self.project_url,
        )


class AddIssueDbTrigger(_AddDbTrigger):
    issue_id: int
    repo_namespace: str
    repo_name: str
    project_url: str

    @staticmethod
    def get_or_create(
        issue_id: int,
        repo_namespace: str,
        repo_name: str,
        project_url: str,
    ) -> Optional[AbstractTriggerDbType]:
        return IssueModel.get_or_create(
            issue_id=issue_id,
            namespace=repo_namespace,
            repo_name=repo_name,
            project_url=project_url,
        )

    @property
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        return self.get_or_create(
            issue_id=self.issue_id,
            repo_namespace=self.repo_namespace,
            repo_name=self.repo_name,
            project_url=self.project_url,
        )


class AddBranchPushDbTrigger(_AddDbTrigger):
    git_ref: str
    repo_namespace: str
    repo_name: str
    project_url: str

    @staticmethod
    def get_or_create(
        git_ref: str,
        repo_namespace: str,
        repo_name: str,
        project_url: str,
    ) -> Optional[AbstractTriggerDbType]:
        return GitBranchModel.get_or_create(
            branch_name=git_ref,
            namespace=repo_namespace,
            repo_name=repo_name,
            project_url=project_url,
        )

    @property
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        return self.get_or_create(
            git_ref=self.git_ref,
            repo_namespace=self.repo_namespace,
            repo_name=self.repo_name,
            project_url=self.project_url,
        )
