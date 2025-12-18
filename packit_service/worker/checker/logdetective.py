# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging

from packit_service.worker.checker.abstract import Checker

logger = logging.getLogger(__name__)


class IsEventForJob(Checker):
    def pre_check(self) -> bool:
        if self.data.identifier != self.job_config.identifier:
            logger.debug(
                f"Skipping reporting, identifiers don't match "
                f"(identifier of the test job to report: {self.data.identifier}, "
                f"identifier from job config: {self.job_config.identifier}).",
            )
            return False
        return True
