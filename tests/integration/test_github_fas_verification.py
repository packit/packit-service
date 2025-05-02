# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json

import pytest
from celery.canvas import Signature
from flexmock import flexmock
from ogr.services.github import GithubProject
from packit.config import Deployment

from packit_service.config import ServiceConfig
from packit_service.constants import SANDCASTLE_WORK_DIR
from packit_service.models import (
    AllowlistModel,
    AllowlistStatus,
    GithubInstallationModel,
)
from packit_service.package_config_getter import PackageConfigGetter
from packit_service.worker.allowlist import Allowlist
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.tasks import run_github_fas_verification_handler
from tests.spellbook import DATA_DIR, first_dict_value, get_parameters_from_results


def issue_comment_event():
    return json.loads(
        (DATA_DIR / "webhooks" / "github" / "issue_comment_verify_fas.json").read_text(),
    )


def test_verification_successful():
    config = ServiceConfig()
    config.command_handler_work_dir = SANDCASTLE_WORK_DIR
    config.deployment = Deployment.prod
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(config)

    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("get_releases").and_return([])
    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).and_return(None)

    issue_comment = flexmock()

    issue = flexmock(
        author="packit-as-a-service[bot]",
        title="User example-user needs to be approved.",
        close=lambda: None,
        get_comment=lambda issue_id: issue_comment,
    )
    flexmock(issue_comment).should_receive("add_reaction").once()
    flexmock(GithubProject).should_receive("get_issue").and_return(issue)

    processing_results = SteveJobs().process_message(issue_comment_event())
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    flexmock(Allowlist).should_receive("is_namespace_or_parent_approved").and_return(
        False,
    )
    flexmock(Allowlist).should_receive("is_denied").and_return(False)
    flexmock(Allowlist).should_receive(
        "is_github_username_from_fas_account_matching",
    ).with_args(fas_account="my-fas-account", sender_login="phracek").and_return(True)
    flexmock(AllowlistModel).should_receive("add_namespace").with_args(
        "github.com/example-user",
        AllowlistStatus.approved_automatically.value,
        "my-fas-account",
    ).once()
    flexmock(GithubInstallationModel).should_receive("get_by_account_login").with_args(
        "example-user",
    ).and_return(flexmock(sender_login="phracek"))

    msg = (
        "Namespace `github.com/example-user` approved successfully "
        "using FAS account `my-fas-account`!"
    )
    flexmock(issue).should_receive("comment").with_args(msg).once()

    results = run_github_fas_verification_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]


def test_verification_not_successful():
    config = ServiceConfig()
    config.command_handler_work_dir = SANDCASTLE_WORK_DIR
    config.deployment = Deployment.prod
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(config)

    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("get_releases").and_return([])
    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).and_return(None)

    issue_comment = flexmock()

    issue = flexmock(
        author="packit-as-a-service[bot]",
        title="User example-user needs to be approved.",
        close=lambda: None,
        get_comment=lambda issue_id: issue_comment,
    )
    flexmock(issue_comment).should_receive("add_reaction").once()
    flexmock(GithubProject).should_receive("get_issue").and_return(issue)

    processing_results = SteveJobs().process_message(issue_comment_event())
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    flexmock(Allowlist).should_receive("is_namespace_or_parent_approved").and_return(
        False,
    )
    flexmock(Allowlist).should_receive("is_denied").and_return(False)
    flexmock(Allowlist).should_receive(
        "is_github_username_from_fas_account_matching",
    ).with_args(fas_account="my-fas-account", sender_login="phracek").and_return(False)
    flexmock(AllowlistModel).should_receive("add_namespace").never()
    flexmock(GithubInstallationModel).should_receive("get_by_account_login").with_args(
        "example-user",
    ).and_return(flexmock(sender_login="phracek"))

    msg = (
        "We were not able to find a match between the GitHub Username field in the FAS account "
        "`my-fas-account` and GitHub user `phracek`. Please, check that you have set "
        "[the field]"
        "(https://accounts.fedoraproject.org/user/my-fas-account/settings/profile/#github) "
        "correctly and that your profile [is not private](https://accounts.fedoraproject.org/"
        "user/my-fas-account/settings/profile/#is_private) "
        "and try again or contact [us](https://packit.dev/#contact)."
    )
    flexmock(issue).should_receive("comment").with_args(msg).once()

    results = run_github_fas_verification_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]


