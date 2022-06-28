# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
This file defines classes for job handlers related to Bodhi
"""
import logging
from os import getenv
from typing import Optional

from celery import Task
from fedora.client import AuthError

from packit.constants import DEFAULT_BODHI_NOTE

from packit.exceptions import PackitException

from packit.api import PackitAPI
from packit.config.aliases import get_branches

from packit.config import JobConfig, JobType, PackageConfig
from packit.local_project import LocalProject
from packit.utils.repo import RepositoryCache
from packit_service.config import PackageConfigGetter
from packit_service.constants import (
    CONTACTS_URL,
    DEFAULT_RETRY_LIMIT,
    KojiBuildState,
    RETRY_INTERVAL_IN_MINUTES_WHEN_USER_ACTION_IS_NEEDED,
)
from packit_service.worker.events.koji import KojiBuildEvent
from packit_service.worker.handlers.abstract import (
    JobHandler,
    TaskName,
    configured_as,
    reacts_to,
)
from packit_service.worker.result import TaskResults

logger = logging.getLogger(__name__)


@configured_as(job_type=JobType.bodhi_update)
@reacts_to(event=KojiBuildEvent)
class CreateBodhiUpdateHandler(JobHandler):
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
        task: Optional[Task] = None,
    ):
        super().__init__(
            package_config=package_config,
            job_config=job_config,
            event=event,
        )
        self.task = task

        # lazy properties
        self._koji_build_event: Optional[KojiBuildEvent] = None

    @property
    def koji_build_event(self):
        if not self._koji_build_event:
            self._koji_build_event = KojiBuildEvent.from_event_dict(
                self.data.event_dict
            )
        return self._koji_build_event

    def pre_check(self) -> bool:
        """
        We react only on finished builds (=KojiBuildState.complete)
        and configured branches.
        By default, we use `fedora-stable` alias.
        (Rawhide updates are already created automatically.)
        """
        if self.koji_build_event.state != KojiBuildState.complete:
            logger.debug(
                f"Skipping build '{self.koji_build_event.build_id}' "
                f"on '{self.koji_build_event.git_ref}'. "
                f"Build not finished yet."
            )
            return False

        if self.koji_build_event.git_ref not in (
            configured_branches := get_branches(
                *(self.job_config.dist_git_branches or {"fedora-stable"}),
                default_dg_branch="rawhide",  # Koji calls it rawhide, not main
            )
        ):
            logger.info(
                f"Skipping build on '{self.data.git_ref}'. "
                f"Bodhi update configured only for '{configured_branches}'."
            )
            return False
        return True

    def run(self) -> TaskResults:
        self.local_project = LocalProject(
            git_project=self.project,
            working_dir=self.service_config.command_handler_work_dir,
            cache=RepositoryCache(
                cache_path=self.service_config.repository_cache,
                add_new=self.service_config.add_repositories_to_repository_cache,
            )
            if self.service_config.repository_cache
            else None,
        )
        packit_api = PackitAPI(
            self.service_config,
            self.job_config,
            downstream_local_project=self.local_project,
        )
        try:
            packit_api.create_update(
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

                body += f"*Try {self.task.request.retries+1}/{self.get_retry_limit()+1}"

                # Notify user on each task run and set a more generous retry interval
                # to let the user fix this issue in the meantime.
                if not self.is_last_try():
                    body += (
                        f": Task will be retried in "
                        f"{RETRY_INTERVAL_IN_MINUTES_WHEN_USER_ACTION_IS_NEEDED} minutes.*"
                    )
                    self.retry(
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
                notify = self.is_last_try()
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

    def is_last_try(self) -> bool:
        """
        Returns True if the current celery task is run for the last try.
        More info about retries can be found here:
        https://celeryproject.readthedocs.io/en/latest/userguide/tasks.html#retrying
        """
        return self.task.request.retries == self.get_retry_limit()

    def get_retry_limit(self) -> int:
        """
        Returns the limit of the celery task retries.
        (Packit uses this env.var. in HandlerTaskWithRetry base class
        to set `max_retries` in `retry_kwargs`.)
        """
        return int(getenv("CELERY_RETRY_LIMIT", DEFAULT_RETRY_LIMIT))

    def retry(self, ex: Exception, delay: Optional[int] = None) -> None:
        """
        Retries the celery task.
        Argument `throw` is set to False to not retry
        the task also because of the `autoretry_for` mechanism.

        More info about retries can be found here:
        https://celeryproject.readthedocs.io/en/latest/userguide/tasks.html#retrying

        :param ex: Exception needs to be specified.
        :param delay: Number of seconds task waits before being available to workers
        """
        retries = self.task.request.retries
        delay = delay if delay is not None else 60 * 2**retries
        logger.info(f"Will retry for the {retries + 1}. time in {delay}s.")
        kargs = self.task.request.kwargs.copy()
        self.task.retry(exc=ex, countdown=delay, throw=False, args=(), kwargs=kargs)
