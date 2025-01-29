# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from typing import Optional

from packit_service.service.db_project_events import AddBranchPushEventToDb

from .abstract import PagureEvent


class Commit(AddBranchPushEventToDb, PagureEvent):
    def __init__(
        self,
        repo_namespace: str,
        repo_name: str,
        git_ref: str,
        project_url: str,
        commit_sha: str,
        committer: str,
        pr_id: Optional[int],
    ):
        super().__init__(project_url=project_url, pr_id=pr_id)
        self.repo_namespace = repo_namespace
        self.repo_name = repo_name
        self.git_ref = git_ref
        self.commit_sha = commit_sha
        self.identifier = git_ref
        self.committer = committer

    @classmethod
    def event_type(cls) -> str:
        return "pagure.push.Commit"
