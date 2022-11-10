# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
from logging import getLogger
from typing import Optional

from ogr.parsing import RepoUrl

from packit_service.models import ProjectReleaseModel, AbstractTriggerDbType
from packit_service.worker.events import AbstractForgeIndependentEvent

logger = getLogger(__name__)


class NewHotnessUpdateEvent(AbstractForgeIndependentEvent):
    def __init__(
        self,
        package_name: str,
        version: str,
        distgit_project_url: str,
    ):
        super().__init__()
        self.package_name = package_name
        self.version = version
        self.project_url = distgit_project_url

        self._repo_url: Optional[RepoUrl] = None

    @property
    def upstream_project_url(self) -> str:
        return self.package_config.upstream_project_url

    @property
    def repo_url(self) -> Optional[RepoUrl]:
        if not self._repo_url:
            self._repo_url = RepoUrl.parse(self.upstream_project_url)
        return self._repo_url

    @property
    def upstream_repo_namespace(self) -> Optional[str]:
        return self.repo_url.namespace if self.repo_url else None

    @property
    def upstream_repo_name(self) -> Optional[str]:
        return self.repo_url.repo if self.repo_url else None

    @property
    def tag_name(self):
        if not self.package_config.upstream_tag_template:
            return self.version

        return self.package_config.upstream_tag_template.format(version=self.version)

    @property
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        return ProjectReleaseModel.get_or_create(
            tag_name=self.tag_name,
            namespace=self.upstream_repo_namespace,
            repo_name=self.upstream_repo_name,
            project_url=self.upstream_project_url,
            # TODO do we need to have the upstream commit hash?
            commit_hash=None,
        )

    @property
    def commit_sha(self):
        # this is used for getting the package configuration, by default the method
        # gets it from the default branch of the repository which is what we want
        return None

    def pre_check(self):
        """
        Check that package config (with upstream_project_url set) is present
        and that we were able to parse repo namespace, name and the tag name.
        """
        if not self.package_config:
            logger.warning(
                "Package config not present in the dist-git repository with version update."
            )
            return False

        if not self.package_config.upstream_project_url:
            logger.warning("upstream_project_url not set in the package config.")
            return False

        if not (self.upstream_repo_name and self.upstream_repo_namespace):
            logger.warning(
                "Not able to parse repo name/repo namespace from the "
                "upstream_project_url defined in the config."
            )
            return False

        if not self.tag_name:
            logger.warning("Not able to get the upstream tag name.")
            return False

        return True
