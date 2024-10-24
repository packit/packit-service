# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
import logging
from enum import Enum

from ogr.abstract import AccessLevel, GitProject

logger = logging.getLogger(__name__)


class DistgitAllowedAccountsAlias(Enum):
    all_admins = "all_admins"
    all_committers = "all_committers"


class DistgitAccountsChecker:
    def __init__(
        self,
        project: GitProject,
        accounts_list: list[str],
        account_to_check: str,
    ):
        self.project = project
        self.accounts_list = accounts_list
        self.account_to_check = account_to_check

    @staticmethod
    def is_distgit_allowed_accounts_alias(value: str) -> bool:
        return any(value == alias.value for alias in DistgitAllowedAccountsAlias)

    def check_allowed_accounts(self) -> bool:
        """
        Check whether the account_to_check matches one of the values in accounts_list
        (considering the groups and aliases).
        """
        logger.info(
            f"Checking {self.account_to_check} in list of accounts: {self.accounts_list}",
        )

        direct_account_names = [
            value
            for value in self.accounts_list
            if not self.is_distgit_allowed_accounts_alias(value) and not value.startswith("@")
        ]

        # check the direct account names to prevent unneeded API interactions
        if self.account_to_check in direct_account_names:
            return True

        all_accounts = set()

        for value in self.accounts_list:
            if self.is_distgit_allowed_accounts_alias(value):
                all_accounts.update(self.expand_maintainer_alias(value))
            elif value.startswith("@"):
                try:
                    # remove @
                    group_name = value[1:]
                    group = self.project.service.get_group(group_name)
                    all_accounts.update(group.members)
                except Exception as ex:
                    logger.debug(
                        f"Exception while getting the members of group {value}: {ex!r}",
                    )
                    continue
            else:
                all_accounts.add(value)

        logger.debug(f"Expanded accounts list: {all_accounts}")
        return self.account_to_check in all_accounts

    def expand_maintainer_alias(self, alias: str) -> set[str]:
        """
        Expand the 'all_admins' and 'all_committers' aliases to users.
        """
        # see AccessLevel mapping
        # https://github.com/packit/ogr/blob/d183a6c6459231c2a60bacd6b827502c92a130ef/ogr/abstract.py#L1079
        # all_admins -> Pagure "admin" and "maintainer" access
        # all_committers -> on top of that "commit" access
        access_levels = [AccessLevel.maintain]

        if alias == DistgitAllowedAccountsAlias.all_committers.value:
            access_levels.extend([AccessLevel.admin, AccessLevel.push])

        accounts = self.project.get_users_with_given_access(access_levels)

        logger.debug(f"Expanded {alias}: {accounts}")
        return accounts
