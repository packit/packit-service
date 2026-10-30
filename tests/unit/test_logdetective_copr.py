# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""Copr Log Detective trigger and configuration tests."""

from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace

import pytest
from celery import Celery
from flexmock import flexmock
from packit.schema import PackageConfigSchema

from packit_service import models
from packit_service.config import ServiceConfig
from packit_service.constants import COPR_API_FAIL_STATE
from packit_service.models import (
    CoprBuildGroupModel,
    CoprBuildTargetModel,
    GitBranchModel,
    LogDetectiveBuildSystem,
    LogDetectiveReportContext,
    LogDetectiveResult,
    LogDetectiveRunGroupModel,
    LogDetectiveRunModel,
    PipelineModel,
    ProjectReleaseModel,
    PullRequestModel,
    SRPMBuildModel,
)
from packit_service.worker.handlers.copr import CoprBuildEndHandler, CoprBuildStartHandler
from packit_service.worker.helpers import logdetective


@pytest.mark.parametrize("srpm", [False, True])
@pytest.mark.parametrize("valid", [False, True])
@pytest.mark.parametrize("duplicate", [False, True])
@pytest.mark.parametrize("stored_log", [False, True])
def test_copr_trigger_persists_verified_log(monkeypatch, srpm, valid, duplicate, stored_log):
    if srpm:
        build = SRPMBuildModel()
        build.logs_url = "https://example.test/srpm/builder-live.log"
    else:
        build = CoprBuildTargetModel(identifier="job")
        build.build_logs_url = "https://example.test/rpm/builder-live.log"
        build.target = "fedora-rawhide-x86_64"
        build.group_of_targets = CoprBuildGroupModel(id=9)
        build.copr_build_group_id = 9
    if not stored_log:
        if srpm:
            build.logs_url = None
        else:
            build.build_logs_url = None
    build.build_start_time = datetime(2026, 10, 1, 0, 0, 0)
    build.build_finished_time = datetime(2026, 10, 1, 0, 0, 10)
    branch = GitBranchModel()
    branch.name = "main"
    monkeypatch.setattr(build, "get_project_event_object", lambda: branch)
    event = SimpleNamespace(
        build=build,
        build_id=42,
        owner="packit",
        project_name="example",
        pkg="pkg",
        identifier="main",  # Source branch, not the job configuration identifier.
        get_copr_build_logs_url=lambda: {
            "builder-live.log": "https://example.test/fallback/builder-live.log",
            "backend.log": "https://example.test/fallback/backend.log",
        },
    )
    checked_urls = []
    monkeypatch.setattr(
        logdetective,
        "verify_artifact",
        lambda url: checked_urls.append(url) or valid,
    )
    monkeypatch.setattr(CoprBuildTargetModel, "has_newer_run", lambda *_: False)
    monkeypatch.setattr(SRPMBuildModel, "has_newer_run", lambda *_: False)
    if not srpm:
        monkeypatch.setattr(build, "get_srpm_build", lambda: None)
    created = []
    submitted = []
    queued = []
    monkeypatch.setattr(
        LogDetectiveRunModel,
        "create_for_copr_build",
        lambda build_model, **kw: created.append((build_model, kw)) or (5, not duplicate),
    )
    monkeypatch.setattr(
        LogDetectiveRunModel,
        "get_by_id",
        lambda *_: SimpleNamespace(
            id=5,
            build_system=LogDetectiveBuildSystem.copr,
            copr_build_target=build if not srpm else None,
            srpm_build=build if srpm else None,
        ),
    )
    monkeypatch.setattr(
        logdetective.LogDetectiveRunHelper,
        "submit_analysis",
        lambda self, run, url, token: submitted.append((run, url, token)),
    )
    monkeypatch.setattr(
        Celery, "send_task", lambda self, *args, **kwargs: queued.append((args, kwargs))
    )
    handler = SimpleNamespace(
        build=build,
        copr_event=event,
        pushgateway=SimpleNamespace(log_detective_runs_started=SimpleNamespace(inc=lambda: None)),
        service_config=SimpleNamespace(
            logdetective_url="https://ld.test", logdetective_token="token"
        ),
    )
    assert CoprBuildEndHandler.trigger_log_detective_analysis(handler) is (valid and not duplicate)
    stored_url = build.logs_url if srpm else build.build_logs_url
    base_url = stored_url.rsplit("/", 1)[0] if stored_url else "https://example.test/fallback"
    log_names = ["builder-live.log", "backend.log"]
    assert checked_urls == [f"{base_url}/{name}" for name in log_names]
    assert len(created) == int(valid)
    assert len(submitted) == len(queued) == int(valid and not duplicate)
    if valid:
        if not duplicate:
            assert submitted[0][1:] == ("https://ld.test", "token")
            assert queued == [
                (("task.process_log_detective_run",), {"args": (5,), "countdown": 120})
            ]
        assert created[0][0] is build
        run = created[0][1]
        assert run["target_build"] == "42"
        assert run["target"] == ("srpm-builds" if srpm else "fedora-rawhide-x86_64")
        assert run["identifier"] == (None if srpm else "job")
        assert run["selected_logs"] == [
            {"name": name, "url": f"{base_url}/{name}"} for name in log_names
        ]
        assert "Build failed in Copr for packit/example" in run["analysis_commentary"]
        assert ("SRPM build." if srpm else "RPM chroot build.") in run["analysis_commentary"]
        assert "builder-live.log" in run["analysis_commentary"]
        assert "backend.log" in run["analysis_commentary"]
        assert "Branch build (main)." in run["analysis_commentary"]
        assert "Build ran for 10 seconds before failing." in run["analysis_commentary"]


