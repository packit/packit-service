# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from typing import Optional

from ogr.abstract import GitProject
from packit.config import (
    JobConfig,
    JobConfigTriggerType,
    JobType,
    PackageConfig,
    aliases,
)

from packit_service.config import ServiceConfig
from packit_service.events.event_data import EventData
from packit_service.models import ProjectEventModel
from packit_service.worker.helpers.job_helper import BaseJobHelper

logger = logging.getLogger(__name__)


class SyncReleaseHelper(BaseJobHelper):
    job_type: JobType
    status_name: str

    def __init__(
        self,
        service_config: ServiceConfig,
        package_config: PackageConfig,
        project: GitProject,
        metadata: EventData,
        db_project_event: ProjectEventModel,
        job_config: JobConfig,
        branches_override: Optional[set[str]] = None,
    ):
        super().__init__(
            service_config=service_config,
            package_config=package_config,
            project=project,
            metadata=metadata,
            db_project_event=db_project_event,
            job_config=job_config,
        )
        self.branches_override = branches_override
        self._check_names: Optional[list[str]] = None
        self._default_dg_branch: Optional[str] = None
        self._job: Optional[JobConfig] = None

    @property
    def default_dg_branch(self) -> str:
        """
        Get the default branch of the distgit project.
        """
        raise NotImplementedError("Use subclass.")

    def _filter_override_branches(self, branches: set[str]) -> set[str]:
        """
        If a branch has been overriden filter it out.
        If we re-run the job in a subset of branches
        the overriden branches are those where the job
        has not to be run again.
        """
        if self.branches_override:
            logger.debug(f"Branches override: {self.branches_override}")
            branches = branches & self.branches_override
        return branches

    @property
    def branches(self) -> set[str]:
        """
        Return all valid branches from config.
        """
        branches = aliases.get_branches(
            *self.job.dist_git_branches,
            default_dg_branch=self.default_dg_branch,
            default=self.default_dg_branch,
        )
        return self._filter_override_branches(branches)

    def get_fast_forward_merge_branches_for(self, source_branch: str) -> set[str]:
        """
        Returns a list of branches that can be fast forwarded merging
        the specified source_branch. They are listed in the config.

        source_branch: source branch
        """
        return aliases.get_fast_forward_merge_branches_for(
            self.job.dist_git_branches,
            source_branch,
            default=self.default_dg_branch,
            default_dg_branch=self.default_dg_branch,
        )

    @property
    def job(self) -> Optional[JobConfig]:
        """
        Check if there is JobConfig for propose downstream defined
        :return: JobConfig or None
        """
        if not self._job:
            for job in [self.job_config, *self.package_config.jobs]:
                if job.type == self.job_type and (
                    self._db_project_object
                    and (
                        self._db_project_object.job_config_trigger_type == job.trigger
                        # pull-from-upstream can be retriggered by a dist-git PR comment,
                        # in which case the trigger types don't match
                        or (
                            job.type == JobType.pull_from_upstream
                            and self._db_project_object.job_config_trigger_type
                            == JobConfigTriggerType.pull_request
                            and job.trigger == JobConfigTriggerType.release
                        )
                    )
                ):
                    self._job = job
                    break
        return self._job

    def report_status_for_branch(self, branch, description, state, url):
        raise NotImplementedError("Use subclass")

    def report_status_to_all(
        self,
        description,
        state,
        url="",
        markdown_content=None,
    ):
        raise NotImplementedError("Use subclass")
