# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
from json import dumps

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
    # flexmock config before import as it fails on looking for config
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(
        flexmock(validate_webhooks=True)
    )
    from packit_service.service.api import webhooks

    webhooks.config = mock_config

    with Flask(__name__).test_request_context():
        payload = {"zen": "Keep it logically awesome."}

        request._cached_data = request.data = dumps(payload).encode()
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
        payload = {
            "project": {
                "path_with_namespace": "multi/part/namespace/repo",
                "http_url": "https://gitlab.com/multi/part/namespace/repo.git",
            }
        }
        request._cached_data = request.data = dumps(payload).encode()
        request.headers = headers
        if not is_good:
            with pytest.raises(ValidationFailed):
                webhooks.GitlabWebhook.validate_token(temp)
        else:
            webhooks.GitlabWebhook.validate_token(temp)
