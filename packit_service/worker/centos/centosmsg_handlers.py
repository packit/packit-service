import logging
import shutil
from os import getenv
from pathlib import Path
from typing import Optional, Callable, List

from ogr import PagureService
from ogr.abstract import GitProject, CommitStatus

from packit.api import PackitAPI
from packit.config import JobConfig, PackageConfig, JobType
from packit.local_project import LocalProject
from packit_service.config import (
    ServiceConfig,
    GithubPackageConfigGetter,
    PagurePackageConfigGetter,
)
from packit_service.constants import PERMISSIONS_ERROR_WRITE_OR_ADMIN
from packit_service.sentry_integration import push_scope_to_sentry
from packit_service.service.events import (
    PullRequestCommentEvent,
    PullRequestEvent,
    PushGitHubEvent,
    TheJobTriggerType,
)
from packit_service.worker.build import CoprBuildJobHelper
from packit_service.worker.centos.events import (
    Event,
    PagurePullRequestCommentEvent,
)
from packit_service.worker.result import HandlerResults

logger = logging.getLogger(__name__)

PACKIT_COMMAND_SWITCH = "/packit"


class Handler:
    def __init__(self, config: ServiceConfig):
        self.config: ServiceConfig = config
        self.api: Optional[PackitAPI] = None
        self.local_project: Optional[LocalProject] = None

    def run(self) -> HandlerResults:
        raise NotImplementedError("This should have been implemented.")

    def get_tag_info(self) -> dict:
        tags = {"handler": getattr(self, "name", "generic-handler")}
        # repository info for easier filtering events that were grouped based on event type
        if self.local_project:
            tags.update(
                {
                    "repository": self.local_project.repo_name,
                    "namespace": self.local_project.namespace,
                }
            )
        return tags

    def run_n_clean(self) -> HandlerResults:
        try:
            with push_scope_to_sentry() as scope:
                for k, v in self.get_tag_info().items():
                    scope.set_tag(k, v)
                return self.run()
        finally:
            self.clean()

    def _clean_workplace(self):
        # clean only when we are in k8s for sure
        if not getenv("KUBERNETES_SERVICE_HOST"):
            logger.debug("this is not a kubernetes pod, won't clean")
            return
        logger.debug("removing contents of the PV")
        p = Path(self.config.command_handler_work_dir)

        # Do not clean dir if does not exist
        if not p.is_dir():
            logger.debug(
                f"Directory {self.config.command_handler_work_dir} does not exist."
            )
            return

        # remove everything in the volume, but not the volume dir
        dir_items = list(p.iterdir())
        if dir_items:
            logger.info("volume is not empty")
            logger.debug("content: %s" % [g.name for g in dir_items])
        for item in dir_items:
            # symlink pointing to a dir is also a dir and a symlink
            if item.is_symlink() or item.is_file():
                item.unlink()
            else:
                shutil.rmtree(item)

    def pre_check(self) -> bool:
        """
        Implement this method for those handlers, where you want to check if the properties are
        correct. If this method returns False during runtime, execution of service code is skipped.

        :return: False if we can skip the job execution.
        """
        return True

    def clean(self):
        """ clean up the mess once we're done """
        logger.info("cleaning up the mess")
        if self.api:
            self.api.clean()
        self._clean_workplace()


class JobHandler(Handler):
    """ Generic interface to handle different type of inputs """

    type: JobType
    triggers: List[TheJobTriggerType]

    def __init__(
        self, config: ServiceConfig, job_config: Optional[JobConfig], event: Event
    ):
        super().__init__(config)
        self.job_config: Optional[JobConfig] = job_config
        self.event = event
        self._clean_workplace()

    def run(self) -> HandlerResults:
        raise NotImplementedError("This should have been implemented.")


class AbstractPagureJobHandler(JobHandler, PagurePackageConfigGetter):
    pass


