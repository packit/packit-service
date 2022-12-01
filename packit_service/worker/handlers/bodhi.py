# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
This file defines classes for job handlers related to Bodhi
"""
import logging
from typing import Tuple, Type

from celery import Task
from fedora.client import AuthError

from packit.config import JobConfig, JobType, PackageConfig
from packit.exceptions import PackitException
from packit_service.constants import (
    CONTACTS_URL,
    RETRY_INTERVAL_IN_MINUTES_WHEN_USER_ACTION_IS_NEEDED,
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
    GetKojiBuildData,
    GetKojiBuildDataFromKojiBuildEventMixin,
    GetKojiBuildDataFromKojiServiceMixin,
    GetKojiBuildEventMixin,
)
from packit_service.worker.mixin import (
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
            self.packit_api.create_update(
                dist_git_branch=self.dist_git_branch,
                update_type="enhancement",
                koji_builds=[self.nvr],  # it accepts NVRs, not build IDs
            )
        except PackitException as ex:
            logger.debug(f"Bodhi update failed to be created: {ex}")

            if isinstance(ex.__cause__, AuthError):
                body = self._error_message_for_auth_error(ex)
                notify = True
                known_error = True
            else:
                body = (
                    f"Bodhi update creation failed for `{self.nvr}`:\n"
                    "```\n"
                    f"{ex}\n"
                    "```"
                )
                # Notify user just on the last run.
                notify = self.celery_task.is_last_try()
                known_error = False

            if notify:
                report_in_issue_repository(
                    issue_repository=self.job_config.issue_repository,
                    service_config=self.service_config,
                    title="Fedora Bodhi update failed to be created",
                    message=body
                    + f"\n\n---\n\n*Get in [touch with us]({CONTACTS_URL}) if you need some help.*",
                    comment_to_existing=body,
                )
            else:
                logger.debug("User will not be notified about the failure.")

            if not known_error:
                # This will cause `autoretry_for` mechanism to re-trigger the celery task.
                raise ex

        # `success=True` for all known errors
        # (=The task was correctly processed.)
        # Sentry issue will be created otherwise.
        return TaskResults(success=True, details={})

    def _error_message_for_auth_error(self, ex: PackitException) -> str:
        body = (
            f"Bodhi update creation failed for `{self.nvr}` "
            f"because of the missing permissions.\n\n"
            f"Please, give {self.service_config.fas_user} user `commit` rights in the "
            f"[dist-git settings]({self.data.project_url}/adduser).\n\n"
        )

        body += (
            f"*Try {self.celery_task.retries + 1}/"
            f"{self.celery_task.get_retry_limit() + 1}"
        )

        # Notify user on each task run and set a more generous retry interval
        # to let the user fix this issue in the meantime.
        if not self.celery_task.is_last_try():
            body += (
                f": Task will be retried in "
                f"{RETRY_INTERVAL_IN_MINUTES_WHEN_USER_ACTION_IS_NEEDED} minutes.*"
            )
            self.celery_task.retry(
                delay=RETRY_INTERVAL_IN_MINUTES_WHEN_USER_ACTION_IS_NEEDED * 60,
                ex=ex,
            )
        else:
            body += "*"

        return body


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


@configured_as(job_type=JobType.bodhi_update)
@reacts_to(event=PullRequestCommentPagureEvent)
@reacts_to(event=IssueCommentEvent)
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
