# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
This file defines classes for job handlers related to Bodhi
"""
import logging
import abc
from typing import Tuple, Type

from celery import Task

from packit.config import JobConfig, JobType, PackageConfig
from packit.exceptions import PackitException
from packit_service.constants import (
    MSG_RETRIGGER,
    MSG_GET_IN_TOUCH,
    MSG_DOWNSTREAM_JOB_ERROR_HEADER,
)
from packit_service.worker.checker.abstract import Checker
from packit_service.worker.checker.bodhi import (
    IsAuthorAPackager,
    HasIssueCommenterRetriggeringPermissions,
    IsKojiBuildCompleteAndBranchConfiguredCheckEvent,
    IsKojiBuildCompleteAndBranchConfiguredCheckService,
)
from packit_service.worker.events import (
    PullRequestCommentPagureEvent,
    IssueCommentEvent,
    IssueCommentGitlabEvent,
)
from packit_service.worker.events.koji import KojiBuildEvent
from packit_service.worker.handlers.abstract import (
    TaskName,
    configured_as,
    reacts_to,
    RetriableJobHandler,
    run_for_comment,
)
from packit_service.worker.handlers.mixin import (
    GetKojiBuildDataFromKojiServiceMultipleBranches,
    GetKojiBuildDataFromKojiBuildEventMixin,
    GetKojiBuildDataFromKojiServiceMixin,
    GetKojiBuildEventMixin,
    GetKojiBuildData,
    KojiBuildData,
)
from packit_service.worker.mixin import (
    ConfigFromDistGitUrlMixin,
    GetBranchesFromIssueMixin,
    PackitAPIWithDownstreamMixin,
)
from packit_service.worker.reporting import report_in_issue_repository
from packit_service.worker.result import TaskResults

logger = logging.getLogger(__name__)


class BodhiUpdateHandler(
    RetriableJobHandler, PackitAPIWithDownstreamMixin, GetKojiBuildData
):
    topic = "org.fedoraproject.prod.buildsys.build.state.change"

    def __init__(
        self,
        package_config: PackageConfig,
        job_config: JobConfig,
        event: dict,
        celery_task: Task,
    ):
        super().__init__(
            package_config=package_config,
            job_config=job_config,
            event=event,
            celery_task=celery_task,
        )

    def run(self) -> TaskResults:
        try:
            koji_build_data = None
            for koji_build_data in self:
                logger.debug(
                    f"Create update for dist-git branch: {koji_build_data.dist_git_branch} "
                    f"and nvr: {koji_build_data.nvr}."
                )
                self.packit_api.create_update(
                    dist_git_branch=koji_build_data.dist_git_branch,
                    update_type="enhancement",
                    koji_builds=[koji_build_data.nvr],  # it accepts NVRs, not build IDs
                )
        except PackitException as ex:
            logger.debug(f"Bodhi update failed to be created: {ex}")

            body = f"``` {ex} ```"
            # Notify user just on the last run.
            notify = self.celery_task.is_last_try()

            if notify:
                self.report_in_issue_repository(
                    koji_build_data=koji_build_data, error=body
                )
            else:
                logger.debug("User will not be notified about the failure.")

            # This will cause `autoretry_for` mechanism to re-trigger the celery task.
            raise ex

        # `success=True` for all known errors
        # (=The task was correctly processed.)
        # Sentry issue will be created otherwise.
        return TaskResults(success=True, details={})

    @abc.abstractmethod
    def get_trigger_type_description(self, koji_build_data: KojiBuildData) -> str:
        """Describe the user's action which triggered the Bodhi update

        Args:
            koji_build_data: koji build data associated with the
            retriggered Bodhi update
        """

    def report_in_issue_repository(
        self, koji_build_data: KojiBuildData, error: str
    ) -> None:
        body = MSG_DOWNSTREAM_JOB_ERROR_HEADER.format(
            object="Bodhi update", dist_git_url=self.packit_api.dg.local_project.git_url
        )
        if koji_build_data:
            body += f"| `{koji_build_data.dist_git_branch}` | {error} |\n"
        else:
            body += f"| | {error} |\n"

        msg_retrigger = MSG_RETRIGGER.format(
            job="update",
            command="create-update",
            place="issue",
            packit_comment_command_prefix=self.service_config.comment_command_prefix,
        )
        trigger_type_description = self.get_trigger_type_description(koji_build_data)
        body_msg = (
            f"{body}\n{trigger_type_description}\n\n{msg_retrigger}{MSG_GET_IN_TOUCH}\n"
        )

        report_in_issue_repository(
            issue_repository=self.job_config.issue_repository,
            service_config=self.service_config,
            title="Fedora Bodhi update failed to be created",
            message=body_msg,
            comment_to_existing=body_msg,
        )


@configured_as(job_type=JobType.bodhi_update)
@reacts_to(event=KojiBuildEvent)
class CreateBodhiUpdateHandler(
    BodhiUpdateHandler,
    RetriableJobHandler,
    GetKojiBuildEventMixin,
    GetKojiBuildDataFromKojiBuildEventMixin,
):
    """
    This handler can create a bodhi update for successful Koji builds.
    """

    task_name = TaskName.bodhi_update

    @staticmethod
    def get_checkers() -> Tuple[Type[Checker], ...]:
        """We react only on finished builds (=KojiBuildState.complete)
        and configured branches.
        """
        logger.debug("Bodhi update will be re-triggered via dist-git PR comment.")
        return (IsKojiBuildCompleteAndBranchConfiguredCheckEvent,)

    def get_trigger_type_description(self, koji_build_data: KojiBuildData) -> str:
        return (
            f"Fedora Bodhi update was triggered by "
            f"Koji build {koji_build_data.nvr}."
        )


@configured_as(job_type=JobType.bodhi_update)
@reacts_to(event=PullRequestCommentPagureEvent)
@run_for_comment(command="create-update")
class RetriggerBodhiUpdateHandler(
    BodhiUpdateHandler, GetKojiBuildDataFromKojiServiceMixin
):
    """
    This handler can re-trigger a bodhi update if any successful Koji build.
    """

    task_name = TaskName.retrigger_bodhi_update

    @staticmethod
    def get_checkers() -> Tuple[Type[Checker], ...]:
        """We react only on finished builds (=KojiBuildState.complete)
        and configured branches.
        """
        logger.debug("Bodhi update will be re-triggered via dist-git PR comment.")
        return (
            IsAuthorAPackager,
            HasIssueCommenterRetriggeringPermissions,
            IsKojiBuildCompleteAndBranchConfiguredCheckService,
        )

    def get_trigger_type_description(self, _: KojiBuildData) -> str:
        return (
            f"Fedora Bodhi update was re-triggered "
            f"by comment in dist-git PR with id {self.data.pr_id}."
        )


@configured_as(job_type=JobType.bodhi_update)
@reacts_to(event=IssueCommentEvent)
@reacts_to(event=IssueCommentGitlabEvent)
@run_for_comment(command="create-update")
class IssueCommentRetriggerBodhiUpdateHandler(
    BodhiUpdateHandler,
    ConfigFromDistGitUrlMixin,
    GetBranchesFromIssueMixin,
    GetKojiBuildDataFromKojiServiceMultipleBranches,
):
    """
    This handler can re-trigger a bodhi update if any successful Koji build.
    """

    task_name = TaskName.issue_comment_retrigger_bodhi_update

    @staticmethod
    def get_checkers() -> Tuple[Type[Checker], ...]:
        """We react only on finished builds (=KojiBuildState.complete)
        and configured branches.
        """
        logger.debug("Bodhi update will be re-triggered via dist-git PR comment.")
        return (HasIssueCommenterRetriggeringPermissions,)

    def get_trigger_type_description(self, _: KojiBuildData) -> str:
        return (
            f"Fedora Bodhi update was re-triggered by "
            f"comment in issue {self.data.issue_id}."
        )
