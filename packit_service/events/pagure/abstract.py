# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from logging import getLogger
from typing import Optional

from packit.config import PackageConfig

from packit_service.package_config_getter import PackageConfigGetter

from ..abstract.base import ForgeIndependent

logger = getLogger(__name__)


class PagureEvent(ForgeIndependent):
    def __init__(self, project_url: str, pr_id: Optional[int] = None, **kwargs):
        super().__init__(pr_id=pr_id)
        self.project_url: str = project_url
        self.git_ref: Optional[str] = None  # git ref that can be 'git checkout'-ed
        self.identifier: Optional[str] = (
            None  # will be shown to users -- e.g. in logs or in the copr-project name
        )

    def get_packages_config(self) -> Optional[PackageConfig]:
        logger.debug(
            f"Getting packages_config:\n"
            f"\tproject: {self.project}\n"
            f"\tdefault_branch: {self.project.default_branch}\n",
        )

        return PackageConfigGetter.get_package_config_from_repo(
            base_project=None,
            project=self.project,
            pr_id=None,
            reference=self.project.default_branch,
            fail_when_missing=self.fail_when_config_file_missing,
        )
