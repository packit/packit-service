# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""Copr Log Detective trigger and configuration tests."""

from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from celery import Celery
from packit.config.common_package_config import Deployment

from packit_service import models
from packit_service.config import FedoraCISettings, ServiceConfig
from packit_service.constants import COPR_API_FAIL_STATE
from packit_service.fedora_ci_config import FedoraCIConfig
from packit_service.models import (
    CoprBuildGroupModel,
    CoprBuildTargetModel,
    LogDetectiveBuildSystem,
    LogDetectiveReportContext,
    LogDetectiveResult,
    LogDetectiveRunGroupModel,
    LogDetectiveRunModel,
    PipelineModel,
    SRPMBuildModel,
)
from packit_service.worker.handlers import copr as copr_handler
from packit_service.worker.handlers.copr import CoprBuildEndHandler
from packit_service.worker.helpers import logdetective


@pytest.mark.parametrize("system", list(LogDetectiveBuildSystem))
def test_independent_switches_and_project_exclusion(system):
    config = ServiceConfig(
        deployment=Deployment.stg,
        logdetective_koji_enabled=system == LogDetectiveBuildSystem.koji,
        logdetective_copr_enabled=system == LogDetectiveBuildSystem.copr,
        fedora_ci=FedoraCISettings(disabled_projects_for_logdetective={"disabled"}),
    )
    gate = FedoraCIConfig(config)
    assert gate.is_logdetective_enabled("enabled", system)
    other_system = (
        LogDetectiveBuildSystem.copr
        if system == LogDetectiveBuildSystem.koji
        else LogDetectiveBuildSystem.koji
    )
    assert not gate.is_logdetective_enabled("enabled", other_system)
    assert not gate.is_logdetective_enabled("disabled", system)


