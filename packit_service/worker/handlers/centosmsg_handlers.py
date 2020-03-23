import logging

from ogr import PagureService
from ogr.abstract import GitProject

from packit_service.config import ServiceConfig, GithubPackageConfigGetter
from packit_service.constants import PERMISSIONS_ERROR_WRITE_OR_ADMIN
from packit_service.service.events import PullRequestCommentEvent
from packit_service.worker.handlers import JobHandler
from packit_service.worker.result import HandlerResults

logger = logging.getLogger(__name__)

PACKIT_COMMAND_SWITCH = "/packit"


class CommentHandler(JobHandler, GithubPackageConfigGetter):
    def __init__(self, config: ServiceConfig, event: PullRequestCommentEvent):
        super().__init__(config=config, event=event, job_config=None)
        self.config = config
        self.event: PullRequestCommentEvent = event
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
