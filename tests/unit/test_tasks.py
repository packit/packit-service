# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import prometheus_client
import pytest
import redis
from celery.app.task import Task
from flexmock import flexmock
from packit.exceptions import PackitException

from packit_service.constants import REDIS_PIDBOX_TTL_SECONDS
from packit_service.worker.handlers import CoprBuildHandler
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.tasks import cleanup_orphaned_pidbox_queues, run_copr_build_handler


def test_autoretry():
    flexmock(prometheus_client).should_receive("push_to_gateway")
    flexmock(CoprBuildHandler).should_receive("run_job").and_raise(
        PackitException,
    ).once()

    # verify that retry is called automatically
    flexmock(Task).should_receive("retry").and_raise(PackitException).once()
    with pytest.raises(PackitException):
        run_copr_build_handler({}, {}, {})


def test_cleanup_orphaned_pidbox_queues():
    """Test that pidbox cleanup scans keys, sets TTL, and pushes metrics."""
    # Mock Redis client
    redis_client = flexmock()
    redis_client.should_receive("scan").with_args(
        cursor=0,
        match="*.reply.celery.pidbox",
        count=100,
    ).and_return((0, ["key1.reply.celery.pidbox", "key2.reply.celery.pidbox"])).once()

    # key1 has no TTL (-1), key2 already has TTL
    redis_client.should_receive("ttl").with_args("key1.reply.celery.pidbox").and_return(-1).once()
    redis_client.should_receive("ttl").with_args("key2.reply.celery.pidbox").and_return(1800).once()

    # Only key1 should get TTL set
    redis_client.should_receive("expire").with_args(
        "key1.reply.celery.pidbox",
        REDIS_PIDBOX_TTL_SECONDS,
    ).once()

    redis_client.should_receive("dbsize").and_return(42).once()

    # Mock Redis constructor
    flexmock(redis).should_receive("Redis").and_return(redis_client).once()

    # Mock Pushgateway
    gauge = flexmock()
    gauge.should_receive("set").with_args(42).once()

    pushgateway = flexmock(redis_keys_total=gauge)
    pushgateway.should_receive("push").once()

    flexmock(Pushgateway).new_instances(pushgateway)
    flexmock(prometheus_client).should_receive("push_to_gateway")

    cleanup_orphaned_pidbox_queues()
