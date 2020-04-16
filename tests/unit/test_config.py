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
from marshmallow import ValidationError

from packit_service.config import ServiceConfig, Deployment


@pytest.fixture()
def service_config_valid():
    return {
        "debug": True,
        "deployment": "prod",
        "authentication": {
            "github.com": {
                "github_app_id": "11111",
                "github_app_cert_path": "/path/lib",
            },
            "src.fedoraproject.org": {
                "instance_url": "https://src.fedoraproject.org",
                "token": "BINGO",
            },
        },
        "fas_user": "santa",
        "fas_password": "does-not-exist",
        "keytab_path": "/secrets/fedora.keytab",
        "webhook_secret": "secret",
        "validate_webhooks": True,
        "disable_sentry": False,
        "testing_farm_secret": "granko",
        "command_handler": "sandcastle",
        "command_handler_work_dir": "/sandcastle",
        "command_handler_image_reference": "docker.io/usercont/sandcastle",
        "command_handler_k8s_namespace": "packit-test-sandbox",
        "admins": ["Dasher", "Dancer", "Vixen", "Comet", "Blitzen"],
        "server_name": "hub.packit.org",
    }


def test_parse_valid(service_config_valid):
    config = ServiceConfig.get_from_dict(service_config_valid)
    assert config.debug
    assert config.deployment == Deployment.prod
    assert config.fas_user == "santa"
    assert config.fas_password == "does-not-exist"
    assert config.keytab_path == "/secrets/fedora.keytab"
    assert config.webhook_secret == "secret"
    assert config.validate_webhooks
    assert config.disable_sentry is False
    assert config.testing_farm_secret == "granko"
    assert config.command_handler_work_dir == "/sandcastle"
    assert config.admins == {"Dasher", "Dancer", "Vixen", "Comet", "Blitzen"}
    assert config.server_name == "hub.packit.org"


@pytest.fixture()
def service_config_invalid():
    return {
        "deployment": False,  # wrong option
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


def test_parse_invalid(service_config_invalid):
    with pytest.raises(ValidationError):
        ServiceConfig.get_from_dict(service_config_invalid)


@pytest.fixture()
def service_config_missing():
    return {}


def test_parse_missing(service_config_missing):
    with pytest.raises(ValidationError):
        ServiceConfig.get_from_dict(service_config_missing)


@pytest.mark.parametrize(
    "sc", ((ServiceConfig.get_from_dict({"deployment": "stg"})), (ServiceConfig()),)
)
def test_config_opts(sc):
    """ test that ServiceConfig knows all the options """
    assert sc.server_name is not None
    assert sc.deployment == Deployment.stg
    assert sc.admins is not None
    assert sc.command_handler is not None
    assert sc.command_handler_work_dir is not None
    assert sc.command_handler_pvc_env_var is not None
    assert sc.command_handler_image_reference is not None
    assert sc.command_handler_k8s_namespace is not None
    assert sc.fas_password is not None
    assert sc.testing_farm_secret is not None
    assert sc.github_requests_log_path is not None
    assert sc.webhook_secret is not None
    assert sc.validate_webhooks is not None
    assert sc.disable_sentry is not None
