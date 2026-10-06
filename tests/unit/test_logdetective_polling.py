# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
"""The Log Detective task protocol and per-run babysitting decisions."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

import pytest
import requests
from celery import Celery
from celery.exceptions import Retry

from packit_service import models
from packit_service.models import LogDetectiveReportContext, LogDetectiveResult
from packit_service.worker import tasks as worker_tasks
from packit_service.worker.helpers import logdetective as polling

TASK_ID = "7d036221-ec50-4d31-b714-09edaccf1486"


@pytest.fixture(autouse=True)
def log_detective_config(monkeypatch):
    """Keep each helper test independent of deployment configuration."""
    monkeypatch.setattr(
        polling.ServiceConfig,
        "get_service_config",
        lambda: SimpleNamespace(
            logdetective_url="https://logdetective.example",
            logdetective_token="secret",
            logdetective_request_timeout=30,
        ),
    )


class SavedRun(SimpleNamespace):
    """Expose the model's request builder on a lightweight saved run fixture."""

    def analysis_request(self) -> dict:
        """Build the API request using the production model method."""
        return models.LogDetectiveRunModel.analysis_request(self)  # type: ignore[arg-type]


def response(status="scheduled", code=200, result=None, error=None):
    """Build a generic Log Detective task response."""
    item = MagicMock(status_code=code)
    item.json.return_value = {
        "id": TASK_ID,
        "taskType": "generic",
        "createdAt": "2026-09-18T10:00:00Z",
        "status": status,
        "result": result,
        "error": error,
    }
    return item


def run(**overrides):
    """Build a saved API run with caller supplied field overrides."""
    values = {
        "id": 5,
        "analysis_id": TASK_ID,
        "selected_logs": [{"name": "build.log", "url": "https://example.org/build.log"}],
        "analysis_commentary": "The build failed.",
        "accepted_time": datetime.utcnow(),
        "submitted_time": datetime.utcnow(),
        "status": LogDetectiveResult.running,
    }
    values.update(overrides)
    return SavedRun(**values)


@pytest.mark.parametrize(
    "field,value",
    [("id", "wrong"), ("taskType", "koji"), ("createdAt", None), ("status", "unknown")],
)
def test_invalid_task_envelopes_are_rejected(field, value):
    """Submission rejects responses that do not identify a valid generic task."""
    reply = response(code=202)
    reply.json.return_value[field] = value
    with pytest.raises(ValueError, match="Invalid Log Detective task envelope"):
        polling.LogDetectiveRunHelper._envelope(reply, run(), 202)


def test_submit_replays_exact_body_and_marks_accepted(monkeypatch):
    """Submission sends the persisted body and client supplied ID unchanged."""
    saved = run(accepted_time=None)
    posted = []
    monkeypatch.setattr(
        polling.requests, "post", lambda *a, **kw: posted.append((a, kw)) or response(code=202)
    )
    accepted = []
    monkeypatch.setattr(
        polling.LogDetectiveRunModel, "mark_api_accepted", lambda run_id: accepted.append(run_id)
    )
    polling.LogDetectiveRunHelper(5).submit_analysis(
        saved, "https://logdetective.example", "secret"
    )
    assert posted[0][1]["json"] == saved.analysis_request()
    assert posted[0][1]["headers"] == {"Authorization": "Bearer secret"}
    assert posted[0][1]["timeout"] == 30
    assert accepted == [5]


@pytest.mark.parametrize(
    "saved",
    [
        {"selected_logs": None, "analysis_commentary": None},
        {
            "selected_logs": [{"name": "build.log", "url": "https://example.org"}],
            "analysis_commentary": None,
        },
    ],
)
def test_analysis_request_requires_saved_inputs(saved):
    """Historical or incomplete rows cannot produce an API submission."""
    with pytest.raises(ValueError, match="no saved API inputs"):
        run(**saved).analysis_request()


