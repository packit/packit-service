# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
import re

from packit_service.worker.checker.abstract import Checker
from packit_service.worker.events.enums import GitlabEventAction
from packit_service.worker.events import (
    MergeRequestGitlabEvent,
    PullRequestGithubEvent,
    PushGitHubEvent,
    PushGitlabEvent,
    PushPagureEvent,
)
from packit_service.constants import (
    KOJI_PRODUCTION_BUILDS_ISSUE,
    PERMISSIONS_ERROR_WRITE_OR_ADMIN,
)
from packit_service.worker.reporting import BaseCommitStatus
from packit_service.worker.handlers.mixin import GetKojiBuildJobHelperMixin

logger = logging.getLogger(__name__)


class PermissionOnKoji(Checker, GetKojiBuildJobHelperMixin):
    def pre_check(self) -> bool:
        if (
            self.data.event_type == MergeRequestGitlabEvent.__name__
            and self.data.event_dict["action"] == GitlabEventAction.closed.value
        ):
            # Not interested in closed merge requests
            return False

        if self.data.event_type in (
            PushGitHubEvent.__name__,
            PushGitlabEvent.__name__,
            PushPagureEvent.__name__,
        ):
            configured_branch = self.koji_build_helper.job_build_branch
            if not re.match(configured_branch, self.data.git_ref):
                logger.info(
                    f"Skipping build on '{self.data.git_ref}'. "
                    f"Push configured only for '{configured_branch}'."
                )
                return False

        if self.data.event_type in (
            PullRequestGithubEvent.__name__,
            MergeRequestGitlabEvent.__name__,
        ):
            user_can_merge_pr = self.project.can_merge_pr(self.data.actor)
            if not (user_can_merge_pr or self.data.actor in self.service_config.admins):
                self.koji_build_helper.report_status_to_all(
                    description=PERMISSIONS_ERROR_WRITE_OR_ADMIN,
                    state=BaseCommitStatus.neutral,
                )
                return False

        if not self.koji_build_helper.is_scratch:
            msg = "Non-scratch builds not possible from upstream."
            self.koji_build_helper.report_status_to_all(
                description=msg,
                state=BaseCommitStatus.neutral,
                url=KOJI_PRODUCTION_BUILDS_ISSUE,
            )
            return False

        return True
