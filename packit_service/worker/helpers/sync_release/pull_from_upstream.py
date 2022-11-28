# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from typing import Optional, Set

from ogr.abstract import GitProject

from packit.config import JobType, PackageConfig, JobConfig
from packit_service.config import ServiceConfig
from packit_service.models import AbstractTriggerDbType
from packit_service.worker.events import EventData
from packit_service.worker.helpers.sync_release.sync_release import SyncReleaseHelper

logger = logging.getLogger(__name__)


class PullFromUpstreamHelper(SyncReleaseHelper):
    job_type = JobType.pull_from_upstream

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
            branches_override=branches_override,
        )

    @property
    def default_dg_branch(self) -> str:
        """
        Get the default branch of the distgit project.
        """
        if not self._default_dg_branch:
            git_project = self.service_config.get_project(
                url=self.metadata.event_dict.get("distgit_project_url")
            )
            self._default_dg_branch = git_project.default_branch
        return self._default_dg_branch