def test_copr_log_selection_keeps_available_logs(monkeypatch):
    """A missing Copr log must not hide other available log inputs."""
    checked_urls = []

    def verify(url):
        checked_urls.append(url)
        return url.endswith("backend.log")

    monkeypatch.setattr(logdetective, "verify_artifact", verify)
    candidates = [
        ("builder-live.log", "https://example.test/builder-live.log"),
        ("backend.log", "https://example.test/backend.log"),
    ]
    assert logdetective.select_verified_logs(candidates) == [
        {"name": "backend.log", "url": "https://example.test/backend.log"}
    ]
    assert checked_urls == [url for _, url in candidates]


def test_copr_start_keeps_live_builder_log_link():
    saved_urls = []
    handler = SimpleNamespace(
        copr_event=SimpleNamespace(
            get_copr_build_logs_url=lambda: {
                "builder-live.log": "https://example.test/builder-live.log",
                "backend.log": "https://example.test/backend.log",
            }
        ),
        build=SimpleNamespace(set_build_logs_url=saved_urls.append),
    )
    CoprBuildStartHandler.set_logs_url(handler)
    assert saved_urls == ["https://example.test/builder-live.log"]


@pytest.mark.parametrize(
    ("project_model", "attribute", "value", "expected"),
    [
        (PullRequestModel, "pr_id", 17, "PR build (PR #17)."),
        (GitBranchModel, "name", "main", "Branch build (main)."),
        (ProjectReleaseModel, "tag_name", "v1", "Release build (tag: v1)."),
        (None, None, None, None),
    ],
)
def test_shared_commentary_project_and_duration(project_model, attribute, value, expected):
    """Set mapped fields after construction for SQLAlchemy's generated constructor."""
    project_object = project_model() if project_model is not None else None
    if project_object is not None:
        setattr(project_object, attribute, value)
    helper = logdetective.LogDetectiveTriggerHelper()
    result = helper._build_commentary(
        opening_parts=["Build failed."],
        project_object=project_object,
        start_time=datetime(2026, 10, 1, 0, 0, 0),
        end_time=datetime(2026, 10, 1, 0, 0, 10),
        extra_parts=[],
        log_parts=["Inspect the log."],
    )
    assert "Build ran for 10 seconds before failing." in result
    assert "Inspect the log." in result
    if expected:
        assert expected in result
    else:
        assert "PR build" not in result


@pytest.mark.parametrize(
    ("start", "end"),
    [(None, 10), ("invalid", 10), (10, 5), (float("nan"), 10)],
)
def test_shared_commentary_omits_invalid_duration(start, end):
    helper = logdetective.LogDetectiveTriggerHelper()
    result = helper._build_commentary(
        opening_parts=["Build failed."],
        project_object=None,
        start_time=start,
        end_time=end,
        extra_parts=[],
        log_parts=[],
    )
    assert "Build ran" not in result


