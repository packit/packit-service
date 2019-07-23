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
from typing import Optional, Any

import requests
import logging

from frambo.dict_in_redis import PersistentDict
from ogr.abstract import GitProject
from packit.config import JobTriggerType

from packit_service.constants import FAQ_URL
from packit_service.service.events import (
    PullRequestEvent,
    ReleaseEvent,
    WhitelistStatus,
    InstallationEvent,
)
from packit_service.worker.handler import BuildStatusReporter

logger = logging.getLogger(__name__)


class Whitelist:
    def __init__(self):
        self.db = PersistentDict(hash_name="whitelist")

    @staticmethod
    def _is_packager(account_login: str) -> bool:
        """
        If GitHub username is same as FAS username this method checks if user is packager.
        User is considered to be packager when he/she has the badge:
         `If you build it... (Koji Success I)`
        :param account_login: str, Github username
        :return: bool
        """

        url = f"https://badges.fedoraproject.org/user/{account_login}/json"
        data = requests.get(url)
        if not data:
            return False
        assertions = data.json().get("assertions")
        if not assertions:
            return False
        for item in assertions:
            if "Succesfully completed a koji build." in item.get("description"):
                logger.info(f"User: {account_login} is a packager in Fedora!")
                return True
        logger.info(
            f"Cannot verify whether user: {account_login} is a packager in Fedora."
        )
        return False

    def add_account(self, github_app: InstallationEvent) -> bool:
        """
        Add account to whitelist, if automatic verification of user
        (check if user is packager in fedora) fails, account is still inserted in whitelist
         with status : `waiting`.
         Then a scripts in files/scripts have to be executed for manual approval
        :param github_app: github app installation info
        :return:
        """
        # we want to verify if user who installed the application is packager
        if Whitelist._is_packager(github_app.sender_login):
            github_app.status = WhitelistStatus.approved_automatically
            self.db[github_app.account_login] = github_app.get_dict()
            logger.info(f"Account {github_app.account_login} whitelisted!")
            return True
        else:
            logger.error(
                "Failed to verify that user is Fedora packager. "
                "This could be caused by different github username than FAS username "
                "or that user is not a packager."
            )
            github_app.status = WhitelistStatus.waiting
            self.db[github_app.account_login] = github_app.get_dict()
            logger.info(
                f"Account {github_app.account_login} inserted "
                f"to whitelist with status: waiting for approval"
            )
            return False

    def approve_account(self, account_name: str) -> bool:
        """
        Approve user manually
        :param account_name: account name for approval
        :return:
        """
        account = self.db[account_name] or {}
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
            s = WhitelistStatus(self.db[account_name]["status"])
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
                    r.report("failure", msg, url=FAQ_URL)
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
