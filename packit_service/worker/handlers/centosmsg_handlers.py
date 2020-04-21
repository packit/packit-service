import logging
from typing import Optional, Callable, Union

from ogr.abstract import GitProject

from packit.config import JobConfig, PackageConfig, JobType
from packit_service.config import (
    ServiceConfig,
    PagurePackageConfigGetter,
)
from packit_service.service.events import (
    PushGitHubEvent,
    TheJobTriggerType,
    PullRequestPagureEvent,
    PushPagureEvent,
    PullRequestCommentPagureEvent,
)
from packit_service.worker.build import CoprBuildJobHelper
from packit_service.worker.handlers.abstract import use_for, JobHandler
from packit_service.worker.handlers.comment_action_handler import (
    CommentAction,
    CommentActionHandler,
)
from packit_service.worker.result import HandlerResults

logger = logging.getLogger(__name__)


class AbstractPagureJobHandler(JobHandler, PagurePackageConfigGetter):
    pass


class AbstractPagureCoprBuildHandler(AbstractPagureJobHandler):
    event: Union[PullRequestPagureEvent]

    def __init__(
        self,
        config: ServiceConfig,
        job_config: JobConfig,
        event: Union[PullRequestPagureEvent, PushPagureEvent],
    ):
        super().__init__(config=config, job_config=job_config, event=event)

        # lazy property
        self._copr_build_helper: Optional[CoprBuildJobHelper] = None
        self._package_config: Optional[PackageConfig] = None
        self._project: Optional[GitProject] = None

    @property
    def copr_build_helper(self) -> CoprBuildJobHelper:
        if not self._copr_build_helper:
            self._copr_build_helper = CoprBuildJobHelper(
                config=self.config,
                package_config=self.package_config,
                project=self.project,
                event=self.event,
                job=self.job_config,
            )
        return self._copr_build_helper

    @property
    def project(self) -> GitProject:
        if not self._project:
            self._project = self.event.get_project()
        return self._project

    @property
    def package_config(self) -> PackageConfig:
        if not self._package_config:
            self._package_config = self.event.get_package_config()
            self._package_config.upstream_project_url = self.event.project_url
        return self._package_config

    def run(self) -> HandlerResults:
        return self.copr_build_helper.run_copr_build()

    def pre_check(self) -> bool:
        is_copr_build: Callable[
            [JobConfig], bool
        ] = lambda job: job.type == JobType.copr_build

        if self.job_config.type == JobType.tests and any(
            filter(is_copr_build, self.package_config.jobs)
        ):
            logger.info(
                "Skipping build for testing. The COPR build is defined in the config."
            )
            return False
        return True


@use_for(job_type=JobType.copr_build)
@use_for(job_type=JobType.build)
class PagurePullRequestCoprBuildHandler(AbstractPagureCoprBuildHandler):
    triggers = [
        TheJobTriggerType.pull_request,
    ]
    event: PullRequestPagureEvent

    def __init__(
        self,
        config: ServiceConfig,
        job_config: JobConfig,
        event: PullRequestPagureEvent,
    ):
        super().__init__(config=config, job_config=job_config, event=event)

    def pre_check(self) -> bool:
        return (
            super().pre_check()
            and isinstance(self.event, PullRequestPagureEvent)
            and self.event.trigger == TheJobTriggerType.pull_request
        )


@use_for(job_type=JobType.copr_build)
@use_for(job_type=JobType.build)
class PushPagureCoprBuildHandler(AbstractPagureCoprBuildHandler):
    triggers = [
        TheJobTriggerType.push,
    ]

    def __init__(
        self, config: ServiceConfig, job_config: JobConfig, event: PushPagureEvent,
    ):
        super().__init__(
            config=config, job_config=job_config, event=event,
        )
        self.base_ref = event.commit_sha

    def pre_check(self) -> bool:
        valid = (
            super().pre_check()
            and isinstance(self.event, PushGitHubEvent)
            and self.event.trigger == TheJobTriggerType.push
        )
        if not valid:
            return False

        configured_branch = self.copr_build_helper.job_build.metadata.get(
            "branch", "master"
        )
        if configured_branch != self.event.git_ref:
            logger.info(
                f"Skipping build on {self.event.git_ref}'. "
                f"Push configured only for ('{configured_branch}')."
            )
            return False
        return True


class PagurePullRequestCommentCoprBuildHandler(
    CommentActionHandler, PagurePackageConfigGetter
):
    """ Handler for PR comment `/packit copr-build` """

    type = CommentAction.copr_build
    triggers = [TheJobTriggerType.pr_comment]
    event: PullRequestCommentPagureEvent

    def __init__(self, config: ServiceConfig, event: PullRequestCommentPagureEvent):
        super().__init__(config=config, event=event)
        self.config = config
        self.event = event
        self.project: GitProject = event.get_project()
        self.package_config: PackageConfig = self.get_package_config_from_repo(
            self.project, self.event.commit_sha, self.event.pr_id
        )
        self.package_config.upstream_project_url = event.project_url

    def run(self) -> HandlerResults:

        cbh = CoprBuildJobHelper(
            self.config, self.package_config, self.project, self.event
        )
        handler_results = cbh.run_copr_build()

        return handler_results
