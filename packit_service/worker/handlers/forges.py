# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
This file defines classes for job handlers specific for Github hooks
TODO: The build and test handlers are independent and should be moved away.
"""

import logging

from packit.config import (
    Deployment,
    JobConfig,
)
from packit.config.package_config import PackageConfig

from packit_service.constants import CONTACTS_URL, DOCS_APPROVAL_URL, NOTIFICATION_REPO
from packit_service.events import (
    github,
)
from packit_service.models import (
    AllowlistModel,
    AllowlistStatus,
    GithubInstallationModel,
)
from packit_service.utils import get_packit_commands_from_comment
from packit_service.worker.allowlist import Allowlist
from packit_service.worker.checker.abstract import Checker
from packit_service.worker.checker.forges import IsIssueInNotificationRepoChecker
from packit_service.worker.handlers.abstract import (
    JobHandler,
    TaskName,
    reacts_to,
)
from packit_service.worker.mixin import (
    ConfigFromEventMixin,
    GetIssueMixin,
    PackitAPIWithDownstreamMixin,
)
from packit_service.worker.reporting import create_issue_if_needed
from packit_service.worker.result import TaskResults

logger = logging.getLogger(__name__)


@reacts_to(event=github.installation.Installation)
class GithubAppInstallationHandler(
    JobHandler,
    ConfigFromEventMixin,
    PackitAPIWithDownstreamMixin,
):
    task_name = TaskName.installation

    # https://developer.github.com/v3/activity/events/types/#events-api-payload-28

    def __init__(
        self,
        package_config: PackageConfig,
        job_config: JobConfig,
        event: dict,
    ):
        super().__init__(
            package_config=package_config,
            job_config=job_config,
            event=event,
        )

        self.installation_event = github.installation.Installation.from_event_dict(event)
        self.account_type = self.installation_event.account_type
        self.account_login = self.installation_event.account_login
        self.sender_login = self.installation_event.sender_login
        self._project = self.service_config.get_project(url=NOTIFICATION_REPO)

    def run(self) -> TaskResults:
        """
        Discover information about organization/user which wants to install packit on his repository
        Try to allowlist automatically if mapping from github username to FAS account can prove a
        match.

        Returns:
            Result of the run task.
        """
        previous_installation = GithubInstallationModel.get_by_account_login(
            self.installation_event.account_login,
        )
        previous_sender_login = (
            previous_installation.sender_login if previous_installation else None
        )

        GithubInstallationModel.create_or_update(event=self.installation_event)
        # try to add user to allowlist
        allowlist = Allowlist(self.service_config)
        namespace = f"github.com/{self.account_login}"
        # if the namespace was not on our allowlist (in any state) or
        # the app was reinstalled by someone else than previously
        # and the namespace was not approved, create an issue in notifications repo
        existing_allowlist_entry = AllowlistModel.get_namespace(namespace)
        if not existing_allowlist_entry or (
            previous_installation is not None
            and not allowlist.is_namespace_or_parent_approved(namespace)
            and previous_sender_login != self.sender_login
        ):
            if allowlist.is_github_username_from_fas_account_matching(
                fas_account=self.sender_login,
                sender_login=self.sender_login,
            ):
                AllowlistModel.add_namespace(
                    namespace,
                    AllowlistStatus.approved_automatically.value,
                    self.sender_login,
                )
                msg = f"Account {self.account_login} approved automatically."
                logger.debug(msg)
                return TaskResults(success=True, details={"msg": msg})

            # Create an issue in our repository, so we are notified when someone install the app
            create_issue_if_needed(
                project=self.project,
                title=f"{self.account_type} {self.account_login} needs to be approved.",
                message=(
                    f"Hi @{self.sender_login}, we need to approve you in "
                    f"order to start using Packit-as-a-Service"
                    f"{'-stg' if self.service_config.deployment == Deployment.stg else ''}. "
                    "We are now onboarding Fedora contributors who have a valid "
                    "[Fedora Account System](https://fedoraproject.org/wiki/Account_System) "
                    "account. \n\nHowever, your GitHub username does not match the FAS account "
                    "username or you currently don't have the `GitHub Username` field set in your "
                    "FAS account or your profile is private. "
                    "Please, set the `GitHub Username`"
                    " field in the settings of the FAS account (if you don't have it set already)"
                    f" and provide it in a **comment in this issue** as \n\n"
                    f"```\n{self.service_config.comment_command_prefix} verify-fas "
                    "<my-fas-username>\n``` \n\n(and make sure your profile [is not private]"
                    f"(https://accounts.fedoraproject.org/user/{self.sender_login}/"
                    "settings/profile/#is_private)). "
                    "We automatically check for the match between the `GitHub"
                    " Username` field in the provided FAS account and the Github account that "
                    "triggers the verification and approve you for using our service if they "
                    "match.\n\n"
                    "Here is a link to the settings page:\n"
                    f"https://accounts.fedoraproject.org/user/{self.sender_login}"
                    "/settings/profile/#github (update the FAS account in the URL if needed)."
                    "\n\n"
                    "For more info, please check out the documentation: "
                    f"{DOCS_APPROVAL_URL}"
                ),
                add_packit_prefix=False,
            )
            msg = f"{self.account_type} {self.account_login} needs to be approved manually!"
            AllowlistModel.add_namespace(namespace, AllowlistStatus.waiting.value)
        else:
            msg = f"{self.account_type} {self.account_login} is already on our allowlist."

        logger.info(msg)
        return TaskResults(success=True, details={"msg": msg})


@reacts_to(event=github.issue.Comment)
class GithubFasVerificationHandler(
    JobHandler,
    PackitAPIWithDownstreamMixin,
    GetIssueMixin,
):
    task_name = TaskName.github_fas_verification

    def __init__(
        self,
        package_config: PackageConfig,
        job_config: JobConfig,
        event: dict,
    ):
        super().__init__(
            package_config=package_config,
            job_config=job_config,
            event=event,
        )
        self.sender_login = self.data.actor
        self.comment = self.data.event_dict.get("comment")

    @staticmethod
    def get_checkers() -> tuple[type[Checker], ...]:
        return (IsIssueInNotificationRepoChecker,)

    def run(self) -> TaskResults:
        """
        Discover information about organization/user which wants to verify the FAS account.
        Allowlist automatically if mapping from github username to FAS account can prove a match.

        Returns:
            TaskResults
        """
        logger.debug(
            f"Going to run verification of FAS account triggered by comment: {self.comment}",
        )
        # e.g. User Bebaabeni needs to be approved.
        _, account_login, _ = self.issue.title.split(maxsplit=2)
        original_sender_login = GithubInstallationModel.get_by_account_login(
            account_login,
        ).sender_login
        logger.debug(f"Original sender login: {original_sender_login}")
        namespace = f"github.com/{account_login}"
        command_parts = get_packit_commands_from_comment(
            self.comment,
            self.service_config.comment_command_prefix,
        )
        # we expect ["verify-fas", "fas-account"]
        if len(command_parts) != 2:
            msg = "Incorrect format of the Packit verification comment command."
            logger.debug(msg)
            self.issue.comment(
                f"{msg} The expected format: `/packit verify-fas my-fas-account`",
            )
            return TaskResults(success=False, details={"msg": msg})

        fas_account = command_parts[1]

        if original_sender_login != self.sender_login:
            msg = (
                "Packit verification comment command not created by the person who "
                "installed the application."
            )
            logger.debug(msg)
            self.issue.comment(msg)
            return TaskResults(success=True, details={"msg": msg})

        return self.verify(namespace=namespace, fas_account=fas_account)

    def verify(self, namespace: str, fas_account: str) -> TaskResults:
        """
        Verify the information about namespace in our allowlist and try
        to match fas_account.
        """
        allowlist = Allowlist(service_config=self.service_config)
        if (
            approved := allowlist.is_namespace_or_parent_approved(namespace)
        ) or allowlist.is_denied(namespace):
            msg = f"Namespace `{namespace}` {'was already approved' if approved else 'is denied'}."
            logger.debug(msg)
            self.issue.comment(msg)
            if approved:
                self.issue.close()
            return TaskResults(success=True, details={"msg": msg})

        if allowlist.is_github_username_from_fas_account_matching(
            fas_account=fas_account,
            sender_login=self.sender_login,
        ):
            msg = (
                f"Namespace `{namespace}` approved successfully using FAS account `{fas_account}`!"
            )
            logger.debug(msg)
            self.issue.comment(msg)
            self.issue.close()

            # store the fas account in the DB for the namespace
            AllowlistModel.add_namespace(
                namespace,
                AllowlistStatus.approved_automatically.value,
                fas_account,
            )

        else:
            logger.debug(
                f"No match between FAS account `{fas_account}` "
                f"and GitHub user `{self.sender_login}` found.",
            )
            msg = (
                f"We were not able to find a match between the GitHub Username field "
                f"in the FAS account `{fas_account}` and GitHub user `{self.sender_login}`. "
                f"Please, check that you have set "
                f"[the field]"
                f"(https://accounts.fedoraproject.org/user/{fas_account}/settings/profile/#github) "
                f"correctly and that your profile [is not private]"
                f"(https://accounts.fedoraproject.org/user/{fas_account}/"
                "settings/profile/#is_private)"
                f" and try again or contact "
                f"[us]({CONTACTS_URL})."
            )
            self.issue.comment(msg)

        return TaskResults(success=True, details={"msg": msg})
