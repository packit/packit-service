# MIT License
#
# Copyright (c) 2018-2019 Red Hat, Inc.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
import pytest
from flask import Flask, request
from flexmock import flexmock

from packit_service.config import ServiceConfig


@pytest.fixture()
def mock_config():
    config = flexmock(ServiceConfig)
    config.webhook_secret = "testing-secret"
    return config


@pytest.mark.parametrize(
    "digest, is_good",
    [
        # hmac.new(webhook_secret, msg=payload, digestmod=hashlib.sha1).hexdigest()
        ("4e0281ef362383a2ab30c9dde79167da3b300b58", True),
        ("abcdefghijklmnopqrstuvqxyz", False),
    ],
)
def test_validate_signature(mock_config, digest, is_good):
    payload = b'{"zen": "Keep it logically awesome."}'
    headers = {"X-Hub-Signature": f"sha1={digest}"}

    # flexmock config before import as it fails on looking for config
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(
        flexmock(ServiceConfig)
    )
    from packit_service.service.api import webhooks

    webhooks.config = mock_config

    with Flask(__name__).test_request_context():
        request._cached_data = request.data = payload
        request.headers = headers
        assert webhooks.GithubWebhook.validate_signature() is is_good
