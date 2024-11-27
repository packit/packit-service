# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from datetime import datetime, timedelta

import pytest
from flexmock import flexmock

from packit_service.models import (
    TestingFarmResult,
    filter_most_recent_target_models_by_status,
    filter_most_recent_target_names_by_status,
)


@pytest.fixture
def models():
    model1 = flexmock(
        target="target-a",
        identifier="",
        submitted_time=datetime.now() - timedelta(hours=2),
        status=TestingFarmResult.passed,
    )

    model2 = flexmock(
        target="target-a",
        identifier="",
        submitted_time=datetime.now(),
        status=TestingFarmResult.passed,
    )

    model3 = flexmock(
        target="target-a",
        identifier="",
        submitted_time=datetime.now() - timedelta(hours=1),
        status=TestingFarmResult.failed,
    )

    model4 = flexmock(
        target="target-b",
        identifier="",
        submitted_time=datetime.now(),
        status=TestingFarmResult.failed,
    )

    model5 = flexmock(
        target="target-b",
        identifier="",
        submitted_time=datetime.now() - timedelta(hours=1),
        status=TestingFarmResult.passed,
    )

    return [model1, model2, model3, model4, model5]


def test_filter_most_recent_target_models_by_status(models):
    assert filter_most_recent_target_models_by_status(
        models,
        [TestingFarmResult.passed],
    ) == {models[1]}


def test_filter_most_recent_target_names_by_status(models):
    assert filter_most_recent_target_names_by_status(
        models,
        [TestingFarmResult.passed],
    ) == {("target-a", "")}
