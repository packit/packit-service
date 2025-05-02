# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json

import pytest
from celery.canvas import Signature
from flexmock import flexmock

from packit_service.config import ServiceConfig
from packit_service.constants import SANDCASTLE_WORK_DIR
from packit_service.models import (
    AllowlistModel,
    GithubInstallationModel,
)
from packit_service.worker.allowlist import Allowlist
from packit_service.worker.handlers import forges
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.tasks import run_installation_handler
from tests.spellbook import DATA_DIR, first_dict_value, get_parameters_from_results


def installation_event():
    return json.loads(
        (DATA_DIR / "webhooks" / "github" / "installation_created.json").read_text(),
    )


def test_installation():
    config = ServiceConfig()
    config.command_handler_work_dir = SANDCASTLE_WORK_DIR
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(config)
    flexmock(GithubInstallationModel).should_receive(
        "get_by_account_login",
    ).and_return()
    flexmock(GithubInstallationModel).should_receive("create_or_update").once()
    flexmock(AllowlistModel).should_receive("get_namespace").with_args(
        "github.com/packit-service",
    ).and_return(None)
    flexmock(Allowlist).should_receive(
        "is_github_username_from_fas_account_matching",
    ).with_args(fas_account="jpopelka", sender_login="jpopelka").and_return(False)
    flexmock(forges).should_receive("create_issue_if_needed").once()
    flexmock(AllowlistModel).should_receive("add_namespace")

    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    processing_results = SteveJobs().process_message(installation_event())
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    results = run_installation_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]


def test_reinstallation_already_approved_namespace():
    config = ServiceConfig()
    config.command_handler_work_dir = SANDCASTLE_WORK_DIR
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(config)
    flexmock(GithubInstallationModel).should_receive("get_by_account_login").and_return(
        flexmock(sender_login="jpopelka"),
    )
    flexmock(GithubInstallationModel).should_receive("create_or_update").once()
    flexmock(AllowlistModel).should_receive("get_namespace").with_args(
        "github.com/packit-service",
    ).and_return(flexmock())
    flexmock(Allowlist).should_receive("is_namespace_or_parent_approved").with_args(
        "github.com/packit-service",
    ).and_return(True)
    flexmock(forges).should_receive("create_issue_if_needed").never()

    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    processing_results = SteveJobs().process_message(installation_event())
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    results = run_installation_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]


def test_reinstallation_denied_namespace():
    config = ServiceConfig()
    config.command_handler_work_dir = SANDCASTLE_WORK_DIR
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(config)
    flexmock(GithubInstallationModel).should_receive("get_by_account_login").and_return(
        flexmock(sender_login="jpopelka"),
    )
    flexmock(GithubInstallationModel).should_receive("create_or_update").once()
    flexmock(AllowlistModel).should_receive("get_namespace").with_args(
        "github.com/packit-service",
    ).and_return(flexmock())
    flexmock(Allowlist).should_receive("is_namespace_or_parent_approved").with_args(
        "github.com/packit-service",
    ).and_return(False)
    flexmock(Allowlist).should_receive("is_denied").with_args(
        "github.com/packit-service",
    ).and_return(True)
    flexmock(forges).should_receive("create_issue_if_needed").never()

    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    processing_results = SteveJobs().process_message(installation_event())
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    results = run_installation_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]


@pytest.mark.parametrize(
    "previous_sender_login, create_issue",
    [("jpopelka", False), ("flachman", True)],
)
def test_reinstallation_not_approved_namespace(previous_sender_login, create_issue):
    config = ServiceConfig()
    config.command_handler_work_dir = SANDCASTLE_WORK_DIR
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(config)
    flexmock(GithubInstallationModel).should_receive("get_by_account_login").and_return(
        flexmock(sender_login=previous_sender_login),
    )
    flexmock(GithubInstallationModel).should_receive("create_or_update").once()
    flexmock(AllowlistModel).should_receive("get_namespace").with_args(
        "github.com/packit-service",
    ).and_return(flexmock())
    flexmock(Allowlist).should_receive("is_namespace_or_parent_approved").with_args(
        "github.com/packit-service",
    ).and_return(False)
    flexmock(Allowlist).should_receive("is_denied").with_args(
        "github.com/packit-service",
    ).and_return(False)
    if create_issue:
        flexmock(Allowlist).should_receive(
            "is_github_username_from_fas_account_matching",
        ).with_args(fas_account="jpopelka", sender_login="jpopelka").and_return(False)
        flexmock(forges).should_receive("create_issue_if_needed").once()
        flexmock(AllowlistModel).should_receive("add_namespace").once()
    else:
        flexmock(forges).should_receive("create_issue_if_needed").never()

    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    processing_results = SteveJobs().process_message(installation_event())
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    results = run_installation_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]
