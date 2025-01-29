# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import datetime
import json

import pytest
from flexmock import flexmock
from packit.config import (
    CommonPackageConfig,
    JobConfig,
    JobConfigTriggerType,
    JobType,
    PackageConfig,
)

from packit_service.events.openscanhub.task import Finished, Started
from packit_service.models import OSHScanModel
from packit_service.worker.parser import Parser
from tests.spellbook import DATA_DIR


@pytest.fixture()
def openscanhub_task_finished_event():
    with open(DATA_DIR / "fedmsg" / "open_scan_hub_task_finished.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def openscanhub_task_started_event():
    with open(DATA_DIR / "fedmsg" / "open_scan_hub_task_started.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def scan_config_and_db(add_pull_request_event_with_sha_123456):
    db_project_object, db_project_event = add_pull_request_event_with_sha_123456
    db_build = (
        flexmock(
            build_id="55",
            identifier=None,
            status="success",
            build_submitted_time=datetime.datetime.utcnow(),
            target="the-target",
            owner="the-owner",
            project_name="the-namespace-repo_name-5",
            commit_sha="123456",
            project_event=flexmock(),
            srpm_build=flexmock(url=None)
            .should_receive("set_url")
            .with_args("https://some.host/my.srpm")
            .mock(),
        )
        .should_receive("get_project_event_object")
        .and_return(db_project_object)
        .mock()
        .should_receive("get_project_event_model")
        .and_return(db_project_event)
        .mock()
    )
    flexmock(Finished).should_receive(
        "get_packages_config",
    ).and_return(
        PackageConfig(
            jobs=[
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=["fedora-rawhide-x86_64"],
                        ),
                    },
                ),
            ],
            packages={"package": CommonPackageConfig()},
        ),
    )
    flexmock(OSHScanModel).should_receive("get_by_task_id").and_return(
        flexmock(copr_build_target=db_build),
    )


def test_parse_openscanhub_task_finished(
    openscanhub_task_finished_event,
    scan_config_and_db,
):
    event_object = Parser.parse_event(openscanhub_task_finished_event)

    assert isinstance(event_object, Finished)
    assert event_object.task_id == 15649
    assert (
        event_object.issues_added_url
        == "http://openscanhub.fedoraproject.org/task/15649/log/added.js?format=raw"
    )
    assert (
        event_object.issues_fixed_url
        == "http://openscanhub.fedoraproject.org/task/15649/log/fixed.js?format=raw"
    )
    assert event_object.scan_results_url == (
        "http://openscanhub.fedoraproject.org/task/15649/log/gvisor-tap-vsock"
        "-0.7.5-1.20241007054606793155.pr405.23.g829aafd6/scan-results.js?format=raw"
    )
    assert event_object.db_project_event
    assert event_object.db_project_object
    assert event_object.project
    assert json.dumps(event_object.get_dict())


def test_parse_openscanhub_task_started(
    openscanhub_task_started_event,
    scan_config_and_db,
):
    event_object = Parser.parse_event(openscanhub_task_started_event)

    assert isinstance(event_object, Started)
    assert event_object.task_id == 15649
    assert event_object.db_project_event
    assert event_object.db_project_object
    assert event_object.project
    assert json.dumps(event_object.get_dict())