@pytest.mark.parametrize("existing_koji_group", [False, True])
def test_copr_chroots_share_one_analysis_group(monkeypatch, existing_koji_group):
    """A second failed chroot must not clone the same build's pipelines."""
    build = CoprBuildTargetModel(copr_build_group_id=9)
    pipeline = PipelineModel(copr_build_group=CoprBuildGroupModel(id=9))
    if existing_koji_group:
        pipeline.log_detective_run_group = LogDetectiveRunGroupModel()
        pipeline.log_detective_run_group.log_detective_run_targets.append(
            LogDetectiveRunModel(build_system=LogDetectiveBuildSystem.koji)
        )
    pipelines = [pipeline]
    added = []
    group_query = flexmock()
    group_query.should_receive("filter_by").and_return(group_query).twice()
    group_query.should_receive("with_for_update").and_return(group_query).twice()
    group_query.should_receive("one_or_none").and_return(CoprBuildGroupModel(id=9)).twice()
    pipeline_query = flexmock()
    pipeline_query.should_receive("filter_by").and_return(pipeline_query).twice()
    pipeline_query.should_receive("all").replace_with(lambda: list(pipelines)).twice()
    session = flexmock()

    def add(item):
        added.append(item)
        if isinstance(item, PipelineModel) and item is not pipeline:
            pipelines.append(item)

    session.should_receive("add").replace_with(add).twice()
    session.should_receive("query").replace_with(
        lambda model: group_query if model is CoprBuildGroupModel else pipeline_query
    )

    @contextmanager
    def transaction(**_):
        yield session

    monkeypatch.setattr(models, "sa_session_transaction", transaction)
    first = LogDetectiveRunGroupModel.get_or_create_for_copr_build(build)
    second = LogDetectiveRunGroupModel.get_or_create_for_copr_build(build)
    assert first is second
    assert len(pipelines) == (2 if existing_koji_group else 1)
    assert pipelines[-1].log_detective_run_group is first
    assert len(added) == 2  # group and one pipeline, once each


def test_srpm_freshness_uses_build_time_and_matches_unnamed_package(monkeypatch):
    """A later build must supersede an SRPM with a null package name."""
    build = SRPMBuildModel(id=1, submitted_time=datetime(2026, 10, 1))
    build.runs = [PipelineModel(package_name=None)]
    monkeypatch.setattr(
        build,
        "get_project_event_model",
        lambda: SimpleNamespace(type="pull_request", event_id=7, commit_sha="abc"),
    )
    conditions = []
    query = flexmock()
    query.should_receive("join").and_return(query)

    def filter_query(*args):
        conditions.extend(args)
        return query

    query.should_receive("filter").replace_with(filter_query)
    query.should_receive("exists").and_return("exists-query")
    session = flexmock()
    session.should_receive("query").replace_with(
        lambda arg: query if arg is SRPMBuildModel else session
    )
    session.should_receive("scalar").and_return(True)

    @contextmanager
    def transaction(**_):
        yield session

    monkeypatch.setattr(models, "sa_session_transaction", transaction)
    assert SRPMBuildModel.has_newer_run(build)
    assert any("package_name IS NULL" in str(condition) for condition in conditions)
    assert any("submitted_time >" in str(condition) for condition in conditions)


def test_copr_freshness_uses_build_group_time_after_pipeline_clone(monkeypatch):
    """An analysis pipeline clone must not move a Copr build's ordering time."""
    build = CoprBuildTargetModel(id=1, identifier="job", target="fedora-rawhide-x86_64")
    build.group_of_targets = CoprBuildGroupModel(id=9, submitted_time=datetime(2026, 10, 1))
    build.group_of_targets.runs.append(PipelineModel(datetime=datetime(2026, 10, 3)))
    monkeypatch.setattr(
        build,
        "get_project_event_model",
        lambda: SimpleNamespace(type="pull_request", event_id=7, commit_sha="abc"),
    )
    conditions = []
    query = flexmock()
    query.should_receive("join").and_return(query)

    def filter_query(*args):
        conditions.extend(args)
        return query

    query.should_receive("filter").replace_with(filter_query)
    query.should_receive("exists").and_return("exists-query")
    session = flexmock()
    session.should_receive("query").replace_with(
        lambda arg: query if arg is CoprBuildTargetModel else session
    )
    session.should_receive("scalar").and_return(True)

    @contextmanager
    def transaction(**_):
        yield session

    monkeypatch.setattr(models, "sa_session_transaction", transaction)
    assert CoprBuildTargetModel.has_newer_run(build)
    assert any("copr_build_groups.submitted_time >" in str(c) for c in conditions)
    assert all("pipelines.datetime" not in str(c) for c in conditions)


