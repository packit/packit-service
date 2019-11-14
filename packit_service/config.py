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
from typing import Set

from yaml import safe_load

from packit.config import RunCommandType, Config
from packit.exceptions import PackitException
from packit_service.constants import (
    SANDCASTLE_WORK_DIR,
    SANDCASTLE_PVC,
    SANDCASTLE_IMAGE,
    SANDCASTLE_DEFAULT_PROJECT,
    CONFIG_FILE_NAME,
)
from packit_service.schema import SERVICE_CONFIG_SCHEMA

logger = logging.getLogger(__name__)


class Deployment(enum.Enum):
    dev = "dev"
    stg = "stg"
    prod = "prod"


class ServiceConfig(Config):
    SCHEMA = SERVICE_CONFIG_SCHEMA

    def __init__(self):
        super().__init__()

        self.deployment: Deployment = Deployment.stg

        self.webhook_secret: str = ""
        self.testing_farm_secret: str = ""
        self.validate_webhooks: bool = True

        # List of github users who are allowed to trigger p-s on any repository
        self.admins: Set[str] = set()

        # %%% ACTIONS HANDLER CONFIGURATION %%%
        # these values are specific to packit service when we run actions in a sandbox

        # name of the handler to run actions and commands, default to current env
        self.command_handler: RunCommandType = RunCommandType.local
        # a dir where the PV is mounted: both in sandbox and in worker
        self.command_handler_work_dir: str = ""
        # name of the PVC so that the sandbox has the same volume mounted
        self.command_handler_pvc_env_var: str = ""  # pointer to pointer, lol
        # name of sandbox container image
        self.command_handler_image_reference: str = SANDCASTLE_IMAGE
        # do I really need to explain this?
        self.command_handler_k8s_namespace: str = SANDCASTLE_DEFAULT_PROJECT

        # path to a file where OGR should store HTTP requests
        self.github_requests_log_path: str = ""

    @classmethod
    def get_from_dict(cls, raw_dict: dict, validate=True) -> "ServiceConfig":
        if validate:
            cls.validate(raw_dict)

        user_config = super().get_from_dict(raw_dict=raw_dict, validate=False)
        config = ServiceConfig()
        config.__dict__.update(user_config.__dict__)

        config.webhook_secret = raw_dict.get("webhook_secret", "")
        config.testing_farm_secret = raw_dict.get("testing_farm_secret", "")
        config.deployment = Deployment(raw_dict.get("deployment", ""))
        config.validate_webhooks = raw_dict.get("validate_webhooks", False)
        config.admins = set(raw_dict.get("admins", []))

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
