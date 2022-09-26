# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging

from packit.config.aliases import get_branches

from packit_service.constants import KojiBuildState

from packit_service.worker.checker.abstract import Checker
from packit_service.worker.handlers.mixin import GetKojiBuildEventMixin

logger = logging.getLogger(__name__)


class IsKojiBuildComplete(Checker, GetKojiBuildEventMixin):
    def pre_check(self) -> bool:
        """Check if builds are finished (=KojiBuildState.complete)
        and branches are configured.
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
