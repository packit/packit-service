# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from packit_service.service.db_project_events import AddBranchPushEventToDb
from packit_service.worker.events.github.abstract import GithubEvent


class Push(AddBranchPushEventToDb, GithubEvent):
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

    @classmethod
    def event_type(cls) -> str:
        return "github.push.Push"
