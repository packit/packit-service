# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
from typing import Optional

from packit_service.service.db_project_events import (
    AddReleaseEventToDb,
)

from .abstract import GitlabEvent


class Release(AddReleaseEventToDb, GitlabEvent):
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

    @classmethod
    def event_type(cls) -> str:
        return "gitlab.release.Release"

    @property
    def commit_sha(self):
        return self._commit_sha

    def get_dict(self, default_dict: Optional[dict] = None) -> dict:
        result = super().get_dict()
        result["commit_sha"] = self.commit_sha
        return result
