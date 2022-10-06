# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from typing import Optional, Protocol

from fasjson_client import Client
from fasjson_client.errors import APIError

from ogr.abstract import Issue

from packit.api import PackitAPI
from packit.local_project import LocalProject
from packit.utils.repo import RepositoryCache

from ogr.abstract import GitProject, PullRequest, PRStatus

from packit_service.config import ServiceConfig
from packit_service.worker.events import EventData

from packit_service.constants import (
    FASJSON_URL,
)

logger = logging.getLogger(__name__)


class Config(Protocol):
    data: EventData

    @property
    def project(self) -> Optional[GitProject]:
        ...

    @property
    def service_config(self) -> Optional[ServiceConfig]:
        ...

    @property
    def project_url(self) -> str:
        ...


class ConfigMixin(Config):
    _project: Optional[GitProject] = None
    _service_config: Optional[ServiceConfig] = None
    data: EventData

    @property
    def service_config(self) -> ServiceConfig:
        if not self._service_config:
            self._service_config = ServiceConfig.get_service_config()
        return self._service_config

    @property
    def project(self) -> Optional[GitProject]:
        if not self._project and self.data.project_url:
            self._project = self.service_config.get_project(url=self.data.project_url)
        return self._project

    @property
    def project_url(self) -> str:
        return self.data.project_url


class PackitAPIProtocol(Config):
    api: Optional[PackitAPI] = None
    local_project: Optional[LocalProject] = None

    @property
    def packit_api(self) -> PackitAPI:
        ...


class PackitAPIWithDownstreamProtocol(PackitAPIProtocol):
    _packit_api: Optional[PackitAPI] = None

    def is_packager(self, user) -> bool:
        """Check that the given FAS user
        is a packager

        Args:
            user (str) FAS user account name
        Returns:
            true if a packager false otherwise
        """
        ...


class PackitAPIWithDownstreamMixin(PackitAPIWithDownstreamProtocol):
    _packit_api: Optional[PackitAPI] = None

    @property
    def packit_api(self):
        if not self._packit_api:
            self._packit_api = PackitAPI(
                self.service_config,
                self.job_config,
                downstream_local_project=self.local_project,
            )
        return self._packit_api

    def is_packager(self, user):
        self.packit_api.init_kerberos_ticket()
        client = Client(FASJSON_URL)
        try:
            groups = client.list_user_groups(username=user)
        except APIError:
            logger.debug(f"Unable to get groups for user {user}.")
            return False
        return "packager" in [group["groupname"] for group in groups.result]


class PackitAPIWithUpstreamMixin(PackitAPIProtocol):
    _packit_api: Optional[PackitAPI] = None

    @property
    def packit_api(self):
        if not self._packit_api:
            self._packit_api = PackitAPI(
                self.service_config,
                self.job_config,
                upstream_local_project=self.local_project,
            )
        return self._packit_api


class LocalProjectMixin(ConfigMixin):
    _local_project: Optional[LocalProject] = None

    @property
    def local_project(self) -> LocalProject:
        if not self._local_project:
            self._local_project = LocalProject(
                git_project=self.project,
                working_dir=self.service_config.command_handler_work_dir,
                cache=RepositoryCache(
                    cache_path=self.service_config.repository_cache,
                    add_new=self.service_config.add_repositories_to_repository_cache,
                )
                if self.service_config.repository_cache
                else None,
            )
        return self._local_project


class GetPagurePullRequest(Protocol):
    @property
    def pull_request(self) -> PullRequest:
        ...

    def get_pr_author(self) -> Optional[str]:
        ...


class GetPagurePullRequestMixin(GetPagurePullRequest):
    _pull_request: Optional[PullRequest] = None

    @property
    def pull_request(self):
        if not self._pull_request and self.data.event_dict["committer"] == "pagure":
            logger.debug(
                f"Getting pull request with head commit {self.data.commit_sha}"
                f"for repo {self.project.namespace}/{self.project.repo}"
            )
            prs = [
                pr
                for pr in self.project.get_pr_list(status=PRStatus.all)
                if pr.head_commit == self.data.commit_sha
            ]
            if prs:
                self._pull_request = prs[0]
        return self._pull_request

    def get_pr_author(self):
        """Get the login of the author of the PR (if there is any corresponding PR)."""
        return self.pull_request.author if self.pull_request else None


class GetIssue(Protocol):
    @property
    def issue(self) -> Issue:
        ...


class GetIssueMixin(GetIssue, ConfigMixin):
    _issue: Optional[Issue] = None

    @property
    def issue(self):
        if not self._issue:
            self._issue = self.project.get_issue(self.data.issue_id)
        return self._issue
