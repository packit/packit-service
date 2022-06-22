# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
This file defines classes for job handlers specific for Github hooks
TODO: The build and test handlers are independent and should be moved away.
"""
import logging

from packit.config import (
    JobConfig,
    Deployment,
)
from packit.config.package_config import PackageConfig
from packit_service.models import (
    GithubInstallationModel,
)
from packit_service.worker.allowlist import Allowlist
from packit_service.worker.events import (
    InstallationEvent,
    IssueCommentEvent,
)
from packit_service.worker.handlers.abstract import (
    JobHandler,
    TaskName,
    reacts_to,
    get_packit_commands_from_comment,
)
from packit_service.worker.result import TaskResults

logger = logging.getLogger(__name__)


@reacts_to(event=InstallationEvent)
class GithubAppInstallationHandler(JobHandler):
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

        self.installation_event = InstallationEvent.from_event_dict(event)
        self.account_type = self.installation_event.account_type
        self.account_login = self.installation_event.account_login
        self.sender_login = self.installation_event.sender_login
        self._project = self.service_config.get_project(
            url="https://github.com/packit/notifications"
        )

    def run(self) -> TaskResults:
        """
        Discover information about organization/user which wants to install packit on his repository
        Try to allowlist automatically if mapping from github username to FAS account can prove a
        match.
        :return: TaskResults
        """
        GithubInstallationModel.create(event=self.installation_event)
        # try to add user to allowlist
        allowlist = Allowlist(self.service_config)
        if not allowlist.add_namespace(
            f"github.com/{self.account_login}", self.sender_login
        ):
            # Create an issue in our repository, so we are notified when someone install the app
            self.project.create_issue(
                title=f"{self.account_type} {self.account_login} needs to be approved.",
                body=(
                    f"Hi @{self.sender_login}, we need to approve you in "
                    f"order to start using Packit-as-a-Service"
                    f"{'-stg' if self.service_config.deployment == Deployment.stg else ''}. "
                    "We are now onboarding Fedora contributors who have a valid "
                    "[Fedora Account System](https://fedoraproject.org/wiki/Account_System) "
                    "account. \n\nIf you have such an account, please set the `GitHub Username`"
                    " field in the settings of the FAS account (if you don't have it set already)"
                    f" and provide it in a comment in this issue as "
                    f"`{self.service_config.comment_command_prefix} verify-fas "
                    "<my-fas-username>`. We automatically check for the match between the `GitHub"
                    " Username` field in the provided FAS account and the Github account that "
                    "triggers the verification and approve you for using our service if they "
                    "match.\n\n"
                    "Here is a link to the settings page:\n"
                    f"https://accounts.fedoraproject.org/user/{self.sender_login}"
                    "/settings/profile/#github (update the FAS account in the URL if needed)"
                    "\n\n"
                    "For more info, please check out the documentation: "
                    "https://packit.dev/docs/packit-service"
                ),
            )
            msg = f"{self.account_type} {self.account_login} needs to be approved manually!"
        else:
            msg = (
                f"{self.account_type} {self.account_login} is already on our allowlist."
            )

        logger.info(msg)
        return TaskResults(success=True, details={"msg": msg})


@reacts_to(event=IssueCommentEvent)
class GithubFasVerificationHandler(JobHandler):
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
        self._issue = None

    @property
    def issue(self):
        if not self._issue:
            self._issue = self.project.get_issue(self.data.issue_id)
        return self._issue

    def pre_check(self) -> bool:
        """
        Checks whether the Packit verification command is placed in
        packit/notifications repository in the issue our service created.
        """
        if not (
            self.project.namespace == "packit" and self.project.repo == "notifications"
        ):
            logger.debug(
                "Packit verification comment command not placed in packit/notifications repository."
            )
            return False

        issue_author = self.issue.author
        if (
            self.service_config.deployment == Deployment.prod
            and issue_author != "packit-as-a-service[bot]"
        ) or (
            self.service_config.deployment == Deployment.stg
            and issue_author != "packit-as-a-service-stg[bot]"
        ):
            logger.debug(
                f"Packit verification comment command placed on issue with author "
                f"other than our app: {issue_author}"
            )
            return False

        return True

    def run(self) -> TaskResults:
        """
        Discover information about organization/user which wants to verify the FAS account.
        Allowlist automatically if mapping from github username to FAS account can prove a match.

        Returns:
            TaskResults
        """
        logger.debug(
            f"Going to run verification of FAS account triggered by comment:"
            f" {self.comment}"
        )
        # e.g. User Bebaabeni needs to be approved.
        _, account_login, _ = self.issue.title.split(maxsplit=2)
        original_sender_login = GithubInstallationModel.get_by_account_login(
            account_login
        ).sender_login
        logger.debug(f"Original sender login: {original_sender_login}")
        namespace = f"github.com/{account_login}"
        command_parts = get_packit_commands_from_comment(
            self.comment, self.service_config.comment_command_prefix
        )
        # we expect ["verify-fas", "fas-account"]
        if len(command_parts) != 2:
            msg = "Incorrect format of the Packit verification comment command."
            logger.debug(msg)
            self.issue.comment(
                f"{msg} The expected format: `/packit verify-fas my-fas-account`"
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
        if allowlist.is_approved(namespace):
            msg = f"Namespace `{namespace}` was already approved."
            logger.debug(msg)
            self.issue.comment(msg)
            self.issue.close()
            return TaskResults(success=True, details={"msg": msg})

        if allowlist.verify_fas(
            namespace=namespace, sender_login=self.sender_login, fas_account=fas_account
        ):
            msg = (
                f"Namespace `{namespace}` approved successfully "
                f"using FAS account `{fas_account}`!"
            )
            logger.debug(msg)
            self.issue.comment(msg)
            self.issue.close()

        else:
            msg = (
                f"We were not able to find a match between the GitHub Username field "
                f"in the FAS account `{fas_account}` and GitHub user `{self.sender_login}`. "
                f"Please, check that you have set "
                f"[the field]"
                f"(https://accounts.fedoraproject.org/user/{fas_account}/settings/profile/#github) "
                f"correctly and try again or contact "
                f"[us](https://packit.dev/#contact)."
            )
            logger.debug(msg)
            self.issue.comment(msg)

        return TaskResults(success=True, details={"msg": msg})
