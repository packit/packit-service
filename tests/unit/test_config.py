# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import pytest
from flexmock import flexmock
from marshmallow import ValidationError
from packit.exceptions import PackitConfigException

from packit_service import package_config_getter
from packit_service.config import (
    Deployment,
    MRTarget,
    ServiceConfig,
)
from packit_service.constants import TESTING_FARM_API_URL
from packit_service.package_config_getter import PackageConfigGetter
from packit_service.worker.reporting import create_issue_if_needed


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
        "command_handler": "sandcastle",
        "command_handler_work_dir": "/tmp/sandcastle",
        "command_handler_image_reference": "quay.io/packit/sandcastle",
        "command_handler_k8s_namespace": "packit-test-sandbox",
        "admins": ["Dasher", "Dancer", "Vixen", "Comet", "Blitzen"],
        "server_name": "hub.packit.org",
        "gitlab_token_secret": "jwt_secret",
        "gitlab_mr_targets_handled": [
            {"repo": "redhat/centos-stream/src/.+", "branch": "c9s"},
            {"repo": "packit-service/src/.+"},
            {"branch": "rawhide"},
        ],
        "enabled_private_namespaces": [
            "gitlab.com/private/namespace",
            "github.com/other-private-namespace",
        ],
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
    assert config.testing_farm_api_url == TESTING_FARM_API_URL
    assert config.command_handler_work_dir == "/tmp/sandcastle"
    assert config.admins == {"Dasher", "Dancer", "Vixen", "Comet", "Blitzen"}
    assert config.server_name == "hub.packit.org"
    assert config.gitlab_token_secret == "jwt_secret"
    assert len(config.gitlab_mr_targets_handled) == 3
    assert MRTarget("redhat/centos-stream/src/.+", "c9s") in config.gitlab_mr_targets_handled
    assert MRTarget("packit-service/src/.+", None) in config.gitlab_mr_targets_handled
    assert MRTarget(None, "rawhide") in config.gitlab_mr_targets_handled
    assert config.enabled_private_namespaces == {
        "gitlab.com/private/namespace",
        "github.com/other-private-namespace",
    }
    assert config.package_config_path_override is None


def test_parse_optional_values(service_config_valid):
    """When optional values are set, they are correctly parsed"""
    config = ServiceConfig.get_from_dict(
        {
            **service_config_valid,
            "testing_farm_api_url": "https://other.url",
            "package_config_path_override": ".distro/source-git.yaml",
        },
    )
    assert config.testing_farm_api_url == "https://other.url"
    assert config.package_config_path_override == ".distro/source-git.yaml"


@pytest.fixture(scope="module")
def service_config_invalid():
    return {
        "deployment": False,  # wrong option
        "authentication": {
            "github.com": {
                "github_app_id": "11111",
                "github_app_cert_path": "/path/lib",
            },
        },
        "webhook_secret": "secret",
        "command_handler_work_dir": "/sandcastle",
        "command_handler_image_reference": "quay.io/packit/sandcastle",
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
    "sc",
    (
        (ServiceConfig.get_from_dict({"deployment": "stg"})),
        (ServiceConfig()),
    ),
)
def test_config_opts(sc):
    """test that ServiceConfig knows all the options"""
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


@pytest.mark.parametrize(
    "project,reference,base_project,ret,package_config_path",
    [
        (
            flexmock(repo="packit", namespace="packit"),
            None,
            None,
            flexmock(),
            None,
        ),
        (
            flexmock(repo="ogr", namespace="packit"),
            "some-branch",
            None,
            flexmock(),
            None,
        ),
        (
            flexmock(repo="ogr", namespace="fork"),
            "some-branch",
            flexmock(repo="ogr", namespace="packit"),
            flexmock(),
            None,
        ),
        (
            None,
            "some-branch",
            flexmock(repo="ogr", namespace="packit"),
            flexmock(),
            ".distro/source-git.yaml",
        ),
    ],
)
def test_get_package_config_from_repo(
    project,
    reference,
    base_project,
    ret,
    package_config_path,
):
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(
        flexmock(package_config_path_override=package_config_path),
    ).once()
    flexmock(package_config_getter).should_receive("get_package_config_from_repo").with_args(
        project=(base_project or project),
        ref=reference,
        package_config_path=package_config_path,
    ).once().and_return(ret)
    PackageConfigGetter.get_package_config_from_repo(
        project=project,
        reference=reference,
        base_project=base_project,
    )


