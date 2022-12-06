# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from typing import Optional, List, Set

from ogr.abstract import GitProject

from packit.config import JobType, PackageConfig, JobConfig
from packit.config.aliases import get_branches
from packit_service.config import ServiceConfig
from packit_service.models import AbstractTriggerDbType
from packit_service.trigger_mapping import are_job_types_same
from packit_service.worker.events import EventData
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
        db_trigger: AbstractTriggerDbType,
        job_config: JobConfig,
        branches_override: Optional[Set[str]] = None,
    ):
        super().__init__(
            service_config=service_config,
            package_config=package_config,
            project=project,
            metadata=metadata,
            db_trigger=db_trigger,
            job_config=job_config,
        )
        self.branches_override = branches_override
        self._check_names: Optional[List[str]] = None
        self._default_dg_branch: Optional[str] = None
        self._job: Optional[JobConfig] = None

    @property
    def default_dg_branch(self) -> str:
        """
        Get the default branch of the distgit project.
        """
        raise NotImplementedError("Use subclass.")

    @property
    def branches(self) -> Set[str]:
        """
        Return all valid branches from config.
        """
        branches = get_branches(
            *self.job.dist_git_branches, default=self.default_dg_branch
        )
        if self.branches_override:
            logger.debug(f"Branches override: {self.branches_override}")
            branches = branches & self.branches_override

        return branches

    @property
    def job(self) -> Optional[JobConfig]:
        """
        Check if there is JobConfig for propose downstream defined
        :return: JobConfig or None
        """
        if not self._job:
            for job in [self.job_config] + self.package_config.jobs:
                if are_job_types_same(job.type, self.job_type) and (
                    self.db_trigger
                    and self.db_trigger.job_config_trigger_type == job.trigger
                ):
                    self._job = job
                    break
        return self._job

    def report_status_for_branch(self, branch, description, state, url):
        raise NotImplementedError("Use subclass")
