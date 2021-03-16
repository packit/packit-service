# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json

from celery.canvas import Signature
from flexmock import flexmock

from ogr.services.github import GithubProject
from packit_service.config import ServiceConfig
from packit_service.constants import SANDCASTLE_WORK_DIR
from packit_service.models import (
    InstallationModel,
)
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.tasks import run_installation_handler
from packit_service.worker.allowlist import Allowlist
from tests.spellbook import DATA_DIR, first_dict_value, get_parameters_from_results


def installation_event():
    return json.loads(
        (DATA_DIR / "webhooks" / "github" / "installation_created.json").read_text()
    )


def test_installation():
    config = ServiceConfig()
    config.command_handler_work_dir = SANDCASTLE_WORK_DIR
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(config)

    flexmock(InstallationModel).should_receive("create").once()
    flexmock(Allowlist).should_receive("add_account").with_args(
        "packit-service", "jpopelka"
    ).and_return(False)
    flexmock(GithubProject).should_receive("create_issue").once()

    flexmock(Signature).should_receive("apply_async").once()
    processing_results = SteveJobs().process_message(installation_event())
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )

    results = run_installation_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]
