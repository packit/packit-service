# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
Unit tests for LogDetectiveKojiTriggerHelper class.
"""

import pytest
import requests
from flexmock import flexmock

from packit_service.constants import LOGDETECTIVE_PACKIT_SERVER_URL, KojiTaskState
from packit_service.models import (
    LogDetectiveBuildSystem,
    LogDetectiveResult,
    LogDetectiveRunGroupModel,
    LogDetectiveRunModel,
)
from packit_service.worker.helpers.logdetective import LogDetectiveKojiTriggerHelper


@pytest.fixture
def mock_pushgateway():
    pushgateway = flexmock()
    pushgateway.log_detective_runs_started = flexmock()
    pushgateway.log_detective_runs_started.should_receive("inc")
    pushgateway.should_receive("push").and_return()
    return pushgateway


@pytest.fixture
def mock_koji_task_failed_event():
    mock_group = flexmock(runs=[flexmock()])
    mock_build_model = flexmock(group_of_targets=mock_group)

    event = flexmock(
        task_id=12345,
        state=KojiTaskState.failed,
        old_state=KojiTaskState.open,
        commit_sha="abc123",
        project_url="https://github.com/test/repo",
        pr_id=42,
        target="fedora-rawhide-x86_64",
        build_model=mock_build_model,
    )

    event.should_receive("get_koji_build_logs_url").with_args(12345).and_return(
        "https://kojipkgs.fedoraproject.org//work/tasks/2345/12345/build.log"
    )
    return event


def test_logdetective_koji_init_sets_artifacts_correctly(
    mock_koji_task_failed_event, mock_pushgateway
):
    helper = LogDetectiveKojiTriggerHelper(
        mock_koji_task_failed_event,
        mock_pushgateway,
        LOGDETECTIVE_PACKIT_SERVER_URL,
    )

    assert "build.log" in helper.artifacts
    assert (
        helper.artifacts["build.log"]
        == "https://kojipkgs.fedoraproject.org//work/tasks/2345/12345/build.log"
    )


def test_logdetective_koji_success(mock_koji_task_failed_event, mock_pushgateway):
    mock_response = flexmock(status_code=200)
    mock_response.should_receive("raise_for_status")
    mock_response.should_receive("json").and_return(
        {
            "log_detective_analysis_id": "test-uuid-123",
            "creation_time": "2026-01-01T12:00:00",
        }
    )

    flexmock(requests).should_receive("post").with_args(
        f"{LOGDETECTIVE_PACKIT_SERVER_URL}/api/analyze",
        json={
            "artifacts": {
                "build.log": "https://kojipkgs.fedoraproject.org//work/tasks/2345/12345/build.log"
            },
            "target_build": "12345",
            "build_system": LogDetectiveBuildSystem.koji.value,
            "commit_sha": "abc123",
            "project_url": "https://github.com/test/repo",
            "pr_id": 42,
        },
        timeout=30,
    ).once().and_return(mock_response)

    mock_group_run = flexmock()
    flexmock(LogDetectiveRunGroupModel).should_receive("create").once().and_return(mock_group_run)
    flexmock(LogDetectiveRunModel).should_receive("create").with_args(
        LogDetectiveResult.running,
        "12345",
        "fedora-rawhide-x86_64",
        LogDetectiveBuildSystem.koji,
        "test-uuid-123",
        mock_group_run,
    ).once()

    mock_koji_task_failed_event.build_model.should_receive("add_log_detective_run").with_args(
        "test-uuid-123"
    ).once()

    helper = LogDetectiveKojiTriggerHelper(
        mock_koji_task_failed_event,
        mock_pushgateway,
        LOGDETECTIVE_PACKIT_SERVER_URL,
    )
    result = helper.trigger_log_detective_analysis()

    assert result["success"] is True
    assert result["details"]["log_detective_analysis_id"] == "test-uuid-123"
    assert "Successfully triggered" in result["details"]["msg"]


def test_logdetective_koji_http_error(mock_koji_task_failed_event, mock_pushgateway):
    flexmock(requests).should_receive("post").and_raise(
        requests.exceptions.HTTPError("500 Server Error")
    )

    # DB operations should NOT be called on failure
    flexmock(LogDetectiveRunGroupModel).should_receive("create").never()
    flexmock(LogDetectiveRunModel).should_receive("create").never()

    helper = LogDetectiveKojiTriggerHelper(
        mock_koji_task_failed_event,
        mock_pushgateway,
        LOGDETECTIVE_PACKIT_SERVER_URL,
    )
    result = helper.trigger_log_detective_analysis()

    assert result["success"] is False
    assert "Failed to trigger Log Detective" in result["details"]["msg"]


def test_logdetective_koji_connection_error(mock_koji_task_failed_event, mock_pushgateway):
    flexmock(requests).should_receive("post").and_raise(
        requests.exceptions.ConnectionError("Connection refused")
    )

    helper = LogDetectiveKojiTriggerHelper(
        mock_koji_task_failed_event,
        mock_pushgateway,
        LOGDETECTIVE_PACKIT_SERVER_URL,
    )
    result = helper.trigger_log_detective_analysis()

    assert result["success"] is False
    assert "Failed to trigger Log Detective" in result["details"]["msg"]


def test_logdetective_koji_json_decode_error(mock_koji_task_failed_event, mock_pushgateway):
    mock_response = flexmock()
    mock_response.should_receive("raise_for_status")
    mock_response.should_receive("json").and_raise(
        requests.exceptions.JSONDecodeError("Invalid JSON", "", 0)
    )

    flexmock(requests).should_receive("post").and_return(mock_response)

    helper = LogDetectiveKojiTriggerHelper(
        mock_koji_task_failed_event,
        mock_pushgateway,
        LOGDETECTIVE_PACKIT_SERVER_URL,
    )
    result = helper.trigger_log_detective_analysis()

    assert result["success"] is False
    assert "Failed to parse Log Detective response" in result["details"]["msg"]


def test_logdetective_koji_timeout(mock_koji_task_failed_event, mock_pushgateway):
    flexmock(requests).should_receive("post").and_raise(
        requests.exceptions.Timeout("Request timed out")
    )

    helper = LogDetectiveKojiTriggerHelper(
        mock_koji_task_failed_event,
        mock_pushgateway,
        LOGDETECTIVE_PACKIT_SERVER_URL,
    )
    result = helper.trigger_log_detective_analysis()

    assert result["success"] is False
    assert "Failed to trigger Log Detective" in result["details"]["msg"]
