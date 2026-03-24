# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
Integration tests for Log Detective with Koji builds.
"""

import pytest
import requests
from flexmock import Mock, flexmock
from packit.config.common_package_config import Deployment

from packit_service.config import FedoraCISettings, ServiceConfig
from packit_service.constants import LOGDETECTIVE_PACKIT_SERVER_URL
from packit_service.events import koji
from packit_service.models import (
    KojiBuildTargetModel,
    LogDetectiveBuildSystem,
    LogDetectiveResult,
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
    flexmock(koji.result.Task).should_receive("get_packages_config").and_return(None)
    flexmock(KojiBuildTargetModel).should_receive("get_by_task_id").and_return(
        koji_build_pr_downstream
    )

    koji_build_pr_downstream.should_receive("set_build_start_time").once()
    koji_build_pr_downstream.should_receive("set_build_finished_time").once()
    koji_build_pr_downstream.should_receive("set_status").with_args(
        "failure" if failed_builds else "success"
    ).once()
    koji_build_pr_downstream.should_receive("set_build_logs_urls").once()
    koji_build_pr_downstream.should_receive("set_web_url").once()

    flexmock(StatusReporter).should_receive("set_status").and_return().once()

    if failed_builds > 0:
        mock_ld_response = flexmock(status_code=200)
        mock_ld_response.should_receive("raise_for_status")
        mock_ld_response.should_receive("json").and_return(
            {
                "log_detective_analysis_id": "test-analysis-id-123",
                "creation_time": "2026-01-01T12:00:00Z",
            },
            {
                "log_detective_analysis_id": "test-analysis-id-456",
                "creation_time": "2026-01-01T12:00:05Z",
            },
        ).one_by_one()
        flexmock(requests).should_receive("post").and_return(mock_ld_response)

    mock_group_run = flexmock(id=1)
    flexmock(LogDetectiveRunGroupModel).should_receive("create").times(
        int(failed_builds > 0)
    ).and_return(mock_group_run)
    if failed_builds > 0:
        flexmock(LogDetectiveRunModel).should_receive("create").with_args(
            LogDetectiveResult.running,
            "123456",
            "rawhide-x86_64",
            LogDetectiveBuildSystem.koji,
            "test-analysis-id-123",
            mock_group_run,
        )
    if failed_builds > 1:
        flexmock(LogDetectiveRunModel).should_receive("create").with_args(
            LogDetectiveResult.running,
            "123457",
            "rawhide-noarch",
            LogDetectiveBuildSystem.koji,
            "test-analysis-id-456",
            mock_group_run,
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

    koji_build_pr_downstream.should_receive("add_log_detective_run").times(failed_builds)

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

    koji_build_pr_downstream.should_receive("set_build_start_time").once()
    koji_build_pr_downstream.should_receive("set_build_finished_time").once()
    koji_build_pr_downstream.should_receive("set_status").with_args("failure").once()
    koji_build_pr_downstream.should_receive("set_build_logs_urls").once()
    koji_build_pr_downstream.should_receive("set_web_url").once()

    flexmock(StatusReporter).should_receive("set_status").and_return().once()

    # Log Detective should NOT be called
    flexmock(requests).should_receive("post").never()
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

    koji_build_pr_downstream.should_receive("add_log_detective_run").never()

    results = run_downstream_koji_scratch_build_report_handler(failed_build_event, None, None)
    assert first_dict_value(results["job"])["success"]
