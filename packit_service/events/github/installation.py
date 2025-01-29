# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from typing import Optional, Union

from packit_service.models import AllowlistStatus

from ..event import Event


class Installation(Event):
    def __init__(
        self,
        installation_id: int,
        account_login: str,
        account_id: int,
        account_url: str,
        account_type: str,
        created_at: Union[int, float, str],
        repositories: list[str],
        sender_id: int,
        sender_login: str,
        status: AllowlistStatus = AllowlistStatus.waiting,
    ):
        super().__init__(created_at)
        self.installation_id = installation_id
        self.actor = account_login
        # account == namespace (user/organization) into which the app has been installed
        self.account_login = account_login
        self.account_id = account_id
        self.account_url = account_url
        self.account_type = account_type
        # repos within the account/namespace in the scope of the installation
        self.repositories = repositories
        # sender == user who installed the app into 'account'
        self.sender_id = sender_id
        self.sender_login = sender_login
        self.status = status

    @classmethod
    def event_type(cls) -> str:
        return "github.installation.Installation"

    @classmethod
    def from_event_dict(cls, event: dict):
        return Installation(
            installation_id=event.get("installation_id"),
            account_login=event.get("account_login"),
            account_id=event.get("account_id"),
            account_url=event.get("account_url"),
            account_type=event.get("account_type"),
            created_at=event.get("created_at"),
            repositories=event.get("repositories"),
            sender_id=event.get("sender_id"),
            sender_login=event.get("sender_login"),
        )

    def get_dict(self, default_dict: Optional[dict] = None) -> dict:
        result = super().get_dict()
        result["status"] = result["status"].value
        return result

    @property
    def packages_config(self):
        return None

    @property
    def project(self):
        return self.get_project()

    def get_project(self):
        return None

    # [SAFETY] There is no base project associated with the GitHub Installation.
    @property
    def base_project(self):
        return None

    # [SAFETY] We only register installation in the database, there are no
    # actions being done, other than creating an issue in our »own« repository
    # to verify the user.
    def get_packages_config(self):
        return None
