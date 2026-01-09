# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import pytest
from flexmock import flexmock

from packit_service.service.app import packit_as_a_service as application
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.tasks import celery_app


@pytest.fixture
def client():
    application.config["TESTING"] = True
    # this affects all tests actually, heads up!
    application.config["SERVER_NAME"] = "localhost:5000"
    application.config["PREFERRED_URL_SCHEME"] = "https"

    with application.test_client() as client:
        # the first call usually fails
        client.get("https://localhost:5000/api/")
        yield client


@pytest.fixture(autouse=True)
def _setup_app_context_for_test():
    """
    Given app is session-wide, sets up a app context per test to ensure that
    app and request stack is not shared between tests.
    """
    ctx = application.app_context()
    ctx.push()
    yield  # tests will run here
    ctx.pop()


@pytest.fixture
def logdetective_analysis_success_event():
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
        "log_detective_analysis_id": "4f2fa9aa-8fe6-4325-a317-473ca180e75d",
        "commit_sha": "9deb98c730bb4123f518ca13a0dbec5d7c0669ca",
        "project_url": "https://github.com/packit/packit",
        "pr_id": 123,
    }


@pytest.fixture
def logdetective_analysis_error_event():
    return {
        "topic": "org.fedoraproject.prod.logdetective.analysis",
        "log_detective_response": None,
        "target_build": "123456",
        "build_system": "copr",
        "status": "error",
        "identifier": "4f2fa9aa-8fe6-4325-a317-473ca180e75d",
        "log_detective_analysis_start": "2025-12-10 10:57:57.341695+00:00",
        "log_detective_analysis_id": "4f2fa9aa-8fe6-4325-a317-473ca180e75d",
        "commit_sha": "9deb98c730bb4123f518ca13a0dbec5d7c0669ca",
        "project_url": "https://github.com/packit/packit",
        "pr_id": 123,
    }


@pytest.fixture
def mock_metrics_counters():
    # Mock for general metric object
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

    # Inject the mock instance
    flexmock(Pushgateway).new_instances(mock_pushgateway)

    return {
        "mock_counter": mock_counter,
        "mock_histogram": mock_histogram,
        "mock_pushgateway": mock_pushgateway,
    }


@pytest.fixture
def eager_celery_tasks():
    """Configure Celery to run tasks locally and synchronously
    This allows mocks to work and avoids timeouts waiting for a worker"""

    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    celery_app.conf.task_store_eager_result = True
