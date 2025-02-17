# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import datetime

import pytest
from flexmock import flexmock

from packit_service.worker.handlers import (
    CoprBuildHandler,
    TestingFarmHandler,
)
from packit_service.worker.jobs import SteveJobs


@pytest.mark.parametrize(
    "handler, targets",
    [
        pytest.param(CoprBuildHandler, 0, id="correct handler, no builds"),
        pytest.param(TestingFarmHandler, 5, id="invalid handler, builds are present"),
    ],
)
def test_copr_metrics_ignored(handler, targets):
    event = flexmock()

    counter = flexmock()
    counter.should_receive("inc").never()

    pushgateway = flexmock(copr_builds_queued=counter)

    jobs = SteveJobs(event)
    jobs.pushgateway = pushgateway

    jobs.push_copr_metrics(handler, targets)


def test_copr_metrics_pushed():
    event = flexmock()

    counter = flexmock()
    counter.should_receive("inc").with_args(7).once()

    pushgateway = flexmock(copr_builds_queued=counter)

    jobs = SteveJobs(event)
    jobs.pushgateway = pushgateway

    jobs.push_copr_metrics(CoprBuildHandler, 7)


def test_delayed():
    created_at = datetime.datetime(2023, 3, 14)
    event = flexmock(created_at=created_at, event_type=lambda: "event.Delayed")

    counter = flexmock()
    counter.should_receive("inc").once()

    first = flexmock()
    first.should_receive("observe").once()

    last = flexmock()
    last.should_receive("observe").once()

    pushgateway = flexmock(
        first_initial_status_time=first,
        last_initial_status_time=last,
        no_status_after_25_s=counter,
    )

    jobs = SteveJobs(event)
    jobs.pushgateway = pushgateway

    jobs.push_statuses_metrics([created_at + datetime.timedelta(seconds=42)])
