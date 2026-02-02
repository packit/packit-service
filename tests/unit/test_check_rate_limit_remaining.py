# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import pytest
from flexmock import flexmock

from packit_service import celerizer
from packit_service.constants import (
    CELERY_TASK_RATE_LIMITED_QUEUE,
    RATE_LIMIT_THRESHOLD,
    RATE_LIMITED_QUEUE_EXPIRES_SECONDS,
)
from packit_service.worker.handlers.abstract import (
    JobHandler,
    RateLimitRequeueException,
    TaskName,
)


@pytest.fixture
def mock_celery_app_none():
    """Fixture that sets up flexmock for celery_app with current_worker_task = None"""
    mock_app = flexmock()
    mock_app.current_worker_task = None
    flexmock(celerizer).should_receive("get_celery_application").and_return(mock_app)
    return mock_app


@pytest.fixture
def mock_celery_app_with_task():
    """Fixture that sets up flexmock for celery_app with a mock task"""
    from packit_service import celerizer

    mock_task = flexmock()
    mock_app = flexmock()
    mock_app.current_worker_task = mock_task
    flexmock(celerizer).should_receive("get_celery_application").and_return(mock_app)
    return mock_app


@pytest.fixture
def handler(mock_celery_app_none):
    """Fixture that creates and returns a TestHandler instance"""
    # Use mock_celery_app_none as default, tests can override with mock_celery_app_with_task
    return TestHandler(
        package_config=flexmock(),
        job_config=flexmock(),
        event={},
    )


class TestHandler(JobHandler):
    """Test handler implementation for testing"""

    task_name = TaskName.copr_build

    @property
    def project(self):
        return self._project

    @property
    def service_config(self):
        return flexmock(rate_limit_threshold=None)

    @property
    def project_url(self):
        return "https://github.com/test/repo"

    @property
    def packit_api(self):
        from packit.api import PackitAPI

        return flexmock(PackitAPI)

    def clean_api(self):
        pass

    def _run(self):
        from packit_service.worker.result import TaskResults

        return TaskResults(success=True, details={})


def test_check_rate_limit_remaining_no_celery_task(handler, mock_celery_app_none):
    """Test that method returns early when no celery task is found"""
    # Should return without raising
    handler.check_rate_limit_remaining()


def test_check_rate_limit_remaining_no_project(handler, mock_celery_app_with_task):
    """Test that method returns early when project is None"""
    # Set _project to None - the project property will return None
    handler._project = None

    # Should return without raising
    handler.check_rate_limit_remaining()


def test_check_rate_limit_remaining_high_rate_limit(handler, mock_celery_app_with_task):
    """Test that method continues when rate limit is high"""
    mock_service = flexmock(get_rate_limit_remaining=lambda: RATE_LIMIT_THRESHOLD + 100)
    mock_project = flexmock(service=mock_service)
    handler._project = mock_project

    # Should return without raising
    handler.check_rate_limit_remaining()


def test_check_rate_limit_remaining_low_rate_limit_reschedule(handler, monkeypatch):
    """Test that method reschedules task when rate limit is low"""
    mock_service = flexmock(
        get_rate_limit_remaining=lambda namespace=None, repo=None: RATE_LIMIT_THRESHOLD - 50
    )
    mock_project = flexmock(service=mock_service, namespace="test", repo="repo")
    handler._project = mock_project

    from packit_service.worker.handlers import abstract

    mock_request = flexmock(
        kwargs={"event": {}, "package_config": {}, "job_config": {}},
        delivery_info={"routing_key": "long-running"},
    )
    mock_task = flexmock(
        name=TaskName.copr_build,
        request=mock_request,
    )

    # Use monkeypatch to directly replace celery_app with mock_app
    # Since celery_app is imported inside the method, we need to patch it in the module
    mock_app = flexmock()
    mock_app.current_worker_task = mock_task
    monkeypatch.setattr("packit_service.celerizer.celery_app", mock_app)

    mock_sig = flexmock()
    flexmock(abstract).should_receive("signature").and_return(mock_sig).once()
    # Verify apply_async is called with correct parameters
    mock_sig.should_receive("apply_async").with_args(
        queue=CELERY_TASK_RATE_LIMITED_QUEUE,
        expires=RATE_LIMITED_QUEUE_EXPIRES_SECONDS,
    ).once()

    # Should raise RateLimitRequeueException
    with pytest.raises(RateLimitRequeueException):
        handler.check_rate_limit_remaining()


def test_check_rate_limit_remaining_already_in_rate_limited_queue(handler):
    """Test that method continues when task is already in rate-limited queue"""
    from packit_service import celerizer

    mock_service = flexmock(get_rate_limit_remaining=lambda: RATE_LIMIT_THRESHOLD - 50)
    mock_project = flexmock(service=mock_service)
    handler._project = mock_project

    mock_request = flexmock(delivery_info={"routing_key": CELERY_TASK_RATE_LIMITED_QUEUE})
    mock_task = flexmock(
        name=TaskName.copr_build,
        request=mock_request,
    )
    mock_app = flexmock()
    mock_app.current_worker_task = mock_task
    flexmock(celerizer).should_receive("get_celery_application").and_return(mock_app)

    # Should return without raising (task already in rate-limited queue)
    handler.check_rate_limit_remaining()


def test_check_rate_limit_remaining_project_exception(
    handler, mock_celery_app_with_task, monkeypatch
):
    """Test that method handles exceptions when getting project"""
    # Mock project property to raise exception
    from ogr.exceptions import OgrException

    # Use monkeypatch to replace the project property with one that raises
    def project_raises(self):
        raise OgrException("Test exception")

    monkeypatch.setattr(TestHandler, "project", property(project_raises))

    # Should return without raising
    handler.check_rate_limit_remaining()
