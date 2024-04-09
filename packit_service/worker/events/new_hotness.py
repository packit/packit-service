# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from abc import abstractmethod
from functools import cached_property
from logging import getLogger
from typing import Optional, Dict

from ogr.abstract import GitProject
from ogr.parsing import RepoUrl

from packit.config import PackageConfig, JobConfigTriggerType
from packit_service.config import ServiceConfig, PackageConfigGetter
from packit_service.models import ProjectReleaseModel, ProjectEventModel
from packit_service.worker.events import Event
from packit_service.worker.events.event import use_for_job_config_trigger

logger = getLogger(__name__)


class AnityaUpdateEvent(Event):
    def __init__(
        self,
        package_name: str,
        distgit_project_url: str,
        release_monitoring_project_id: int,
    ):
        super().__init__()

        self.package_name = package_name
        self.distgit_project_url = distgit_project_url
        self.release_monitoring_project_id = release_monitoring_project_id

        self._repo_url: Optional[RepoUrl] = None
        self._db_project_object: Optional[ProjectReleaseModel] = None
        self._db_project_event: Optional[ProjectEventModel] = None

        self._package_config_searched = False
        self._package_config: Optional[PackageConfig] = None

    @property
    @abstractmethod
    def version(self) -> str:
        ...

    @cached_property
    def project(self) -> Optional[GitProject]:
        return self.get_project()

    def get_project(self) -> Optional[GitProject]:
        if not self.distgit_project_url:
            return None

        return ServiceConfig.get_service_config().get_project(
            url=self.distgit_project_url
        )

    @property
    def base_project(self):
        return None

    def _add_release_and_event(self):
        if not self._db_project_object or not self._db_project_event:
            if not (
                self.tag_name
                and self.repo_name
                and self.repo_namespace
                and self.project_url
            ):
                logger.info(
                    "Not going to create the DB project event, not valid arguments."
                )
                return None

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

        return self.packages_config.upstream_tag_template.format(version=self.version)

    def get_dict(
        self, default_dict: Optional[Dict] = None, store_event: bool = False
    ) -> dict:
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


# the decorator is needed in case the DB project event is not created (not valid arguments)
# but we still want to report from pre_check of the PullFromUpstreamHandler
@use_for_job_config_trigger(trigger_type=JobConfigTriggerType.release)
class NewHotnessUpdateEvent(AnityaUpdateEvent):
    def __init__(
        self,
        package_name: str,
        version: str,
        distgit_project_url: str,
        bug_id: int,
        release_monitoring_project_id: int,
    ):
        super().__init__(
            package_name=package_name,
            distgit_project_url=distgit_project_url,
            release_monitoring_project_id=release_monitoring_project_id,
        )
        self._version = version
        self.bug_id = bug_id

    @property
    def version(self) -> str:
        return self._version


# TODO: Uncomment once it is possible to deduce the version for the sync-release
# action.
# @use_for_job_config_trigger(trigger_type=JobConfigTriggerType.release)
class AnityaVersionUpdateEvent(AnityaUpdateEvent):
    def __init__(
        self,
        package_name: str,
        versions: list[str],
        distgit_project_url: str,
        release_monitoring_project_id: int,
    ):
        super().__init__(
            package_name=package_name,
            distgit_project_url=distgit_project_url,
            release_monitoring_project_id=release_monitoring_project_id,
        )

        self._versions = versions

    @property
    def version(self) -> str:
        # TODO: Handle here or further down the chain? we should be able to get
        # the package config from dist-git and resolve the next version; how are
        # we going to choose the version?
        #   a) latest greatest
        #   b) next unreleased?
        # this can be also influenced by the mask…
        #
        # It would be ideal to handle here because of the serialization…
        raise NotImplementedError()
