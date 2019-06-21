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

import requests
import logging

from frambo.dict_in_redis import PersistentDict

logger = logging.getLogger(__name__)


class GithubAppData:
    def __init__(self, installation_id: int, account_login: str, account_id: int,
                 account_url: str, account_type: str, created_at: int, sender_id: int,
                 sender_login: str, whitelisted: str = ""):
        self.installation_id = installation_id
        self.account_login = account_login
        self.account_id = account_id
        self.account_url = account_url
        self.account_type = account_type
        self.created_at = created_at
        self.sender_id = sender_id
        self.sender_login = sender_login
        self.whitelisted = whitelisted


class Whitelist:

    def __init__(self):
        self.db = PersistentDict(hash_name="whitelist")

    def _is_packager(self, account_login: str) -> bool:
        """
        If GitHub username is same as FAS username this method checks if user is packager.
        User is considered to be packager when he/she has the badge:
         `If you build it... (Koji Success I)`
        :param account_login: str, Github username
        :return: bool
        """

        url = f"https://badges.fedoraproject.org/user/{account_login}/json"
        data = requests.get(url)
        assertions = data.json().get("assertions")
        if not assertions:
            return False
        for item in data.json().get("assertions"):
            if "Succesfully completed a koji build." in item.get("description"):
                return True
        return False

    def add_account(self, github_app, force=False) -> bool:
        """
        Add account to whitelist
        :param github_app: github app installation info
        :param force: manual insert
        :return:
        """
        if not force:
            if self._is_packager(github_app.account_login):
                github_app.whitelisted = "auto"
                self.db[github_app.account_login] = github_app.get_dict()
                logger.info(f"Account {github_app.account_login} whitelisted!")
                return True
            else:
                logger.error("Failed to verify that user is Fedora packager. "
                             "This could be caused by different github username than FAS username "
                             "or that user is not a packager.")
                return False

        # force option is provided
        github_app.whitelisted = "manual"
        self.db[github_app.sender_login] = github_app.get_dict()

        return True

    def remove_user(self, account_login):
        del self.db[account_login]
        # TODO: delete all artifacts from copr
