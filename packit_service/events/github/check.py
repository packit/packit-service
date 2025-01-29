# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import os
from typing import Optional

from packit_service.models import (
    AbstractProjectObjectDbType,
    GitBranchModel,
    ProjectEventModel,
    ProjectReleaseModel,
    PullRequestModel,
)

from .abstract import GithubEvent


class Rerun(GithubEvent):
    def __init__(
        self,
        check_name_job: str,
        check_name_target: str,
        project_url: str,
        repo_namespace: str,
        repo_name: str,
        db_project_event: ProjectEventModel,
        commit_sha: str,
        actor: str,
        pr_id: Optional[int] = None,
        job_identifier: Optional[str] = None,
    ):
        super().__init__(project_url=project_url, pr_id=pr_id)
        self.check_name_job = check_name_job
        self.check_name_target = check_name_target
        self.repo_namespace = repo_namespace
        self.repo_name = repo_name
        self.commit_sha = commit_sha
        self.actor = actor
        self._db_project_event = db_project_event
        self._db_project_object: AbstractProjectObjectDbType = (
            db_project_event.get_project_event_object()
        )
        self.job_identifier = job_identifier

    @classmethod
    def event_type(cls) -> str:
        assert os.environ.get("PYTEST_VERSION"), "Should be initialized only during tests"
        return "test.github.check.Rerun"

    @property
    def build_targets_override(self) -> Optional[set[tuple[str, str]]]:
        if self.check_name_job in {"rpm-build", "production-build", "koji-build"}:
            return {(self.check_name_target, self.job_identifier)}
        return None

    @property
    def tests_targets_override(self) -> Optional[set[tuple[str, str]]]:
        if self.check_name_job == "testing-farm":
            return {(self.check_name_target, self.job_identifier)}
        return None

    @property
    def branches_override(self) -> Optional[set[str]]:
        if self.check_name_job == "propose-downstream":
            return {self.check_name_target}
        return None


class Commit(Rerun):
    _db_project_object: GitBranchModel

    def __init__(
        self,
        project_url: str,
        repo_namespace: str,
        repo_name: str,
        commit_sha: str,
        git_ref: str,
        check_name_job: str,
        check_name_target: str,
        db_project_event,
        actor: str,
        job_identifier: Optional[str] = None,
    ):
        super().__init__(
            check_name_job=check_name_job,
            check_name_target=check_name_target,
            project_url=project_url,
            repo_namespace=repo_namespace,
            repo_name=repo_name,
            db_project_event=db_project_event,
            commit_sha=commit_sha,
            actor=actor,
            job_identifier=job_identifier,
        )
        self.identifier = git_ref
        self.git_ref = git_ref

    @classmethod
    def event_type(cls) -> str:
        return "github.check.Commit"


class PullRequest(Rerun):
    _db_project_object: PullRequestModel

    def __init__(
        self,
        pr_id: int,
        repo_namespace: str,
        repo_name: str,
        project_url: str,
        commit_sha: str,
        check_name_job: str,
        check_name_target: str,
        db_project_event,
        actor: str,
        job_identifier: Optional[str] = None,
    ):
        super().__init__(
            check_name_job=check_name_job,
            check_name_target=check_name_target,
            project_url=project_url,
            repo_namespace=repo_namespace,
            repo_name=repo_name,
            db_project_event=db_project_event,
            commit_sha=commit_sha,
            pr_id=pr_id,
            actor=actor,
            job_identifier=job_identifier,
        )
        self.identifier = str(pr_id)
        self.git_ref = None

    @classmethod
    def event_type(cls) -> str:
        return "github.check.PullRequest"


class Release(Rerun):
    _db_project_object: ProjectReleaseModel

    def __init__(
        self,
        repo_namespace: str,
        repo_name: str,
        tag_name: str,
        project_url: str,
        commit_sha: str,
        check_name_job: str,
        check_name_target: str,
        db_project_event,
        actor: str,
        job_identifier: Optional[str] = None,
    ):
        super().__init__(
            check_name_job=check_name_job,
            check_name_target=check_name_target,
            project_url=project_url,
            repo_namespace=repo_namespace,
            repo_name=repo_name,
            db_project_event=db_project_event,
            commit_sha=commit_sha,
            actor=actor,
            job_identifier=job_identifier,
        )
        self.tag_name = tag_name
        self.git_ref = tag_name
        self.identifier = tag_name

    @classmethod
    def event_type(cls) -> str:
        return "github.check.Release"