@pytest.mark.parametrize("srpm", [False, True])
@pytest.mark.parametrize("valid", [False, True])
def test_copr_trigger_persists_verified_log(monkeypatch, srpm, valid):
    if srpm:
        build = SRPMBuildModel()
        build.logs_url = "https://example.test/srpm/builder-live.log"
    else:
        build = CoprBuildTargetModel()
        build.build_logs_url = "https://example.test/rpm/builder-live.log"
        build.target = "fedora-rawhide-x86_64"
        build.group_of_targets = CoprBuildGroupModel()
    event = SimpleNamespace(
        build=build,
        build_id=42,
        owner="packit",
        project_name="example",
        pkg="pkg",
        identifier="job",
        get_copr_build_logs_url=lambda: "https://example.test/fallback",
    )
    monkeypatch.setattr(logdetective, "verify_artifact", lambda url: valid)
    groups = []
    created = []
    submitted = []
    queued = []
    monkeypatch.setattr(
        LogDetectiveRunGroupModel, "create", lambda runs: groups.append(runs) or object()
    )
    monkeypatch.setattr(
        LogDetectiveRunGroupModel,
        "get_or_create_for_copr_build",
        lambda target: groups.append(target) or object(),
    )
    monkeypatch.setattr(
        LogDetectiveRunModel, "create", lambda **kw: created.append(kw) or SimpleNamespace(id=5)
    )
    monkeypatch.setattr(
        copr_handler.LogDetectiveRunHelper,
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
    assert CoprBuildEndHandler.trigger_log_detective_analysis(handler) is valid
    assert len(groups) == len(created) == len(submitted) == len(queued) == int(valid)
    if valid:
        assert submitted[0][1:] == ("https://ld.test", "token")
        assert queued == [(("task.process_log_detective_run",), {"args": (5,), "countdown": 120})]
        assert groups == ([build.runs] if srpm else [build])
        run = created[0]
        assert run["build_system"] == LogDetectiveBuildSystem.copr
        assert run["target_build"] == "42"
        assert run["target"] == ("srpm-builds" if srpm else "fedora-rawhide-x86_64")
        assert run["selected_logs"] == [
            {"name": "builder-live.log", "url": build.logs_url if srpm else build.build_logs_url}
        ]
        assert run["srpm_build"] is (build if srpm else None)
        assert run["copr_build_target"] is (None if srpm else build)


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
    session = MagicMock()
    session.add.side_effect = lambda item: (
        pipelines.append(item) if isinstance(item, PipelineModel) and item is not pipeline else None
    )
    group_query = MagicMock()
    group_query.filter_by.return_value.with_for_update.return_value.one.return_value = (
        CoprBuildGroupModel(id=9)
    )
    pipeline_query = MagicMock()
    pipeline_query.filter_by.return_value.all.side_effect = lambda: list(pipelines)
    session.query.side_effect = lambda model: (
        group_query if model is CoprBuildGroupModel else pipeline_query
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
    assert session.add.call_count == 2  # group and one pipeline, once each
    assert group_query.filter_by.return_value.with_for_update.call_count == 2


def test_srpm_freshness_uses_build_time_and_matches_unnamed_package(monkeypatch):
    """A later build must supersede an SRPM with a null package name."""
    build = SRPMBuildModel(id=1, submitted_time=datetime(2026, 10, 1))
    build.runs = [PipelineModel(package_name=None)]
    monkeypatch.setattr(
        build,
        "get_project_event_model",
        lambda: SimpleNamespace(type="pull_request", event_id=7, commit_sha="abc"),
    )
    query = MagicMock()
    query.join.return_value = query
    query.filter.return_value = query
    query.exists.return_value = "exists-query"
    session = MagicMock()
    session.query.side_effect = lambda arg: query if arg is SRPMBuildModel else session
    session.scalar.return_value = True
    session.query.return_value = query

    @contextmanager
    def transaction(**_):
        yield session

    monkeypatch.setattr(models, "sa_session_transaction", transaction)
    assert SRPMBuildModel.has_newer_run(build)
    conditions = query.filter.call_args.args
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
    query = MagicMock()
    query.join.return_value = query
    query.filter.return_value = query
    query.exists.return_value = "exists-query"
    session = MagicMock()
    session.query.side_effect = lambda arg: query if arg is CoprBuildTargetModel else session
    session.scalar.return_value = True

    @contextmanager
    def transaction(**_):
        yield session

    monkeypatch.setattr(models, "sa_session_transaction", transaction)
    assert CoprBuildTargetModel.has_newer_run(build)
    conditions = query.filter.call_args.args
    assert any("copr_build_groups.submitted_time >" in str(c) for c in conditions)
    assert all("pipelines.datetime" not in str(c) for c in conditions)


@pytest.mark.parametrize("status", [COPR_API_FAIL_STATE, 0, 1, 3])
@pytest.mark.parametrize("enabled", [False, True])
def test_copr_handler_requires_failure_and_enablement_gate(monkeypatch, status, enabled):
    calls = []
    project = SimpleNamespace(get_web_url=lambda: "https://example.test/project")
    gate = SimpleNamespace(is_logdetective_enabled=lambda *args: calls.append("gate") or enabled)
    monkeypatch.setattr(FedoraCIConfig, "get_config", lambda: gate)
    handler = SimpleNamespace(
        copr_event=SimpleNamespace(status=status, build_id=42),
        service_config=SimpleNamespace(
            logdetective_copr_enabled=True,
            logdetective_url="https://ld.test",
            logdetective_token="token",
        ),
        project=project,
        pushgateway=object(),
        trigger_log_detective_analysis=lambda: calls.append("trigger"),
    )
    CoprBuildEndHandler.trigger_log_detective_if_configured(handler)
    expected = ["gate", "trigger"] if enabled else ["gate"]
    assert calls == (expected if status == COPR_API_FAIL_STATE else [])


def test_copr_reports_distinct_target_check(monkeypatch):
    context = LogDetectiveReportContext(
        project_url="https://example.test/project",
        commit_sha="abc",
        pr_id=None,
        branch="main",
        target="srpm-builds",
        status=LogDetectiveResult.complete,
        build_system=LogDetectiveBuildSystem.copr,
    )
    monkeypatch.setattr(LogDetectiveRunModel, "get_report_context", lambda *_: context)
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
    logdetective.LogDetectiveRunHelper(5)._report(LogDetectiveResult.complete)
    assert reports[0]["check_name"] == "Log Detective analysis - srpm-builds"
