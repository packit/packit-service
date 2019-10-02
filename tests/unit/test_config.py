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
import pytest
from packit.exceptions import PackitInvalidConfigException

from packit_service.config import ServiceConfig, Deployment


def get_service_config_missing():
    # missing required fields
    return {}


def get_service_config_invalid():
    # wrong option
    return {
        "deployment": False,
        "authentication": {
            "github.com": {
                "github_app_id": "11111",
                "github_app_cert_path": "/path/lib",
            }
        },
        "webhook_secret": "secret",
        "command_handler_work_dir": "/sandcastle",
        "command_handler_image_reference": "docker.io/usercont/sandcastle",
        "command_handler_k8s_namespace": "packit-test-sandbox",
    }


def get_service_config_valid():
    return {
        "deployment": "prod",
        "authentication": {
            "github.com": {
                "github_app_id": "11111",
                "github_app_cert_path": "/path/lib",
            }
        },
        "webhook_secret": "secret",
        "command_handler": "sandcastle",
        "command_handler_work_dir": "/sandcastle",
        "command_handler_image_reference": "docker.io/usercont/sandcastle",
        "command_handler_k8s_namespace": "packit-test-sandbox",
    }


@pytest.fixture()
def service_config_missing():
    return get_service_config_missing()


@pytest.fixture()
def service_config_valid():
    return get_service_config_valid()


@pytest.fixture()
def service_config_invalid():
    return get_service_config_invalid()


def test_parse_valid(service_config_valid):
    config = ServiceConfig.get_from_dict(service_config_valid)
    assert config.deployment == Deployment("prod")


def test_parse_invalid(service_config_invalid):
    with pytest.raises(PackitInvalidConfigException):
        ServiceConfig.get_from_dict(service_config_invalid)


def test_parse_missing(service_config_missing):
    with pytest.raises(PackitInvalidConfigException):
        ServiceConfig.get_from_dict(service_config_missing)
