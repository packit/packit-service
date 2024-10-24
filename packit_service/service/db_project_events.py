# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
This file contains helper classes for events.
"""

from typing import Optional

from ogr.abstract import GitProject

from packit_service.models import (
    GitBranchModel,
    IssueModel,
    ProjectEventModel,
    ProjectReleaseModel,
    PullRequestModel,
)


class AddReleaseEventToDb:
    tag_name: str
    repo_namespace: str
    repo_name: str
    project_url: str
    _release: ProjectReleaseModel = None
    _event: ProjectEventModel = None

    def _add_release_and_event(self):
        if not self._release or not self._event:
            self._release, self._event = ProjectEventModel.add_release_event(
                tag_name=self.tag_name,
                namespace=self.repo_namespace,
                repo_name=self.repo_name,
                project_url=self.project_url,
                commit_hash=self.commit_sha,
            )
        return self._release, self._event

    @property
    def commit_sha(self):
        """
        To please the mypy.
        """
        raise NotImplementedError()

    @property
    def db_project_object(self) -> ProjectReleaseModel:
        (release, _) = self._add_release_and_event()
        return release

    @property
    def db_project_event(self) -> ProjectEventModel:
        (_, event) = self._add_release_and_event()
        return event

    def get_dict(self, default_dict: Optional[dict] = None) -> dict:
        result = super().get_dict()  # type: ignore
        result.pop("_release", None)
        result.pop("_event", None)
        return result


class AddBranchPushEventToDb:
    git_ref: str
    repo_namespace: str
    repo_name: str
    project_url: str
    commit_sha: str
    _branch: GitBranchModel = None
    _event: ProjectEventModel = None

    def _add_branch_and_event(self):
        if not self._branch or not self._event:
            self._branch, self._event = ProjectEventModel.add_branch_push_event(
                branch_name=self.git_ref,
                namespace=self.repo_namespace,
                repo_name=self.repo_name,
                project_url=self.project_url,
                commit_sha=self.commit_sha,
            )
        return self._branch, self._event

    @property
    def db_project_object(self) -> GitBranchModel:
        (branch, _) = self._add_branch_and_event()
        return branch

    @property
    def db_project_event(self) -> ProjectEventModel:
        (_, event) = self._add_branch_and_event()
        return event

    def get_dict(self, default_dict: Optional[dict] = None) -> dict:
        result = super().get_dict()  # type: ignore
        result.pop("_branch", None)
        result.pop("_event", None)
        return result


class AddPullRequestEventToDb:
    pr_id: int
    project: GitProject
    project_url: str
    commit_sha: str
    _pull_request: PullRequestModel = None
    _event: ProjectEventModel = None

    def _add_pull_request_and_event(self):
        if not self._pull_request or not self._event:
            self._pull_request, self._event = ProjectEventModel.add_pull_request_event(
                pr_id=self.pr_id,
                namespace=self.project.namespace,
                repo_name=self.project.repo,
                project_url=self.project_url,
                commit_sha=self.commit_sha,
            )
        return self._pull_request, self._event

    @property
    def db_project_object(self) -> PullRequestModel:
        (pull_request, _) = self._add_pull_request_and_event()
        return pull_request

    @property
    def db_project_event(self) -> ProjectEventModel:
        (_, event) = self._add_pull_request_and_event()
        return event

    def get_dict(self, default_dict: Optional[dict] = None) -> dict:
        result = super().get_dict()  # type: ignore
        result.pop("_pull_request", None)
        result.pop("_event", None)
        return result


class AddIssueEventToDb:
    issue_id: int
    repo_namespace: str
    repo_name: str
    project_url: str
    commit_sha: str
    _issue: IssueModel = None
    _event: ProjectEventModel = None

    def _add_issue_and_event(self):
        if not self._issue or not self._event:
            self._issue, self._event = ProjectEventModel.add_issue_event(
                issue_id=self.issue_id,
                namespace=self.repo_namespace,
                repo_name=self.repo_name,
                project_url=self.project_url,
            )
        return self._issue, self._event

    @property
    def db_project_object(self) -> IssueModel:
        (issue, _) = self._add_issue_and_event()
        return issue

    @property
    def db_project_event(self) -> ProjectEventModel:
        (_, event) = self._add_issue_and_event()
        return event

    def get_dict(self, default_dict: Optional[dict] = None) -> dict:
        result = super().get_dict()  # type: ignore
        result.pop("_issue", None)
        result.pop("_event", None)
        return result
