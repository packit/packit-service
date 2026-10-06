# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
Integration tests for Log Detective with Koji builds.
"""

import pytest
from flexmock import Mock, flexmock
from packit.config.common_package_config import Deployment

import packit_service.worker.helpers.logdetective as logdetective_module
from packit_service.config import FedoraCISettings, ServiceConfig
from packit_service.constants import LOGDETECTIVE_PACKIT_SERVER_URL
from packit_service.events import koji
from packit_service.models import (
    KojiBuildTargetModel,
    LogDetectiveRunGroupModel,
    LogDetectiveRunModel,
)
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.reporting import StatusReporter
from packit_service.worker.tasks import run_downstream_koji_scratch_build_report_handler
from tests.spellbook import first_dict_value


@pytest.fixture
def koji_scratch_build_fixture(failed_builds: int):
    arches = []
    if failed_builds > 0:
        arches.append("x86_64")
    if failed_builds > 1:
        arches.append("noarch")
    return {
        "task_id": 12345,
        "state": "FAILED" if failed_builds > 0 else "CLOSED",
        "old_state": "OPEN",
        "rpm_build_task_ids": {"x86_64": 123456, "noarch": 123457},
        "rpm_build_failed_arch_list": arches,
        "start_time": 1767225600,
        "completion_time": 1767225600 + 7200,
        "project_url": "https://src.fedoraproject.org/rpms/packit",
    }


@pytest.mark.parametrize("failed_builds", [0, 1, 2], indirect=False)
def test_logdetective_koji_build_scratch_downstream(
    failed_builds,
    koji_scratch_build_fixture,
    koji_build_pr_downstream: Mock,
):
    """
    Failed downstream Koji build triggers Log Detective.
    This tests the full flow: message => handler => helper => external calls.
    """

    project = flexmock(repo="packit", namespace="rpms")
    project.should_receive("get_web_url").and_return("https://src.fedoraproject.org/rpms/packit")

    service_config = flexmock(
        logdetective_enabled=True,
        fedora_ci=FedoraCISettings(),
        logdetective_url=LOGDETECTIVE_PACKIT_SERVER_URL,
        koji_logs_url="https://kojipkgs.fedoraproject.org",
        deployment=Deployment.prod,
        logdetective_token="secret-123",
    )
    service_config.should_receive("get_project").and_return(project)

    flexmock(ServiceConfig).should_receive("get_service_config").and_return(service_config)

    koji_build_pr_downstream.target = "rawhide"
    koji_build_pr_downstream.nvr = "packit-0.123.0-1.fc00"
    koji_build_pr_downstream.scratch = True
    koji_build_pr_downstream.sidetag = None
    koji_build_pr_downstream.build_submission_stdout = "MOCK STDOUT"
    flexmock(koji.result.Task).should_receive("get_packages_config").and_return(None)
    flexmock(KojiBuildTargetModel).should_receive("get_by_task_id").and_return(
        koji_build_pr_downstream
    )
    flexmock(KojiBuildTargetModel).should_receive("has_newer_run").and_return(False)

    koji_build_pr_downstream.should_receive("set_build_start_time").once()
    koji_build_pr_downstream.should_receive("set_build_finished_time").once()
    koji_build_pr_downstream.should_receive("set_status").with_args(
        "failure" if failed_builds else "success"
    ).once()
    koji_build_pr_downstream.should_receive("set_build_logs_urls").once()
    koji_build_pr_downstream.should_receive("set_web_url").once()

    flexmock(StatusReporter).should_receive("set_status").and_return().once()

    if failed_builds > 0:
        flexmock(logdetective_module.LogDetectiveRunHelper).should_receive("submit_analysis").times(
            failed_builds
        )
        flexmock(logdetective_module).should_receive("verify_artifact").and_return(True)
        flexmock(logdetective_module.celery_app).should_receive("send_task").with_args(
            "task.process_log_detective_run", args=(1,), countdown=120
        ).times(failed_builds)

    mock_group_run = flexmock(id=1)
    flexmock(LogDetectiveRunGroupModel).should_receive("create").times(
        int(failed_builds > 0)
    ).and_return(mock_group_run)
    flexmock(LogDetectiveRunModel).should_receive("create").times(failed_builds).and_return(
        flexmock(id=1)
    )

    pushgateway = flexmock(
        log_detective_runs_started=flexmock(),
        fedora_ci_koji_builds_started=flexmock(),
        fedora_ci_koji_builds_finished=flexmock(),
        fedora_ci_koji_build_finished_time=flexmock(),
    )
    pushgateway.log_detective_runs_started.should_receive("inc").times(failed_builds).and_return()
    pushgateway.fedora_ci_koji_builds_finished.should_receive("inc").once().and_return()
    pushgateway.should_receive("push").and_return()
    flexmock(Pushgateway).new_instances(pushgateway)

    results = run_downstream_koji_scratch_build_report_handler(
        koji_scratch_build_fixture, None, None
    )
    assert first_dict_value(results["job"])["success"]


def test_logdetective_skipped_when_project_disabled(
    koji_build_pr_downstream: Mock,
):
    """
    Log Detective is NOT triggered when the project is in disabled_projects_for_logdetective,
    even though the build failed and logdetective is globally enabled.
    """
    failed_build_event = {
        "task_id": 12345,
        "state": "FAILED",
        "old_state": "OPEN",
        "rpm_build_task_ids": {"x86_64": 123456, "noarch": 123457},
        "rpm_build_failed_arch_list": ["noarch"],
        "start_time": 1767225600,
        "completion_time": 1767225600 + 7200,
        "project_url": "https://src.fedoraproject.org/rpms/packit",
    }

    project = flexmock(repo="packit", namespace="rpms")
    project.should_receive("get_web_url").and_return("https://src.fedoraproject.org/rpms/packit")

    service_config = flexmock(
        logdetective_enabled=True,
        fedora_ci=FedoraCISettings(
            disabled_projects_for_logdetective={"https://src.fedoraproject.org/rpms/packit"},
        ),
        logdetective_url=LOGDETECTIVE_PACKIT_SERVER_URL,
        koji_logs_url="https://kojipkgs.fedoraproject.org",
        deployment=Deployment.prod,
        logdetective_token="secret-123",
    )
    service_config.should_receive("get_project").and_return(project)

    flexmock(ServiceConfig).should_receive("get_service_config").and_return(service_config)

    koji_build_pr_downstream.target = "rawhide"
    flexmock(koji.result.Task).should_receive("get_packages_config").and_return(None)
    flexmock(KojiBuildTargetModel).should_receive("get_by_task_id").and_return(
        koji_build_pr_downstream
    )
    flexmock(KojiBuildTargetModel).should_receive("has_newer_run").and_return(False)

    koji_build_pr_downstream.should_receive("set_build_start_time").once()
    koji_build_pr_downstream.should_receive("set_build_finished_time").once()
    koji_build_pr_downstream.should_receive("set_status").with_args("failure").once()
    koji_build_pr_downstream.should_receive("set_build_logs_urls").once()
    koji_build_pr_downstream.should_receive("set_web_url").once()

    flexmock(StatusReporter).should_receive("set_status").and_return().once()

    # Log Detective should NOT be called
    flexmock(logdetective_module.LogDetectiveRunHelper).should_receive("submit_analysis").never()
    flexmock(LogDetectiveRunGroupModel).should_receive("create").never()
    flexmock(LogDetectiveRunModel).should_receive("create").never()

    pushgateway = flexmock(
        log_detective_runs_started=flexmock(),
        fedora_ci_koji_builds_started=flexmock(),
        fedora_ci_koji_builds_finished=flexmock(),
        fedora_ci_koji_build_finished_time=flexmock(),
    )
    pushgateway.log_detective_runs_started.should_receive("inc").never()
    pushgateway.fedora_ci_koji_builds_finished.should_receive("inc").once().and_return()
    pushgateway.should_receive("push").and_return()
    flexmock(Pushgateway).new_instances(pushgateway)

    results = run_downstream_koji_scratch_build_report_handler(failed_build_event, None, None)
    assert first_dict_value(results["job"])["success"]