@pytest.mark.parametrize(
    "state,result,error,expected",
    [
        ("done", {"explanation": "failed"}, None, LogDetectiveResult.complete),
        ("error", None, {"message": "analysis failed"}, LogDetectiveResult.error),
        ("cancelled", None, None, LogDetectiveResult.error),
    ],
)
def test_terminal_poll_reports_before_persisting(monkeypatch, state, result, error, expected):
    """The Packit result remains running while Fedora CI receives the result."""
    saved = run()
    monkeypatch.setattr(polling.LogDetectiveRunModel, "get_by_id", lambda *_: saved)
    monkeypatch.setattr(
        polling.requests, "get", lambda *a, **kw: response(state, result=result, error=error)
    )
    events = []

    def report(_, status):
        assert saved.status == LogDetectiveResult.running
        events.append(("report", status))

    def finish(_, status, body, message):
        events.append(("persist", status, body, message))
        saved.status = status
        return saved.submitted_time

    monkeypatch.setattr(polling.LogDetectiveRunHelper, "_report", report)
    monkeypatch.setattr(polling.LogDetectiveRunModel, "finish_api_run", finish)
    monkeypatch.setattr(
        polling.LogDetectiveRunHelper, "_record_completion_metrics", lambda *_: None
    )
    polling.LogDetectiveRunHelper(5).advance()
    assert events[0] == ("report", expected)
    assert events[1][0:2] == ("persist", expected)


@pytest.mark.parametrize("state", ["scheduled", "in_progress", "cancelling"])
def test_active_poll_keeps_run_running(monkeypatch, state):
    """An active response leaves retry timing entirely to Celery."""
    monkeypatch.setattr(polling.LogDetectiveRunModel, "get_by_id", lambda *_: run())
    requested = []
    monkeypatch.setattr(
        polling.requests, "get", lambda *a, **kw: requested.append(kw) or response(state)
    )
    monkeypatch.setattr(
        polling.LogDetectiveRunHelper,
        "_finish",
        lambda *a: pytest.fail("Active analysis was finalized"),
    )
    polling.LogDetectiveRunHelper(5).advance()
    assert requested[0]["timeout"] == 30


def test_uncertain_submission_retries_same_id(monkeypatch):
    """A timed out POST is retried with the identical saved request."""
    saved = run(accepted_time=None)
    monkeypatch.setattr(polling.LogDetectiveRunModel, "get_by_id", lambda *_: saved)
    posts = []

    def post(*a, **kw):
        posts.append(kw["json"])
        if len(posts) == 1:
            raise requests.Timeout("unknown outcome")
        return response(code=202)

    monkeypatch.setattr(polling.requests, "post", post)
    monkeypatch.setattr(polling.LogDetectiveRunModel, "mark_api_accepted", lambda *_: None)
    helper = polling.LogDetectiveRunHelper(5)
    helper.advance()
    helper.advance()
    assert posts == [saved.analysis_request(), saved.analysis_request()]


@pytest.mark.parametrize("aware", [False, True])
def test_seven_day_timeout_after_active_poll(monkeypatch, aware):
    """An accepted run is polled once before it times out."""
    now = datetime.now(timezone.utc) if aware else datetime.utcnow()
    saved = run(submitted_time=now - timedelta(days=8))
    monkeypatch.setattr(polling.LogDetectiveRunModel, "get_by_id", lambda *_: saved)
    calls = []
    monkeypatch.setattr(polling.requests, "get", lambda *a, **kw: calls.append(True) or response())
    monkeypatch.setattr(polling.LogDetectiveRunHelper, "_finish", lambda *args: calls.append(args))
    polling.LogDetectiveRunHelper(5).advance()
    assert calls[0] is True
    assert calls[1][1:] == (LogDetectiveResult.error, None, "Log Detective analysis timed out")


def test_terminal_result_wins_over_timeout(monkeypatch):
    """A retrievable result is still reported when the retry occurs after seven days."""
    saved = run(submitted_time=datetime.utcnow() - timedelta(days=8))
    monkeypatch.setattr(polling.LogDetectiveRunModel, "get_by_id", lambda *_: saved)
    monkeypatch.setattr(
        polling.requests, "get", lambda *a, **kw: response("done", result={"explanation": "found"})
    )
    completed = []
    monkeypatch.setattr(polling.LogDetectiveRunHelper, "_finish", lambda *a: completed.append(a))
    polling.LogDetectiveRunHelper(5).advance()
    assert completed[0][1] == LogDetectiveResult.complete


