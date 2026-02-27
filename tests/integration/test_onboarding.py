# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json

import pytest
from celery.canvas import Signature
from flexmock import flexmock
from ogr.services.pagure import PagureProject
from packit.api import PackitAPI
from packit.config import Deployment

from packit_service.config import ServiceConfig
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.tasks import (
    run_onboarding_request_handler,
)
from tests.spellbook import DATA_DIR, first_dict_value, get_parameters_from_results


@pytest.fixture()
def onboarding_request_event():
    return json.loads((DATA_DIR / "webhooks" / "onboarding" / "request.json").read_text())


def test_onboarding(onboarding_request_event, tmp_path):
    dg_project = (
        flexmock(PagureProject(namespace="rpms", repo="packit", service=flexmock(read_only=False)))
        .should_receive("is_private")
        .and_return(False)
        .mock()
        .should_receive("get_files")
        .and_return(["packit.spec", "sources"])
        .mock()
    )
    service_config = (
        flexmock(deployment=Deployment.stg)
        .should_receive("get_project")
        .and_return(dg_project)
        .mock()
    )
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(service_config)

    flexmock(PackitAPI).should_receive("init_kerberos_ticket")

    git_repo = tmp_path / "dist-git"
    git_repo.mkdir()

    dg = (
        flexmock(local_project=flexmock(working_dir=git_repo))
        .should_receive("create_branch")
        .twice()
        .mock()
        .should_receive("update_branch")
        .once()
        .mock()
        .should_receive("switch_branch")
        .twice()
        .mock()
        .should_receive("reset_workdir")
        .once()
        .mock()
        .should_receive("commit")
        .once()
        .mock()
    )
    flexmock(PackitAPI).should_receive("dg").and_return(dg)

    flexmock(PackitAPI).should_receive("push_and_create_pr").once()

    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").twice().and_return()

    processing_results = SteveJobs().process_message(onboarding_request_event)
    event_dict, _, job_config, package_config = get_parameters_from_results(
        processing_results[:1],
    )
    assert json.dumps(event_dict)
    results = run_onboarding_request_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]

    generated_config = (git_repo / ".packit.yaml").read_text()

    assert "pull_from_upstream" in generated_config
    assert "koji_build" in generated_config
    assert "bodhi_update" in generated_config
