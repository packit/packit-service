# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from typing import Optional

from packit.config import PackageConfig

from .abstract.base import ForgeIndependent


class Request(ForgeIndependent):
    @classmethod
    def event_type(cls) -> str:
        return "onboarding.Request"

    def __init__(
        self,
        package: str,
        open_pr: bool = True,
    ):
        super().__init__(project_url=f"https://src.fedoraproject.org/rpms/{package}")
        self.open_pr = open_pr

    def get_packages_config(self) -> Optional[PackageConfig]:
        return None
