# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json
from datetime import datetime

import pytest
from flexmock import flexmock
from ogr.services.github import GithubProject

from packit_service.events.testing_farm import Result
from packit_service.models import (
    CoprBuildTargetModel,
    TestingFarmResult,
    TFTTestRunTargetModel,
    get_submitted_time_from_model,
)
from packit_service.worker.helpers.testing_farm import TestingFarmClient
from packit_service.worker.parser import Parser
from tests.spellbook import DATA_DIR


@pytest.fixture()
def testing_farm_notification():
    with open(DATA_DIR / "webhooks" / "testing_farm" / "notification.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def testing_farm_results():
    with open(DATA_DIR / "webhooks" / "testing_farm" / "results.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def testing_farm_results_error():
    with open(DATA_DIR / "webhooks" / "testing_farm" / "results_error.json") as outfile:
        return json.load(outfile)


@pytest.mark.parametrize("identifier", [None, "foo"])
def test_parse_testing_farm_notification(
    testing_farm_notification,
    testing_farm_results,
    identifier,
):
    request_id = "129bd474-e4d3-49e0-9dec-d994a99feebc"
    flexmock(TestingFarmClient).should_receive("get_request_details").with_args(
        request_id,
    ).and_return(testing_farm_results)
    flexmock(TFTTestRunTargetModel).should_receive("get_by_pipeline_id").and_return(
        flexmock(
            project_event=flexmock(),
            data={"base_project_url": "https://github.com/packit/packit"},
            commit_sha="12345",
            identifier=identifier,
        )
        .should_receive("get_project_event_object")
        .and_return(flexmock(pr_id=10))
        .once()
        .mock()
        .should_receive("get_project_event_model")
        .and_return(
            flexmock(
                pr_id=10,
                packages_config={
                    "specfile_path": "path.spec",
                    "downstream_package_name": "packit",
                },
            ),
        )
        .once()
        .mock(),
    )
    event_object = Parser.parse_event(testing_farm_notification)

    assert isinstance(event_object, Result)
    assert event_object.pipeline_id == request_id
    assert event_object.result == TestingFarmResult.passed
    assert event_object.project_url == "https://github.com/packit/packit"
    assert event_object.commit_sha == "12345"
    assert not event_object.summary
    assert event_object.compose == "Fedora-32"
    assert event_object.copr_build_id == "1810530"
    assert event_object.copr_chroot == "fedora-32-x86_64"
    assert event_object.db_project_object
    assert isinstance(event_object.project, GithubProject)
    assert event_object.project.full_repo_name == "packit/packit"
    assert event_object.identifier == identifier
    assert event_object.packages_config


def test_parse_testing_farm_notification_error(
    testing_farm_notification,
    testing_farm_results_error,
):
    request_id = "129bd474-e4d3-49e0-9dec-d994a99feebc"
    flexmock(TestingFarmClient).should_receive("get_request_details").with_args(
        request_id,
    ).and_return(testing_farm_results_error)
    flexmock(TFTTestRunTargetModel).should_receive("get_by_pipeline_id").and_return(
        flexmock(
            project_event=flexmock(),
            data={"base_project_url": "https://github.com/packit/packit"},
            commit_sha="12345",
            identifier=None,
        )
        .should_receive("get_project_event_object")
        .and_return(flexmock(pr_id=10))
        .once()
        .mock(),
    )
    event_object = Parser.parse_event(testing_farm_notification)

    assert isinstance(event_object, Result)
    assert event_object.pipeline_id == request_id
    assert event_object.result == TestingFarmResult.error
    assert event_object.project_url == "https://github.com/packit/packit"
    assert event_object.commit_sha == "12345"
    assert event_object.summary == "something went wrong"
    assert event_object.compose == "Fedora-32"
    assert event_object.copr_build_id == "1810530"
    assert event_object.copr_chroot == "fedora-32-x86_64"
    assert event_object.db_project_object
    assert isinstance(event_object.project, GithubProject)
    assert event_object.project.full_repo_name == "packit/packit"
    assert not event_object.identifier


def test_get_project_testing_farm_notification(
    testing_farm_notification,
    testing_farm_results,
    mock_config,
):
    request_id = "129bd474-e4d3-49e0-9dec-d994a99feebc"
    flexmock(TestingFarmClient).should_receive("get_request_details").with_args(
        request_id,
    ).and_return(testing_farm_results)
    flexmock(TFTTestRunTargetModel).should_receive("get_by_pipeline_id").with_args(
        request_id,
    ).and_return(
        flexmock(data={"base_project_url": "abc"}, commit_sha="12345", identifier=None),
    )
    event_object = Parser.parse_event(testing_farm_notification)

    assert isinstance(event_object, Result)
    assert isinstance(event_object.pipeline_id, str)
    assert event_object.pipeline_id == request_id
    assert event_object.project_url == "abc"
    assert event_object.commit_sha == "12345"


def test_json_testing_farm_notification(
    testing_farm_notification,
    testing_farm_results,
):
    flexmock(TestingFarmClient).should_receive("get_request_details").and_return(
        testing_farm_results,
    )
    flexmock(TFTTestRunTargetModel).should_receive("get_by_pipeline_id").and_return(
        flexmock(data={"base_project_url": "abc"}, commit_sha="12345", identifier=None),
    )
    event_object = Parser.parse_event(testing_farm_notification)
    assert json.dumps(event_object.pipeline_id)


def test_get_submitted_time_from_model():
    date = datetime.utcnow()

    fake_tf = flexmock(submitted_time=date)
    flexmock(TFTTestRunTargetModel).new_instances(fake_tf)
    tf = TFTTestRunTargetModel()
    tf.__class__ = TFTTestRunTargetModel
    assert date == get_submitted_time_from_model(tf)

    fake_copr = flexmock(build_submitted_time=date)
    flexmock(CoprBuildTargetModel).new_instances(fake_copr)
    copr = CoprBuildTargetModel()
    copr.__class__ = CoprBuildTargetModel  # to pass in isinstance(model, CoprBuildTargetModel)
    assert date == get_submitted_time_from_model(copr)
