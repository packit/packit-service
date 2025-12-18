# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from datetime import datetime

import pytest
from flexmock import flexmock
from packit.config.common_package_config import Deployment

from packit_service.config import ServiceConfig
from packit_service.models import (
    BuildStatus,
    CoprBuildGroupModel,
    CoprBuildTargetModel,
    LogDetectiveBuildSystem,
    LogDetectiveResult,
    LogDetectiveRunGroupModel,
    LogDetectiveRunModel,
    ProjectEventModel,
    PullRequestModel,
    Session,
    SRPMBuildModel,
)
from packit_service.worker.checker.logdetective import IsEventForJob
from packit_service.worker.helpers.fedora_ci import FedoraCIHelper
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.reporting.enums import BaseCommitStatus
from packit_service.worker.tasks import celery_app, process_message


@pytest.fixture
def logdetective_analysis_event():
    return {
        "topic": "org.fedoraproject.prod.logdetective.analysis",
        "log_detective_response": {
            "explanation": {
                "text": "The RPM build failed due to...",
                "logprobs": None,
            },
            "response_certainty": 0.95,
            "snippets": [],
        },
        "target_build": "123456",
        "build_system": "copr",
        "status": "complete",
        "identifier": "4f2fa9aa-8fe6-4325-a317-473ca180e75d",
        "log_detective_analysis_start": "2025-12-10 10:57:57.341695+00:00",
        "commit_sha": "9deb98c730bb4123f518ca13a0dbec5d7c0669ca",
        "project_url": "https://github.com/packit/packit",
        "pr_id": 123,
    }


def test_logdetective_process_message(clean_before_and_after, logdetective_analysis_event):
    """Test that the processing of a Log Detective event
    via the main Celery task `process_message`.
    """
    # Configure Celery to run tasks locally and synchronously
    # This allows mocks to work and avoids timeouts waiting for a worker
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    celery_app.conf.task_store_eager_result = True

    # Create the Project Event and Pull Request
    pr_model = PullRequestModel.get_or_create(
        pr_id=123,
        namespace="packit",
        repo_name="packit",
        project_url=logdetective_analysis_event["project_url"],
    )

    project_event = ProjectEventModel.get_or_create(
        type=pr_model.project_event_model_type,
        event_id=pr_model.id,
        commit_sha=logdetective_analysis_event["commit_sha"],
    )

    # Create a PipelineModel linking the event and the SRPM build
    _, pipeline = SRPMBuildModel.create_with_new_run(
        project_event_model=project_event, package_name="packit"
    )

    # The .create() method handles the logic of attaching to the pipeline
    copr_group = CoprBuildGroupModel.create(run_model=pipeline)

    copr_build = CoprBuildTargetModel.create(
        build_id=logdetective_analysis_event["target_build"],
        project_name="packit-packit-123",
        owner="packit",
        web_url="https://copr.fedorainfracloud.org/coprs/packit/packit-123/build/123456/",
        target="fedora-rawhide-x86_64",
        status=BuildStatus.failure,
        copr_build_group=copr_group,
    )

    # This ensures the LD run is associated with the correct PR/Commit
    ld_group = LogDetectiveRunGroupModel.create(run_models=[pipeline])

    ld_run = LogDetectiveRunModel.create(
        status=LogDetectiveResult.running,
        target_build=logdetective_analysis_event["target_build"],
        build_system=LogDetectiveBuildSystem.copr,
        identifier=logdetective_analysis_event["identifier"],
        log_detective_run_group=ld_group,
    )

    # Under normal circumstances, the default `submitted_time`
    # would be the current time. However, that would prevent us from testing
    # full logic of the `set_status` method. Instead we set the `submitted_time`
    # to a value from `logdetective_analysis_event`.
    expected_time = datetime.fromisoformat(
        logdetective_analysis_event["log_detective_analysis_start"]
    )
    ld_run.submitted_time = expected_time

    # Manually link the run to the target build (create doesn't do this part)
    ld_run.copr_build_target = copr_build
    Session().add(ld_run)
    Session().commit()

    service_config = ServiceConfig().get_service_config()
    service_config.enabled_projects_for_fedora_ci = {logdetective_analysis_event["project_url"]}

    # Set deployment to prod to disable guppy memory profiling logic
    # which causes UnboundLocalError when guppy is missing.
    service_config.deployment = Deployment.prod
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(service_config)

    mock_project = flexmock(
        repo="packit",
        namespace="packit",
        service=flexmock(instance_url="https://github.com", hostname="github.com"),
    )
    # Mock retrieving the PR and its target branch
    mock_project.should_receive("get_pr").with_args(123).and_return(flexmock(target_branch="main"))

    # Return our mock project when requested
    flexmock(service_config).should_receive("get_project").with_args(
        url=logdetective_analysis_event["project_url"]
    ).and_return(mock_project)

    # IsEventForJob might return False because we don't have a job_config in Fedora CI flow
    flexmock(IsEventForJob).should_receive("pre_check").and_return(True)

    flexmock(FedoraCIHelper).should_receive("report").with_args(
        state=BaseCommitStatus.success,
        description="Log Detective analysis status: complete",
        url="https://copr.fedorainfracloud.org/coprs/packit/packit-123/build/123456/",
        check_name="Log Detective Analysis",
    ).once()

    # Mock the metric objects
    mock_counter = flexmock()
    mock_counter.should_receive("inc").and_return()

    mock_histogram = flexmock()
    mock_histogram.should_receive("observe").and_return()

    mock_pushgateway = flexmock()
    mock_pushgateway.should_receive("push").and_return()

    # Specific metrics for Log Detective
    mock_pushgateway.log_detective_runs_finished = mock_counter
    mock_pushgateway.log_detective_run_finished = mock_histogram
    mock_pushgateway.log_detective_runs_started = mock_counter  # Use mock_counter for this too

    # General metrics used by SteveJobs
    mock_pushgateway.events_processed = mock_counter
    mock_pushgateway.events_not_handled = mock_counter
    mock_pushgateway.events_pre_check_failed = mock_counter

    flexmock(Pushgateway).new_instances(mock_pushgateway)

    # Inject the mock instance
    flexmock(Pushgateway).new_instances(mock_pushgateway)

    # This verifies the exact method we want to test is called with correct args
    flexmock(LogDetectiveRunModel).should_call("set_status").with_args(
        LogDetectiveResult.complete,
        log_detective_analysis_start=datetime.fromisoformat(
            logdetective_analysis_event["log_detective_analysis_start"]
        ),
    ).once()

    result = process_message.apply(
        args=[logdetective_analysis_event],
        kwargs={"source": "fedora-messaging", "event_type": "logdetective.analysis"},
        throw=True,
    )
    result = result.get()
    # Verify task success
    assert result, "Task returned no results"
    assert result[0]["success"], f"Task failed: {result[0]}"

    Session().expire_all()

    # Reload from DB to verify changes
    run_model_after = LogDetectiveRunModel.get_by_identifier(
        logdetective_analysis_event["identifier"]
    )

    assert run_model_after.status == LogDetectiveResult.complete
    assert run_model_after.log_detective_response is not None
    assert (
        run_model_after.log_detective_response["response_certainty"]
        == logdetective_analysis_event["log_detective_response"]["response_certainty"]
    )

    # Verify timestamp was updated from the event
    # database stores timestamp as UTC, but without timezone information
    # we need to remove timezone information here, to get a match
    assert run_model_after.submitted_time == expected_time.replace(tzinfo=None)
