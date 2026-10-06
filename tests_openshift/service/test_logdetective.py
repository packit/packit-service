# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
"""Persisted Log Detective babysitting and the existing Packit result API."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from flask import url_for

from packit_service.models import (
    BuildStatus,
    KojiBuildGroupModel,
    KojiBuildTargetModel,
    LogDetectiveBuildSystem,
    LogDetectiveResult,
    LogDetectiveRunGroupModel,
    LogDetectiveRunModel,
    ProjectEventModel,
    PullRequestModel,
    Session,
    SRPMBuildModel,
)
from packit_service.worker.helpers import logdetective as polling
from packit_service.worker.helpers.fedora_ci import FedoraCIHelper
from packit_service.worker.reporting import BaseCommitStatus


def test_missing_log_detective_run_needs_no_report(clean_before_and_after):
    """A deleted run cannot send an external report."""
    assert LogDetectiveRunModel.get_report_context(-1, LogDetectiveResult.error) is None


def test_polled_result_is_available_through_packit_api(client, clean_before_and_after, monkeypatch):
    """A polled API result remains readable through Packit's result endpoint."""
    analysis_id = "7d036221-ec50-4d31-b714-09edaccf1486"
    group = LogDetectiveRunGroupModel.create([])
    run = LogDetectiveRunModel.create(
        status=LogDetectiveResult.running,
        target_build="12345",
        target="rawhide-x86_64",
        build_system=LogDetectiveBuildSystem.koji,
        log_detective_analysis_id=analysis_id,
        log_detective_run_group=group,
        selected_logs=[{"name": "build.log", "url": "https://example.org/build.log"}],
        analysis_commentary="The build failed.",
    )
    expected_request = {
        "id": analysis_id,
        "files": [{"name": "build.log", "url": "https://example.org/build.log"}],
        "build_metadata": {"commentary": "The build failed."},
    }
    assert run.analysis_request() == expected_request
    LogDetectiveRunModel.create(
        status=LogDetectiveResult.running,
        target_build="historical",
        target="rawhide-x86_64",
        build_system=LogDetectiveBuildSystem.koji,
        log_detective_analysis_id="historical-message-run",
        log_detective_run_group=group,
    )
    LogDetectiveRunModel.mark_api_accepted(run.id)
    assert run.accepted_time is not None
    Session().expire_all()
    assert LogDetectiveRunModel.get_by_id(run.id).analysis_request() == expected_request
    assert LogDetectiveRunModel.get_pending_api_run_ids() == [run.id]

    monkeypatch.setattr(
        polling.ServiceConfig,
        "get_service_config",
        lambda: SimpleNamespace(
            logdetective_url="https://logdetective.example",
            logdetective_token="secret",
            logdetective_request_timeout=1,
        ),
    )
    reply = MagicMock(status_code=200, headers={})
    reply.json.return_value = {
        "id": analysis_id,
        "taskType": "generic",
        "createdAt": "2026-09-18T10:00:00Z",
        "status": "done",
        "result": {"explanation": "The build failed"},
        "error": None,
    }
    monkeypatch.setattr(polling.requests, "get", lambda *a, **kw: reply)
    status_during_report = []

    def report(*_):
        """Read committed state before allowing the terminal transition."""
        Session().expire_all()
        status_during_report.append(LogDetectiveRunModel.get_by_id(run.id).status)

    monkeypatch.setattr(polling.LogDetectiveRunHelper, "_report", report)
    monkeypatch.setattr(
        polling,
        "Pushgateway",
        lambda: SimpleNamespace(
            log_detective_runs_finished=SimpleNamespace(inc=lambda: None),
            log_detective_run_finished=SimpleNamespace(observe=lambda *_: None),
            push=lambda: None,
        ),
    )

    polling.LogDetectiveRunHelper(run.id).advance()
    assert status_during_report == [LogDetectiveResult.running]
    assert (
        LogDetectiveRunModel.finish_api_run(run.id, LogDetectiveResult.error, None, "late") is None
    )
    Session().expire_all()
    result = client.get(url_for("api.log-detective_log_detective_result", id=run.id))
    assert result.status_code == 200
    assert result.json["status"] == "complete"
    assert result.json["analysis_id"] == analysis_id
    assert result.json["log_detective_response"] == {"explanation": "The build failed"}
    assert LogDetectiveRunModel.get_pending_api_run_ids() == []


def test_terminal_report_recovers_project_and_build_context(clean_before_and_after, monkeypatch):
    """A terminal run can report using its persisted build and project links."""
    project_url = "https://github.com/packit/packit"
    pr = PullRequestModel.get_or_create(
        pr_id=42, namespace="packit", repo_name="packit", project_url=project_url
    )
    event = ProjectEventModel.get_or_create(
        type=pr.project_event_model_type, event_id=pr.id, commit_sha="abc123"
    )
    _, pipeline = SRPMBuildModel.create_with_new_run(
        project_event_model=event, package_name="packit"
    )
    build_group = KojiBuildGroupModel.create(run_model=pipeline)
    build = KojiBuildTargetModel.create(
        task_id="12345",
        scratch=True,
        web_url="https://koji.example/taskinfo?taskID=12345",
        target="rawhide",
        status=BuildStatus.failure,
        koji_build_group=build_group,
    )
    group = LogDetectiveRunGroupModel.create([pipeline])
    run = LogDetectiveRunModel.create(
        status=LogDetectiveResult.running,
        target_build="12346",
        target="rawhide-noarch",
        build_system=LogDetectiveBuildSystem.koji,
        log_detective_analysis_id="7d036221-ec50-4d31-b714-09edaccf1486",
        log_detective_run_group=group,
        selected_logs=[{"name": "build.log", "url": "https://example.org/build.log"}],
        analysis_commentary="The build failed.",
        koji_build_target=build,
    )
    Session().expire_all()

    project = SimpleNamespace(get_pr=lambda *_: SimpleNamespace(target_branch="main"))
    config = SimpleNamespace(get_project=lambda **_: project, deployment="prod")
    monkeypatch.setattr(polling.ServiceConfig, "get_service_config", lambda: config)
    monkeypatch.setattr(KojiBuildTargetModel, "has_newer_run", lambda *_: False)
    monkeypatch.setattr(polling, "get_check_name_prefix", lambda *_: "Packit")
    reports = []

    def report(helper, **kwargs):
        """Capture the Fedora CI call without sending an external status."""
        reports.append((helper, kwargs))

    monkeypatch.setattr(FedoraCIHelper, "report", report)
    polling.LogDetectiveRunHelper(run.id)._report(LogDetectiveResult.complete)
    assert len(reports) == 1
    helper, values = reports[0]
    assert helper.project is project
    assert helper.metadata.commit_sha == "abc123" and helper.metadata.pr_id == 42
    assert helper.target_branch == "rawhide-noarch"
    assert values["state"] == BaseCommitStatus.success
    assert values["check_name"] == "Packit - Log Detective analysis"
    polling.LogDetectiveRunHelper(run.id)._report(LogDetectiveResult.complete)
    assert len(reports) == 2  # Concurrent checks may duplicate the external call.
    LogDetectiveRunModel.finish_api_run(run.id, LogDetectiveResult.complete, {"a": 1}, None)
    polling.LogDetectiveRunHelper(run.id)._report(LogDetectiveResult.complete)
    assert len(reports) == 2
    Session().expire_all()
    assert LogDetectiveRunModel.get_by_id(run.id).status == LogDetectiveResult.complete
