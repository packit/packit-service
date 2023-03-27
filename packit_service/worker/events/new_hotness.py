# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
from logging import getLogger
from typing import Optional, Dict

from ogr.abstract import GitProject
from ogr.parsing import RepoUrl

from packit.config import PackageConfig, JobConfigTriggerType
from packit_service.config import ServiceConfig, PackageConfigGetter
from packit_service.models import ProjectReleaseModel
from packit_service.worker.events import Event
from packit_service.worker.events.event import use_for_job_config_trigger

logger = getLogger(__name__)


# the decorator is needed in case the DB trigger is not created (not valid arguments)
# but we still want to report from pre_check of the PullFromUpstreamHandler
@use_for_job_config_trigger(trigger_type=JobConfigTriggerType.release)
class NewHotnessUpdateEvent(Event):
    def __init__(
        self,
        package_name: str,
        version: str,
        distgit_project_url: str,
    ):
        super().__init__()
        self.package_name = package_name
        self.version = version
        self.distgit_project_url = distgit_project_url

        self._repo_url: Optional[RepoUrl] = None

    @property
    def project(self):
        if not self._project:
            self._project = self.get_project()
        return self._project

    def get_project(self) -> Optional[GitProject]:
        if not self.distgit_project_url:
            return None

        return ServiceConfig.get_service_config().get_project(
            url=self.distgit_project_url
        )

    @property
    def base_project(self):
        return None

    @property
    def db_trigger(self) -> Optional[ProjectReleaseModel]:
        if not (
            self.tag_name
            and self.repo_name
            and self.repo_namespace
            and self.project_url
        ):
            logger.info("Not going to create the DB trigger, not valid arguments.")
            return None

        return ProjectReleaseModel.get_or_create(
            tag_name=self.tag_name,
            namespace=self.repo_namespace,
            repo_name=self.repo_name,
            project_url=self.project_url,
            commit_hash=None,
        )

    @property
    def packages_config(self):
        if not self._package_config_searched and not self._package_config:
            self._package_config = self.get_packages_config()
            self._package_config_searched = True
        return self._package_config

    def get_packages_config(self) -> Optional[PackageConfig]:
        logger.debug(f"Getting package_config:\n" f"\tproject: {self.project}\n")

        package_config = PackageConfigGetter.get_package_config_from_repo(
            base_project=None,
            project=self.project,
            pr_id=None,
            reference=None,
            fail_when_missing=False,
        )

        return package_config

    @property
    def project_url(self) -> Optional[str]:
        return (
            self.packages_config.upstream_project_url if self.packages_config else None
        )

    @property
    def repo_url(self) -> Optional[RepoUrl]:
        if not self._repo_url:
            self._repo_url = RepoUrl.parse(self.project_url)
        return self._repo_url

    @property
    def repo_namespace(self) -> Optional[str]:
        return self.repo_url.namespace if self.repo_url else None

    @property
    def repo_name(self) -> Optional[str]:
        return self.repo_url.repo if self.repo_url else None

    @property
    def tag_name(self):
        if not (self.packages_config and self.packages_config.upstream_tag_template):
            return self.version

        return self.packages_config.upstream_tag_template.format(version=self.version)

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        d = self.__dict__
        d["project_url"] = self.project_url
        d["tag_name"] = self.tag_name
        d["repo_name"] = self.repo_name
        d["repo_namespace"] = self.repo_namespace
        result = super().get_dict(d)
        result.pop("_repo_url")
        return result
