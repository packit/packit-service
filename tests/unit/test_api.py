# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from datetime import datetime, timezone

import pytest

from packit_service.models import optional_time
from packit_service.service.api.system import get_commit_from_version


@pytest.mark.parametrize(
    "input_object,expected_type",
    [(datetime.now(timezone.utc), str), (None, type(None))],
)
def test_optional_time(input_object, expected_type):
    # optional_time returns a string if its passed a datetime object
    # None if passed a NoneType object
    assert isinstance(optional_time(input_object), expected_type)


@pytest.mark.parametrize(
    "version,commit",
    [
        ("0.76.0.post18+g116edc5", "116edc5"),
        ("0.1.dev1+gc03b1bd.d20230615", "c03b1bd"),
        ("0.18.0.post4+g28cb117", "28cb117"),
        ("0.45.1.dev2+g3b0fc3b", "3b0fc3b"),
    ],
)
def test_get_commit_from_version(version, commit):
    assert get_commit_from_version(version) == commit
