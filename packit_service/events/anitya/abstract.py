# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from abc import abstractmethod
from functools import cached_property
from logging import getLogger
from typing import Optional

from ogr.abstract import GitProject
from ogr.parsing import RepoUrl
from packit.config import PackageConfig

from packit_service.config import ServiceConfig
from packit_service.models import ProjectEventModel, ProjectReleaseModel
from packit_service.package_config_getter import PackageConfigGetter

from ..event import Event

logger = getLogger(__name__)


class AnityaUpdate(Event):
    def __init__(
        self,
        package_name: str,
        distgit_project_url: str,
        anitya_project_id: int,
        anitya_project_name: str,
    ):
        super().__init__()

        self.package_name = package_name
        self.distgit_project_url = distgit_project_url
        self.anitya_project_id = anitya_project_id
        self.anitya_project_name = anitya_project_name

        self._repo_url: Optional[RepoUrl] = None
        self._db_project_object: Optional[ProjectReleaseModel] = None
        self._db_project_event: Optional[ProjectEventModel] = None

        self._package_config_searched = False
        self._package_config: Optional[PackageConfig] = None

    @property
    @abstractmethod
    def version(self) -> str: ...

    @cached_property
    def project(self) -> Optional[GitProject]:
        return self.get_project()

    def get_project(self) -> Optional[GitProject]:
        if not self.distgit_project_url:
            return None

        return ServiceConfig.get_service_config().get_project(
            url=self.distgit_project_url,
        )

    @property
    def base_project(self):
        return None

    def _add_release_and_event(self):
        if not self._db_project_object or not self._db_project_event:
            if not self.project_url:
                (
                    self._db_project_object,
                    self._db_project_event,
                ) = ProjectEventModel.add_anitya_version_event(
                    version=self.version,
                    project_name=self.anitya_project_name,
                    project_id=self.anitya_project_id,
                    package=self.package_name,
                )
                return

            if not (self.tag_name and self.repo_name and self.repo_namespace and self.project_url):
                logger.info(
                    "Not going to create the DB project event, not valid arguments.",
                )
                return

            (
                self._db_project_object,
                self._db_project_event,
            ) = ProjectEventModel.add_release_event(
                tag_name=self.tag_name,
                namespace=self.repo_namespace,
                repo_name=self.repo_name,
                project_url=self.project_url,
                commit_hash=None,
            )

    @property
    def db_project_object(self) -> Optional[ProjectReleaseModel]:
        if not self._db_project_object:
            self._add_release_and_event()
        return self._db_project_object

    @property
    def db_project_event(self) -> Optional[ProjectEventModel]:
        if not self._db_project_event:
            self._add_release_and_event()
        return self._db_project_event

    @property
    def packages_config(self):
        if not self._package_config_searched and not self._package_config:
            self._package_config = self.get_packages_config()
            self._package_config_searched = True
        return self._package_config

    def get_packages_config(self) -> Optional[PackageConfig]:
        logger.debug(f"Getting package_config:\n\tproject: {self.project}\n")

        return PackageConfigGetter.get_package_config_from_repo(
            base_project=None,
            project=self.project,
            pr_id=None,
            reference=None,
            fail_when_missing=False,
        )

    @property
    def project_url(self) -> Optional[str]:
        return self.packages_config.upstream_project_url if self.packages_config else None

    @cached_property
    def repo_url(self) -> Optional[RepoUrl]:
        return RepoUrl.parse(self.project_url)

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

        return self.packages_config.upstream_tag_template.format(
            version=self.version,
            upstream_package_name=self.packages_config.upstream_package_name,
        )

    def get_dict(self, default_dict: Optional[dict] = None) -> dict:
        d = self.__dict__
        d["project_url"] = self.project_url
        d["tag_name"] = self.tag_name
        d["repo_name"] = self.repo_name
        d["repo_namespace"] = self.repo_namespace
        d["version"] = self.version
        result = super().get_dict(d)
        result.pop("project")
        result.pop("repo_url")
        return result
