# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from typing import Optional

from ogr.abstract import GitProject
from packit.config import (
    PackageConfig,
    get_package_config_from_repo,
)
from packit.exceptions import (
    PackitConfigException,
    PackitMissingConfigException,
)

from packit_service.config import ServiceConfig
from packit_service.constants import (
    CONTACTS_URL,
    DOCS_HOW_TO_CONFIGURE_URL,
    DOCS_VALIDATE_CONFIG,
    DOCS_VALIDATE_HOOKS,
)
from packit_service.worker.reporting import comment_without_duplicating, create_issue_if_needed

logger = logging.getLogger(__name__)


class PackageConfigGetter:
    @staticmethod
    def get_package_config_from_repo(
        project: GitProject,
        reference: Optional[str] = None,
        base_project: Optional[GitProject] = None,
        pr_id: Optional[int] = None,
        fail_when_missing: bool = True,
    ) -> Optional[PackageConfig]:
        """
        Get the package config and catch the invalid config scenario and possibly no-config scenario
        """

        if not base_project and not project:
            return None

        project_to_search_in = base_project or project
        try:
            package_config: PackageConfig = get_package_config_from_repo(
                project=project_to_search_in,
                ref=reference,
                package_config_path=ServiceConfig.get_service_config().package_config_path_override,
            )
            if not package_config and fail_when_missing:
                raise PackitMissingConfigException(
                    f"No config file for packit (e.g. `.packit.yaml`) found in "
                    f"{project_to_search_in.full_repo_name} on commit {reference}",
                )
        except PackitConfigException as ex:
            message = (
                f"{ex}\n\n"
                if isinstance(ex, PackitMissingConfigException)
                else f"Failed to load packit config file:\n```\n{ex}\n```\n"
            )

            message += (
                "For more info, please check out "
                f"[the documentation]({DOCS_HOW_TO_CONFIGURE_URL}) "
                "or [contact the Packit team]"
                f"({CONTACTS_URL}). You can also use "
                f"our CLI command [`config validate`]({DOCS_VALIDATE_CONFIG}) or our "
                f"[pre-commit hooks]({DOCS_VALIDATE_HOOKS}) for validation of the configuration."
            )

            if pr_id:
                comment_without_duplicating(body=message, pr_or_issue=project.get_pr(pr_id))
            elif created_issue := create_issue_if_needed(
                project,
                title="Invalid config",
                message=message,
            ):
                logger.debug(
                    f"Created issue for invalid packit config: {created_issue.url}",
                )
            raise ex

        return package_config
