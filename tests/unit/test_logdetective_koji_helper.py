# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
Unit tests for LogDetectiveKojiTriggerHelper class.
"""

from unittest.mock import MagicMock

import pytest
import requests
from flexmock import flexmock

import packit_service.worker.helpers.logdetective as logdetective_module
from packit_service.constants import LOGDETECTIVE_PACKIT_SERVER_URL, KojiTaskState
from packit_service.models import (
    GitBranchModel,
    LogDetectiveBuildSystem,
    LogDetectiveResult,
    LogDetectiveRunGroupModel,
    LogDetectiveRunModel,
    ProjectReleaseModel,
    PullRequestModel,
)
from packit_service.worker.helpers.logdetective import (
    LogDetectiveKojiTriggerHelper,
    logger,
)


@pytest.fixture
def mock_pushgateway_log_detective_inc():
    pushgateway = flexmock()
    pushgateway.log_detective_runs_started = flexmock()
    pushgateway.log_detective_runs_started.should_receive("inc").once()
    pushgateway.should_receive("push").and_return()
    return pushgateway


@pytest.fixture
def mock_pushgateway_log_detective_no_inc():
    pushgateway = flexmock()
    pushgateway.log_detective_runs_started = flexmock()
    pushgateway.log_detective_runs_started.should_receive("inc").never()
    pushgateway.should_receive("push").never()
    return pushgateway


@pytest.fixture
def mock_event_data():
    return flexmock(
        commit_sha="abc123",
        project_url="https://github.com/test/repo",
        pr_id=42,
    )


@pytest.fixture
def mock_koji_task_failed_event():
    mock_group = flexmock(runs=[flexmock()])
    mock_build_model = flexmock(
        group_of_targets=mock_group,
        nvr="test-package-1.0-1.fc44",
        scratch=False,
        sidetag=None,
        build_submission_stdout=None,
    )

    return flexmock(
        task_id=12340,
        state=KojiTaskState.failed,
        old_state=KojiTaskState.open,
        target="rawhide",
        build_model=mock_build_model,
        rpm_build_task_ids={"x86_64": 12345},
        rpm_build_failed_arch_list=["x86_64"],
        db_project_object=None,
        start_time=1000,
        completion_time=1045,
    )


def test_logdetective_koji_set_payload(mock_koji_task_failed_event, mock_event_data):
    """
    Build and send the correct payload, then connection error happens ->
    check proper handling and logging
    """
    helper = LogDetectiveKojiTriggerHelper(
        mock_koji_task_failed_event,
        mock_event_data,
        flexmock(),
        "https://kojipkgs.fedoraproject.org",
        LOGDETECTIVE_PACKIT_SERVER_URL,
        "secret-123",
    )

    request_json = {
        "artifacts": {
            "root.log": "https://kojipkgs.fedoraproject.org//work/tasks/2345/12345/root.log",
            "mock_output.log": (
                "https://kojipkgs.fedoraproject.org//work/tasks/2345/12345/mock_output.log"
            ),
            "build.log": "https://kojipkgs.fedoraproject.org//work/tasks/2345/12345/build.log",
        },
        "build_metadata": {
            "commentary": (
                "Build was executed in downstream Koji"
                " using containerized environment provided by Mock."
                " Package NVR: test-package-1.0-1.fc44, target: rawhide, arch: x86_64."
                " Official (non-scratch) build."
                " Build ran for 45 seconds before failing."
                " The build.log contains output of the package build"
                " and is the most likely source of the root cause."
                " The mock_output.log is a general log from Mock."
                " The root.log is a log from creation of the chroot environment."
            ),
        },
        "target_build": "12345",
        "build_system": "koji",
        "commit_sha": "abc123",
        "project_url": "https://github.com/test/repo",
        "pr_id": 42,
    }
    flexmock(logdetective_module).should_receive("verify_artifact").and_return(True)
    flexmock(requests).should_receive("post").with_args(
        "https://logdetective01.fedorainfracloud.org/analyze",
        json=request_json,
        timeout=30,
        headers={"Authorization": "Bearer secret-123"},
    ).once().and_raise(requests.exceptions.ConnectionError)

    flexmock(LogDetectiveRunGroupModel).should_receive("create").never()
    flexmock(logger).should_receive("info").with_args(
        "Triggered Log Detective for a failed Koji build "
        "(child taskID = 12345, arch = x86_64, trigger = fail)"
    )

    trigger_success_list = helper.trigger_log_detective_analysis()
    assert not all(trigger_success_list)


def test_logdetective_koji_success(
    mock_koji_task_failed_event, mock_event_data, mock_pushgateway_log_detective_inc
):
    mock_response = flexmock(status_code=200)
    mock_response.should_receive("raise_for_status")
    mock_response.should_receive("json").and_return(
        {
            "log_detective_analysis_id": "test-uuid-123",
            "creation_time": "2026-01-01T12:00:00",
        }
    )
    flexmock(logdetective_module).should_receive("verify_artifact").and_return(True)
    flexmock(requests).should_receive("post").with_args(
        f"{LOGDETECTIVE_PACKIT_SERVER_URL}/analyze",
        json={
            "artifacts": {
                "root.log": ("https://kojipkgs.fedoraproject.org//work/tasks/2345/12345/root.log"),
                "mock_output.log": (
                    "https://kojipkgs.fedoraproject.org//work/tasks/2345/12345/mock_output.log"
                ),
                "build.log": (
                    "https://kojipkgs.fedoraproject.org//work/tasks/2345/12345/build.log"
                ),
            },
            "build_metadata": {
                "commentary": (
                    "Build was executed in downstream Koji"
                    " using containerized environment provided by Mock."
                    " Package NVR: test-package-1.0-1.fc44, target: rawhide, arch: x86_64."
                    " Official (non-scratch) build."
                    " Build ran for 45 seconds before failing."
                    " The build.log contains output of the package build"
                    " and is the most likely source of the root cause."
                    " The mock_output.log is a general log from Mock."
                    " The root.log is a log from creation of the chroot environment."
                ),
            },
            "target_build": "12345",
            "build_system": LogDetectiveBuildSystem.koji.value,
            "commit_sha": "abc123",
            "project_url": "https://github.com/test/repo",
            "pr_id": 42,
        },
        timeout=30,
        headers={"Authorization": "Bearer secret-123"},
    ).once().and_return(mock_response)

    mock_group_run = flexmock()
    flexmock(LogDetectiveRunGroupModel).should_receive("create").once().and_return(mock_group_run)
    flexmock(LogDetectiveRunModel).should_receive("create").with_args(
        LogDetectiveResult.running,
        "12345",
        "rawhide-x86_64",
        LogDetectiveBuildSystem.koji,
        "test-uuid-123",
        mock_group_run,
    ).once()

    mock_koji_task_failed_event.build_model.should_receive("add_log_detective_run").with_args(
        "test-uuid-123"
    ).once()

    flexmock(logger).should_receive("info").with_args(
        "Triggered Log Detective for a failed Koji build "
        "(child taskID = 12345, arch = x86_64, trigger = success)"
    )
    flexmock(logger).should_call("warning").never()
    flexmock(logger).should_call("error").never()

    helper = LogDetectiveKojiTriggerHelper(
        mock_koji_task_failed_event,
        mock_event_data,
        mock_pushgateway_log_detective_inc,
        "https://kojipkgs.fedoraproject.org",
        LOGDETECTIVE_PACKIT_SERVER_URL,
        "secret-123",
    )
    trigger_success_list = helper.trigger_log_detective_analysis()

    assert all(trigger_success_list)


def test_logdetective_koji_http_error(
    mock_koji_task_failed_event, mock_event_data, mock_pushgateway_log_detective_no_inc
):
    mock_response = flexmock(status_code=500)
    mock_response.should_receive("raise_for_status")
    flexmock(logdetective_module).should_receive("verify_artifact").and_return(True)
    flexmock(requests).should_receive("post").and_raise(
        requests.exceptions.HTTPError("500 Server Error")
    )
    flexmock(LogDetectiveRunGroupModel).should_receive("create").never()
    flexmock(LogDetectiveRunModel).should_receive("create").never()
    flexmock(logger).should_receive("warning").with_args(
        "Failed to get response from Log Detective: 500 Server Error", exc_info=True
    ).once()

    helper = LogDetectiveKojiTriggerHelper(
        mock_koji_task_failed_event,
        mock_event_data,
        mock_pushgateway_log_detective_no_inc,
        "https://kojipkgs.fedoraproject.org",
        LOGDETECTIVE_PACKIT_SERVER_URL,
        "secret-123",
    )
    trigger_success = helper.trigger_log_detective_analysis()

    assert not all(trigger_success)


def test_logdetective_koji_connection_error(
    mock_koji_task_failed_event, mock_event_data, mock_pushgateway_log_detective_no_inc
):
    mock_response = flexmock()
    mock_response.should_receive("raise_for_status")
    flexmock(logdetective_module).should_receive("verify_artifact").and_return(True)
    flexmock(requests).should_receive("post").and_raise(requests.exceptions.ConnectionError)

    flexmock(LogDetectiveRunGroupModel).should_receive("create").never()
    flexmock(LogDetectiveRunModel).should_receive("create").never()
    flexmock(logger).should_receive("warning").once()

    helper = LogDetectiveKojiTriggerHelper(
        mock_koji_task_failed_event,
        mock_event_data,
        mock_pushgateway_log_detective_no_inc,
        "https://kojipkgs.fedoraproject.org",
        LOGDETECTIVE_PACKIT_SERVER_URL,
        "secret-123",
    )
    trigger_success = helper.trigger_log_detective_analysis()

    assert not all(trigger_success)


def test_logdetective_koji_json_decode_error(
    mock_koji_task_failed_event, mock_event_data, mock_pushgateway_log_detective_no_inc
):
    mock_response = flexmock()
    mock_response.should_receive("raise_for_status")
    mock_response.should_receive("json").and_raise(
        requests.exceptions.JSONDecodeError("Invalid JSON", "", 0)
    )
    flexmock(logdetective_module).should_receive("verify_artifact").and_return(True)
    flexmock(requests).should_receive("post").and_return(mock_response)
    flexmock(LogDetectiveRunGroupModel).should_receive("create").never()
    flexmock(LogDetectiveRunModel).should_receive("create").never()
    flexmock(logger).should_receive("warning").once()

    helper = LogDetectiveKojiTriggerHelper(
        mock_koji_task_failed_event,
        mock_event_data,
        mock_pushgateway_log_detective_no_inc,
        "https://kojipkgs.fedoraproject.org",
        LOGDETECTIVE_PACKIT_SERVER_URL,
        "secret-123",
    )
    trigger_success = helper.trigger_log_detective_analysis()

    assert not all(trigger_success)


def test_logdetective_koji_timeout(
    mock_koji_task_failed_event, mock_event_data, mock_pushgateway_log_detective_no_inc
):
    flexmock(logdetective_module).should_receive("verify_artifact").and_return(True)
    flexmock(requests).should_receive("post").and_raise(
        requests.exceptions.Timeout("Request timed out")
    )
    flexmock(logger).should_receive("warning").with_args(
        "Failed to get response from Log Detective: Request timed out",
        exc_info=True,
    ).once()

    helper = LogDetectiveKojiTriggerHelper(
        mock_koji_task_failed_event,
        mock_event_data,
        mock_pushgateway_log_detective_no_inc,
        "https://kojipkgs.fedoraproject.org",
        LOGDETECTIVE_PACKIT_SERVER_URL,
        "secret-123",
    )
    trigger_success = helper.trigger_log_detective_analysis()

    assert not all(trigger_success)


def test_logdetective_koji_missing_id(
    mock_koji_task_failed_event, mock_event_data, mock_pushgateway_log_detective_no_inc
):
    mock_response = flexmock(status_code=200)
    mock_response.should_receive("raise_for_status")
    mock_response.should_receive("json").and_return(
        {
            "creation_time": "2026-01-01T12:00:00",
        }
    )
    flexmock(logdetective_module).should_receive("verify_artifact").and_return(True)
    flexmock(requests).should_receive("post").and_return(mock_response)
    flexmock(logger).should_receive("warning").with_args(
        "Log Detective response is missing log_detective_analysis_id",
    ).once()

    helper = LogDetectiveKojiTriggerHelper(
        mock_koji_task_failed_event,
        mock_event_data,
        mock_pushgateway_log_detective_no_inc,
        "https://kojipkgs.fedoraproject.org",
        LOGDETECTIVE_PACKIT_SERVER_URL,
        "secret-123",
    )
    trigger_success = helper.trigger_log_detective_analysis()

    assert not all(trigger_success)


def test_logdetective_koji_missing_time(
    mock_koji_task_failed_event, mock_event_data, mock_pushgateway_log_detective_no_inc
):
    mock_response = flexmock(status_code=200)
    mock_response.should_receive("raise_for_status")
    mock_response.should_receive("json").and_return(
        {
            "log_detective_analysis_id": "test-uuid-123",
        }
    )
    flexmock(logdetective_module).should_receive("verify_artifact").and_return(True)
    flexmock(requests).should_receive("post").and_return(mock_response)
    flexmock(logger).should_receive("warning").with_args(
        "Log Detective response is missing creation_time",
    ).once()

    helper = LogDetectiveKojiTriggerHelper(
        mock_koji_task_failed_event,
        mock_event_data,
        mock_pushgateway_log_detective_no_inc,
        "https://kojipkgs.fedoraproject.org",
        LOGDETECTIVE_PACKIT_SERVER_URL,
        "secret-123",
    )
    trigger_success = helper.trigger_log_detective_analysis()

    assert not all(trigger_success)


def _make_helper(koji_event, event_data=None):
    """Return a LogDetectiveKojiTriggerHelper with stub credentials."""
    if event_data is None:
        event_data = flexmock(commit_sha="abc", project_url="https://example.com", pr_id=None)
    return LogDetectiveKojiTriggerHelper(
        koji_event,
        event_data,
        flexmock(),
        "https://kojipkgs.fedoraproject.org",
        LOGDETECTIVE_PACKIT_SERVER_URL,
        "token",
    )


def _make_build_model(*, nvr="pkg-1.0-1.fc44", scratch=False, sidetag=None, stdout=None):
    return flexmock(
        nvr=nvr,
        scratch=scratch,
        sidetag=sidetag,
        build_submission_stdout=stdout,
    )


def _make_event(
    build_model, *, target="rawhide", db_project_object=None, start_time=1000, completion_time=1045
):
    return flexmock(
        build_model=build_model,
        target=target,
        db_project_object=db_project_object,
        start_time=start_time,
        completion_time=completion_time,
    )


def test_format_duration_valid():
    event = _make_event(_make_build_model(), start_time=1000, completion_time=1060)
    helper = _make_helper(event)
    assert helper._format_duration() == "Build ran for 60 seconds before failing."


def test_format_duration_zero_seconds():
    event = _make_event(_make_build_model(), start_time=1000, completion_time=1000)
    helper = _make_helper(event)
    assert helper._format_duration() == "Build ran for 0 seconds before failing."


def test_format_duration_start_time_none():
    event = _make_event(_make_build_model(), start_time=None, completion_time=1045)
    helper = _make_helper(event)
    assert helper._format_duration() == ""


def test_format_duration_completion_time_none():
    event = _make_event(_make_build_model(), start_time=1000, completion_time=None)
    helper = _make_helper(event)
    assert helper._format_duration() == ""


def test_format_duration_non_numeric_start():
    event = _make_event(_make_build_model(), start_time="not-a-number", completion_time=1045)
    helper = _make_helper(event)
    assert helper._format_duration() == ""


def test_format_duration_non_numeric_completion():
    event = _make_event(_make_build_model(), start_time=1000, completion_time="bad")
    helper = _make_helper(event)
    assert helper._format_duration() == ""


def test_format_duration_negative():
    # end before start — physically impossible but must be handled gracefully
    event = _make_event(_make_build_model(), start_time=2000, completion_time=1000)
    helper = _make_helper(event)
    assert helper._format_duration() == ""


def test_build_commentary_baseline(mock_koji_task_failed_event):
    """Non-scratch, no project object, known NVR, valid duration, no sidetag, no stdout."""
    helper = _make_helper(mock_koji_task_failed_event)
    result = helper._build_commentary("x86_64")
    assert "Build was executed in downstream Koji" in result
    assert "Package NVR: test-package-1.0-1.fc44, target: rawhide, arch: x86_64." in result
    assert "Official (non-scratch) build." in result
    assert "Build ran for 45 seconds before failing." in result
    assert result.endswith("The root.log is a log from creation of the chroot environment.")


def test_build_commentary_scratch_build():
    build = _make_build_model(scratch=True)
    event = _make_event(build)
    helper = _make_helper(event)
    result = helper._build_commentary("x86_64")
    assert "Scratch build." in result
    assert "Official (non-scratch) build." not in result


def test_build_commentary_unknown_nvr():
    build = _make_build_model(nvr=None)
    event = _make_event(build)
    helper = _make_helper(event)
    result = helper._build_commentary("x86_64")
    assert "Package NVR: unknown" in result


def test_build_commentary_unknown_target():
    build = _make_build_model()
    event = _make_event(build, target=None)
    helper = _make_helper(event)
    result = helper._build_commentary("x86_64")
    assert "target: unknown" in result


def test_build_commentary_pr_build():
    build = _make_build_model()
    pr_model = MagicMock(spec=PullRequestModel)
    pr_model.pr_id = 99
    event = _make_event(build, db_project_object=pr_model)
    helper = _make_helper(event)
    result = helper._build_commentary("x86_64")
    assert "PR build (PR #99)." in result
    assert "Branch build" not in result
    assert "Release build" not in result


def test_build_commentary_branch_build():
    build = _make_build_model()
    branch_model = MagicMock(spec=GitBranchModel)
    branch_model.name = "main"
    event = _make_event(build, db_project_object=branch_model)
    helper = _make_helper(event)
    result = helper._build_commentary("aarch64")
    assert "Branch build (main)." in result
    assert "PR build" not in result
    assert "Release build" not in result


def test_build_commentary_release_build():
    build = _make_build_model()
    release_model = MagicMock(spec=ProjectReleaseModel)
    release_model.tag_name = "v1.2.3"
    event = _make_event(build, db_project_object=release_model)
    helper = _make_helper(event)
    result = helper._build_commentary("s390x")
    assert "Release build (tag: v1.2.3)." in result
    assert "PR build" not in result
    assert "Branch build" not in result


def test_build_commentary_no_project_object():
    build = _make_build_model()
    event = _make_event(build, db_project_object=None)
    helper = _make_helper(event)
    result = helper._build_commentary("x86_64")
    assert "PR build" not in result
    assert "Branch build" not in result
    assert "Release build" not in result


def test_build_commentary_no_duration():
    build = _make_build_model()
    event = _make_event(build, start_time=None, completion_time=None)
    helper = _make_helper(event)
    result = helper._build_commentary("x86_64")
    assert "seconds before failing" not in result


def test_build_commentary_with_sidetag():
    build = _make_build_model(sidetag="f44-build-side-12345")
    event = _make_event(build)
    helper = _make_helper(event)
    result = helper._build_commentary("x86_64")
    assert "Built in sidetag: f44-build-side-12345." in result
    assert "Sidetag builds use an isolated buildroot" in result


def test_build_commentary_without_sidetag():
    build = _make_build_model(sidetag=None)
    event = _make_event(build)
    helper = _make_helper(event)
    result = helper._build_commentary("x86_64")
    assert "Built in sidetag" not in result


def test_build_commentary_with_build_submission_stdout():
    build = _make_build_model(stdout="Task submitted: 12345")
    event = _make_event(build)
    helper = _make_helper(event)
    result = helper._build_commentary("x86_64")
    assert "Build submission output: Task submitted: 12345" in result


def test_build_commentary_without_build_submission_stdout():
    build = _make_build_model(stdout=None)
    event = _make_event(build)
    helper = _make_helper(event)
    result = helper._build_commentary("x86_64")
    assert "Build submission output" not in result


def test_build_commentary_all_optional_fields():
    """All optional fields present simultaneously."""
    build = _make_build_model(
        scratch=True,
        sidetag="f44-side-99",
        stdout="Submitted OK",
    )
    pr_model = MagicMock(spec=PullRequestModel)
    pr_model.pr_id = 7
    event = _make_event(build, db_project_object=pr_model, start_time=500, completion_time=800)
    helper = _make_helper(event)
    result = helper._build_commentary("ppc64le")

    assert "Scratch build." in result
    assert "PR build (PR #7)." in result
    assert "Build ran for 300 seconds before failing." in result
    assert "Built in sidetag: f44-side-99." in result
    assert "Build submission output: Submitted OK" in result
