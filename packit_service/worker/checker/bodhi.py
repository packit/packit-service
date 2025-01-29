# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging

from packit.config.aliases import get_branches

from packit_service.constants import MSG_GET_IN_TOUCH, KojiBuildState
from packit_service.events import (
    github,
    gitlab,
    koji,
    pagure,
)
from packit_service.worker.checker.abstract import (
    ActorChecker,
    Checker,
)
from packit_service.worker.checker.helper import DistgitAccountsChecker
from packit_service.worker.handlers.mixin import (
    GetKojiBuildData,
    GetKojiBuildDataFromKojiBuildEventMixin,
    GetKojiBuildDataFromKojiBuildTagEventMixin,
    GetKojiBuildDataFromKojiServiceMixin,
    GetKojiBuildEventMixin,
)
from packit_service.worker.mixin import (
    ConfigFromEventMixin,
    PackitAPIWithDownstreamMixin,
)
from packit_service.worker.reporting import report_in_issue_repository

logger = logging.getLogger(__name__)


class IsKojiBuildCompleteAndBranchConfigured(Checker, GetKojiBuildData):
    def pre_check(self) -> bool:
        """Check if builds are finished (=KojiBuildState.complete)
        and branches are configured.
        By default, we use `fedora-stable` alias.
        (Rawhide updates are already created automatically.)
        """

        if self.data.event_type in (
            pagure.pr.Comment.event_type(),
            koji.result.Build.event_type(),
            koji.tag.Build.event_type(),
        ):
            for koji_build_data in self:
                if koji_build_data.state != KojiBuildState.complete:
                    logger.debug(
                        f"Skipping build '{koji_build_data.build_id}' "
                        f"on '{koji_build_data.dist_git_branch}'. "
                        f"Build not finished yet.",
                    )
                    return False

                if koji_build_data.dist_git_branch not in (
                    configured_branches := get_branches(
                        *(self.job_config.dist_git_branches or {"fedora-stable"}),
                        default_dg_branch="rawhide",  # Koji calls it rawhide, not main
                    )
                ):
                    logger.info(
                        f"Skipping build on '{koji_build_data.dist_git_branch}'. "
                        f"Bodhi update configured only for '{configured_branches}'.",
                    )
                    return False

        return True


class IsKojiBuildOwnerMatchingConfiguration(Checker, GetKojiBuildEventMixin):
    def pre_check(self) -> bool:
        """Check if the build submitter matches the configuration"""

        if self.data.event_type in (koji.result.Build.event_type(),):
            owner = self.koji_build_event.owner
            configured_builders = self.job_config.allowed_builders

            if not DistgitAccountsChecker(
                self.project,
                accounts_list=configured_builders,
                account_to_check=owner,
            ).check_allowed_accounts():
                logger.info(
                    f"Owner of the build ({owner}) does not match the "
                    f"configuration: {configured_builders}",
                )
                return False

        return True


class IsKojiBuildCompleteAndBranchConfiguredCheckEvent(
    IsKojiBuildCompleteAndBranchConfigured,
    GetKojiBuildEventMixin,
    GetKojiBuildDataFromKojiBuildEventMixin,
): ...


class IsKojiBuildCompleteAndBranchConfiguredCheckSidetag(
    IsKojiBuildCompleteAndBranchConfigured,
    GetKojiBuildDataFromKojiBuildTagEventMixin,
): ...


class IsKojiBuildCompleteAndBranchConfiguredCheckService(
    IsKojiBuildCompleteAndBranchConfigured,
    GetKojiBuildDataFromKojiServiceMixin,
): ...


class HasIssueCommenterRetriggeringPermissions(ActorChecker, ConfigFromEventMixin):
    """To be able to retrigger a Bodhi update the issue commenter should
    have write permission on the project.
    """

    def _pre_check(self) -> bool:
        has_write_access = self.project.has_write_access(user=self.actor)
        if self.data.event_type in (
            github.issue.Comment.event_type(),
            gitlab.issue.Comment.event_type(),
        ):
            logger.debug(
                f"Re-triggering Bodhi update through comment in "
                f"repo {self.project_url} and issue {self.data.issue_id} "
                f"by {self.actor}.",
            )
            if not has_write_access:
                msg = (
                    f"Re-triggering Bodhi update through comment in "
                    f"repo **{self.project_url}** and issue **{self.data.issue_id}** "
                    f"is not allowed for the user *{self.actor}* "
                    f"which has not write permissions on the project."
                )
                logger.info(msg)
                issue = self.project.get_issue(self.data.issue_id)
                report_in_issue_repository(
                    issue_repository=self.job_config.issue_repository,
                    service_config=self.service_config,
                    title=issue.title,
                    message=msg + MSG_GET_IN_TOUCH,
                    comment_to_existing=msg,
                )
                return False

            return True
        if self.data.event_type in (pagure.pr.Comment.event_type(),):
            logger.debug(
                f"Re-triggering Bodhi update via dist-git comment in "
                f"repo {self.project_url} and #PR {self.data.pr_id} "
                f"by {self.actor}.",
            )
            if not has_write_access:
                msg = (
                    f"Re-triggering Bodhi update via dist-git comment in "
                    f"**PR#{self.data.pr_id}** and project **{self.project.repo}** "
                    f"is not allowed for the user *{self.actor}* "
                    f"which has not write permissions on the project."
                )
                logger.info(msg)
                title = "Re-triggering Bodhi update through comment in issue failed"
                report_in_issue_repository(
                    issue_repository=self.job_config.issue_repository,
                    service_config=self.service_config,
                    title=title,
                    message=msg + MSG_GET_IN_TOUCH,
                    comment_to_existing=msg,
                )
                return False

            return True

        return True


class IsAuthorAPackager(ActorChecker, PackitAPIWithDownstreamMixin):
    def _pre_check(self) -> bool:
        if self.data.event_type not in (pagure.pr.Comment.event_type(),) or self.is_packager(
            user=self.actor
        ):
            return True

        title = "Re-triggering Bodhi update through dist-git comment in PR failed"
        msg = (
            f"Re-triggering Bodhi update via dist-git comment in **PR#{self.data.pr_id}**"
            f" and project **{self.project_url}** is not allowed, user *{self.actor}* "
            "is not a packager."
        )
        logger.info(msg)
        report_in_issue_repository(
            issue_repository=self.job_config.issue_repository,
            service_config=self.service_config,
            title=title,
            message=msg + MSG_GET_IN_TOUCH,
            comment_to_existing=msg,
        )
        return False
