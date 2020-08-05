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
from typing import Set, Optional, List

from yaml import safe_load

from ogr.abstract import GitProject
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

    def __init__(
        self,
        deployment: Deployment = Deployment.stg,
        webhook_secret: str = "",
        testing_farm_secret: str = "",
        validate_webhooks: bool = True,
        admins: list = None,
        fas_password: Optional[str] = "",
        bugzilla_url: str = "",
        bugzilla_api_key: str = "",
        pr_accepted_labels: List[str] = None,
        gitlab_webhook_tokens: List[str] = None,
        gitlab_token_secret: str = "",
        **kwargs,
    ):
        super().__init__(**kwargs)

        self.deployment = deployment
        self.webhook_secret = webhook_secret
        self.testing_farm_secret = testing_farm_secret
        self.validate_webhooks = validate_webhooks

        # fas.fedoraproject.org needs password to authenticate
        # 'fas_user' is inherited from packit.config.Config
        self.fas_password = fas_password

        self.bugzilla_url = bugzilla_url
        self.bugzilla_api_key = bugzilla_api_key
        # Labels/Tags to mark a PR as accepted - handler will create a bug & attach patch from PR
        self.pr_accepted_labels: Set[str] = set(pr_accepted_labels or ["accepted"])

        # List of github users who are allowed to trigger p-s on any repository
        self.admins: Set[str] = set(admins or [])

        # for flask SERVER_NAME so we can create links to logs
        self.server_name: str = ""

        # Makeshift for now to authenticate webhooks coming from gitlab instances
        # Old way of authenticating
        self.gitlab_webhook_tokens: Set[str] = set(gitlab_webhook_tokens or [])

        # Gitlab token secret to decode JWT tokens
        self.gitlab_token_secret: str = gitlab_token_secret

    def __repr__(self):
        def hide(token: str) -> str:
            return f"{token[:1]}***{token[-1:]}" if token else ""

        return (
            f"{self.__class__.__name__}("
            f"{super().__repr__()}, "
            f"deployment='{self.deployment}', "
            f"webhook_secret='{hide(self.webhook_secret)}', "
            f"testing_farm_secret='{hide(self.testing_farm_secret)}', "
            f"validate_webhooks='{self.validate_webhooks}', "
            f"admins='{self.admins}', "
            f"fas_password='{hide(self.fas_password)}', "
            f"bugzilla_url='{self.bugzilla_url}', "
            f"bugzilla_api_key='{hide(self.bugzilla_api_key)}', "
            f"gitlab_webhook_tokens='{self.gitlab_webhook_tokens}',"
            f"gitlab_token_secret='{hide(self.gitlab_token_secret)}',"
            f"server_name='{self.server_name}')"
        )

    @classmethod
    def get_from_dict(cls, raw_dict: dict) -> "ServiceConfig":
        # required to avoid circular imports
        from packit_service.schema import ServiceConfigSchema

        config = ServiceConfigSchema().load_config(raw_dict)

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

        logger.debug(f"Loaded config: {config}")
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


class PackageConfigGetter:
    @staticmethod
    def get_package_config_from_repo(
        project: GitProject,
        reference: Optional[str] = None,
        base_project: Optional[GitProject] = None,
        pr_id: int = None,
        fail_when_missing: bool = True,
        spec_file_path: Optional[str] = None,
    ):
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
                spec_file_path=spec_file_path,
            )
            if not package_config and fail_when_missing:
                raise PackitConfigException(
                    f"No config file found in {project_to_search_in.full_repo_name} "
                    "on ref '{reference}'"
                )
        except PackitConfigException as ex:
            if pr_id:
                project.pr_comment(
                    pr_id, f"Failed to load packit config file:\n```\n{str(ex)}\n```"
                )
            else:
                # TODO: filter when https://github.com/packit/ogr/issues/308 fixed
                issues = project.get_issue_list()
                if "Invalid packit config" not in [x.title for x in issues]:
                    # TODO: store in DB
                    message = (
                        f"Failed to load packit config file:\n```\n{str(ex)}\n```\n"
                        "For more info, please check out the documentation: "
                        "http://packit.dev/packit-as-a-service/ or contact us - "
                        "[Packit team]"
                        "(https://github.com/orgs/packit/teams/the-packit-team)"
                    )

                    i = project.create_issue(
                        title="[packit] Invalid config", body=message
                    )
                    logger.debug(f"Created issue for invalid packit config: {i.url}")
            raise ex
        return package_config
