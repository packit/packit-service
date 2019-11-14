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
from typing import Optional, Any

from fedora.client.fas2 import AccountSystem
from fedora.client import AuthError, FedoraServiceError
from ogr.abstract import GitProject
from packit.config import JobTriggerType
from persistentdict.dict_in_redis import PersistentDict

from packit_service.constants import FAQ_URL
from packit_service.service.events import (
    PullRequestEvent,
    ReleaseEvent,
    WhitelistStatus,
    InstallationEvent,
)
from packit_service.worker.handler import BuildStatusReporter, PRCheckName

logger = logging.getLogger(__name__)


class Whitelist:
    def __init__(self, fas_user: str = None, fas_password: str = None):
        self.db = PersistentDict(hash_name="whitelist")
        self._fas: AccountSystem = AccountSystem(
            username=fas_user, password=fas_password
        )

    def _is_packager(self, account_login: str) -> bool:
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
            if membership.get("name") == "packager":
                logger.info(f"User {account_login!r} is a packager in Fedora!")
                return True

        logger.info(f"Cannot verify whether {account_login!r} is a packager in Fedora.")
        return False

    def get_account(self, account_name: str) -> Optional[dict]:
        """
        Get selected account from DB, return None if it's not there

        :param account_name: account name for approval
        """
        account = self.db.get(account_name)
        if not account:
            return None
        # patch status
        db_status = account["status"]
        if db_status.startswith("WhitelistStatus"):
            account["status"] = db_status.split(".", 1)[1]
            self.db[account_name] = account
        return account

    def add_account(self, github_app: InstallationEvent) -> bool:
        """
        Add account to whitelist, if automatic verification of user
        (check if user is packager in fedora) fails, account is still inserted in whitelist
         with status : `waiting`.
         Then a scripts in files/scripts have to be executed for manual approval
        :param github_app: github app installation info
        :return: was the account (auto/already)-whitelisted?
        """
        if github_app.account_login in self.db:
            return True

        # Do the DB insertion as a first thing to avoid issue#42
        github_app.status = WhitelistStatus.waiting
        self.db[github_app.account_login] = github_app.get_dict()

        # we want to verify if user who installed the application (sender_login) is packager
        if self._is_packager(github_app.sender_login):
            github_app.status = WhitelistStatus.approved_automatically
            self.db[github_app.account_login] = github_app.get_dict()
            logger.info(f"Account {github_app.account_login} whitelisted!")
            return True
        else:
            logger.info(
                "Failed to verify that user is Fedora packager. "
                "This could be caused by different github username than FAS username "
                "or that user is not a packager."
                f"Account {github_app.account_login} inserted "
                "to whitelist with status: waiting for approval"
            )
            return False

    def approve_account(self, account_name: str) -> bool:
        """
        Approve user manually
        :param account_name: account name for approval
        :return:
        """
        account = self.get_account(account_name) or {}
        account["status"] = WhitelistStatus.approved_manually.value
        self.db[account_name] = account
        logger.info(f"Account {account_name} approved successfully")
        return True

    def is_approved(self, account_name: str) -> bool:
        """
        Check if user is approved in the whitelist
        :param account_name:
        :return:
        """
        if account_name in self.db:
            account = self.get_account(account_name)
            db_status = account["status"]
            s = WhitelistStatus(db_status)
            return (
                s == WhitelistStatus.approved_automatically
                or s == WhitelistStatus.approved_manually
            )
        return False

    def remove_account(self, account_name: str) -> bool:
        """
        Remove account from whitelist.
        :param account_name: github login
        :return:
        """
        if account_name in self.db:
            del self.db[account_name]
            # TODO: delete all artifacts from copr
            logger.info(f"User: {account_name} removed from whitelist!")
            return True
        else:
            logger.info(f"User: {account_name} does not exists!")
            return False

    def accounts_waiting(self) -> list:
        """
        Get accounts waiting for approval
        :return: list of accounts waiting for approval
        """

        return [
            key
            for (key, item) in self.db.items()
            if WhitelistStatus(item["status"]) == WhitelistStatus.waiting
        ]

    def check_and_report(self, event: Optional[Any], project: GitProject) -> bool:
        """
        Check if account is approved and report status back in case of PR
        :param event: PullRequest and Release TODO: handle more
        :param project: GitProject
        :return:
        """
        account_name = None
        if isinstance(event, PullRequestEvent):
            account_name = event.base_repo_namespace
        if isinstance(event, ReleaseEvent):
            account_name = event.repo_namespace

        if account_name:
            if not self.is_approved(account_name):
                logger.error(f"User {account_name} is not approved on whitelist!")
                # TODO also check blacklist,
                # but for that we need to know who triggered the action
                if event.trigger == JobTriggerType.pull_request:
                    r = BuildStatusReporter(project, event.commit_sha, None)
                    msg = "Account is not whitelisted!"
                    r.report(
                        "failure",
                        msg,
                        url=FAQ_URL,
                        check_name=PRCheckName.get_build_check(),
                    )
                return False

        return True


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
