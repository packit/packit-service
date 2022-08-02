# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging

from packit.config.aliases import get_branches

from packit_service.constants import KojiBuildState

from packit_service.worker.checker.abstract import ActorChecker, Checker
from packit_service.worker.handlers.mixin import (
    GetKojiBuildData,
    GetKojiBuildDataFromKojiBuildEventMixin,
    GetKojiBuildDataFromKojiServiceMixin,
    GetKojiBuildEventMixin,
)
from packit_service.worker.mixin import ConfigMixin, PackitAPIWithDownstreamMixin

logger = logging.getLogger(__name__)


class IsKojiBuildCompleteAndBranchConfigured(Checker, GetKojiBuildData):
    def pre_check(self) -> bool:
        """Check if builds are finished (=KojiBuildState.complete)
        and branches are configured.
        By default, we use `fedora-stable` alias.
        (Rawhide updates are already created automatically.)
        """
        if self.state != KojiBuildState.complete:
            logger.debug(
                f"Skipping build '{self.build_id}' "
                f"on '{self.dist_git_branch}'. "
                f"Build not finished yet."
            )
            return False

        if self.dist_git_branch not in (
            configured_branches := get_branches(
                *(self.job_config.dist_git_branches or {"fedora-stable"}),
                default_dg_branch="rawhide",  # Koji calls it rawhide, not main
            )
        ):
            logger.info(
                f"Skipping build on '{self.dist_git_branch}'. "
                f"Bodhi update configured only for '{configured_branches}'."
            )
            return False
        return True


class IsKojiBuildCompleteAndBranchConfiguredCheckEvent(
    IsKojiBuildCompleteAndBranchConfigured,
    GetKojiBuildEventMixin,
    GetKojiBuildDataFromKojiBuildEventMixin,
):
    ...


class IsKojiBuildCompleteAndBranchConfiguredCheckService(
    IsKojiBuildCompleteAndBranchConfigured, GetKojiBuildDataFromKojiServiceMixin
):
    ...


class HasAuthorWriteAccess(ActorChecker, ConfigMixin):
    def _pre_check(self) -> bool:
        if not self.project.has_write_access(user=self.actor):
            logger.info(
                f"Re-triggering Bodhi update via dist-git comment in PR#{self.data.pr_id}"
                f" and project {self.project.repo} is not allowed for the user: {self.actor}."
            )
            return False

        return True


class IsAuthorAPackager(ActorChecker, PackitAPIWithDownstreamMixin):
    def _pre_check(self) -> bool:

        if not self.is_packager(user=self.actor):
            logger.info(
                f"Re-triggering Bodhi update via dist-git comment in PR#{self.data.pr_id}"
                f" and project {self.project.repo} is not allowed, user {self.actor} "
                "is not a packager."
            )
            return False

        return True
