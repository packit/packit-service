# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
mock webhook payload and send it to an existing packit service
"""
import pytest
import requests


# TODO: create a script to start service+redis containers before running this
@pytest.mark.xfail  # depends on http://localhost:8443
def test_prop_update_on_packit_020():
    url = "http://localhost:8443/webhooks/github"
    payload = {
        "repository": {
            "name": "packit",
            "html_url": "https://github.com/packit/packit",
            "owner": {"login": "packit-service"},
        },
        "release": {"tag_name": "0.2.0"},
    }
    response = requests.post(url=url, json=payload)
    assert response.ok
