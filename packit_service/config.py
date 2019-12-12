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

from yaml import safe_load

from packit.config import Config
from packit.exceptions import PackitException
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
    def __init__(
        self,
        debug: bool = False,
        dry_run: bool = False,
        fas_user: Optional[str] = None,
        keytab_path: Optional[str] = None,
        webhook_secret: str = "",
        deployment: Deployment = Deployment.stg,
        testing_farm_secret: str = "",
        validate_webhooks: bool = True,
        fas_password: Optional[str] = None,
        admins: Set[str] = None,
        command_handler: str = None,
        command_handler_work_dir: str = SANDCASTLE_WORK_DIR,
        command_handler_pvc_env_var: str = SANDCASTLE_PVC,
        command_handler_image_reference: str = SANDCASTLE_IMAGE,
        command_handler_k8s_namespace: str = SANDCASTLE_DEFAULT_PROJECT,
    ):

        super().__init__(
            debug,
            dry_run,
            fas_user,
            keytab_path,
            webhook_secret,
            command_handler,
            command_handler_work_dir,
            command_handler_pvc_env_var,
            command_handler_image_reference,
            command_handler_k8s_namespace,
        )

        self.deployment: Deployment = deployment
        self.webhook_secret: str = webhook_secret
        self.testing_farm_secret: str = testing_farm_secret
        self.validate_webhooks: bool = validate_webhooks

        # fas.fedoraproject.org needs password to authenticate
        # 'fas_user' is inherited from packit.config.Config
        self.fas_password: Optional[str] = fas_password

        # List of github users who are allowed to trigger p-s on any repository
        self.admins: Set[str] = admins or set()

        # path to a file where OGR should store HTTP requests
        self.github_requests_log_path: str = ""

    @classmethod
    def get_from_dict(cls, raw_dict: dict) -> "ServiceConfig":
        # required to avoid cyclical imports
        from packit_service.schema import ServiceConfigSchema

        return ServiceConfigSchema(strict=True).load(raw_dict).data

    @classmethod
    def get_service_config(cls) -> "ServiceConfig":
        directory = Path.home() / ".config"
        config_file_name_full = directory / CONFIG_FILE_NAME
        logger.debug(f"Loading service config from directory: {directory}")

        try:
            loaded_config = safe_load(open(config_file_name_full))
        except Exception as ex:
            logger.error(f"Cannot load service config '{config_file_name_full}'.")
            raise PackitException(f"Cannot load service config: {ex}.")

        return ServiceConfig.get_from_dict(raw_dict=loaded_config)


service_config = ServiceConfig.get_service_config()
