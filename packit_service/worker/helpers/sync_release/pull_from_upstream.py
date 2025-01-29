# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from typing import Optional

from ogr.abstract import GitProject
from packit.config import JobConfig, JobType, PackageConfig

from packit_service.config import ServiceConfig
from packit_service.events import anitya, pagure
from packit_service.events.event_data import EventData
from packit_service.models import ProjectEventModel
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
            branches_override=branches_override,
        )

    @property
    def default_dg_branch(self) -> str:
        """
        Get the default branch of the distgit project.
        """
        if not self._default_dg_branch:
            if self.metadata.event_type in (anitya.NewHotness.event_type(),):
                distgit_project_url = self.metadata.event_dict.get(
                    "distgit_project_url",
                )
            elif self.metadata.event_type in (pagure.pr.Comment.event_type(),):
                distgit_project_url = self.metadata.event_dict.get("project_url")
            git_project = self.service_config.get_project(url=distgit_project_url)
            self._default_dg_branch = git_project.default_branch
        return self._default_dg_branch

    def report_status_for_branch(self, branch, description, state, url):
        pass

    def report_status_to_all(self, description, state, url="", markdown_content=None):
        pass