def test_expired_get_failure_times_out(monkeypatch):
    """An unavailable terminal result cannot keep an expired run pending forever."""
    saved = run(submitted_time=datetime.utcnow() - timedelta(days=8))
    monkeypatch.setattr(polling.LogDetectiveRunModel, "get_by_id", lambda *_: saved)
    monkeypatch.setattr(
        polling.requests,
        "get",
        lambda *a, **kw: (_ for _ in ()).throw(requests.Timeout("LD unavailable")),
    )
    completed = []
    monkeypatch.setattr(polling.LogDetectiveRunHelper, "_finish", lambda *a: completed.append(a))
    polling.LogDetectiveRunHelper(5).advance()
    assert completed[0][1:] == (
        LogDetectiveResult.error,
        None,
        "Log Detective analysis timed out",
    )


def test_unaccepted_expired_run_does_not_submit(monkeypatch):
    """An old request without a validated POST ends without creating new LD work."""
    saved = run(accepted_time=None, submitted_time=datetime.utcnow() - timedelta(days=8))
    monkeypatch.setattr(polling.LogDetectiveRunModel, "get_by_id", lambda *_: saved)
    monkeypatch.setattr(polling.requests, "post", lambda *a, **kw: pytest.fail("Expired POST"))
    completed = []
    monkeypatch.setattr(polling.LogDetectiveRunHelper, "_finish", lambda *a: completed.append(a))
    polling.LogDetectiveRunHelper(5).advance()
    assert completed[0][1] == LogDetectiveResult.error


def test_report_failure_repolls_and_keeps_result_running(monkeypatch):
    """A failed report makes the next attempt re-fetch the terminal LD result."""
    saved = run()
    monkeypatch.setattr(polling.LogDetectiveRunModel, "get_by_id", lambda *_: saved)
    reads = []
    monkeypatch.setattr(
        polling.requests,
        "get",
        lambda *a, **kw: reads.append(True) or response("done", result={"explanation": "found"}),
    )
    reports = []

    def report(_, status):
        reports.append(status)
        if len(reports) == 1:
            raise requests.Timeout("Fedora CI unavailable")

    def finish(_, status, body, message):
        saved.status = status
        return saved.submitted_time

    monkeypatch.setattr(polling.LogDetectiveRunHelper, "_report", report)
    monkeypatch.setattr(polling.LogDetectiveRunModel, "finish_api_run", finish)
    monkeypatch.setattr(
        polling.LogDetectiveRunHelper, "_record_completion_metrics", lambda *_: None
    )
    assert not polling.check_log_detective_run(5)
    assert saved.status == LogDetectiveResult.running
    assert polling.check_log_detective_run(5)
    assert reads == [True, True]
    assert reports == [LogDetectiveResult.complete] * 2


def test_duplicate_reports_count_one_database_transition(monkeypatch):
    """A duplicate external report cannot double-count a completed run."""
    reports = []
    transitions = []
    metrics = []
    monkeypatch.setattr(
        polling.LogDetectiveRunHelper, "_report", lambda _, status: reports.append(status)
    )

    def finish(*args):
        transitions.append(args)
        return datetime.utcnow() if len(transitions) == 1 else None

    monkeypatch.setattr(polling.LogDetectiveRunModel, "finish_api_run", finish)
    monkeypatch.setattr(
        polling.LogDetectiveRunHelper,
        "_record_completion_metrics",
        lambda _, submitted: metrics.append(submitted),
    )
    helper = polling.LogDetectiveRunHelper(5)
    assert helper._finish(LogDetectiveResult.complete, {"explanation": "found"}, None)
    assert helper._finish(LogDetectiveResult.complete, {"explanation": "found"}, None)
    assert reports == [LogDetectiveResult.complete] * 2
    assert len(transitions) == 2 and len(metrics) == 1


def test_report_context_skips_deleted_or_superseded_build(monkeypatch):
    """A missing or obsolete build needs no external Fedora CI report."""
    saved = run(koji_build_target=None)
    query = MagicMock()
    query.filter_by.return_value.one_or_none.return_value = saved
    session = MagicMock()
    session.query.return_value = query

    @contextmanager
    def transaction(**_):
        yield session

    monkeypatch.setattr(models, "sa_session_transaction", transaction)
    assert models.LogDetectiveRunModel.get_report_context(5, LogDetectiveResult.complete) is None
    saved.koji_build_target = object()
    monkeypatch.setattr(models.KojiBuildTargetModel, "has_newer_run", lambda *_: True)
    assert models.LogDetectiveRunModel.get_report_context(5, LogDetectiveResult.complete) is None


