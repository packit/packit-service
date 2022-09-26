# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging

from packit.config.aliases import get_branches

from packit_service.worker.checker.abstract import Checker
from packit_service.worker.events import (
    PushPagureEvent,
)
from packit_service.worker.events.pagure import PullRequestCommentPagureEvent
from packit_service.worker.handlers.mixin import GetProjectToSyncMixin
from packit_service.worker.mixin import (
    GetPagurePullRequestMixin,
)


logger = logging.getLogger(__name__)


class Permission(Checker, GetPagurePullRequestMixin):
    def pre_check(self) -> bool:
        if self.data.event_type in (PushPagureEvent.__name__,):
            if self.data.git_ref not in (
                configured_branches := get_branches(
                    *self.job_config.dist_git_branches,
                    default="main",
                    with_aliases=True,
                )
            ):
                logger.info(
                    f"Skipping build on '{self.data.git_ref}'. "
                    f"Koji build configured only for '{configured_branches}'."
                )
                return False

            if self.data.event_dict["committer"] == "pagure":
                pr_author = self.get_pr_author()
                logger.debug(f"PR author: {pr_author}")
                if pr_author not in self.job_config.allowed_pr_authors:
                    logger.info(
                        f"Push event {self.data.identifier} with corresponding PR created by"
                        f" {pr_author} that is not allowed in project "
                        f"configuration: {self.job_config.allowed_pr_authors}."
                    )
                    return False
            else:
                committer = self.data.event_dict["committer"]
                logger.debug(f"Committer: {committer}")
                if committer not in self.job_config.allowed_committers:
                    logger.info(
                        f"Push event {self.data.identifier} done by "
                        f"{committer} that is not allowed in project "
                        f"configuration: {self.job_config.allowed_committers}."
                    )
                    return False
        elif self.data.event_type in (PullRequestCommentPagureEvent.__name__,):
            commenter = self.data.actor
            logger.debug(
                f"Triggering downstream koji build through comment by: {commenter}"
            )
            if not self.is_packager(commenter):
                logger.info(
                    f"koji-build retrigger comment event on PR identifier {self.data.pr_id} "
                    f"done by {commenter} which is not a packager."
                )
                return False

        return True


class IsProjectOk(Checker, GetProjectToSyncMixin):
    def pre_check(self) -> bool:
        return self.project_to_sync is not None
