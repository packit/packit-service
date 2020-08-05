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
from typing import Optional

import pytest
from flexmock import flexmock
from marshmallow import ValidationError

from ogr.abstract import GitProject, GitService
from packit.config import PackageConfig, SyncFilesConfig
from packit.exceptions import PackitConfigException
from packit.sync import SyncFilesItem
from packit_service.config import ServiceConfig, Deployment, PackageConfigGetter


@pytest.fixture(scope="module")
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
        "testing_farm_secret": "granko",
        "bugzilla_url": "https://ladybug-zilla",
        "bugzilla_api_key": "ratamahatta",
        "pr_accepted_labels": ["good-enough", "will-maintain-this"],
        "command_handler": "sandcastle",
        "command_handler_work_dir": "/sandcastle",
        "command_handler_image_reference": "docker.io/usercont/sandcastle",
        "command_handler_k8s_namespace": "packit-test-sandbox",
        "admins": ["Dasher", "Dancer", "Vixen", "Comet", "Blitzen"],
        "server_name": "hub.packit.org",
        "gitlab_webhook_tokens": ["token1", "token2", "token3", "aged"],
        "gitlab_token_secret": "jwt_secret",
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
    assert config.testing_farm_secret == "granko"
    assert config.bugzilla_url == "https://ladybug-zilla"
    assert config.bugzilla_api_key == "ratamahatta"
    assert config.pr_accepted_labels == {"good-enough", "will-maintain-this"}
    assert config.command_handler_work_dir == "/sandcastle"
    assert config.admins == {"Dasher", "Dancer", "Vixen", "Comet", "Blitzen"}
    assert config.server_name == "hub.packit.org"
    assert config.gitlab_token_secret == "jwt_secret"
    assert config.gitlab_webhook_tokens == {"token1", "token2", "token3", "aged"}


@pytest.fixture(scope="module")
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
    assert sc.gitlab_token_secret is not None
    assert sc.gitlab_webhook_tokens is not None


@pytest.mark.parametrize(
    "content,project,mock_spec_search,spec_path_option,spec_path,reference",
    [
        (
            "---\nspecfile_path: packit.spec\n"
            "synced_files:\n"
            "  - packit.spec\n"
            "  - src: .packit.yaml\n"
            "    dest: .packit2.yaml",
            GitProject(repo="", service=GitService(), namespace=""),
            True,
            None,
            "packit.spec",
            None,
        ),
        (
            "---\nspecfile_path: packit.spec\n"
            "synced_files:\n"
            "  - packit.spec\n"
            "  - src: .packit.yaml\n"
            "    dest: .packit2.yaml",
            GitProject(repo="", service=GitService(), namespace=""),
            True,
            None,
            "packit.spec",
            "some-branch",
        ),
        (
            "synced_files:\n"
            "  - packit.spec\n"
            "  - src: .packit.yaml\n"
            "    dest: .packit2.yaml",
            GitProject(repo="", service=GitService(), namespace=""),
            True,
            None,
            "packit.spec",
            None,
        ),
        (
            "synced_files:\n"
            "  - packit.spec\n"
            "  - src: .packit.yaml\n"
            "    dest: .packit2.yaml",
            GitProject(repo="", service=GitService(), namespace=""),
            False,
            "packit.spec",
            "packit.spec",
            None,
        ),
        (
            "---\n"
            "synced_files:\n"
            "  - src: .packit.yaml\n"
            "    dest: .packit2.yaml\n"
            "jobs: [{job: build, trigger: pull_request}]\n",
            GitProject(repo="", service=GitService(), namespace=""),
            False,
            "packit.spec",
            "packit.spec",
            None,
        ),
    ],
)
def test_get_package_config_from_repo(
    content,
    project: GitProject,
    mock_spec_search: bool,
    spec_path: Optional[str],
    spec_path_option: Optional[str],
    reference: str,
):
    gp = flexmock(GitProject)
    gp.should_receive("full_repo_name").and_return("a/b")
    gp.should_receive("get_file_content").with_args(
        path=".packit.yaml", ref=reference
    ).and_return(content)
    if mock_spec_search:
        gp.should_receive("get_files").and_return(["packit.spec"]).once()
    config = PackageConfigGetter.get_package_config_from_repo(
        project=project, reference=reference, spec_file_path=spec_path_option
    )
    assert isinstance(config, PackageConfig)
    assert config.specfile_path == spec_path
    assert set(config.get_all_files_to_sync().files_to_sync) == set(
        SyncFilesConfig(
            files_to_sync=[
                SyncFilesItem(src="packit.spec", dest="packit.spec"),
                SyncFilesItem(src=".packit.yaml", dest=".packit2.yaml"),
            ]
        ).files_to_sync
    )
    assert config.create_pr
    for j in config.jobs:
        assert j.specfile_path == spec_path
        assert j.downstream_package_name == config.downstream_package_name
        assert j.upstream_package_name == config.upstream_package_name