class AbstractPagureCoprBuildHandler(AbstractPagureJobHandler):
    def __init__(
        self, config: ServiceConfig, job_config: JobConfig, event, package_config,
    ):
        super().__init__(config=config, job_config=job_config, event=event)

        # if not isinstance(event, (PagurePullRequestEvent, PagurePushEvent)):
        #     raise PackitException(
        #         "Unknown event, only "
        #         "PullRequestEvent, ReleaseEvent, and PushGitHubEvent "
        #         "are accepted."
        #     )

        # lazy property
        self._copr_build_helper: Optional[CoprBuildJobHelper] = None
        self.package_config: Optional[PackageConfig] = package_config
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


class CommentHandler(JobHandler, GithubPackageConfigGetter):
    def __init__(self, config: ServiceConfig, event: PullRequestCommentEvent):
        super().__init__(config=config, event=event, job_config=None)
        self.config = config
        self.event: PagurePullRequestCommentEvent = event
        self.project: GitProject = event.get_project(
            get_project_kwargs=dict(
                service_mapping_update={"git.stg.centos.org": PagureService}
            )
        )
        # Get the latest pull request commit
        self.event.commit_sha = self.project.get_pr(self.event.pr_id).head_commit
        self.event.base_ref = self.event.commit_sha
        self.command2method_mapper = {"ping": self._cmd_ping}

    def run(self) -> HandlerResults:
        """

        :return:


        .. todo::
            check if commented by packit -> avoid unnecesary processing

        """

        parsed_command = self.event.comment.split(maxsplit=2)

        if parsed_command[0] != PACKIT_COMMAND_SWITCH:
            logger.debug("not packit command")
            return HandlerResults(success=True, details={"msg": "not packit command"})

        if not self._is_collaborator():
            logger.debug("not collaborator")
            return HandlerResults(
                success=True, details={"msg": PERMISSIONS_ERROR_WRITE_OR_ADMIN}
            )

        try:
            handler_result = self.command2method_mapper.get(
                parsed_command[1], self._unknown_cmd
            )()
        except IndexError:
            return HandlerResults(
                success=True, details={"msg": "packit command is missing"}
            )

        return handler_result

    def _is_collaborator(self, user=None):
        """
        method returns
        :return:
        """
        logger.debug(f'Checking if user: "{self.event.user_login}" is collaborator')
        collaborators = self.project.who_can_merge_pr()
        if self.event.user_login not in collaborators | self.config.admins:
            self.project.pr_comment(self.event.pr_id, PERMISSIONS_ERROR_WRITE_OR_ADMIN)
            return False
        return True

    def _cmd_ping(self) -> HandlerResults:
        """
        Method handling ping command
        :return:
        """
        logger.debug("processing ping command")
        self.project.get_pr(self.event.pr_id).comment("PONG!")
        return HandlerResults(True, {"msg": "pingpong ok"})

    def _unknown_cmd(self):
        """
        Method which is handling unknown command
        :return:
        """
        logger.debug("unknown command")
        return HandlerResults(True, {"msg": "command not found"})


class PagurePullRequestCoprBuildHandler(AbstractPagureCoprBuildHandler):
    def __init__(
        self, config: ServiceConfig, job_config: JobConfig, event: PullRequestEvent,
    ):
        super().__init__(config=config, job_config=job_config, event=event)

    def run(self) -> HandlerResults:
        collaborators = self.project.who_can_merge_pr()
        if self.event.user_login not in collaborators | self.config.admins:
            self.copr_build_helper.report_status_to_all(
                description=PERMISSIONS_ERROR_WRITE_OR_ADMIN,
                state=CommitStatus.failure,
            )
            return HandlerResults(
                success=True, details={"msg": PERMISSIONS_ERROR_WRITE_OR_ADMIN}
            )
        return super().run()

    def pre_check(self) -> bool:
        return (
            super().pre_check()
            and isinstance(self.event, PullRequestEvent)
            and self.event.trigger == TheJobTriggerType.pull_request
        )


class PushPagureCoprBuildHandler(AbstractPagureCoprBuildHandler):
    def __init__(
        self,
        config: ServiceConfig,
        job_config: JobConfig,
        event: PushGitHubEvent,
        package_config,
    ):
        super().__init__(
            config=config,
            job_config=job_config,
            event=event,
            package_config=package_config,
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
