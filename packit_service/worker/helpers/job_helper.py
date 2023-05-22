# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from functools import partial
from typing import Optional, Union, Dict, Callable

from lazy_object_proxy import Proxy
from ogr.abstract import GitProject
from ogr.exceptions import GitlabAPIException
from ogr.services.gitlab import GitlabProject
from packit.api import PackitAPI
from packit.config import JobConfig
from packit.config.package_config import PackageConfig
from packit.local_project import LocalProject
from packit.utils.repo import RepositoryCache
from packit_service.config import Deployment, ServiceConfig
from packit_service.models import PipelineModel, ProjectEventModel
from packit_service.worker.events import EventData
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.reporting import StatusReporter, BaseCommitStatus

logger = logging.getLogger(__name__)


class BaseJobHelper:
    def __init__(
        self,
        service_config: ServiceConfig,
        package_config: PackageConfig,
        project: GitProject,
        metadata: EventData,
        db_trigger,
        job_config: JobConfig,
        pushgateway: Optional[Pushgateway] = None,
    ):
        self.service_config: ServiceConfig = service_config
        self.job_config = job_config
        self.package_config = package_config
        self.project: GitProject = project
        self.db_trigger = db_trigger
        self.metadata: EventData = metadata
        self.run_model: Optional[PipelineModel] = None
        self.pushgateway = pushgateway

        # lazy properties
        self._api = None
        self._local_project = None
        self._status_reporter: Optional[StatusReporter] = None
        self._base_project: Optional[GitProject] = None
        self._pr_id: Optional[int] = None
        self._is_reporting_allowed: Optional[bool] = None
        self._is_gitlab_instance: Optional[bool] = None

    @property
    def msg_retrigger(self) -> str:
        return ""

    @property
    def local_project(self) -> LocalProject:
        if self._local_project is None:
            self._local_project = LocalProject(
                git_project=self.project,
                working_dir=self.service_config.command_handler_work_dir,
                ref=self.metadata.git_ref,
                pr_id=self.metadata.pr_id,
                cache=RepositoryCache(
                    cache_path=self.service_config.repository_cache,
                    add_new=self.service_config.add_repositories_to_repository_cache,
                )
                if self.service_config.repository_cache
                else None,
                merge_pr=self.package_config.merge_pr_in_ci,
            )
        return self._local_project

    @property
    def is_gitlab_instance(self) -> bool:
        if self._is_gitlab_instance is None:
            self._is_gitlab_instance = isinstance(self.project, GitlabProject)

        return self._is_gitlab_instance

    @property
    def pr_id(self) -> Optional[int]:
        if self._pr_id is None:
            self._pr_id = self.metadata.pr_id
        return self._pr_id

    @property
    def is_reporting_allowed(self) -> bool:
        username = self.project.service.user.get_username()
        if self._is_reporting_allowed is None:
            self._is_reporting_allowed = self.base_project.can_merge_pr(username)
        return self._is_reporting_allowed

    @property
    def base_project(self) -> GitProject:
        """
        Getting the source project info from PR,
        In case of build events we loose the source info.
        """
        if self._base_project is None:
            if self.pr_id:
                self._base_project = self.project.get_pr(
                    pr_id=self.pr_id
                ).source_project
            else:
                self._base_project = self.project
        return self._base_project

    def request_project_access(self) -> None:
        try:
            self.base_project.request_access()
        except GitlabAPIException:
            logger.info("Access already requested")

    @property
    def api(self) -> PackitAPI:
        if not self._api:
            self._api = PackitAPI(
                self.service_config,
                self.job_config,
                # so that the local_project is evaluated only if needed
                Proxy(partial(BaseJobHelper.local_project.__get__, self)),  # type: ignore
            )
        return self._api

    @property
    def api_url(self) -> str:
        return (
            "https://prod.packit.dev/api"
            if self.service_config.deployment == Deployment.prod
            else "https://stg.packit.dev/api"
        )

    @property
    def status_reporter(self) -> StatusReporter:
        if not self._status_reporter:
            trigger = ProjectEventModel.get_or_create(
                type=self.db_trigger.project_event_model_type,
                event_id=self.db_trigger.id,
            )
            self._status_reporter = StatusReporter.get_instance(
                project=self.project,
                commit_sha=self.metadata.commit_sha,
                packit_user=self.service_config.get_github_account_name(),
                event_id=trigger.id if trigger else None,
                pr_id=self.metadata.pr_id,
            )
        return self._status_reporter

    def _report(
        self,
        state: BaseCommitStatus,
        description: str,
        url: str = "",
        check_names: Union[str, list, None] = None,
        markdown_content: str = None,
        links_to_external_services: Optional[Dict[str, str]] = None,
        update_feedback_time: Callable = None,
    ) -> None:
        """
        The status reporting should be done through this method
        so we can extend it in subclasses easily.
        """
        if self.is_gitlab_instance and not self.is_reporting_allowed:
            description = (
                f"{description}\n\n---\nPackit-User does not have access to the "
                "source project (=usually author's fork of the project). "
                "(This is only about the representation of the results. "
                "Packit is still able to do its job without having the permissions.)\n\n"
                "*In case you wish to receive commit statuses instead of comments, please "
                "add login of the author of this comment to your fork with a role "
                "`Reporter`.*"
            )

            final_commit_states = (
                BaseCommitStatus.success,
                BaseCommitStatus.failure,
                BaseCommitStatus.error,
            )
            # We are only commenting final states to avoid multiple comments for a build
            # Ignoring all other states eg. pending, running
            if state not in final_commit_states:
                return

        self.status_reporter.report(
            description=description,
            state=state,
            url=url,
            check_names=check_names,
            markdown_content=markdown_content,
            links_to_external_services=links_to_external_services,
            update_feedback_time=update_feedback_time,
        )

    def report_status_to_configured_job(
        self,
        description: str,
        state: BaseCommitStatus,
        url: str = "",
        markdown_content: str = None,
        links_to_external_services: Optional[Dict[str, str]] = None,
        update_feedback_time: Callable = None,
    ):
        """
        Report status to the particular job from job_config attribute of the helper.
        """
        raise NotImplementedError()
