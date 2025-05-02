# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from logging import getLogger
from typing import Optional, Union

from ogr.abstract import GitProject, PullRequest
from packit.config import PackageConfig

from packit_service.config import ServiceConfig
from packit_service.models import (
    AbstractProjectObjectDbType,
    CoprBuildTargetModel,
    ProjectEventModel,
    TFTTestRunTargetModel,
    filter_most_recent_target_names_by_status,
)
from packit_service.package_config_getter import PackageConfigGetter

from ..event import Event

logger = getLogger(__name__)


class ForgeIndependent(Event):
    commit_sha: Optional[str]
    project_url: str

    def __init__(
        self,
        created_at: Optional[Union[int, float, str]] = None,
        project_url=None,
        pr_id: Optional[int] = None,
        actor: Optional[str] = None,
    ):
        super().__init__(created_at)
        self.project_url = project_url
        self._pr_id = pr_id
        self.fail_when_config_file_missing = False
        self.actor = actor
        self._pull_request_object = None

    @property
    def project(self):
        if not self._project:
            self._project = self.get_project()
        return self._project

    @property
    def base_project(self):
        if not self._base_project:
            self._base_project = self.get_base_project()
        return self._base_project

    @property
    def packages_config(self):
        if not self._package_config_searched and not self._package_config:
            self._package_config = self.get_packages_config()
            self._package_config_searched = True
        return self._package_config

    def get_db_project_object(self) -> Optional[AbstractProjectObjectDbType]:
        raise NotImplementedError()

    def get_db_project_event(self) -> Optional[ProjectEventModel]:
        raise NotImplementedError()

    @property
    def pr_id(self) -> Optional[int]:
        return self._pr_id

    @property
    def pull_request_object(self) -> Optional[PullRequest]:
        if not self._pull_request_object and self.pr_id:
            self._pull_request_object = self.project.get_pr(self.pr_id)
        return self._pull_request_object

    def get_project(self) -> Optional[GitProject]:
        if not (self.project_url or self.db_project_object):
            return None

        return ServiceConfig.get_service_config().get_project(
            url=self.project_url or self.db_project_object.project.project_url,
        )

    def get_base_project(self) -> Optional[GitProject]:
        """Reimplement in the PR events."""
        return None

    def get_packages_config(self) -> Optional[PackageConfig]:
        logger.debug(
            f"Getting packages_config:\n"
            f"\tproject: {self.project}\n"
            f"\tbase_project: {self.base_project}\n"
            f"\treference: {self.commit_sha}\n"
            f"\tpr_id: {self.pr_id}",
        )

        return PackageConfigGetter.get_package_config_from_repo(
            base_project=self.base_project,
            project=self.project,
            reference=self.commit_sha,
            pr_id=self.pr_id,
            fail_when_missing=self.fail_when_config_file_missing,
        )

    def get_all_tf_targets_by_status(
        self,
        statuses_to_filter_with: list[str],
    ) -> Optional[set[tuple[str, str]]]:
        if self.commit_sha is None:
            return None

        logger.debug(
            f"Getting Testing Farm targets for commit sha {self.commit_sha} "
            f"and statuses {statuses_to_filter_with}",
        )
        found_targets = filter_most_recent_target_names_by_status(
            models=TFTTestRunTargetModel.get_all_by_commit_target(
                commit_sha=self.commit_sha,
            ),
            statuses_to_filter_with=statuses_to_filter_with,
        )
        logger.debug(
            f"Testing Farm found targets {found_targets}",
        )
        return found_targets

    def get_all_build_targets_by_status(
        self,
        statuses_to_filter_with: list[str],
    ) -> Optional[set[tuple[str, str]]]:
        if self.commit_sha is None or self.project.repo is None:
            return None

        logger.debug(
            f"Getting COPR build targets for commit sha {self.commit_sha} "
            f"and statuses {statuses_to_filter_with}",
        )
        found_targets = filter_most_recent_target_names_by_status(
            models=CoprBuildTargetModel.get_all_by_commit(commit_sha=self.commit_sha),
            statuses_to_filter_with=statuses_to_filter_with,
        )
        logger.debug(
            f"Builds found targets {found_targets}",
        )
        return found_targets

    def get_non_serializable_attributes(self):
        return [
            *super().get_non_serializable_attributes(),
            "fail_when_config_file_missing",
            "_pull_request_object",
        ]


class Result(ForgeIndependent):
    """
    This class is used only as an Abstract for result events to
    allow Steve properly filter jobs with manual trigger.
    """

    def get_packages_config(self) -> Optional[PackageConfig]:
        if self.db_project_event and (db_config := self.db_project_event.packages_config):
            logger.debug("Getting packages config from DB.")
            return PackageConfig.get_from_dict_without_setting_defaults(db_config)
        return super().get_packages_config()