@pytest.mark.parametrize(
    "comment",
    (["/packit verify-fas more names", "/packit verify-fas"]),
)
def test_verification_incorrect_format(comment):
    event_issue_comment = issue_comment_event()
    event_issue_comment["comment"]["body"] = comment

    config = ServiceConfig()
    config.command_handler_work_dir = SANDCASTLE_WORK_DIR
    config.deployment = Deployment.prod
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(config)

    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("get_releases").and_return([])
    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).and_return(None)

    issue_comment = flexmock()

    issue = flexmock(
        author="packit-as-a-service[bot]",
        title="User example-user needs to be approved.",
        close=lambda: None,
        get_comment=lambda issue_id: issue_comment,
    )
    flexmock(issue_comment).should_receive("add_reaction").once()
    flexmock(GithubProject).should_receive("get_issue").and_return(issue)

    processing_results = SteveJobs().process_message(event_issue_comment)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    flexmock(Allowlist).should_receive(
        "is_github_username_from_fas_account_matching",
    ).never()
    flexmock(AllowlistModel).should_receive("add_namespace").never()
    flexmock(GithubInstallationModel).should_receive("get_by_account_login").with_args(
        "example-user",
    ).and_return(flexmock(sender_login="phracek"))

    msg = (
        "Incorrect format of the Packit verification comment command. The expected format: "
        "`/packit verify-fas my-fas-account`"
    )
    flexmock(issue).should_receive("comment").with_args(msg).once()

    results = run_github_fas_verification_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert not first_dict_value(results["job"])["success"]


def test_verification_already_approved():
    config = ServiceConfig()
    config.command_handler_work_dir = SANDCASTLE_WORK_DIR
    config.deployment = Deployment.prod
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(config)

    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("get_releases").and_return([])
    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).and_return(None)

    issue_comment = flexmock()

    issue = flexmock(
        author="packit-as-a-service[bot]",
        title="User example-user needs to be approved.",
        close=lambda: None,
        get_comment=lambda issue_id: issue_comment,
    )
    flexmock(issue_comment).should_receive("add_reaction").once()
    flexmock(GithubProject).should_receive("get_issue").and_return(issue)

    processing_results = SteveJobs().process_message(issue_comment_event())
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    flexmock(Allowlist).should_receive("is_namespace_or_parent_approved").and_return(
        True,
    )
    flexmock(AllowlistModel).should_receive("add_namespace").never()
    flexmock(GithubInstallationModel).should_receive("get_by_account_login").with_args(
        "example-user",
    ).and_return(flexmock(sender_login="phracek"))

    msg = "Namespace `github.com/example-user` was already approved."
    flexmock(issue).should_receive("comment").with_args(msg).once()

    results = run_github_fas_verification_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]


def test_verification_wrong_repository():
    config = ServiceConfig()
    config.command_handler_work_dir = SANDCASTLE_WORK_DIR
    config.deployment = Deployment.prod
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(config)

    flexmock(Pushgateway).should_receive("push").times(1).and_return()
    flexmock(Signature).should_receive("apply_async").never()
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("get_releases").and_return([])
    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).and_return(None)

    issue = flexmock(
        author="not-packit",
        title="User example-user needs to be approved.",
        close=lambda: None,
    )
    flexmock(GithubProject).should_receive("get_issue").and_return(issue)

    processing_results = SteveJobs().process_message(issue_comment_event())
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)


def test_verification_wrong_issue():
    config = ServiceConfig()
    config.command_handler_work_dir = SANDCASTLE_WORK_DIR
    config.deployment = Deployment.prod
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(config)

    flexmock(Pushgateway).should_receive("push").times(1).and_return()
    flexmock(Signature).should_receive("apply_async").never()
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("get_releases").and_return([])
    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).and_return(None)

    issue = flexmock(
        author="not-packit",
        title="User example-user needs to be approved.",
        close=lambda: None,
    )
    flexmock(GithubProject).should_receive("get_issue").and_return(issue)

    processing_results = SteveJobs().process_message(issue_comment_event())
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)


def test_verification_not_original_triggerer():
    config = ServiceConfig()
    config.command_handler_work_dir = SANDCASTLE_WORK_DIR
    config.deployment = Deployment.prod
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(config)

    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("get_releases").and_return([])
    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).and_return(None)

    issue_comment = flexmock()

    issue = flexmock(
        author="packit-as-a-service[bot]",
        title="User example-user needs to be approved.",
        close=lambda: None,
        get_comment=lambda issue_id: issue_comment,
    )
    flexmock(issue_comment).should_receive("add_reaction").once()
    flexmock(GithubProject).should_receive("get_issue").and_return(issue)

    processing_results = SteveJobs().process_message(issue_comment_event())
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    flexmock(Allowlist).should_receive("is_namespace_or_parent_approved").and_return(
        True,
    )
    flexmock(AllowlistModel).should_receive("add_namespace").never()
    flexmock(GithubInstallationModel).should_receive("get_by_account_login").with_args(
        "example-user",
    ).and_return(flexmock(sender_login="somebody-else"))

    msg = (
        "Packit verification comment command not created by the person who "
        "installed the application."
    )
    flexmock(issue).should_receive("comment").with_args(msg).once()

    results = run_github_fas_verification_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]
