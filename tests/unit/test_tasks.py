# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import prometheus_client
import pytest
from celery.app.task import Task
from flexmock import flexmock
from packit.exceptions import PackitException

from packit_service.worker.handlers import CoprBuildHandler
from packit_service.worker.tasks import push_ogr_namespace_metrics, run_copr_build_handler


def test_autoretry():
    flexmock(prometheus_client).should_receive("push_to_gateway")
    flexmock(CoprBuildHandler).should_receive("run_job").and_raise(
        PackitException,
    ).once()

    # verify that retry is called automatically
    flexmock(Task).should_receive("retry").and_raise(PackitException).once()
    with pytest.raises(PackitException):
        run_copr_build_handler({}, {}, {})


def test_push_metrics_handles_exception():
    """Test that exceptions are handled gracefully."""
    from packit_service.worker import tasks

    mock_tracker = flexmock()
    mock_tracker.should_receive("get_all_counts").and_raise(Exception("Test error")).once()

    flexmock(tasks).should_receive("get_metrics_tracker").and_return(mock_tracker).once()

    push_ogr_namespace_metrics()


def test_push_metrics_resets_after_push():
    """Test that reset is called after pushing metrics."""
    from packit_service.worker import tasks

    mock_tracker = flexmock()
    mock_tracker.should_receive("get_all_counts").and_return({("github", "packit"): 1}).once()
    # Verify reset is called after push
    mock_tracker.should_receive("reset").once()

    flexmock(tasks).should_receive("get_metrics_tracker").and_return(mock_tracker).once()

    # Mock Pushgateway
    mock_gauge = flexmock()
    mock_gauge.should_receive("set").with_args(1).once()

    mock_pushgateway = flexmock()
    mock_pushgateway.ogr_namespace_requests = flexmock()
    mock_pushgateway.ogr_namespace_requests.should_receive("labels").with_args(
        namespace="packit", service_type="github"
    ).and_return(mock_gauge).once()
    mock_pushgateway.should_receive("push").once()

    from packit_service.worker.monitoring import Pushgateway

    flexmock(Pushgateway).should_receive("__init__").and_return(None)
    flexmock(Pushgateway).new_instances(mock_pushgateway)

    push_ogr_namespace_metrics()
