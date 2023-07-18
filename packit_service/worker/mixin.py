# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from abc import abstractmethod
import logging
import re
from typing import Optional, Protocol, Union, List

from fasjson_client import Client
from fasjson_client.errors import APIError

from ogr.abstract import Issue

from packit.api import PackitAPI
from packit.local_project import LocalProject
from packit.utils.repo import RepositoryCache
from packit.config.job_config import JobConfig
from packit.vm_image_build import ImageBuilder

from ogr.abstract import GitProject, PullRequest, PRStatus

from packit_service.config import ServiceConfig
from packit_service.models import CoprBuildTargetModel, BuildStatus
from packit_service.worker.reporting import BaseCommitStatus
from packit_service.worker.events import EventData
from packit_service.worker.helpers.job_helper import BaseJobHelper

from packit_service.constants import (
    FASJSON_URL,
)

logger = logging.getLogger(__name__)


class Config(Protocol):
    data: EventData

    @property
    @abstractmethod
    def project(self) -> Optional[GitProject]:
        ...

    @property
    @abstractmethod
    def service_config(self) -> Optional[ServiceConfig]:
        ...

    @property
    @abstractmethod
    def project_url(self) -> str:
        ...


class ConfigFromEventMixin(Config):
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


class ConfigFromUrlMixin(Config):
    _project: Optional[GitProject] = None
    _service_config: Optional[ServiceConfig] = None
    _project_url: str
    data: EventData

    @property
    def service_config(self) -> ServiceConfig:
        if not self._service_config:
            self._service_config = ServiceConfig.get_service_config()
        return self._service_config

    @property
    def project(self) -> Optional[GitProject]:
        if not self._project and self.data.project_url:
            self._project = self.service_config.get_project(url=self.project_url)
        return self._project

    @property
    def project_url(self) -> str:
        return self._project_url


class ConfigFromDistGitUrlMixin(Config):
    _project: Optional[GitProject] = None
    _service_config: Optional[ServiceConfig] = None
    _project_url: str
    data: EventData

    @property
    def service_config(self) -> ServiceConfig:
        if not self._service_config:
            self._service_config = ServiceConfig.get_service_config()
        return self._service_config

    @property
    def project(self) -> Optional[GitProject]:
        if not self._project and self.data.event_dict["dist_git_project_url"]:
            self._project = self.service_config.get_project(url=self.project_url)
        return self._project

    @property
    def project_url(self) -> str:
        return self.data.event_dict["dist_git_project_url"]


class PackitAPIProtocol(Config):
    local_project: Optional[LocalProject] = None

    @property
    @abstractmethod
    def packit_api(self) -> PackitAPI:
        ...

    @abstractmethod
    def clean_api(self) -> None:
        ...


class PackitAPIWithDownstreamProtocol(PackitAPIProtocol):
    _packit_api: Optional[PackitAPI] = None

    @abstractmethod
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

    def clean_api(self) -> None:
        """TODO: probably we should clean something even here
        but for now let it do the same as before the refactoring
        """
        pass


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

    def clean_api(self) -> None:
        if self._packit_api:
            self._packit_api.clean()


class LocalProjectMixin(Config):
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
    @abstractmethod
    def pull_request(self) -> PullRequest:
        ...

    @abstractmethod
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
    @abstractmethod
    def issue(self) -> Issue:
        ...


class GetIssueMixin(GetIssue, ConfigFromEventMixin):
    _issue: Optional[Issue] = None

    @property
    def issue(self):
        if not self._issue:
            self._issue = self.project.get_issue(self.data.issue_id)
        return self._issue


class GetBranches(Protocol):
    @property
    @abstractmethod
    def branches(self) -> List[str]:
        ...


