# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
import re

from ogr.abstract import AccessLevel
from packit.config.aliases import get_branches
from packit_service.constants import MSG_GET_IN_TOUCH, KojiAllowedAccountsAlias
from packit_service.worker.checker.abstract import Checker, ActorChecker
from packit_service.worker.events import (
    PushPagureEvent,
    IssueCommentEvent,
    IssueCommentGitlabEvent,
)
from packit_service.worker.events.new_hotness import NewHotnessUpdateEvent
from packit_service.worker.events.pagure import PullRequestCommentPagureEvent
from packit_service.worker.handlers.mixin import GetProjectToSyncMixin
from packit_service.worker.mixin import (
    GetPagurePullRequestMixin,
)
from packit_service.worker.reporting import report_in_issue_repository

logger = logging.getLogger(__name__)


class PermissionOnDistgit(Checker, GetPagurePullRequestMixin):
    def contains_specfile_change(self):
        """
        Check whether the dist-git commit contains
        any specfile change (do the check only for pushes from PRs,
        with direct pushes we do the filtering in fedmsg).
        """
        if not self.pull_request:
            return True

        pr_id = self.pull_request.id
        logger.debug(f"PR {pr_id} status: {self.pull_request.status}")
        # Pagure API tends to return ENOPRSTATS error when a pull request is transitioning
        # from open to merged state, give it some extra time
        diff = self.project.get_pr_files_diff(pr_id, retries=5, wait_seconds=5) or {}
        if not any(change.endswith(".spec") for change in diff):
            logger.info(f"PR {pr_id} does not contain a specfile change.")
            return False
        return True

    @staticmethod
    def is_koji_allowed_accounts_alias(value: str) -> bool:
        return any(value == alias.value for alias in KojiAllowedAccountsAlias)

    def check_allowed_accounts(
        self, accounts_list: list[str], account_to_check: str
    ) -> bool:
        """
        Check whether the account_to_check matches one of the values in accounts_list
        (considering the groups and aliases).
        """
        logger.info(f"Checking {account_to_check} in list of accounts: {accounts_list}")

        direct_account_names = [
            value
            for value in accounts_list
            if not self.is_koji_allowed_accounts_alias(value)
            and not value.startswith("@")
        ]

        # check the direct account names to prevent unneeded API interactions
        if account_to_check in direct_account_names:
            return True

        all_accounts = set()

        for value in accounts_list:
            if self.is_koji_allowed_accounts_alias(value):
                all_accounts.update(self.expand_maintainer_alias(value))
            elif value.startswith("@"):
                try:
                    # remove @
                    group_name = value[1:]
                    group = self.project.service.get_group(group_name)
                    all_accounts.update(group.members)
                except Exception as ex:
                    logger.debug(
                        f"Exception while getting the members of group {value}: {ex!r}"
                    )
                    continue
            else:
                all_accounts.add(value)

        logger.debug(f"Expanded accounts list: {all_accounts}")
        return account_to_check in all_accounts

    def expand_maintainer_alias(self, alias: str) -> set[str]:
        """
        Expand the 'all_admins' and 'all_committers' aliases to users.
        """
        # see AccessLevel mapping
        # https://github.com/packit/ogr/blob/d183a6c6459231c2a60bacd6b827502c92a130ef/ogr/abstract.py#L1079
        # all_admins -> Pagure "admin" and "maintainer" access
        # all_committers -> on top of that "commit" access
        access_levels = [AccessLevel.maintain]

        if alias == KojiAllowedAccountsAlias.all_committers.value:
            access_levels.extend([AccessLevel.admin, AccessLevel.push])

        accounts = self.project.get_users_with_given_access(access_levels)

        logger.debug(f"Expanded {alias}: {accounts}")
        return accounts

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
                if not self.pull_request:
                    logger.debug(
                        "Not able to get the pull request "
                        "(may not be the head commit of the PR)."
                    )
                    return False

                if not self.contains_specfile_change():
                    return False

                pr_author = self.get_pr_author()
                logger.debug(f"PR author: {pr_author}")
                if not self.check_allowed_accounts(
                    self.job_config.allowed_pr_authors, pr_author
                ):
                    logger.info(
                        f"Push event {self.data.identifier} with corresponding PR created by"
                        f" {pr_author} that is not allowed in project "
                        f"configuration: {self.job_config.allowed_pr_authors}."
                    )
                    return False
            else:
                committer = self.data.event_dict["committer"]
                logger.debug(f"Committer: {committer}")
                if not self.check_allowed_accounts(
                    self.job_config.allowed_committers, committer
                ):
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
                msg = (
                    f"koji-build retriggering through comment "
                    f"on PR identifier {self.data.pr_id} "
                    f"and project {self.data.project_url} "
                    f"done by {commenter} which is not a packager."
                )
                logger.info(msg)
                report_in_issue_repository(
                    issue_repository=self.job_config.issue_repository,
                    service_config=self.service_config,
                    title=(
                        "Re-triggering downstream koji build "
                        "through comment in dist-git PR failed"
                    ),
                    message=msg + MSG_GET_IN_TOUCH,
                    comment_to_existing=msg,
                )
                return False

        return True


