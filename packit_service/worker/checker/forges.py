# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging

from packit_service.worker.checker.abstract import Checker
from packit_service.worker.mixin import GetIssueMixin

logger = logging.getLogger(__name__)


class IsIssueInNotificationRepoChecker(Checker, GetIssueMixin):
    def pre_check(self) -> bool:
        """Checks whether the Packit verification command is placed in
        packit/notifications repository in the issue our service created.
        """
        if not (self.project.namespace == "packit" and self.project.repo == "notifications"):
            logger.debug(
                "Packit verification comment command not placedin packit/notifications repository.",
            )
            return False

        issue_author = self.issue.author
        if issue_author != self.service_config.get_github_account_name():
            logger.debug(
                f"Packit verification comment command placed on issue with author "
                f"other than our app: {issue_author}",
            )
            return False

        return True