class GetBranchesFromIssueMixin(Config, GetBranches):
    @property
    def branches(self) -> List[str]:
        """Get branches names from an issue comment like the following:


        Packit failed on creating pull-requests in dist-git (https://src.fedoraproject.org/rpms/python-teamcity-messages): # noqa

        | dist-git branch | error |
        | --------------- | ----- |
        | `f37` | `` |


        You can retrigger the update by adding a comment (`/packit propose-downstream`) into this issue.
        """
        branches = set()
        branch_regex = re.compile(r"\s*\| `(\S+)` \|")
        issue = self.data.project.get_issue(self.data.issue_id)
        for line in issue.description.splitlines():
            if m := branch_regex.match(line):
                branches.add(m[1])
        for comment in issue.get_comments():
            for line in comment.body.splitlines():
                if m := branch_regex.match(line):
                    branches.add(m[1])
        return list(branches)


class GetVMImageBuilder(Protocol):
    @property
    @abstractmethod
    def vm_image_builder(self):
        ...


class GetVMImageData(Protocol):
    @property
    @abstractmethod
    def build_id(self) -> str:
        ...

    @property
    @abstractmethod
    def chroot(self) -> str:
        ...

    @property
    @abstractmethod
    def identifier(self) -> str:
        ...

    @property
    @abstractmethod
    def owner(self) -> str:
        ...

    @property
    @abstractmethod
    def project_name(self) -> str:
        ...

    @property
    @abstractmethod
    def image_distribution(self) -> str:
        ...

    @property
    @abstractmethod
    def image_request(self) -> dict:
        ...

    @property
    @abstractmethod
    def image_customizations(self) -> dict:
        ...


class GetVMImageBuilderMixin(Config):
    _vm_image_builder: Optional[ImageBuilder] = None

    @property
    def vm_image_builder(self):
        if not self._vm_image_builder:
            self._vm_image_builder = ImageBuilder(
                self.service_config.redhat_api_refresh_token
            )
        return self._vm_image_builder


class GetVMImageDataMixin(Config):
    job_config: JobConfig
    _copr_build: Optional[CoprBuildTargetModel] = None

    @property
    def chroot(self) -> str:
        return self.job_config.copr_chroot

    @property
    def identifier(self) -> str:
        return self.job_config.identifier

    @property
    def owner(self) -> str:
        return self.job_config.owner or (
            self.copr_build.owner if self.copr_build else None
        )

    @property
    def project_name(self) -> str:
        return self.job_config.project or (
            self.copr_build.project_name if self.copr_build else None
        )

    @property
    def image_name(self) -> str:
        return f"{self.owner}/" f"{self.project_name}/{self.data.pr_id}"

    @property
    def image_distribution(self) -> str:
        return self.job_config.image_distribution

    @property
    def image_request(self) -> dict:
        return self.job_config.image_request

    @property
    def image_customizations(self) -> dict:
        return self.job_config.image_customizations

    @property
    def copr_build(self) -> Optional[CoprBuildTargetModel]:
        if not self._copr_build:
            copr_builds = CoprBuildTargetModel.get_all_by(
                project_name=self.job_config.project,
                commit_sha=self.data.commit_sha,
                owner=self.job_config.owner,
                target=self.job_config.copr_chroot,
                status=BuildStatus.success,
            )

            for copr_build in copr_builds:
                project_event_object = copr_build.get_project_event_object()
                # check whether the event trigger matches
                if project_event_object.id == self.data.db_project_object.id:
                    self._copr_build = copr_build
                    break
        return self._copr_build


class GetReporter(Protocol):
    @abstractmethod
    def report(
        self,
        state: BaseCommitStatus,
        description: str,
        url: str = "",
        check_names: Union[str, list, None] = None,
        markdown_content: str = None,
    ) -> None:
        ...


class GetReporterFromJobHelperMixin(Config):
    _job_helper: BaseJobHelper = None

    @property
    def job_helper(self):
        if not self._job_helper:
            self._job_helper = BaseJobHelper(
                self.service_config,
                self.package_config,
                self.project,
                self.data,
                self.data.db_project_event,
                self.job_config,
                None,
            )
        return self._job_helper

    def report(
        self,
        state: BaseCommitStatus,
        description: str,
        url: str = "",
        check_names: Union[str, list, None] = None,
        markdown_content: str = None,
    ) -> None:
        self.job_helper._report(state, description, url, check_names, markdown_content)
