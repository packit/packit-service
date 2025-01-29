# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from typing import Optional

from packit_service.service.db_project_events import AddReleaseEventToDb

from .abstract import GithubEvent


class Release(AddReleaseEventToDb, GithubEvent):
    def __init__(self, repo_namespace: str, repo_name: str, tag_name: str, project_url: str):
        super().__init__(project_url=project_url)
        self.repo_namespace = repo_namespace
        self.repo_name = repo_name
        self.tag_name = tag_name
        self.git_ref = tag_name
        self.identifier = tag_name
        self._commit_sha: Optional[str] = None

    @classmethod
    def event_type(cls) -> str:
        return "github.release.Release"

    @property
    def commit_sha(self) -> Optional[str]:  # type:ignore
        # mypy does not like properties
        if not self._commit_sha:
            self._commit_sha = self.project.get_sha_from_tag(tag_name=self.tag_name)
        return self._commit_sha

    def get_dict(self, default_dict: Optional[dict] = None) -> dict:
        result = super().get_dict()
        result["commit_sha"] = self.commit_sha
        return result
