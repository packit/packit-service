# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from datetime import datetime, timezone

import pytest

from packit_service.models import optional_time
from packit_service.service.api.system import get_commit_from_version
from packit_service.service.api.usage import process_timestamps


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


@pytest.mark.parametrize(
    "start, end, expected_result",
    (
        (None, None, ([], None, None)),
        ("2023-08-28T03:30:58-07:00", None, ([], "2023-08-28T10:30:58+00:00", None)),
        (None, "2023-08-28T03:30:58-07:00", ([], None, "2023-08-28T10:30:58+00:00")),
        (
            "2023-08-01 02:00:00+02:00",
            "2023-09-01 02:00:00 +02:00",
            ([], "2023-08-01T00:00:00+00:00", "2023-09-01T00:00:00+00:00"),
        ),
        # Have fun trying to find a difference :)
        (
            "2023‐08‐28T03:30:58−07:00",
            None,
            (["From timestamp: invalid format"], None, None),
        ),
        (
            None,
            "2023‐08‐28T03:30:58−07:00",
            (["To timestamp: invalid format"], None, None),
        ),
        (
            "2023‐08‐28T03:30:58−07:00",
            "2023‐08‐28T03:30:58−07:00",
            (
                ["From timestamp: invalid format", "To timestamp: invalid format"],
                None,
                None,
            ),
        ),
    ),
)
def test_process_timestamps(start, end, expected_result):
    assert process_timestamps(start, end) == expected_result
