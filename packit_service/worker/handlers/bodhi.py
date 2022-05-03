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
from packit_service.constants import CONTACTS_URL, DEFAULT_RETRY_LIMIT, KojiBuildState
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
                *(self.job_config.metadata.dist_git_branches or {"fedora-stable"}),
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
            if not self.job_config.issue_repository:
                logger.debug(
                    "No issue repository configured. User will not be notified about the failure."
                )
                raise ex

            known_error = False
            if isinstance(ex.__cause__, AuthError):
                body = (
                    f"Bodhi update creation failed for `{self.koji_build_event.nvr}` "
                    f"because of the missing permissions.\n\n"
                    f"Please, give {self.service_config.fas_user} user `commit` rights in the "
                    f"[dist-git settings]({self.data.project_url}/adduser)."
                )
                known_error = True
            else:
                body = (
                    f"Bodhi update creation failed for `{self.koji_build_event.nvr}`:\n"
                    "```\n"
                    f"{ex}\n"
                    "```"
                )

            if (
                not known_error
                and self.task
                and self.task.request.retries
                < int(getenv("CELERY_RETRY_LIMIT", DEFAULT_RETRY_LIMIT))
            ):
                logger.debug(
                    "Celery task will be retried. User will not be notified about the failure."
                )
                raise ex

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
                + f"\n\n*Get in [touch with us]({CONTACTS_URL}) if you need some help.*",
                comment_to_existing=body,
            )

            if not known_error:
                raise ex
        return TaskResults(success=True, details={})
