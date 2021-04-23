# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from packit_service.service.api.copr_builds import optional_time
from datetime import datetime
import pytest


@pytest.mark.parametrize(
    "input_object,expected_type", [(datetime.utcnow(), str), (None, type(None))]
)
def test_optional_time(input_object, expected_type):
    # optional_time returns a string if its passed a datetime object
    # None if passed a NoneType object
    assert isinstance(optional_time(input_object), expected_type)
