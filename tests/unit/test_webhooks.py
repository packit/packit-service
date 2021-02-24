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
from packit_service.service.api.errors import ValidationFailed


@pytest.fixture()
def mock_config():
    config = flexmock(ServiceConfig)
    config.webhook_secret = "testing-secret"
    config.gitlab_token_secret = "gitlab-token-secret"
    config.gitlab_webhook_tokens = []
    config.validate_webhooks = True
    return config


@pytest.mark.parametrize(
    "headers, is_good",
    [
        # hmac.new(webhook_secret, msg=payload, digestmod=hashlib.sha1).hexdigest()
        ({"X-Hub-Signature": "sha1=4e0281ef362383a2ab30c9dde79167da3b300b58"}, True),
        ({"X-Hub-Signature": "sha1=abcdefghijklmnopqrstuvqxyz"}, False),
        ({}, False),
    ],
)
def test_validate_signature(mock_config, headers, is_good):
    payload = b'{"zen": "Keep it logically awesome."}'

    # flexmock config before import as it fails on looking for config
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(
        flexmock(validate_webhooks=True)
    )
    from packit_service.service.api import webhooks

    webhooks.config = mock_config

    with Flask(__name__).test_request_context():
        request._cached_data = request.data = payload
        request.headers = headers
        if not is_good:
            with pytest.raises(ValidationFailed):
                webhooks.GithubWebhook.validate_signature()
        else:
            webhooks.GithubWebhook.validate_signature()


@pytest.mark.parametrize(
    "headers, is_good",
    [
        (
            {
                "X-Gitlab-Token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9."
                "eyJuYW1lc3BhY2UiOiJtdWx0aS9wYXJ0L25hbWVzcGFjZSIsInJlcG9fbmFtZSI6InJlcG8ifQ."
                "r5-khuzdQJ3b15KZt3E1AqFXjtKfFn_Q1BBwkq04Mf8"
            },
            True,
        ),
        ({"X-Gitlab-Token": "guyirhgrehjguyrhg"}, False),
        ({"X-Gitlab-Token": "None"}, False),
        ({}, False),
    ],
)
def test_validate_token(mock_config, headers, is_good):
    payload = (
        b'{"project": {"path_with_namespace": "multi/part/namespace/repo", '
        b'"http_url": "https://gitlab.com/multi/part/namespace/repo.git"}}'
    )

    # flexmock config before import as it fails on looking for config
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(
        flexmock(ServiceConfig)
    )

    from packit_service.service.api import webhooks

    if "X-Gitlab-Token" not in headers and not is_good:
        flexmock(webhooks.GitlabWebhook).should_receive(
            "create_confidential_issue_with_token"
        ).mock()

    webhooks.config = mock_config

    temp = webhooks.GitlabWebhook()
    with Flask(__name__).test_request_context():
        request._cached_data = request.data = payload
        request.headers = headers
        if not is_good:
            with pytest.raises(ValidationFailed):
                webhooks.GitlabWebhook.validate_token(temp)
        else:
            webhooks.GitlabWebhook.validate_token(temp)
