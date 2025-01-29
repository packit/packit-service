# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from typing import Callable, Optional

from ogr.abstract import GitProject
from packit.config import JobConfig, JobType, PackageConfig

from packit_service.config import ServiceConfig
from packit_service.events.event_data import EventData
from packit_service.models import ProjectEventModel
from packit_service.worker.helpers.sync_release.sync_release import SyncReleaseHelper
from packit_service.worker.reporting import BaseCommitStatus

logger = logging.getLogger(__name__)


class ProposeDownstreamJobHelper(SyncReleaseHelper):
    job_type = JobType.propose_downstream
    status_name: str = "propose-downstream"

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
        if not self._default_dg_branch:
            git_project = self.service_config.get_project(
                url=self.package_config.dist_git_package_url,
            )
            self._default_dg_branch = git_project.default_branch
        return self._default_dg_branch

    def report_status_for_branch(
        self,
        branch: str,
        description: str,
        state: BaseCommitStatus,
        url: str = "",
        markdown_content: Optional[str] = None,
    ):
        if self.job and branch in self.branches:
            cs = self.get_check(branch)
            self._report(
                description=description,
                state=state,
                url=url,
                check_names=cs,
                markdown_content=markdown_content,
            )

    @classmethod
    def get_check_cls(
        cls,
        branch: Optional[str] = None,
        project_event_identifier: Optional[str] = None,
        identifier: Optional[str] = None,
    ) -> str:
        """
        Get name of the commit status for propose-downstream job for the given branch
        and identifier.
        """
        branch_str = f":{branch}" if branch else ""
        trigger_str = f":{project_event_identifier}" if project_event_identifier else ""
        optional_suffix = f":{identifier}" if identifier else ""
        return f"{cls.status_name}{trigger_str}{branch_str}{optional_suffix}"

    def get_check(self, branch: Optional[str] = None) -> str:
        return self.get_check_cls(branch, identifier=self.job_config.identifier)

    @property
    def msg_retrigger(self) -> str:
        return ""

    @property
    def check_names(self) -> list[str]:
        """
        List of full names of the commit statuses for propose-downstream job.

        e.g. ["propose-downstream:f34", "propose-downstream:f35"]
        """
        if not self._check_names:
            self._check_names = [self.get_check(branch) for branch in self.branches]
        return self._check_names

    def report_status_to_all(
        self,
        description: str,
        state: BaseCommitStatus,
        url: str = "",
        markdown_content: Optional[str] = None,
        links_to_external_services: Optional[dict[str, str]] = None,
        update_feedback_time: Optional[Callable] = None,
    ) -> None:
        if self.job_type:
            self._report(
                description=description,
                state=state,
                url=url,
                check_names=self.check_names,
                markdown_content=markdown_content,
                links_to_external_services=links_to_external_services,
                update_feedback_time=update_feedback_time,
            )

    def report_status_to_configured_job(
        self,
        description: str,
        state: BaseCommitStatus,
        url: str = "",
        markdown_content: Optional[str] = None,
        links_to_external_services: Optional[dict[str, str]] = None,
        update_feedback_time: Optional[Callable] = None,
    ):
        self.report_status_to_all(
            description=description,
            state=state,
            url=url,
            markdown_content=markdown_content,
            links_to_external_services=links_to_external_services,
            update_feedback_time=update_feedback_time,
        )
