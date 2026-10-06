# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import pytest

from packit_service.service.app import packit_as_a_service as application


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