def test_get_package_config_from_repo_no_project():
    """When neither a project nor a base_project is provided,
    None is returned and no exception is raised.
    """
    flexmock(package_config_getter).should_receive("get_package_config_from_repo").never()
    PackageConfigGetter.get_package_config_from_repo(project=None, base_project=None)


def test_get_package_config_from_repo_not_found_exception_pr():
    """Comment on the PR if there is no configuration found, and re-raise the
    exception.
    """
    project = flexmock(full_repo_name="packit/packit")
    flexmock(package_config_getter).should_receive("get_package_config_from_repo").with_args(
        project=project,
        ref=None,
        package_config_path=None,
    ).once().and_return(None)
    pr = flexmock(get_comments=lambda *args, **kwargs: [])
    project.should_receive("get_pr").with_args(2).once().and_return(pr)
    pr.should_receive("comment").once()
    with pytest.raises(PackitConfigException):
        PackageConfigGetter.get_package_config_from_repo(
            project=project,
            reference=None,
            pr_id=2,
        )


def test_get_package_config_from_repo_not_found():
    """Don't fail when config is not found."""
    flexmock(package_config_getter).should_receive(
        "get_package_config_from_repo"
    ).once().and_return(
        None,
    )
    assert (
        PackageConfigGetter.get_package_config_from_repo(
            project=flexmock(full_repo_name="packit/packit"),
            reference=None,
            fail_when_missing=False,
        )
        is None
    )


def test_get_package_config_from_repo_not_found_exception_create_issue():
    project = flexmock(full_repo_name="packit/packit")
    flexmock(package_config_getter).should_receive("get_package_config_from_repo").with_args(
        project=project,
        ref=None,
        package_config_path=None,
    ).once().and_return(None)
    flexmock(package_config_getter).should_receive("create_issue_if_needed").with_args(
        project,
        title=str,
        message=str,
    ).once()
    with pytest.raises(PackitConfigException):
        PackageConfigGetter.get_package_config_from_repo(
            project=project,
            reference=None,
        )


@pytest.mark.parametrize(
    "issues, create_new, title, message, comment_to_existing",
    [
        (
            [flexmock(title="Some random issue"), flexmock(title="Many issues")],
            True,
            "Created issue",
            "Let's make sure to deliver the message",
            None,
        ),
        (
            [
                flexmock(title="Some random issue"),
                flexmock(id=3, title="[packit] I was here"),
                flexmock(title="Many issues"),
            ],
            False,
            "I was here",
            "Down the rabbit hole",
            None,
        ),
        (
            [
                flexmock(title="Some random issue"),
                flexmock(title="[packit] I was here"),
                flexmock(title="Many issues"),
            ],
            True,
            "Something new",
            "Knock, knock! Here we go again!",
            None,
        ),
        (
            [flexmock(title="Some random issue"), flexmock(title="Many issues")],
            True,
            "Created issue",
            "Let's make sure to deliver the message",
            "Let's make sure to deliver the message",
        ),
        (
            [
                flexmock(title="Some random issue"),
                flexmock(
                    title="[packit] I was here",
                    id=3,
                    url="https://github.com/namespace/project",
                    comment=lambda body: None,
                    get_comments=lambda *args, **kwargs: [],
                ),
                flexmock(title="Many issues"),
            ],
            False,
            "I was here",
            "Down the rabbit hole",
            "Down the rabbit hole",
        ),
    ],
)
def test_create_issue_if_needed(
    issues,
    create_new,
    title,
    message,
    comment_to_existing,
):
    project = flexmock()
    check = lambda value: value is None  # noqa
    project.should_receive("get_issue_list").and_return(issues).once()

    if create_new:
        issue_mock = flexmock(
            id=3,
            title="new issue",
            url="https://github.com/namespace/project/issues/3",
        )
        issue_mock.should_receive("comment").times(0)

        project.should_receive("create_issue").with_args(
            title=f"[packit] {title}",
            body=message,
        ).and_return(issue_mock).once()

        check = lambda value: value.title == "new issue"  # noqa

    issue_created = create_issue_if_needed(
        project,
        title,
        message,
        comment_to_existing,
    )
    assert check(issue_created)
