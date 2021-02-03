# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from typing import Any, Iterable, List, Optional, Union, Callable, Dict, Tuple

from fedora.client import AuthError, FedoraServiceError
from fedora.client.fas2 import AccountSystem

from ogr.abstract import CommitStatus, GitProject
from packit.config.job_config import JobConfig
from packit.exceptions import PackitException
from packit_service.config import ServiceConfig
from packit_service.constants import FAQ_URL
from packit_service.models import AllowlistModel
from packit_service.service.events import (
    AbstractCoprBuildEvent,
    DistGitEvent,
    EventData,
    InstallationEvent,
    IssueCommentEvent,
    IssueCommentGitlabEvent,
    KojiBuildEvent,
    MergeRequestCommentGitlabEvent,
    MergeRequestGitlabEvent,
    PullRequestCommentGithubEvent,
    PullRequestCommentPagureEvent,
    PullRequestGithubEvent,
    PullRequestPagureEvent,
    PushGitHubEvent,
    PushGitlabEvent,
    PushPagureEvent,
    ReleaseEvent,
    TestingFarmResultsEvent,
    AllowlistStatus,
)
from packit_service.worker.build import CoprBuildJobHelper

logger = logging.getLogger(__name__)

UncheckedEvent = Union[
    PushPagureEvent,
    PullRequestPagureEvent,
    PullRequestCommentPagureEvent,
    AbstractCoprBuildEvent,
    TestingFarmResultsEvent,
    DistGitEvent,
    InstallationEvent,
    KojiBuildEvent,
]