class HasIssueCommenterRetriggeringPermissions(ActorChecker):
    """To be able to retrigger a koji-build the issue commenter should
    have write permission on the project.
    """

    def _pre_check(self) -> bool:
        if self.data.event_type in (
            IssueCommentEvent.__name__,
            IssueCommentGitlabEvent.__name__,
        ):
            logger.debug(
                f"Re-triggering downstream koji-build through comment in "
                f"repo {self.project.repo} and issue {self.data.issue_id} "
                f"by {self.actor}."
            )
            if not self.project.has_write_access(user=self.actor):
                msg = (
                    f"Re-triggering downstream koji-build through comment in "
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

        return True


class IsProjectOk(Checker, GetProjectToSyncMixin):
    def pre_check(self) -> bool:
        return self.project_to_sync is not None


class ValidInformationForPullFromUpstream(Checker, GetPagurePullRequestMixin):
    """
    Check that package config (with upstream_project_url set) is present
    and that we were able to parse repo namespace, name and the tag name.
    Report in issue repository if not.
    """

    def pre_check(self) -> bool:
        valid = True
        msg_to_report = None
        issue_title = (
            "Pull from upstream could not be run for update "
            f"{self.data.event_dict.get('version')}"
        )

        if not self.package_config.upstream_project_url:
            msg_to_report = (
                "`upstream_project_url` is not set in "
                "the dist-git package configuration."
            )
            valid = False

        if not (
            self.data.event_dict.get("repo_name")
            and self.data.event_dict.get("repo_namespace")
        ):
            msg_to_report = (
                "We were not able to parse repo name or repo namespace from the "
                f"upstream_project_url '{self.package_config.upstream_project_url}' "
                f"defined in the config."
            )
            valid = False

        if (
            self.data.event_type in (NewHotnessUpdateEvent.__name__,)
            and not self.data.tag_name
        ):
            msg_to_report = "We were not able to get the upstream tag name."
            valid = False

        if self.data.event_type in (PullRequestCommentPagureEvent.__name__,):
            commenter = self.data.actor
            logger.debug(
                f"Triggering pull-from-upstream through comment by: {commenter}"
            )
            if not self.is_packager(commenter):
                msg_to_report = (
                    f"pull-from-upstream retriggering through comment "
                    f"on PR identifier {self.data.pr_id} "
                    f"and project {self.data.project_url} "
                    f"done by {commenter} who is not a packager."
                )
                issue_title = "Re-triggering pull-from-upstream "
                "through a comment in dist-git PR failed"
                valid = False

        if msg_to_report:
            logger.debug(msg_to_report)
            report_in_issue_repository(
                issue_repository=self.job_config.issue_repository,
                service_config=self.service_config,
                title=issue_title,
                message=msg_to_report,
                comment_to_existing=msg_to_report,
            )

        return valid


class IsUpstreamTagMatchingConfig(Checker):
    def pre_check(self) -> bool:
        tag = self.data.tag_name

        # if the tag in event is None (pull-from-upstream retriggering), we will filter
        # only matching tags in the handler directly
        if not tag:
            return True

        if upstream_tag_include := self.job_config.upstream_tag_include:
            matching_include_regex = re.match(upstream_tag_include, tag)
            if not matching_include_regex:
                logger.info(
                    f"Tag {tag} doesn't match the upstream_tag_include {upstream_tag_include} "
                    f"from the config. Skipping the syncing."
                )
                return False

        if upstream_tag_exclude := self.job_config.upstream_tag_exclude:
            matching_exclude_regex = re.match(upstream_tag_exclude, tag)
            if matching_exclude_regex:
                logger.info(
                    f"Tag {tag} matches the upstream_tag_exclude {upstream_tag_exclude} "
                    f"from the config. Skipping the syncing."
                )
                return False

        return True
