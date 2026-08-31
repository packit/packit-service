# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from typing import Optional

from fasjson_client import Client
from fasjson_client.errors import APIError
from packit.api import PackitAPI
from packit.exceptions import PackitCommandFailedError

from packit_service.config import ServiceConfig
from packit_service.constants import (
    FASJSON_URL,
    NOTIFICATION_REPO,
)
from packit_service.models import AllowlistModel, AllowlistStatus

logger = logging.getLogger(__name__)


class Allowlist:
    """
    Core allowlist functionality for managing namespace approval/denial.

    This class provides static and instance methods for checking and managing
    namespace allowlist entries without depending on job helpers or event handlers.
    """

    def __init__(self, service_config: ServiceConfig):
        self.service_config = service_config

    @staticmethod
    def _strip_protocol_and_add_git(url: Optional[str]) -> Optional[str]:
        """
        Remove the protocol from the URL and add .git suffix.

        Args:
            url (Optional[str]): URL to remove protocol from and add .git suffix to.

        Returns:
            URL without the protocol with added .git suffix. If not given URL returns
            None.
        """
        if not url:
            return None
        return url.split("://")[1] + ".git"

    def init_kerberos_ticket(self):
        """
        Try to init kerberos ticket.

        Returns:
            Whether the initialisation was successful.
        """
        try:
            logger.debug("Initialising Kerberos ticket so that we can use fasjson API.")
            PackitAPI(
                config=self.service_config,
                package_config=None,
            ).init_kerberos_ticket()
        except PackitCommandFailedError as ex:
            msg = f"Kerberos authentication error: {ex.stderr_output}"
            logger.error(msg)
            return False

        return True

    def is_github_username_from_fas_account_matching(self, fas_account, sender_login):
        """
        Compares the Github username from the FAS account
        to the username of the one who triggered the installation.

        Args:
            fas_account: FAS account for which we will get the account info.
            sender_login: Login of the user that will be checked for be match
                            against info from FAS.

        Returns:
            True if there was a match found. False if we were not able to run kinit or
            the check for match was not successful.
        """
        if not self.init_kerberos_ticket():
            return False

        logger.info(
            f"Going to check match for Github username from FAS account {fas_account} and"
            f" Github account {sender_login}.",
        )
        client = Client(FASJSON_URL)
        try:
            user_info = client.get_user(username=fas_account).result
        # e.g. User not found
        except APIError as e:
            logger.debug(f"We were not able to get the user: {e}")
            return False

        is_private = user_info.get("is_private")
        if is_private:
            logger.debug("The account is private.")
            return False

        github_username = user_info.get("github_username")
        if github_username:
            logger.debug(
                f"github_username from FAS account {fas_account}: {github_username}",
            )
            return github_username == sender_login

        logger.debug("github_username not set.")
        return False

    @staticmethod
    def approve_namespace(namespace: str):
        """
        Approve namespace manually.

        Args:
            namespace (str): Namespace in the format of `github.com/namespace` or
                `github.com/namespace/repository.git`.
        """
        AllowlistModel.add_namespace(
            namespace=namespace,
            status=AllowlistStatus.approved_manually.value,
        )

        logger.info(f"Account {namespace!r} approved successfully.")

    @staticmethod
    def deny_namespace(namespace: str):
        """
        Deny namespace.

        Args:
            namespace (str): Namespace in the format of `github.com/namespace` or
                `github.com/namespace/repository.git`.
        """
        AllowlistModel.add_namespace(namespace=namespace, status=AllowlistStatus.denied)

        logger.info(f"Account {namespace!r} denied successfully.")

    @staticmethod
    def is_namespace_or_parent_approved(namespace: str) -> bool:
        """
        Checks if namespace or any parent namespace is approved in the allowlist.

        Args:
            namespace (str): Namespace in format `example.com/namespace/repository.git`,
                where `/repository.git` is optional.

        Returns:
            `True` if namespace is approved, `False` otherwise.
        """
        if not namespace:
            return False

        separated_path = [namespace, None]
        while len(separated_path) > 1:
            if matching_namespace := AllowlistModel.get_namespace(separated_path[0]):
                status = AllowlistStatus(matching_namespace.status)
                if status != AllowlistStatus.waiting:
                    return status in (
                        AllowlistStatus.approved_automatically,
                        AllowlistStatus.approved_manually,
                    )

            separated_path = separated_path[0].rsplit("/", 1)

        logger.info(f"Could not find approved entry for: {namespace}")
        return False

    @staticmethod
    def is_namespace_or_parent_denied(namespace: str) -> bool:
        """
        Checks if namespace or any parent namespace is denied in the allowlist.

        Args:
            namespace (str): Namespace in format `example.com/namespace/repository.git`,
                where `/repository.git` is optional.

        Returns:
            `True` if namespace is approved, `False` otherwise.
        """
        if not namespace:
            return False

        separated_path = [namespace, None]
        while len(separated_path) > 1:
            if matching_namespace := AllowlistModel.get_namespace(separated_path[0]):
                status = AllowlistStatus(matching_namespace.status)
                if status == AllowlistStatus.denied:
                    logger.info(f"Namespace {namespace} is denied.")
                    return True

            separated_path = separated_path[0].rsplit("/", 1)

        logger.info(f"Could not find denied entry for: {namespace}")
        return False

    @staticmethod
    def is_denied(namespace: str) -> bool:
        model = AllowlistModel.get_namespace(namespace)
        return bool(model) and model.status == AllowlistStatus.denied

    @staticmethod
    def remove_namespace(namespace: str) -> bool:
        """
        Remove namespace from the allowlist.

        Args:
            namespace (str): Namespace to be removed in format of `github.com/namespace`
                or `github.com/namespace/repository.git` if for specific repository.

        Returns:
            `True` if the namespace was in the allowlist before, `False` otherwise.
        """
        if not AllowlistModel.get_namespace(namespace):
            logger.info(f"Namespace {namespace!r} does not exist!")
            return False

        AllowlistModel.remove_namespace(namespace)
        logger.info(f"Namespace {namespace!r} removed from allowlist!")

        return True

    @staticmethod
    def get_namespaces_by_status(status: AllowlistStatus) -> list[str]:
        return [account.namespace for account in AllowlistModel.get_by_status(status.value)]

    @staticmethod
    def waiting_namespaces() -> list[str]:
        """
        Get namespaces waiting for approval.

        Returns:
            List of namespaces that are waiting for approval.
        """
        return Allowlist.get_namespaces_by_status(AllowlistStatus.waiting)

    @staticmethod
    def denied_namespaces() -> list[str]:
        """
        Get denied namespace.

        Returns:
            List of namespaces that are denied.
        """
        return Allowlist.get_namespaces_by_status(AllowlistStatus.denied)

    def get_approval_issue(self, namespace) -> Optional[str]:
        for issue in self.service_config.get_project(
            url=NOTIFICATION_REPO,
        ).get_issue_list(author=self.service_config.get_github_account_name()):
            if issue.title.strip().endswith(f" {namespace} needs to be approved."):
                return issue.url
        return None
