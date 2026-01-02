# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json
from datetime import datetime, timezone

import pytest
from flexmock import flexmock
from packit.config import JobConfig, JobConfigTriggerType, JobType, PackageConfig

from packit_service.events.logdetective import Result as LogDetectiveResultEvent
from packit_service.models import (
    CoprBuildTargetModel,
    KojiBuildTargetModel,
    LogDetectiveBuildSystem,
    LogDetectiveResult,
    LogDetectiveRunModel,
)
from packit_service.worker.handlers import DownstreamLogDetectiveResultsHandler
from packit_service.worker.helpers.fedora_ci import FedoraCIHelper
from packit_service.worker.parser import Parser
from packit_service.worker.reporting import BaseCommitStatus
from packit_service.worker.result import TaskResults


def test_logdetective_result_event_type():
    """Test event type return value"""

    assert LogDetectiveResultEvent.event_type() == "logdetective.result"


@pytest.fixture
def handler_and_models():
    package_config = PackageConfig(
        jobs=[
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={"package": {}},
            )
        ],
        packages={"package": {}},
    )
    job_config = JobConfig(
        type=JobType.copr_build,
        trigger=JobConfigTriggerType.pull_request,
        packages={"package": {}},
    )
    event_data = {
        "status": "complete",
        "log_detective_analysis_start": "2024-01-01T12:00:00+00:00",
        "target_build": "999",
        "build_system": "copr",
        "log_detective_analysis_id": "123456",
    }

    handler = DownstreamLogDetectiveResultsHandler(
        package_config=package_config,
        job_config=job_config,
        event=event_data,
    )

    flexmock(handler).should_receive("project").and_return(flexmock())

    return handler


@pytest.mark.parametrize("status", [e.value for e in LogDetectiveResult])
@pytest.mark.parametrize("build_system", [e.value for e in LogDetectiveBuildSystem])
def test_parse_logdetective_analysis_result(
    logdetective_analysis_result,
    log_detective_result_event_creation,
    log_detective_run_model_get_by_identifier,
    build_system,
    status,
):
    """Test standard message from Log Detective with all combinations of build systems
    and result states"""

    logdetective_analysis_result["build_system"] = build_system
    logdetective_analysis_result["status"] = status

    event_object = Parser.parse_event(logdetective_analysis_result)

    assert isinstance(event_object, LogDetectiveResultEvent)
    assert isinstance(event_object.log_detective_response, dict)
    assert event_object.target_build == "9999"
    assert event_object.status == status
    assert event_object.build_system == LogDetectiveBuildSystem(build_system)

    # Attempt to serialize dictionary form of the object
    object_dict = event_object.get_dict()
    json.dumps(object_dict)


@pytest.mark.parametrize("build_system", [e.value for e in LogDetectiveBuildSystem])
def test_parse_logdetective_analysis_result_error(
    logdetective_analysis_result_error,
    log_detective_result_event_creation,
    log_detective_run_model_get_by_identifier,
    build_system,
):
    """When analysis returns `error` result, the `log_detective_response`
    is left empty."""

    logdetective_analysis_result_error["build_system"] = build_system
    event_object = Parser.parse_event(logdetective_analysis_result_error)

    assert isinstance(event_object, LogDetectiveResultEvent)
    assert event_object.log_detective_response is None
    assert event_object.target_build == "9999"
    assert event_object.status == "error"
    assert event_object.build_system == LogDetectiveBuildSystem(build_system)

    # Attempt to serialize dictionary form of the object
    object_dict = event_object.get_dict()
    json.dumps(object_dict)


def test_parse_logdetective_analysis_result_wrong_build_system(logdetective_analysis_result):
    """Test that results from unsupported build systems are discarded"""

    logdetective_analysis_result["build_system"] = "unsupported_build_system"
    event_object = Parser.parse_event(logdetective_analysis_result)

    assert event_object is None


