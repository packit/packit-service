# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging

from packit_service.config import ServiceConfig

logger = logging.getLogger(__name__)


class FedoraCIConfig:
    """Per-project Fedora CI configuration.

    Single point for all per-project opt-in/opt-out decisions.
    Currently reads from ServiceConfig. Can be changed to load
    from an external config repo in the future.
    (see https://github.com/packit/packit-service/issues/3025)
    """

    _instance = None

    def __init__(self, service_config: ServiceConfig):
        self._service_config = service_config
        self._settings = service_config.fedora_ci

    @classmethod
    def get_config(cls) -> "FedoraCIConfig":
        if cls._instance is None:
            cls._instance = cls(ServiceConfig.get_service_config())
        return cls._instance

    def is_project_enabled(self, project_url: str) -> bool:
        """Determine if project should be processed as Fedora CI based on configuration.

        When fedora_ci_run_by_default=False (opt-in mode):
            - Only projects in enabled_projects are processed
        When fedora_ci_run_by_default=True (opt-out mode):
            - Only projects under https://src.fedoraproject.org/rpms are processed
            - Projects in disabled_projects are excluded

        Args:
            project_url: The project URL to check

        Returns:
            True if the project should be processed as Fedora CI, False otherwise
        """
        if self._service_config.fedora_ci_run_by_default:
            # Opt-out mode: run by default for Fedora RPMs unless explicitly disabled
            enabled = (
                project_url.startswith(
                    ("https://src.fedoraproject.org/rpms", "https://src.fedoraproject.org/tests")
                )
            ) and project_url not in self._settings.disabled_projects
        else:
            # Opt-in mode: only run if explicitly enabled
            enabled = project_url in self._settings.enabled_projects
        if not enabled:
            logger.info(f"Fedora CI not enabled for project {project_url}.")
        return enabled

    def is_eln_enabled(self, project_url: str) -> bool:
        """Should ELN builds/tests run for this project?"""
        if project_url in self._settings.disabled_projects_for_eln:
            logger.info(f"ELN disabled for project {project_url}.")
            return False
        return True

    def is_logdetective_enabled(self, project_url: str) -> bool:
        """Should Log Detective analysis run for this project?"""
        if not self._service_config.logdetective_enabled:
            logger.info(f"Log Detective globally disabled, skipping for {project_url}.")
            return False
        if project_url in self._settings.disabled_projects_for_logdetective:
            logger.info(f"Log Detective disabled for project {project_url}.")
            return False
        return True