def test_report_context_rejects_anitya_project(monkeypatch):
    """Fedora CI reporting requires a Git project URL and commit SHA."""
    saved = run(
        koji_build_target=object(),
        get_project=lambda: models.AnityaProjectModel(),
        get_project_event_model=lambda: SimpleNamespace(commit_sha="abc123"),
    )
    query = MagicMock()
    query.filter_by.return_value.one_or_none.return_value = saved
    session = MagicMock()
    session.query.return_value = query

    @contextmanager
    def transaction(**_):
        yield session

    monkeypatch.setattr(models, "sa_session_transaction", transaction)
    monkeypatch.setattr(models.KojiBuildTargetModel, "has_newer_run", lambda *_: False)
    with pytest.raises(ValueError, match="Missing reporting context"):
        models.LogDetectiveRunModel.get_report_context(5, LogDetectiveResult.complete)


def test_late_acceptance_does_not_change_terminal_run(monkeypatch):
    """An overlapping POST cannot change an already finalized run."""
    saved = run(status=LogDetectiveResult.complete, accepted_time=None)
    query = MagicMock()
    query.filter_by.return_value.with_for_update.return_value.one_or_none.return_value = saved
    session = MagicMock()
    session.query.return_value = query

    @contextmanager
    def transaction(**_):
        yield session

    monkeypatch.setattr(models, "sa_session_transaction", transaction)
    models.LogDetectiveRunModel.mark_api_accepted(5)
    assert saved.accepted_time is None


def test_repeated_api_failures_leave_run_pending(monkeypatch):
    """Transient API failures do not end an analysis after five attempts."""
    saved = run()
    monkeypatch.setattr(polling.LogDetectiveRunModel, "get_by_id", lambda *_: saved)
    monkeypatch.setattr(
        polling.requests,
        "get",
        lambda *a, **kw: (_ for _ in ()).throw(requests.Timeout("API unavailable")),
    )
    monkeypatch.setattr(
        polling.LogDetectiveRunHelper,
        "_report",
        lambda *a: pytest.fail("Transient API failure started reporting"),
    )
    for _ in range(6):
        polling.LogDetectiveRunHelper(5).advance()
    assert saved.status == LogDetectiveResult.running


def test_babysit_reads_all_running_api_runs(monkeypatch):
    """The recovery scan reads every running API row without reserving it."""
    rows = [SimpleNamespace(id=3), SimpleNamespace(id=8)]
    query = MagicMock()
    query.filter.return_value.all.return_value = rows
    session = MagicMock()
    session.query.return_value = query

    @contextmanager
    def transaction(**_):
        yield session

    monkeypatch.setattr(models, "sa_session_transaction", transaction)
    assert models.LogDetectiveRunModel.get_pending_api_run_ids() == [3, 8]
    session.query.assert_called_once_with(models.LogDetectiveRunModel.id)
    query.filter.return_value.limit.assert_not_called()
    query.filter.return_value.with_for_update.assert_not_called()


def test_beat_recovers_each_running_run(monkeypatch, caplog):
    """The per-run checker isolates a failure so Beat reaches the next run."""
    monkeypatch.setattr(
        worker_tasks.LogDetectiveRunModel, "get_pending_api_run_ids", lambda: [3, 8]
    )
    monkeypatch.setattr(polling.LogDetectiveRunModel, "get_by_id", lambda *_: run())
    checked = []

    def advance(run_id):
        checked.append(run_id)
        if run_id == 3:
            raise RuntimeError("poll failed")

    monkeypatch.setattr(
        polling,
        "LogDetectiveRunHelper",
        lambda run_id: SimpleNamespace(advance=lambda: advance(run_id)),
    )
    monkeypatch.setattr(
        worker_tasks.process_log_detective_run,
        "delay",
        lambda *_: pytest.fail("Beat enqueued a per-run task"),
    )
    worker_tasks.babysit_pending_log_detective_runs()
    assert checked == [3, 8]
    assert "Could not check Log Detective run 3" in caplog.text