def test_get_package_config_from_repo_alternative_config_name():
    gp = flexmock(GitProject)
    gp.should_receive("full_repo_name").and_return("a/b")
    gp.should_receive("get_file_content").with_args(
        path=".packit.yaml", ref=None
    ).and_raise(FileNotFoundError, "not found")
    gp.should_receive("get_file_content").with_args(
        path=".packit.yml", ref=None
    ).and_return(
        "---\nspecfile_path: packit.spec\n"
        "synced_files:\n"
        "  - packit.spec\n"
        "  - src: .packit.yaml\n"
        "    dest: .packit2.yaml"
    )
    config = PackageConfigGetter.get_package_config_from_repo(
        project=GitProject(repo="", service=GitService(), namespace=""),
        reference=None,
        spec_file_path="packit.spec",
    )
    assert isinstance(config, PackageConfig)
    assert config.specfile_path == "packit.spec"
    assert config.synced_files == SyncFilesConfig(
        files_to_sync=[
            SyncFilesItem(src="packit.spec", dest="packit.spec"),
            SyncFilesItem(src=".packit.yaml", dest=".packit2.yaml"),
        ]
    )
    assert config.create_pr


def test_get_package_config_from_repo_not_found_exception_pr():
    gp = flexmock(GitProject)
    gp.should_receive("full_repo_name").and_return("a/b")
    gp.should_receive("get_file_content").and_raise(FileNotFoundError, "not found")
    gp.should_receive("pr_comment").and_return(None).once()
    with pytest.raises(PackitConfigException):
        PackageConfigGetter.get_package_config_from_repo(
            project=GitProject(repo="", service=GitService(), namespace=""),
            reference=None,
            pr_id=2,
        )


def test_get_package_config_from_repo_not_found_exception_existing_issue():
    gp = flexmock(GitProject)
    gp.should_receive("full_repo_name").and_return("a/b")
    gp.should_receive("get_file_content").and_raise(FileNotFoundError, "not found")
    gp.should_receive("get_issue_list").and_return(
        [flexmock(title="Invalid packit config")]
    ).once()
    with pytest.raises(PackitConfigException):
        PackageConfigGetter.get_package_config_from_repo(
            project=GitProject(repo="", service=GitService(), namespace=""),
            reference=None,
        )


def test_get_package_config_from_repo_not_found_exception_nonexisting_issue():
    gp = flexmock(GitProject)
    gp.should_receive("full_repo_name").and_return("a/b")
    gp.should_receive("get_file_content").and_raise(FileNotFoundError, "not found")
    gp.should_receive("get_issue_list").and_return(
        [flexmock(title="issue 1"), flexmock(title="issue 2")]
    ).once()
    gp.should_receive("create_issue").and_return(flexmock(url="the url")).once()
    with pytest.raises(PackitConfigException):
        PackageConfigGetter.get_package_config_from_repo(
            project=GitProject(repo="", service=GitService(), namespace=""),
            reference=None,
        )
