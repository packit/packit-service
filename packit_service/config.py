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
import enum
import logging
from pathlib import Path
from typing import Set, Optional

from ogr.abstract import GitProject
from yaml import safe_load

from packit.config import (
    RunCommandType,
    Config,
    get_package_config_from_repo,
    PackageConfig,
)
from packit.exceptions import PackitException, PackitConfigException
from packit_service.constants import (
    SANDCASTLE_WORK_DIR,
    SANDCASTLE_PVC,
    SANDCASTLE_IMAGE,
    SANDCASTLE_DEFAULT_PROJECT,
    CONFIG_FILE_NAME,
)

logger = logging.getLogger(__name__)


class Deployment(enum.Enum):
    dev = "dev"
    stg = "stg"
    prod = "prod"


class ServiceConfig(Config):
    service_config = None
    SCHEMA = SERVICE_CONFIG_SCHEMA

    def __init__(
        self,
        deployment: Deployment = Deployment.stg,
        webhook_secret: str = "",
        testing_farm_secret: str = "",
        validate_webhooks: bool = True,
        admins: list = None,
        **kwargs,
    ):
        super().__init__(**kwargs)

        self.deployment = deployment

        # duplicate - also in Config -> can be removed?
        self.webhook_secret = webhook_secret
        self.testing_farm_secret = testing_farm_secret
        self.validate_webhooks = validate_webhooks

        # fas.fedoraproject.org needs password to authenticate
        # 'fas_user' is inherited from packit.config.Config
        self.fas_password: Optional[str] = ""

        # List of github users who are allowed to trigger p-s on any repository
        self.admins: Set[str] = set(admins or [])

        # for flask SERVER_NAME so we can create links to logs
        self.server_name: str = ""

        # for flask SERVER_NAME so we can create links to logs
        self.server_name: str = ""

    @classmethod
    def get_from_dict(cls, raw_dict: dict) -> "ServiceConfig":
        # required to avoid circular imports
        from packit_service.schema import ServiceConfigSchema

        config = ServiceConfigSchema(strict=True).load(raw_dict).data

        config.webhook_secret = raw_dict.get("webhook_secret", "")
        config.testing_farm_secret = raw_dict.get("testing_farm_secret", "")
        config.deployment = Deployment(raw_dict.get("deployment", ""))
        config.validate_webhooks = raw_dict.get("validate_webhooks", False)
        config.fas_password = raw_dict.get("fas_password", None)
        config.admins = set(raw_dict.get("admins", []))
        config.server_name = raw_dict.get("server_name", "localhost:5000")

        config.command_handler = RunCommandType.local
        a_h = raw_dict.get("command_handler")
        if a_h:
            config.command_handler = RunCommandType(a_h)
        config.command_handler_work_dir = raw_dict.get(
            "command_handler_work_dir", SANDCASTLE_WORK_DIR
        )
        config.command_handler_pvc_env_var = raw_dict.get(
            "command_handler_pvc_env_var", SANDCASTLE_PVC
        )
        config.command_handler_image_reference = raw_dict.get(
            "command_handler_image_reference", SANDCASTLE_IMAGE
        )
        # default project for oc cluster up
        config.command_handler_k8s_namespace = raw_dict.get(
            "command_handler_k8s_namespace", SANDCASTLE_DEFAULT_PROJECT
        )

        return config

    @classmethod
    def get_service_config(cls) -> "ServiceConfig":
        if cls.service_config is None:
            directory = Path.home() / ".config"
            config_file_name_full = directory / CONFIG_FILE_NAME
            logger.debug(f"Loading service config from directory: {directory}")

            try:
                loaded_config = safe_load(open(config_file_name_full))
            except Exception as ex:
                logger.error(f"Cannot load service config '{config_file_name_full}'.")
                raise PackitException(f"Cannot load service config: {ex}.")

            cls.service_config = ServiceConfig.get_from_dict(raw_dict=loaded_config)
        return cls.service_config


class GithubPackageConfigGetter:
    def get_package_config_from_repo(
        self,
        project: GitProject,
        reference: str,
        pr_id: int = None,
        fail_when_missing: bool = True,
    ):
        """
        Get the package config and catch the invalid config scenario and possibly no-config scenario
        Static because of the easier mocking.
        """
        try:
            package_config: PackageConfig = get_package_config_from_repo(
                project, reference
            )
            if not package_config and fail_when_missing:
                raise PackitConfigException(
                    f"No config file found in {project.full_repo_name}"
                )
        except PackitConfigException as ex:
            if pr_id:
                project.pr_comment(
                    pr_id, f"Failed to load packit config file:\n```\n{str(ex)}\n```"
                )
            else:
                # TODO: filter when https://github.com/packit-service/ogr/issues/308 fixed
                issues = project.get_issue_list()
                if "Invalid packit config" not in [x.title for x in issues]:
                    # TODO: store in DB
                    message = (
                        f"Failed to load packit config file:\n```\n{str(ex)}\n```\n"
                        "For more info, please check out the documentation: "
                        "http://packit.dev/packit-as-a-service/ or contact us - "
                        "[Packit team]"
                        "(https://github.com/orgs/packit-service/teams/the-packit-team)"
                    )

                    i = project.create_issue(
                        title="[packit] Invalid config", body=message
                    )
                    logger.debug(f"Created issue for invalid packit config: {i.url}")
            raise ex
        return package_config