class Allowlist:
    def __init__(self, fas_user: str = None, fas_password: str = None):
        self._fas: AccountSystem = AccountSystem(
            username=fas_user, password=fas_password
        )

    def _signed_fpca(self, account_login: str) -> bool:
        """
        Check if the user is a packager, by checking if their GitHub
        username is in the 'packager' group in FAS. Works only the user's
        username is the same in GitHub and FAS.
        :param account_login: str, Github username
        :return: bool
        """

        try:
            person = self._fas.person_by_username(account_login)
        except AuthError as e:
            logger.error(f"FAS authentication failed: {e!r}")
            return False
        except FedoraServiceError as e:
            logger.error(f"FAS query failed: {e!r}")
            return False

        if not person:
            logger.info(f"Not a FAS username {account_login!r}.")
            return False

        for membership in person.get("memberships", []):
            if membership.get("name") == "cla_fpca":
                logger.info(f"User {account_login!r} signed FPCA!")
                return True

        logger.info(f"Cannot verify whether {account_login!r} signed FPCA.")
        return False

    def add_account(self, account_login: str, sender_login: str) -> bool:
        """
        Add account to allowlist.
        Status is set to 'waiting' or to 'approved_automatically'
        if the account is a packager in Fedora.
        :param sender_login: login of the user who installed the app into 'account'
        :param account_login: login of the account into which the app was installed
        :return: was the account (auto/already)-allowlisted?
        """
        # TODO: Switch to AllowlistModel
        if AllowlistModel.get_account(account_login):
            return True

        # TODO: Switch to AllowlistStatus
        AllowlistModel.add_account(account_login, AllowlistStatus.waiting.value)

        if self._signed_fpca(sender_login):
            AllowlistModel.add_account(
                account_login, AllowlistStatus.approved_automatically.value
            )
            return True

        return False

    @staticmethod
    def approve_account(account_name: str):
        """
        Approve user manually
        :param account_name: account name for approval
        """
        AllowlistModel.add_account(
            account_name=account_name, status=AllowlistStatus.approved_manually.value
        )

        logger.info(f"Account {account_name!r} approved successfully.")

    @staticmethod
    def is_approved(account_name: str) -> bool:
        """
        Check if user is approved in the allowlist
        :param account_name: account name to check
        :return:
        """
        account = AllowlistModel.get_account(account_name)
        if not account:
            return False

        return AllowlistStatus(account.status) in (
            AllowlistStatus.approved_automatically,
            AllowlistStatus.approved_manually,
        )

    @staticmethod
    def remove_account(account_name: str) -> bool:
        """
        Remove account from allowlist.
        :param account_name: account name for removing
        :return: has the account existed before?
        """
        account_existed = False

        if AllowlistModel.get_account(account_name):
            AllowlistModel.remove_account(account_name)
            logger.info(f"Account {account_name!r} removed from postgres allowlist!")
            account_existed = True

        if not account_existed:
            logger.info(f"Account {account_name!r} does not exist!")

        return account_existed

    @staticmethod
    def accounts_waiting() -> List[str]:
        """
        Get accounts waiting for approval
        :return: list of accounts waiting for approval
        """
        return [
            account.account_name
            for account in AllowlistModel.get_accounts_by_status(
                AllowlistStatus.waiting.value
            )
        ]

    def _check_unchecked_event(
        self,
        event: UncheckedEvent,
        project: GitProject,
        service_config: ServiceConfig,
        job_configs: Iterable[JobConfig],
    ) -> bool:
        # Allowlist checks do not apply to CentOS (Pagure, GitLab)
        logger.info(f"{type(event)} event does not require allowlist checks.")
        return True

    def _check_release_push_event(
        self,
        event: Union[ReleaseEvent, PushGitHubEvent, PushGitlabEvent],
        project: GitProject,
        service_config: ServiceConfig,
        job_configs: Iterable[JobConfig],
    ) -> bool:
        # TODO: modify event hierarchy so we can use some abstract classes instead
        namespace = event.repo_namespace
        if not namespace:
            raise KeyError(f"Failed to get namespace from {type(event)!r}")

        if self.is_approved(namespace):
            return True

        logger.info("Refusing release event on not allowlisted repo namespace.")
        return False

    def _check_pr_event(
        self,
        event: Union[
            PullRequestGithubEvent,
            PullRequestCommentGithubEvent,
            MergeRequestGitlabEvent,
            MergeRequestCommentGitlabEvent,
        ],
        project: GitProject,
        service_config: ServiceConfig,
        job_configs: Iterable[JobConfig],
    ) -> bool:
        account_name = event.user_login
        if not account_name:
            raise KeyError(f"Failed to get account_name from {type(event)}")
        namespace = event.target_repo_namespace

        namespace_approved = self.is_approved(namespace)
        user_approved = (
            project.can_merge_pr(account_name)
            or project.get_pr(event.pr_id).author == account_name
        )

        if namespace_approved and user_approved:
            # TODO: clear failing check when present
            return True

        msg = (
            f"Namespace {namespace} is not on our allowlist!"
            if not namespace_approved
            else f"Account {account_name} has no write access nor is author of PR!"
        )
        logger.error(msg)
        if isinstance(
            event, (PullRequestCommentGithubEvent, MergeRequestCommentGitlabEvent)
        ):
            project.pr_comment(event.pr_id, msg)
        else:
            for job_config in job_configs:
                job_helper = CoprBuildJobHelper(
                    service_config=service_config,
                    package_config=event.get_package_config(),
                    project=project,
                    metadata=EventData.from_event_dict(event.get_dict()),
                    db_trigger=event.db_trigger,
                    job_config=job_config,
                )
                msg = (
                    "Namespace is not allowed!"
                    if not namespace_approved
                    else "User cannot trigger!"
                )
                job_helper.report_status_to_all(
                    description=msg, state=CommitStatus.error, url=FAQ_URL
                )

        return False

    def _check_issue_comment_event(
        self,
        event: Union[IssueCommentEvent, IssueCommentGitlabEvent],
        project: GitProject,
        service_config: ServiceConfig,
        job_configs: Iterable[JobConfig],
    ) -> bool:
        account_name = event.user_login
        if not account_name:
            raise KeyError(f"Failed to get account_name from {type(event)}")
        namespace = event.repo_namespace

        namespace_approved = self.is_approved(namespace)
        user_approved = project.can_merge_pr(account_name)

        if namespace_approved and user_approved:
            return True

        msg = (
            f"Namespace {namespace} is not on our allowlist!"
            if not namespace_approved
            else f"Account {account_name} has no write access!"
        )
        logger.error(msg)
        project.issue_comment(event.issue_id, msg)
        return False

    def check_and_report(
        self,
        event: Optional[Any],
        project: GitProject,
        service_config: ServiceConfig,
        job_configs: Iterable[JobConfig],
    ) -> bool:
        """
        Check if account is approved and report status back in case of PR
        :param service_config: service config
        :param event: PullRequest and Release TODO: handle more
        :param project: GitProject
        :param job_configs: iterable of jobconfigs - so we know how to update status of the PR
        :return:
        """
        CALLBACKS: Dict[
            Union[type, Tuple[Union[type, Tuple[Any, ...]], ...]], Callable
        ] = {
            (  # events that are not checked against allowlist
                PushPagureEvent,
                PullRequestPagureEvent,
                PullRequestCommentPagureEvent,
                AbstractCoprBuildEvent,
                TestingFarmResultsEvent,
                DistGitEvent,
                InstallationEvent,
                KojiBuildEvent,
            ): self._check_unchecked_event,
            (
                ReleaseEvent,
                PushGitHubEvent,
                PushGitlabEvent,
            ): self._check_release_push_event,
            (
                PullRequestGithubEvent,
                PullRequestCommentGithubEvent,
                MergeRequestGitlabEvent,
                MergeRequestCommentGitlabEvent,
            ): self._check_pr_event,
            (
                IssueCommentEvent,
                IssueCommentGitlabEvent,
            ): self._check_issue_comment_event,
        }

        # Administrators
        user_login = getattr(event, "user_login", None)
        if user_login and user_login in service_config.admins:
            logger.info(f"{user_login} is admin, you shall pass.")
            return True

        for related_events, callback in CALLBACKS.items():
            if isinstance(event, related_events):
                return callback(event, project, service_config, job_configs)

        msg = f"Failed to validate account: Unrecognized event type {type(event)!r}."
        logger.error(msg)
        raise PackitException(msg)
