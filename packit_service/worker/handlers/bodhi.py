# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
This file defines classes for job handlers related to Bodhi
"""
import logging
from typing import Tuple

from celery import Task
from fedora.client import AuthError

from packit.constants import DEFAULT_BODHI_NOTE

from packit.exceptions import PackitException

from packit.config import JobConfig, JobType, PackageConfig
from packit_service.config import PackageConfigGetter
from packit_service.constants import (
    CONTACTS_URL,
    RETRY_INTERVAL_IN_MINUTES_WHEN_USER_ACTION_IS_NEEDED,
)
from packit_service.worker.checker.bodhi import IsKojiBuildComplete
from packit_service.worker.events.koji import KojiBuildEvent
from packit_service.worker.handlers.abstract import (
    TaskName,
    configured_as,
    reacts_to,
    RetriableJobHandler,
)
from packit_service.worker.handlers.mixin import GetKojiBuildEventMixin
from packit_service.worker.mixin import LocalProjectMixin
from packit_service.worker.result import TaskResults

logger = logging.getLogger(__name__)


@configured_as(job_type=JobType.bodhi_update)
@reacts_to(event=KojiBuildEvent)
class CreateBodhiUpdateHandler(
    RetriableJobHandler, LocalProjectMixin, GetKojiBuildEventMixin
):
    """
    This handler can create a bodhi update for successful Koji builds.
    """

    topic = "org.fedoraproject.prod.buildsys.build.state.change"
    task_name = TaskName.bodhi_update

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

    @staticmethod
    def get_checkers() -> Tuple:
        """We react only on finished builds (=KojiBuildState.complete)
        and configured branches.
        """
        return (IsKojiBuildComplete,)

    def run(self) -> TaskResults:
        try:
            self.packit_api.create_update(
                dist_git_branch=self.koji_build_event.git_ref,
                update_type="enhancement",
                update_notes=DEFAULT_BODHI_NOTE,
                koji_builds=[
                    self.koji_build_event.nvr  # it accepts NVRs, not build IDs
                ],
            )
        except PackitException as ex:
            logger.debug(f"Bodhi update failed to be created: {ex}")

            if isinstance(ex.__cause__, AuthError):
                body = (
                    f"Bodhi update creation failed for `{self.koji_build_event.nvr}` "
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

                notify = True
                known_error = True
            else:
                body = (
                    f"Bodhi update creation failed for `{self.koji_build_event.nvr}`:\n"
                    "```\n"
                    f"{ex}\n"
                    "```"
                )
                # Notify user just on the last run.
                notify = self.celery_task.is_last_try()
                known_error = False

            if notify:
                self.notify_user_about_failure(body)
            else:
                logger.debug("User will not be notified about the failure.")

            if not known_error:
                # This will cause `autoretry_for` mechanism to re-trigger the celery task.
                raise ex

        # `success=True` for all known errors
        # (=The task was correctly processed.)
        # Sentry issue will be created otherwise.
        return TaskResults(success=True, details={})

    def notify_user_about_failure(self, body: str) -> None:
        """
        If user configures `issue_repository`,
        Packit will create there an issue with the details.
        If the issue already exists and is opened, comment will be added
        instead of creating a new issue.

        The issue will be a place where to re-trigger the job.

        :param body: content sent to the user
            (comment or issue description;
             for issue description a contact footer is added)
        """
        if not self.job_config.issue_repository:
            logger.debug(
                "No issue repository configured. User will not be notified about the failure."
            )
            return

        logger.debug(
            f"Issue repository configured. We will create "
            f"a new issue in {self.job_config.issue_repository}"
            "or update the existing one."
        )
        issue_repo = self.service_config.get_project(
            url=self.job_config.issue_repository
        )
        PackageConfigGetter.create_issue_if_needed(
            project=issue_repo,
            title="Fedora Bodhi update failed to be created",
            message=body
            + f"\n\n---\n\n*Get in [touch with us]({CONTACTS_URL}) if you need some help.*",
            comment_to_existing=body,
        )