@pytest.mark.parametrize("status", [COPR_API_FAIL_STATE, 0, 1, 3])
@pytest.mark.parametrize("job_enabled", [False, True])
@pytest.mark.parametrize("copr_enabled", [False, True])
@pytest.mark.parametrize("koji_enabled", [False, True])
def test_copr_handler_requires_failure_and_both_copr_switches(
    status, job_enabled, copr_enabled, koji_enabled
):
    calls = []
    handler = SimpleNamespace(
        copr_event=SimpleNamespace(status=status, build_id=42),
        service_config=SimpleNamespace(
            log_detective_koji_enabled=koji_enabled,
            log_detective_copr_enabled=copr_enabled,
            logdetective_url="https://ld.test",
            logdetective_token="token",
        ),
        project=object(),
        job_config=SimpleNamespace(log_detective_enabled=job_enabled),
        pushgateway=object(),
        trigger_log_detective_analysis=lambda: calls.append("trigger"),
    )
    CoprBuildEndHandler.trigger_log_detective_if_configured(handler)
    assert calls == (
        ["trigger"] if status == COPR_API_FAIL_STATE and copr_enabled and job_enabled else []
    )


@pytest.mark.parametrize(
    "package_setting,job_setting,package_override,expected",
    [
        (None, None, None, {"first": True, "second": True}),
        (False, None, None, {"first": False, "second": True}),
        (False, True, None, {"first": True, "second": True}),
        (True, False, None, {"first": False, "second": False}),
        (False, True, False, {"first": False, "second": True}),
        (False, False, True, {"first": True, "second": False}),
    ],
)
def test_copr_handler_uses_packit_resolved_log_detective_config(
    package_setting, job_setting, package_override, expected
):
    """Copr events use the package-specific job view produced by Packit."""
    first = {"specfile_path": "first.spec"}
    if package_setting is not None:
        first["log_detective_enabled"] = package_setting
    job = {"job": "copr_build", "trigger": "pull_request"}
    if job_setting is not None:
        job["log_detective_enabled"] = job_setting
    if package_override is not None:
        job["packages"] = {"first": {"log_detective_enabled": package_override}, "second": {}}
    config = PackageConfigSchema().load(
        {"packages": {"first": first, "second": {"specfile_path": "second.spec"}}, "jobs": [job]}
    )

    job_views = config.get_job_views()
    assert len(job_views) == 2
    for job_view in job_views:
        calls = []
        handler = SimpleNamespace(
            copr_event=SimpleNamespace(status=COPR_API_FAIL_STATE, build_id=42),
            service_config=SimpleNamespace(log_detective_copr_enabled=True),
            project=object(),
            job_config=job_view,
            trigger_log_detective_analysis=lambda calls=calls: calls.append("trigger"),
        )
        CoprBuildEndHandler.trigger_log_detective_if_configured(handler)
        assert calls == (["trigger"] if expected[job_view.package] else [])


def test_copr_reports_distinct_build_checks(monkeypatch):
    contexts = {
        build_id: LogDetectiveReportContext(
            project_url="https://example.test/project",
            commit_sha="abc",
            pr_id=None,
            branch="main",
            target="srpm-builds",
            status=LogDetectiveResult.complete,
            build_id=str(build_id),
            build_system=LogDetectiveBuildSystem.copr,
        )
        for build_id in (42, 43)
    }
    monkeypatch.setattr(
        LogDetectiveRunModel,
        "get_report_context",
        lambda run_id, _status: contexts[run_id],
    )
    monkeypatch.setattr(
        ServiceConfig,
        "get_service_config",
        lambda: SimpleNamespace(get_project=lambda **_: object()),
    )
    monkeypatch.setattr(logdetective, "get_check_name_prefix", lambda *_: "")
    reports = []
    monkeypatch.setattr(
        logdetective,
        "FedoraCIHelper",
        lambda *args: SimpleNamespace(report=lambda **kw: reports.append(kw)),
    )
    for build_id in contexts:
        logdetective.LogDetectiveRunHelper(build_id)._report(LogDetectiveResult.complete)
    assert [report["check_name"] for report in reports] == [
        "Log Detective analysis - srpm-builds - 42",
        "Log Detective analysis - srpm-builds - 43",
    ]


