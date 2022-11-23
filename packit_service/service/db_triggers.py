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


class AddReleaseDbTrigger:
    tag_name: str
    repo_namespace: str
    repo_name: str
    project_url: str

    @property
    def commit_sha(self):
        """
        To please the mypy.
        """
        raise NotImplementedError()

    @property
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        return ProjectReleaseModel.get_or_create(
            tag_name=self.tag_name,
            namespace=self.repo_namespace,
            repo_name=self.repo_name,
            project_url=self.project_url,
            commit_hash=self.commit_sha,
        )


class AddPullRequestDbTrigger:
    pr_id: int
    project: GitProject
    project_url: str
    actor: str

    @property
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        return PullRequestModel.get_or_create(
            pr_id=self.pr_id,
            namespace=self.project.namespace,
            repo_name=self.project.repo,
            project_url=self.project_url,
            actor=self.actor,
        )


class AddIssueDbTrigger:
    issue_id: int
    repo_namespace: str
    repo_name: str
    project_url: str

    @property
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        return IssueModel.get_or_create(
            issue_id=self.issue_id,
            namespace=self.repo_namespace,
            repo_name=self.repo_name,
            project_url=self.project_url,
        )


class AddBranchPushDbTrigger:
    git_ref: str
    repo_namespace: str
    repo_name: str
    project_url: str

    @property
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        return GitBranchModel.get_or_create(
            branch_name=self.git_ref,
            namespace=self.repo_namespace,
            repo_name=self.repo_name,
            project_url=self.project_url,
        )