def test_run_task_retries_and_stops_after_completion(monkeypatch):
    """Celery backs off pending work and stops once the run is terminal."""
    monkeypatch.setattr(worker_tasks, "check_log_detective_run", lambda *_: False)
    retries = []
    monkeypatch.setattr(
        worker_tasks.process_log_detective_run,
        "retry",
        lambda **kw: retries.append(kw) or (_ for _ in ()).throw(Retry()),
    )
    with pytest.raises(Retry):
        worker_tasks.process_log_detective_run(5)
    assert retries[0]["countdown"] == 30
    assert isinstance(retries[0]["exc"], worker_tasks.LogDetectiveRunPending)
    assert worker_tasks.process_log_detective_run.max_retries == 14
    monkeypatch.setattr(worker_tasks, "check_log_detective_run", lambda *_: True)
    worker_tasks.process_log_detective_run(5)


def test_check_active_run_without_claim(monkeypatch):
    """The per-run task checks a running analysis directly."""
    saved = run()
    monkeypatch.setattr(polling.LogDetectiveRunModel, "get_by_id", lambda *_: saved)
    advanced = []
    monkeypatch.setattr(
        polling,
        "LogDetectiveRunHelper",
        lambda run_id: SimpleNamespace(advance=lambda: advanced.append(run_id)),
    )
    assert not polling.check_log_detective_run(5)
    assert advanced == [5]


def test_completed_run_is_ignored_on_duplicate_delivery(monkeypatch):
    """An already terminal run needs neither a poll nor a report."""
    monkeypatch.setattr(
        polling.LogDetectiveRunModel,
        "get_by_id",
        lambda *_: run(status=LogDetectiveResult.complete),
    )
    monkeypatch.setattr(
        polling.requests, "get", lambda *a, **kw: pytest.fail("Completed run was polled")
    )
    assert polling.check_log_detective_run(5)
    polling.LogDetectiveRunHelper(5).advance()


@pytest.mark.parametrize("code,terminal", [(409, True), (429, False), (503, False)])
def test_http_failure_is_terminal_only_for_permanent_client_error(monkeypatch, code, terminal):
    """Permanent client errors finish a run; transient errors stay pending."""
    monkeypatch.setattr(polling.LogDetectiveRunModel, "get_by_id", lambda *_: run())
    reply = response(code=code)
    reply.raise_for_status.side_effect = requests.HTTPError("failed", response=reply)
    monkeypatch.setattr(polling.requests, "get", lambda *a, **kw: reply)
    finished = []
    monkeypatch.setattr(polling.LogDetectiveRunHelper, "_finish", lambda *a: finished.append(a))
    polling.LogDetectiveRunHelper(5).advance()
    assert bool(finished) == terminal


def test_report_recovers_fedora_ci_context_from_build(monkeypatch):
    """Reporting uses the proposed status and saved build context."""
    project = SimpleNamespace(get_pr=lambda *_: SimpleNamespace(target_branch="main"))
    config = SimpleNamespace(get_project=lambda **_: project)
    context = LogDetectiveReportContext(
        project_url="https://example.org/repo",
        commit_sha="abc123",
        pr_id=42,
        branch=None,
        target="rawhide-noarch",
        status=LogDetectiveResult.complete,
    )
    monkeypatch.setattr(
        polling.LogDetectiveRunModel,
        "get_report_context",
        lambda run_id, status: context if status == LogDetectiveResult.complete else None,
    )
    monkeypatch.setattr(polling.ServiceConfig, "get_service_config", lambda: config)
    monkeypatch.setattr(polling, "get_check_name_prefix", lambda *_: "Packit")
    sent = []

    def make_helper(project_arg, metadata_arg, target_arg):
        sent.append((project_arg, metadata_arg, target_arg))
        return SimpleNamespace(report=lambda **kw: sent.append(kw))

    monkeypatch.setattr(polling, "FedoraCIHelper", make_helper)
    polling.LogDetectiveRunHelper(5)._report(LogDetectiveResult.complete)
    assert sent[0][0] is project
    assert sent[0][1].commit_sha == "abc123" and sent[0][1].pr_id == 42
    assert sent[0][2] == "rawhide-noarch"
    assert sent[1]["check_name"] == "Packit - Log Detective analysis"


