# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import prometheus_client
import pytest
from celery.app.task import Task
from flexmock import flexmock
from packit.exceptions import PackitException

from packit_service.worker.handlers import CoprBuildHandler
from packit_service.worker.tasks import run_copr_build_handler


def test_autoretry():
    flexmock(prometheus_client).should_receive("push_to_gateway")
    flexmock(CoprBuildHandler).should_receive("run_job").and_raise(
        PackitException,
    ).once()

    # verify that retry is called automatically
    flexmock(Task).should_receive("retry").and_raise(PackitException).once()
    with pytest.raises(PackitException):
        run_copr_build_handler({}, {}, {})
