# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
Integration tests for Log Detective with Koji builds.
"""

import pytest
import requests
from flexmock import Mock, flexmock
from packit.config.common_package_config import Deployment

from packit_service.config import ServiceConfig
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
def koji_scratch_build_fixture(build_successful):
    return {
        "task_id": 12345,
        "state": "CLOSED" if build_successful else "FAILED",
        "old_state": "OPEN",
        "rpm_build_task_ids": {"x86_64": 123456, "noarch": 123457},
        "start_time": 1767225600,
        "completion_time": 1767225600 + 7200,
    }


@pytest.mark.parametrize("build_successful", [True, False])
def test_logdetective_koji_build_scratch_downstream(
    build_successful,
    koji_scratch_build_fixture,
    koji_build_pr_downstream: Mock,
):
    """
    Failed downstream Koji build triggers Log Detective.
    This tests the full flow: message => handler => helper => external calls.
    """

    service_config = flexmock(
        logdetective_enabled=True,
        logdetective_url=LOGDETECTIVE_PACKIT_SERVER_URL,
        koji_logs_url="https://kojipkgs.fedoraproject.org",
        deployment=Deployment.prod,
        logdetective_secret="secret-123",
    )

    flexmock(ServiceConfig).should_receive("get_service_config").and_return(service_config)

    koji_build_pr_downstream.target = "rawhide"
    flexmock(koji.result.Task).should_receive("get_packages_config").and_return(None)
    flexmock(KojiBuildTargetModel).should_receive("get_by_task_id").and_return(
        koji_build_pr_downstream
    )

    koji_build_pr_downstream.should_receive("set_build_start_time").once()
    koji_build_pr_downstream.should_receive("set_build_finished_time").once()
    koji_build_pr_downstream.should_receive("set_status").with_args(
        "success" if build_successful else "failure"
    ).once()
    koji_build_pr_downstream.should_receive("set_build_logs_urls").once()
    koji_build_pr_downstream.should_receive("set_web_url").once()

    flexmock(StatusReporter).should_receive("set_status").and_return().once()

    if not build_successful:
        mock_ld_response = flexmock(status_code=200)
        mock_ld_response.should_receive("raise_for_status")
        mock_ld_response.should_receive("json").and_return(
            {
                "log_detective_analysis_id": "test-analysis-id-123",
                "creation_time": "2026-01-01T12:00:00Z",
            }
        )
        flexmock(requests).should_receive("post").and_return(mock_ld_response)

    ld_calls = 0 if build_successful else 1
    mock_group_run = flexmock(id=1)
    flexmock(LogDetectiveRunGroupModel).should_receive("create").times(ld_calls).and_return(
        mock_group_run
    )
    flexmock(LogDetectiveRunModel).should_receive("create").times(ld_calls)

    pushgateway = flexmock(
        log_detective_runs_started=flexmock(),
        fedora_ci_koji_builds_started=flexmock(),
        fedora_ci_koji_builds_finished=flexmock(),
        fedora_ci_koji_build_finished_time=flexmock(),
    )
    pushgateway.log_detective_runs_started.should_receive("inc").times(ld_calls).and_return()
    pushgateway.fedora_ci_koji_builds_finished.should_receive("inc").once().and_return()
    pushgateway.should_receive("push").and_return()
    flexmock(Pushgateway).new_instances(pushgateway)

    koji_build_pr_downstream.should_receive("add_log_detective_run").times(ld_calls)

    results = run_downstream_koji_scratch_build_report_handler(
        koji_scratch_build_fixture, None, None
    )
    assert first_dict_value(results["job"])["success"]
