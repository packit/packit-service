# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging

from packit.config.aliases import get_branches

from packit_service.constants import KojiBuildState

from packit_service.worker.checker.abstract import (
    ActorChecker,
    Checker,
)
from packit_service.worker.handlers.mixin import (
    GetKojiBuildData,
    GetKojiBuildDataFromKojiBuildEventMixin,
    GetKojiBuildDataFromKojiServiceMixin,
    GetKojiBuildEventMixin,
)
from packit_service.worker.mixin import (
    ConfigFromEventMixin,
    PackitAPIWithDownstreamMixin,
)
from packit_service.worker.events import (
    PullRequestCommentPagureEvent,
    IssueCommentEvent,
)

from packit_service.worker.events.koji import KojiBuildEvent

logger = logging.getLogger(__name__)


class IsKojiBuildCompleteAndBranchConfigured(Checker, GetKojiBuildData):
    def pre_check(self) -> bool:
        """Check if builds are finished (=KojiBuildState.complete)
        and branches are configured.
        By default, we use `fedora-stable` alias.
        (Rawhide updates are already created automatically.)
        """

        if self.data.event_type in (
            PullRequestCommentPagureEvent.__name__,
            KojiBuildEvent.__name__,
        ):
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


class HasIssueCommenterRetriggeringPermissions(ActorChecker, ConfigFromEventMixin):
    """To be able to retrigger a Bodhi update the issue commenter should
    have write permission on the project.
    """

    def _pre_check(self) -> bool:
        has_write_access = self.project.has_write_access(user=self.actor)
        if self.data.event_type in (IssueCommentEvent.__name__,):
            logger.debug(
                f"Re-triggering Bodhi update through comment in "
                f"repo {self.project.repo} and issue {self.data.issue_id} "
                f"by {self.actor}."
            )
            if not has_write_access:
                logger.warning(
                    f"Re-triggering Bodhi update through comment in "
                    f"repo {self.project.repo} and issue {self.data.issue_id} "
                    f"is not allowed for the user {self.actor} "
                    f"which has not write permissions on the project."
                )
                return False

            return True
        if self.data.event_type in (PullRequestCommentPagureEvent.__name__,):
            logger.debug(
                f"Re-triggering Bodhi update via dist-git comment in "
                f"repo {self.project.repo} and #PR {self.data.pr_id} "
                f"by {self.actor}."
            )
            if not has_write_access:
                logger.warning(
                    f"Re-triggering Bodhi update via dist-git comment in "
                    f"PR#{self.data.pr_id} and project {self.project.repo} "
                    f"is not allowed for the user {self.actor} "
                    f"which has not write permissions on the project."
                )
                return False

            return True

        return True


class IsAuthorAPackager(ActorChecker, PackitAPIWithDownstreamMixin):
    def _pre_check(self) -> bool:

        if self.data.event_type in (PullRequestCommentPagureEvent.__name__,):
            if not self.is_packager(user=self.actor):
                logger.info(
                    f"Re-triggering Bodhi update via dist-git comment in PR#{self.data.pr_id}"
                    f" and project {self.project.repo} is not allowed, user {self.actor} "
                    "is not a packager."
                )
                return False

        return True
