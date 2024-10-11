# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging

from packit.config import (
    aliases,
)
from packit_service.worker.checker.abstract import Checker

logger = logging.getLogger(__name__)


class RawhideX86Target(
    Checker,
):
    def pre_check(self) -> bool:
        branches = aliases.get_build_targets(
            *self.job_config.targets,
        )
        if "fedora-rawhide-x86_64" not in branches:
            logger.debug(
                "Skipping job configuration with no fedora-rawhide-x86_64 target."
            )
            return False
        return True
