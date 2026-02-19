# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging

from packit.constants import CONFIG_FILE_NAMES

from packit_service.worker.checker.abstract import Checker

logger = logging.getLogger(__name__)


class ProjectIsNotOnboarded(Checker):
    def pre_check(self) -> bool:
        if any(f for f in self.project.get_files(ref="rawhide") if f in CONFIG_FILE_NAMES):
            logger.info(f"Package {self.project.repo} is already onboarded")
            return False
        return True
