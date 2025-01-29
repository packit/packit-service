# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from functools import partial
from typing import Callable, Optional, Union

from lazy_object_proxy import Proxy
from ogr.abstract import GitProject, PullRequest
from ogr.exceptions import GitlabAPIException
from ogr.services.gitlab import GitlabProject
from packit.api import PackitAPI
from packit.config import JobConfig
from packit.config.package_config import PackageConfig
from packit.local_project import (
    CALCULATE,
    NOT_TO_CALCULATE,
    LocalProject,
    LocalProjectBuilder,
)
from packit.utils.repo import RepositoryCache

from packit_service.config import Deployment, ServiceConfig
from packit_service.events.event_data import EventData
from packit_service.models import (
    AbstractProjectObjectDbType,
    PipelineModel,
    ProjectEventModel,
)
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.reporting import BaseCommitStatus, StatusReporter

logger = logging.getLogger(__name__)


class BaseJobHelper:
    require_git_repo_in_local_project: bool = False

    def __init__(
        self,
        service_config: ServiceConfig,
        package_config: PackageConfig,
        project: GitProject,
        metadata: EventData,
        db_project_event: ProjectEventModel,
        job_config: JobConfig,
        pushgateway: Optional[Pushgateway] = None,
    ):
        self.service_config: ServiceConfig = service_config
        self.job_config = job_config
        self.package_config = package_config
        self.project: GitProject = project
        self.metadata: EventData = metadata
        self.run_model: Optional[PipelineModel] = None
        self.pushgateway = pushgateway
        self.db_project_event = db_project_event
        self._db_project_object: AbstractProjectObjectDbType = (
            db_project_event.get_project_event_object() if db_project_event else None
        )

        # lazy properties
        self._api = None
        self._local_project = None
        self._status_reporter: Optional[StatusReporter] = None
        self._base_project: Optional[GitProject] = None
        self._pr_id: Optional[int] = None
        self._is_reporting_allowed: Optional[bool] = None
        self._is_gitlab_instance: Optional[bool] = None
        self._pull_request_object: Optional[PullRequest] = None

    def get_package_name(self) -> Optional[str]:
        """If the package_config is just for one package,
        returns the package name. Otherwise None.
        Helpers should always have PackageConfigView(s)
        references which hold just a single package.
        """
        if len(self.package_config.packages) != 1:
            return None

        return next(iter(self.package_config.packages.keys()))

    @property
    def msg_retrigger(self) -> str:
        return ""

    @property
    def local_project(self) -> LocalProject:
        if self._local_project is None:
            builder = LocalProjectBuilder(
                cache=(
                    RepositoryCache(
                        cache_path=self.service_config.repository_cache,
                        add_new=self.service_config.add_repositories_to_repository_cache,
                    )
                    if self.service_config.repository_cache
                    else None
                ),
            )
            self._local_project = builder.build(
                git_project=self.project,
                working_dir=self.service_config.command_handler_work_dir,
                ref=self.metadata.git_ref,
                pr_id=self.metadata.pr_id,
                merge_pr=self.package_config.merge_pr_in_ci,
                git_url=CALCULATE,
                repo_name=CALCULATE,
                full_name=CALCULATE,
                namespace=CALCULATE,
                git_repo=(
                    CALCULATE if self.require_git_repo_in_local_project else NOT_TO_CALCULATE
                ),
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
    def pull_request_object(self) -> Optional[PullRequest]:
        if not self._pull_request_object and self.pr_id:
            self._pull_request_object = self.project.get_pr(self.pr_id)
        return self._pull_request_object

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
                self._base_project = self.pull_request_object.source_project
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
            self._status_reporter = StatusReporter.get_instance(
                project=self.project,
                commit_sha=self.metadata.commit_sha,
                packit_user=self.service_config.get_github_account_name(),
                project_event_id=(self.db_project_event.id if self.db_project_event else None),
                pr_id=self.metadata.pr_id,
            )
        return self._status_reporter

    def _report(
        self,
        state: BaseCommitStatus,
        description: str,
        url: str = "",
        check_names: Union[str, list, None] = None,
        markdown_content: Optional[str] = None,
        links_to_external_services: Optional[dict[str, str]] = None,
        update_feedback_time: Optional[Callable] = None,
    ) -> None:
        """
        The status reporting should be done through this method
        so we can extend it in subclasses easily.
        """
        if self.is_gitlab_instance and not self.is_reporting_allowed:
            login = self.project.service.user.get_username()
            description = (
                f"{description}\n\n---\nPackit does not have access to the "
                "source project (=usually author's fork of the project). "
                "(This is only about the representation of the results. "
                "Packit is still able to do its job without having the permissions.)\n\n"
                "*In case you wish to receive commit statuses instead of comments, please "
                f"add {login} to your fork with a role `Developer`.*"
                " For more details see our [guide](https://packit.dev/docs/guide#gitlab)."
            )

            # We are only commenting final states to avoid multiple comments for a build
            # Ignoring all other states eg. pending, running
            if not StatusReporter.is_final_state(state):
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
        markdown_content: Optional[str] = None,
        links_to_external_services: Optional[dict[str, str]] = None,
        update_feedback_time: Optional[Callable] = None,
    ):
        """
        Report status to the particular job from job_config attribute of the helper.
        """
        raise NotImplementedError()