@pytest.mark.parametrize(
    "status_str, expected_status, build_system",
    [
        (
            "complete",
            BaseCommitStatus.success,
            "copr",
        ),
        (
            "running",
            BaseCommitStatus.running,
            "copr",
        ),
        (
            "error",
            BaseCommitStatus.error,
            "koji",
        ),
        (
            "unknown",
            BaseCommitStatus.error,
            "koji",
        ),
    ],
)
def test_logdetective_run_success(
    handler_and_models,
    status_str,
    expected_status,
    build_system,
):
    """Test successful run of the handler starting with a record of Log Detective run
    in an unknown state."""
    handler = handler_and_models
    handler.status = LogDetectiveResult.from_string(status_str)
    handler.build_system = build_system

    # Mock LogDetectiveRunModel if the new state is `LogDetectiveResult.unknown`
    # we must change the mock so that the existing state is different
    # otherwise, the `run` method would report that there is nothing left
    # for it to change.
    if status_str == "unknown":
        run_model = flexmock(
            status=LogDetectiveResult.running,
            submitted_time=datetime.now(timezone.utc),
            copr_build_target_id=10,
            koji_build_target_id=20,
        )
    else:
        run_model = flexmock(
            status=LogDetectiveResult.unknown,
            submitted_time=datetime.now(timezone.utc),
            copr_build_target_id=10,
            koji_build_target_id=20,
        )
    flexmock(LogDetectiveRunModel).should_receive("get_by_log_detective_analysis_id").with_args(
        analysis_id="123456"
    ).and_return(run_model)

    # Expect set_status to be called with a datetime object
    run_model.should_receive("set_status").with_args(
        handler.status,
        log_detective_analysis_start=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc).replace(
            tzinfo=None
        ),
    ).once()

    # Mock Build Model
    build_model = flexmock(web_url="https://build.url")
    build_model.should_receive("get_branch_name").and_return("main")

    if build_system == "copr":
        flexmock(CoprBuildTargetModel).should_receive("get_by_id").with_args(10).and_return(
            build_model
        )
    else:
        flexmock(KojiBuildTargetModel).should_receive("get_by_id").with_args(20).and_return(
            build_model
        )

    # Mock FedoraCIHelper report
    flexmock(FedoraCIHelper).should_receive("report").with_args(
        state=expected_status,
        description=f"Log Detective analysis status: {status_str}",
        url="https://build.url",
        check_name="Log Detective Analysis",
    ).once()

    # Mock Metrics
    if status_str == "running":
        flexmock(handler.pushgateway).should_receive("log_detective_runs_started.inc").once()
        flexmock(handler.pushgateway).should_receive("log_detective_runs_finished.inc").never()
    else:
        flexmock(handler.pushgateway).should_receive("log_detective_runs_started.inc").never()
        flexmock(handler.pushgateway).should_receive("log_detective_runs_finished.inc").once()
        flexmock(handler.pushgateway).should_receive("log_detective_run_finished.observe").once()

    result = handler.run()
    assert isinstance(result, TaskResults)
    assert result["success"]
    details = result["details"]
    assert isinstance(details, dict)
    assert len(details) == 0


def test_logdetective_run_unknown_identifier(handler_and_models):
    """Test that when no `LogDetectiveRunModel` can be retrieved,
    the issue is reported in details and result is not a success."""
    handler = handler_and_models

    flexmock(LogDetectiveRunModel).should_receive("get_by_log_detective_analysis_id").with_args(
        analysis_id="123456"
    ).and_return(None)

    result = handler.run()
    assert isinstance(result, TaskResults)
    assert not result["success"]
    details = result["details"]
    assert isinstance(details, dict)
    assert "Unknown identifier received" in details["msg"]


def test_logdetective_run_already_processed(handler_and_models):
    """Test that an already processed run is reported."""
    handler = handler_and_models

    # Simulate DB already having the same status
    run_model = flexmock(status=LogDetectiveResult.complete)
    flexmock(LogDetectiveRunModel).should_receive("get_by_log_detective_analysis_id").and_return(
        run_model
    )

    flexmock(FedoraCIHelper).should_receive("report").never()

    result = handler.run()
    assert isinstance(result, TaskResults)
    assert result["success"]
    details = result["details"]
    assert isinstance(details, dict)
    assert "already processed" in details["msg"]


def test_logdetective_run_build_not_found(handler_and_models):
    handler = handler_and_models

    run_model = flexmock(
        status=LogDetectiveResult.running,
        submitted_time=datetime.now(timezone.utc),
        copr_build_target_id=10,
    )
    flexmock(LogDetectiveRunModel).should_receive("get_by_log_detective_analysis_id").and_return(
        run_model
    )

    # Simulate build lookup failure
    flexmock(CoprBuildTargetModel).should_receive("get_by_id").with_args(10).and_return(None)

    flexmock(FedoraCIHelper).should_receive("report").never()

    result = handler.run()
    assert isinstance(result, TaskResults)
    assert not result["success"]
    details = result["details"]
    assert isinstance(details, dict)
    assert "No build with id" in details["msg"]


def test_logdetective_run_empty_url_fallback(handler_and_models):
    """Test that missing web_url in build model is handled by passing empty string"""
    handler = handler_and_models
    handler.build_system = "copr"

    run_model = flexmock(
        status=LogDetectiveResult.running,
        submitted_time=datetime.now(timezone.utc),
        copr_build_target_id=10,
    )
    run_model.should_receive("set_status")
    flexmock(LogDetectiveRunModel).should_receive("get_by_log_detective_analysis_id").and_return(
        run_model
    )

    # Build has no web_url
    build_model = flexmock(web_url=None)
    build_model.should_receive("get_branch_name").and_return("main")
    flexmock(CoprBuildTargetModel).should_receive("get_by_id").and_return(build_model)

    flexmock(FedoraCIHelper).should_receive("report").with_args(
        state=BaseCommitStatus.success,
        description="Log Detective analysis status: complete",
        url="",
        check_name="Log Detective Analysis",
    ).once()

    result = handler.run()

    assert isinstance(result, TaskResults)
    assert result["success"]
    assert result["details"] == {}


def test_logdetective_run_no_project(handler_and_models):
    """Test that when attribute `project` is none the handler fails gracefully"""

    event_dict = {
        "event_type": "logdetective.result",
        "status": "complete",
        "log_detective_analysis_id": "123456",
        "log_detective_analysis_start": "2024-01-01T12:00:00",
        "target_build": "123456",
        "build_system": "copr",
        "project_url": None,
        "commit_sha": None,
        "pr_id": None,
    }

    handler = DownstreamLogDetectiveResultsHandler(
        package_config=flexmock(), job_config=flexmock(), event=event_dict
    )

    result = handler.run()

    assert isinstance(result, TaskResults)
    assert not result["success"]
    assert result["details"] == {"msg": "No project set for Log Detective run: 123456"}