@pytest.mark.parametrize("srpm", [False, True])
def test_obsolete_copr_event_creates_no_analysis(monkeypatch, srpm):
    build = SRPMBuildModel(id=7) if srpm else CoprBuildTargetModel(id=7)
    if not srpm:
        build.group_of_targets = CoprBuildGroupModel(id=9)
        build.copr_build_group_id = 9
        monkeypatch.setattr(build, "get_srpm_build", lambda: None)
    model = SRPMBuildModel if srpm else CoprBuildTargetModel
    monkeypatch.setattr(model, "has_newer_run", lambda *_: True)
    monkeypatch.setattr(
        LogDetectiveRunModel,
        "create_for_copr_build",
        lambda *a, **kw: pytest.fail("obsolete event created a run"),
    )
    monkeypatch.setattr(
        logdetective, "verify_artifact", lambda *_: pytest.fail("obsolete event checked logs")
    )
    helper = logdetective.LogDetectiveCoprTriggerHelper(
        SimpleNamespace(build_id=42), build, object(), "https://ld.test", "token"
    )
    assert not helper.trigger_log_detective_analysis()


def test_failed_srpm_suppresses_chroot_analysis(monkeypatch):
    build = CoprBuildTargetModel(id=7)
    build.group_of_targets = CoprBuildGroupModel(id=9)
    build.copr_build_group_id = 9
    monkeypatch.setattr(
        build, "get_srpm_build", lambda: SRPMBuildModel(status=models.BuildStatus.failure)
    )
    monkeypatch.setattr(
        LogDetectiveRunModel,
        "create_for_copr_build",
        lambda *a, **kw: pytest.fail("chroot created a run after SRPM failure"),
    )
    helper = logdetective.LogDetectiveCoprTriggerHelper(
        SimpleNamespace(build_id=42), build, object(), "https://ld.test", "token"
    )
    assert not helper.trigger_log_detective_analysis()


@pytest.mark.parametrize("group_id", [None, 99])
def test_missing_copr_group_is_named(monkeypatch, group_id):
    build = CoprBuildTargetModel(id=7, copr_build_group_id=group_id)
    query = flexmock()
    query.should_receive("filter_by").and_return(query)
    query.should_receive("with_for_update").and_return(query)
    if group_id is not None:
        query.should_receive("one_or_none").and_return(None)
    session = flexmock(query=lambda _: query)
    with pytest.raises(ValueError, match=f"target 7.*ID {group_id}"):
        LogDetectiveRunGroupModel._get_or_create_for_copr_build_in_session(session, build)


@pytest.mark.parametrize("group_id", [None, 99])
def test_missing_group_fails_before_srpm_lookup(group_id):
    build = CoprBuildTargetModel(id=7, copr_build_group_id=group_id)
    helper = logdetective.LogDetectiveCoprTriggerHelper(
        SimpleNamespace(build_id=42), build, object(), "https://ld.test", "token"
    )
    with pytest.raises(ValueError, match=f"target 7.*ID {group_id}"):
        helper.trigger_log_detective_analysis()


def test_missing_copr_group_does_not_interrupt_event_handler(caplog):
    handler = SimpleNamespace(
        copr_event=SimpleNamespace(status=COPR_API_FAIL_STATE, build_id=42),
        service_config=SimpleNamespace(log_detective_copr_enabled=True),
        project=object(),
        job_config=SimpleNamespace(log_detective_enabled=True),
        trigger_log_detective_analysis=lambda: (_ for _ in ()).throw(
            ValueError("Copr build target 7 has no build group (ID None)")
        ),
    )
    CoprBuildEndHandler.trigger_log_detective_if_configured(handler)
    assert "Could not trigger Log Detective for Copr build 42" in caplog.text
    assert "Copr build target 7 has no build group (ID None)" in caplog.text
