# MIT License
#
# Copyright (c) 2018-2019 Red Hat, Inc.
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
import logging
from typing import Optional, Any, List

from fedora.client import AuthError, FedoraServiceError
from fedora.client.fas2 import AccountSystem
from ogr.abstract import GitProject, CommitStatus
from packit.exceptions import PackitException
from persistentdict.dict_in_redis import PersistentDict

from packit_service.models import WhitelistModel

from packit_service.config import ServiceConfig
from packit_service.constants import FAQ_URL
from packit_service.service.events import (
    PullRequestEvent,
    PullRequestCommentEvent,
    IssueCommentEvent,
    ReleaseEvent,
    WhitelistStatus,
    InstallationEvent,
    CoprBuildEvent,
    TestingFarmResultsEvent,
    DistGitEvent,
    PushGitHubEvent,
    TheJobTriggerType,
    PushPagureEvent,
    PullRequestPagureEvent,
)
from packit_service.worker.build import CoprBuildJobHelper

logger = logging.getLogger(__name__)


class Whitelist:
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

    @staticmethod
    def get_account(account_name) -> Optional[WhitelistModel]:
        """
        Get selected account from DB, return None if it's not there
        :param account_name: account name for approval
        """
        return WhitelistModel.get_account(account_name=account_name)

    def add_account(self, event: InstallationEvent) -> bool:
        """
        Add account to whitelist.
        Status is set to 'waiting' or to 'approved_automatically'
        if the account is a packager in Fedora.
        :param event: Github app installation info
        :return: was the account (auto/already)-whitelisted?
        """
        if self.is_approved(event.account_login):
            return True

        WhitelistModel.add_account(event.account_login, WhitelistStatus.waiting.value)

        if self._signed_fpca(event.sender_login):
            event.status = WhitelistStatus.approved_automatically
            WhitelistModel.add_account(event.account_login, event.status.value)
            return True

        return False

    @staticmethod
    def approve_account(account_name: str) -> bool:
        """
        Approve user manually
        :param account_name: account name for approval
        :return:
        """
        WhitelistModel.add_account(
            account_name=account_name, status=WhitelistStatus.approved_manually.value
        )

        logger.info(f"Account {account_name} approved successfully")
        return True

    @staticmethod
    def is_approved(account_name: str) -> bool:
        """
        Check if user is approved in the whitelist
        :param account_name:
        :return:
        """
        account = WhitelistModel.get_account(account_name)
        if account:
            db_status = account.status
            s = WhitelistStatus(db_status)
            return (
                s == WhitelistStatus.approved_automatically
                or s == WhitelistStatus.approved_manually
            )

        return False

    @staticmethod
    def remove_account(account_name: str) -> bool:
        """
        Remove account from whitelist.
        :param account_name: github login
        :return:
        """

        account_existed = False

        if WhitelistModel.get_account(account_name):
            WhitelistModel.remove_account(account_name)
            logger.info(f"Account: {account_name} removed from postgres whitelist!")
            account_existed = True

        if not account_existed:
            logger.info(f"Account: {account_name} does not exists!")

        return account_existed

    @staticmethod
    def accounts_waiting() -> List[str]:
        """
        Get accounts waiting for approval
        :return: list of accounts waiting for approval
        """
        return [
            account.account_name
            for account in WhitelistModel.get_accounts_by_status(
                WhitelistStatus.waiting.value
            )
        ]

    def check_and_report(
        self, event: Optional[Any], project: GitProject, config: ServiceConfig
    ) -> bool:
        """
        Check if account is approved and report status back in case of PR
        :param config: service config
        :param event: PullRequest and Release TODO: handle more
        :param project: GitProject
        :return:
        """
        # TODO: modify event hierarchy so we can use some abstract classes instead
        if isinstance(event, (ReleaseEvent, PushGitHubEvent, PushPagureEvent)):
            account_name = event.repo_namespace
            if not account_name:
                raise KeyError(f"Failed to get account_name from {type(event)}")
            if not self.is_approved(account_name):
                logger.info(f"Refusing release event on not whitelisted repo namespace")
                return False
            return True
        if isinstance(
            event,
            (CoprBuildEvent, TestingFarmResultsEvent, DistGitEvent, InstallationEvent),
        ):
            return True
        if isinstance(
            event, (PullRequestEvent, PullRequestPagureEvent, PullRequestCommentEvent)
        ):
            account_name = event.user_login
            if not account_name:
                raise KeyError(f"Failed to get account_name from {type(event)}")
            namespace = event.base_repo_namespace
            # FIXME:
            #  Why check account_name when we whitelist namespace only (in whitelist.add_account())?
            if not (self.is_approved(account_name) or self.is_approved(namespace)):
                msg = f"Neither account {account_name} nor owner {namespace} are on our whitelist!"
                logger.error(msg)
                # TODO also check blacklist,
                # but for that we need to know who triggered the action
                if event.trigger == TheJobTriggerType.pr_comment:
                    project.pr_comment(event.pr_id, msg)
                else:
                    job_helper = CoprBuildJobHelper(
                        config=config,
                        package_config=event.get_package_config(),
                        project=project,
                        event=event,
                    )
                    msg = "Account is not whitelisted!"  # needs to be shorter
                    job_helper.report_status_to_all(
                        description=msg, state=CommitStatus.error, url=FAQ_URL
                    )
                return False
            # TODO: clear failing check when present
            return True
        if isinstance(event, IssueCommentEvent):
            account_name = event.github_login
            if not account_name:
                raise KeyError(f"Failed to get account_name from {type(event)}")
            namespace = event.base_repo_namespace
            # FIXME:
            #  Why check account_name when we whitelist namespace only (in whitelist.add_account())?
            if not (self.is_approved(account_name) or self.is_approved(namespace)):
                msg = f"Neither account {account_name} nor owner {namespace} are on our whitelist!"
                logger.error(msg)
                project.issue_comment(event.issue_id, msg)
                # TODO also check blacklist,
                # but for that we need to know who triggered the action
                return False
            return True

        msg = f"Failed to validate account: Unrecognized event type {type(event)}."
        logger.error(msg)
        raise PackitException(msg)


class Blacklist:
    def __init__(self):
        self.db = PersistentDict(hash_name="blacklist")

    def add_account(self, account_name: str, reason: str) -> bool:
        """
        Add user to blacklist, forbid to trigger copr builds
        :param account_name: str, account
        :param reason: str, reason of ban
        :return:
        """
        self.db[account_name] = {"reason": reason}
        logger.info(f"User: {account_name} added to blacklist!")

        return True

    def remove_account(self, account_name: str) -> bool:
        """
        Remove user from blacklist, allowing him/her to trigger copr builds again
        :param account_name: str, github login
        :return: None
        """
        if account_name in self.db:
            del self.db[account_name]
            logger.info(f"User: {account_name} removed from blacklist!")
            return True
        else:
            logger.info(f"User: {account_name} does not exists!")
            return False
