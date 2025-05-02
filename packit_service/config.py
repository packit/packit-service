# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
import os
from pathlib import Path
from typing import NamedTuple, Optional, Union

from packit.config import (
    Config,
    RunCommandType,
)
from packit.config.common_package_config import Deployment
from packit.exceptions import (
    PackitException,
)
from yaml import safe_load

from packit_service.constants import (
    CONFIG_FILE_NAME,
    SANDCASTLE_DEFAULT_PROJECT,
    SANDCASTLE_IMAGE,
    SANDCASTLE_PVC,
    SANDCASTLE_WORK_DIR,
    TESTING_FARM_API_URL,
)

logger = logging.getLogger(__name__)


class ProjectToSync(NamedTuple):
    """
    Project we want to sync from downstream.
    """

    forge: str
    repo_namespace: str
    repo_name: str
    branch: str
    dg_repo_name: str
    dg_branch: str

    def __repr__(self):
        return (
            f"ProjectToSync(forge={self.forge}, repo_namespace={self.repo_namespace}, "
            f"repo_name={self.repo_name}, branch={self.branch}, "
            f"dg_repo_name={self.dg_repo_name}, dg_branch={self.dg_branch})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ProjectToSync):
            raise NotImplementedError()

        return (
            self.forge == other.forge
            and self.repo_name == other.repo_name
            and self.repo_namespace == other.repo_namespace
            and self.branch == other.branch
            and self.dg_repo_name == other.dg_repo_name
            and self.dg_branch == other.dg_branch
        )


class MRTarget(NamedTuple):
    """
    A pair of repo and branch regexes.
    """

    repo: str
    branch: str

    def __repr__(self):
        return f"MRTarget(repo={self.repo}, branch={self.branch})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, MRTarget):
            raise NotImplementedError()

        return self.repo == other.repo and self.branch == self.branch


class ServiceConfig(Config):
    def __init__(
        self,
        deployment: Deployment = Deployment.stg,
        webhook_secret: str = "",
        testing_farm_secret: str = "",
        testing_farm_api_url: str = "",
        internal_testing_farm_secret: str = "",
        validate_webhooks: bool = True,
        admins: Optional[list] = None,
        fas_password: Optional[str] = "",
        enabled_private_namespaces: Optional[Union[set[str], list[str]]] = None,
        gitlab_token_secret: str = "",
        gitlab_mr_targets_handled: Optional[list[MRTarget]] = None,
        projects_to_sync: Optional[list[ProjectToSync]] = None,
        enabled_projects_for_internal_tf: Optional[Union[set[str], list[str]]] = None,
        dashboard_url: str = "",
        koji_logs_url: str = "https://kojipkgs.fedoraproject.org",
        koji_web_url: str = "https://koji.fedoraproject.org",
        enabled_projects_for_srpm_in_copr: Optional[Union[set[str], list[str]]] = None,
        comment_command_prefix: str = "/packit",
        redhat_api_refresh_token: Optional[str] = None,
        package_config_path_override: Optional[str] = None,
        command_handler_storage_class: Optional[str] = None,
        appcode: Optional[str] = None,
        enabled_projects_for_fedora_ci: Optional[Union[set[str], list[str]]] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)

        self.deployment = deployment
        self.webhook_secret = webhook_secret
        # Common secret to authenticate both, packit service (when sending request to testing farm)
        # and testing farm (when sending notification to packit service's webhook).
        # We might later use different secrets for those two use cases.
        self.testing_farm_secret = testing_farm_secret
        self.testing_farm_api_url = testing_farm_api_url
        self.internal_testing_farm_secret = internal_testing_farm_secret
        self.validate_webhooks = validate_webhooks

        # fas.fedoraproject.org needs password to authenticate
        # 'fas_user' is inherited from packit.config.Config
        self.fas_password = fas_password

        # List of github users who are allowed to trigger p-s on any repository
        self.admins: set[str] = set(admins or [])

        # for flask SERVER_NAME so we can create links to logs
        self.server_name: str = ""

        # Gitlab token secret to decode JWT tokens
        self.gitlab_token_secret: str = gitlab_token_secret

        self.gitlab_mr_targets_handled: list[MRTarget] = gitlab_mr_targets_handled

        # Explicit list of private namespaces we work with
        # e.g.:
        #  - github.com/other-private-namespace
        #  - gitlab.com/private/namespace
        self.enabled_private_namespaces: set[str] = set(
            enabled_private_namespaces or [],
        )
        # Explicit list of project we allow the internal TF instance to be used-
        # e.g.:
        #  - github.com/other-private-namespace/project
        #  - gitlab.com/namespace/project
        self.enabled_projects_for_internal_tf: set[str] = set(
            enabled_projects_for_internal_tf or [],
        )

        # e.g.:
        #  - https://src.fedoraproject.org/rpms/packit
        self.enabled_projects_for_fedora_ci: set[str] = set(enabled_projects_for_fedora_ci or [])

        self.projects_to_sync = projects_to_sync or []

        # Full URL to the dashboard, e.g. https://dashboard.packit.dev
        self.dashboard_url = dashboard_url
        self.koji_logs_url = koji_logs_url
        self.koji_web_url = koji_web_url

        self.enabled_projects_for_srpm_in_copr: set[str] = set(
            enabled_projects_for_srpm_in_copr or [],
        )
        self.comment_command_prefix = comment_command_prefix

        # Token used by the VM Image Builder. Get it here:
        # https://access.redhat.com/management/api
        self.redhat_api_refresh_token = redhat_api_refresh_token

        # Package config path to use, instead of searching for the
        # default names.
        self.package_config_path_override = package_config_path_override

        # Storage class that is used for temporary volumes used by Sandcastle
        self.command_handler_storage_class = command_handler_storage_class

        # Appcode used in MP+ to differentiate applications
        self.appcode = appcode

    service_config = None

    def __repr__(self):
        def hide(token: str) -> str:
            return f"{token[:1]}***{token[-1:]}" if token else ""

        return (
            f"{self.__class__.__name__}("
            f"{super().__repr__()}, "
            f"deployment='{self.deployment}', "
            f"webhook_secret='{hide(self.webhook_secret)}', "
            f"testing_farm_secret='{hide(self.testing_farm_secret)}', "
            f"testing_farm_api_url='{self.testing_farm_api_url}', "
            f"internal_testing_farm_secret='{hide(self.internal_testing_farm_secret)}', "
            f"validate_webhooks='{self.validate_webhooks}', "
            f"admins='{self.admins}', "
            f"fas_password='{hide(self.fas_password)}', "
            f"gitlab_token_secret='{hide(self.gitlab_token_secret)}',"
            f"gitlab_mr_targets_handled='{self.gitlab_mr_targets_handled}', "
            f"enabled_private_namespaces='{self.enabled_private_namespaces}', "
            f"enabled_projects_for_internal_tf='{self.enabled_projects_for_internal_tf}', "
            f"server_name='{self.server_name}', "
            f"dashboard_url='{self.dashboard_url}', "
            f"koji_logs_url='{self.koji_logs_url}', "
            f"koji_web_url='{self.koji_web_url}', "
            f"enabled_projects_for_srpm_in_copr= '{self.enabled_projects_for_srpm_in_copr}', "
            f"comment_command_prefix='{self.comment_command_prefix}', "
            f"redhat_api_refresh_token='{hide(self.redhat_api_refresh_token)}', "
            f"package_config_path_override='{self.package_config_path_override}', "
            f"enabled_projects_for_fedora_ci='{self.enabled_projects_for_fedora_ci}')"
        )

    @classmethod
    def get_from_dict(cls, raw_dict: dict) -> "ServiceConfig":
        # required to avoid circular imports
        from packit_service.schema import ServiceConfigSchema

        config = ServiceConfigSchema().load(raw_dict)

        config.server_name = raw_dict.get("server_name", "localhost:5000")

        config.command_handler = RunCommandType.local
        a_h = raw_dict.get("command_handler")
        if a_h:
            config.command_handler = RunCommandType(a_h)
        config.command_handler_work_dir = raw_dict.get(
            "command_handler_work_dir",
            SANDCASTLE_WORK_DIR,
        )
        config.command_handler_pvc_env_var = raw_dict.get(
            "command_handler_pvc_env_var",
            SANDCASTLE_PVC,
        )
        config.command_handler_image_reference = raw_dict.get(
            "command_handler_image_reference",
            SANDCASTLE_IMAGE,
        )
        # default project for oc cluster up
        config.command_handler_k8s_namespace = raw_dict.get(
            "command_handler_k8s_namespace",
            SANDCASTLE_DEFAULT_PROJECT,
        )

        config.testing_farm_api_url = raw_dict.get(
            "testing_farm_api_url",
            TESTING_FARM_API_URL,
        )

        logger.debug(f"Loaded config: {config}")
        return config

    @classmethod
    def get_service_config(cls) -> "ServiceConfig":
        if cls.service_config is None:
            config_file = os.getenv(
                "PACKIT_SERVICE_CONFIG",
                Path.home() / ".config" / CONFIG_FILE_NAME,
            )
            logger.debug(f"Loading service config from: {config_file}")

            try:
                with open(config_file) as file_stream:
                    loaded_config = safe_load(file_stream)
            except Exception as ex:
                logger.error(f"Cannot load service config '{config_file}'.")
                raise PackitException(f"Cannot load service config: {ex}.") from ex

            cls.service_config = ServiceConfig.get_from_dict(raw_dict=loaded_config)
        return cls.service_config

    def get_project_to_sync(self, dg_repo_name, dg_branch) -> Optional[ProjectToSync]:
        # TODO: Is it ok that we don't check namespace? Can't this be misused from a fork?
        projects = [
            project
            for project in self.projects_to_sync
            if project.dg_repo_name == dg_repo_name and project.dg_branch == dg_branch
        ]
        if projects:
            logger.info(f"Found project to sync: {projects[0]}.")
            return projects[0]
        return None

    def get_github_account_name(self) -> str:
        return {
            Deployment.prod: "packit-as-a-service[bot]",
            Deployment.stg: "packit-as-a-service-stg[bot]",
            Deployment.dev: "packit-as-a-service-dev[bot]",
        }.get(self.deployment)