def test_check_name_without_deployment_prefix(monkeypatch):
    """Keep the plain Log Detective check name when no prefix is configured."""
    context = LogDetectiveReportContext(
        project_url="https://example.org/repo",
        commit_sha="abc123",
        pr_id=None,
        branch="main",
        target="rawhide-x86_64",
        status=LogDetectiveResult.complete,
    )
    monkeypatch.setattr(polling, "get_check_name_prefix", lambda *_: "")
    monkeypatch.setattr(polling.LogDetectiveRunModel, "get_report_context", lambda *_: context)
    monkeypatch.setattr(
        polling.ServiceConfig,
        "get_service_config",
        lambda: SimpleNamespace(get_project=lambda **_: object()),
    )
    sent = []
    monkeypatch.setattr(
        polling, "FedoraCIHelper", lambda *a: SimpleNamespace(report=lambda **kw: sent.append(kw))
    )
    polling.LogDetectiveRunHelper(5)._report(LogDetectiveResult.complete)
    assert sent[0]["check_name"] == "Log Detective analysis"


@pytest.mark.parametrize("post_fails", [False, True])
@pytest.mark.parametrize("enqueue_fails", [False, True])
def test_koji_submission_saves_request_and_build_before_post(
    monkeypatch, post_fails, enqueue_fails
):
    """Saved inputs survive both initial POST and Celery delivery failures."""
    build = MagicMock(nvr="pkg-1-1", scratch=True, sidetag=None, build_submission_stdout=None)
    build.group_of_targets.runs = []
    event = MagicMock(
        build_model=build,
        target="rawhide",
        db_project_object=None,
        start_time=100,
        completion_time=110,
        rpm_build_task_ids={"x86_64": 123},
        rpm_build_failed_arch_list=["x86_64"],
        rpm_build_task_labels={"x86_64": "x86_64"},
    )
    monkeypatch.setattr(polling, "verify_artifact", lambda *_: True)
    monkeypatch.setattr(polling, "uuid4", lambda: UUID(TASK_ID))
    group = object()
    created_groups = []
    monkeypatch.setattr(
        polling.LogDetectiveRunGroupModel,
        "create",
        lambda pipelines: created_groups.append(pipelines) or group,
    )
    created = []
    monkeypatch.setattr(
        polling.LogDetectiveRunModel,
        "create",
        lambda *a, **kw: created.append((a, kw)) or run(),
    )

    def submit(*args, **kwargs):
        created.append(("post", args, kwargs))
        if post_fails:
            raise requests.Timeout("initial POST failed")

    monkeypatch.setattr(polling.LogDetectiveRunHelper, "submit_analysis", submit)
    queued = []

    def send_task(self, *args, **kwargs):
        queued.append((args, kwargs))
        if enqueue_fails:
            raise ConnectionError("broker unavailable")

    # Restore the class method after this test; patching the shared app leaves an
    # instance attribute that shadows class-level mocks in later tests.
    monkeypatch.setattr(Celery, "send_task", send_task)
    failures = []
    monkeypatch.setattr(
        polling.LogDetectiveRunHelper,
        "_api_failure",
        lambda self, exc: failures.append(str(exc)),
    )
    helper = polling.LogDetectiveKojiTriggerHelper(
        event,
        SimpleNamespace(commit_sha="abc", project_url="https://example.org", pr_id=None),
        SimpleNamespace(log_detective_runs_started=SimpleNamespace(inc=lambda: None)),
        "https://koji.example",
        "https://logdetective.example",
        "secret",
    )
    assert helper.trigger_log_detective_analysis() == [True]
    selected_logs = created[0][1]["selected_logs"]
    assert [file["name"] for file in selected_logs] == ["root.log", "mock_output.log", "build.log"]
    assert "arch: x86_64" in created[0][1]["analysis_commentary"]
    assert created[0][1]["log_detective_analysis_id"] == TASK_ID
    assert created_groups == [build.group_of_targets.runs]
    assert created[0][1]["log_detective_run_group"] is group
    assert created[0][1]["koji_build_target"] is build
    assert created[1][0] == "post" and created[1][2] == {}
    assert failures == (["initial POST failed"] if post_fails else [])
    assert queued == [(("task.process_log_detective_run",), {"args": (5,), "countdown": 120})]
