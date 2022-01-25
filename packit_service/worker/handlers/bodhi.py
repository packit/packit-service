# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
This file defines classes for job handlers related to Bodhi
"""
import logging
from typing import Optional

from packit.constants import DEFAULT_BODHI_NOTE

from packit.exceptions import PackitException

from packit.api import PackitAPI
from packit.config.aliases import get_branches

from packit.config import JobConfig, JobType, PackageConfig
from packit.local_project import LocalProject
from packit.utils.repo import RepositoryCache
from packit_service.constants import CONTACTS_URL, KojiBuildState
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
    ):
        super().__init__(
            package_config=package_config,
            job_config=job_config,
            event=event,
        )

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
        We react only on finished builds (=KojiBuildState.open state)
        and configured branches.
        By default, we use `fedora-stable` alias.
        (Rawhide updates are already created automatically.)
        """
        if self.koji_build_event.state != KojiBuildState.open:
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
            stage=self.service_config.use_stage(),
        )
        try:
            packit_api.create_update(
                dist_git_branch=self.koji_build_event.git_ref,
                update_type="enhancement",
                update_notes=DEFAULT_BODHI_NOTE,
                koji_builds=[str(self.koji_build_event.build_id)],
            )
        except PackitException as ex:
            packit_api.downstream_local_project.git_project.commit_comment(
                commit=packit_api.downstream_local_project.commit_hexsha,
                body="Bodhi update failed:\n"
                "```\n"
                "{ex}\n"
                "```\n\n"
                f"*Get in [touch with us]({CONTACTS_URL}) if you need some help.*",
            )
            raise ex
        return TaskResults(success=True, details={})
