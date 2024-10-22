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
    config.validate_webhooks = True
    return config


@pytest.mark.parametrize(
    "headers, is_good",
    [
        # hmac.new(webhook_secret, msg=payload, digestmod=hashlib.sha256).hexdigest()
        (
            {
                "X-Hub-Signature-256": "sha256="
                "7884c9fc5f880c17920b2066e85aae7b57489505a16aa9b56806a924df78f846",
            },
            True,
        ),
        (
            {
                "X-Hub-Signature-256": "sha256="
                "feedfacecafebeef920b2066e85aae7b57489505a16aa9b56806a924df78f666",
            },
            False,
        ),
        ({}, False),
    ],
)
def test_validate_signature(mock_config, headers, is_good):
    # flexmock config before import as it fails on looking for config
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(
        flexmock(validate_webhooks=True),
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
                # token generated for specific repo
                # jwt.encode({"namespace": "multi/part/namespace", "repo_name": "repo"},
                #            "gitlab-token-secret", algorithm="HS256")
                "X-Gitlab-Token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9."
                "eyJuYW1lc3BhY2UiOiJtdWx0aS9wYXJ0L25hbWVzcGFjZSIsInJlcG9fbmFtZSI6InJlcG8ifQ."
                "r5-khuzdQJ3b15KZt3E1AqFXjtKfFn_Q1BBwkq04Mf8",
            },
            True,
        ),
        (
            {
                # token generated for whole group/namespace
                # jwt.encode({"namespace": "multi/part/namespace"},
                #            "gitlab-token-secret", algorithm="HS256")
                "X-Gitlab-Token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9."
                "eyJuYW1lc3BhY2UiOiJtdWx0aS9wYXJ0L25hbWVzcGFjZSJ9."
                "WNasZgIU91hMwKtGeGCILjPIDLU-PpL5rww-BEAzMgU",
            },
            True,
        ),
        (
            {
                # token generated for a different repo
                # jwt.encode({"namespace": "multi/part/namespace", "repo_name": "repo2"},
                #            "gitlab-token-secret", algorithm="HS256")
                "X-Gitlab-Token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9."
                "eyJuYW1lc3BhY2UiOiJtdWx0aS9wYXJ0L25hbWVzcGFjZSIsInJlcG9fbmFtZSI6InJlcG8yIn0."
                "vyQYbtmaCyHfDKpfmyk_uAn9QvDulnaIy2wZ1xgc-uI",
            },
            False,
        ),
        ({"X-Gitlab-Token": "None"}, False),
        ({}, False),
    ],
)
def test_validate_token(mock_config, headers, is_good):
    # flexmock config before import as it fails on looking for config
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(
        flexmock(ServiceConfig),
    )

    from packit_service.service.api import webhooks

    if "X-Gitlab-Token" not in headers and not is_good:
        flexmock(webhooks.GitlabWebhook).should_receive(
            "create_confidential_issue_with_token",
        ).mock()

    webhooks.config = mock_config

    temp = webhooks.GitlabWebhook()
    with Flask(__name__).test_request_context():
        payload = {
            "project": {
                "http_url": "https://gitlab.com/multi/part/namespace/repo.git",
            },
        }
        request._cached_data = request.data = dumps(payload).encode()
        request.headers = headers
        if not is_good:
            with pytest.raises(ValidationFailed):
                webhooks.GitlabWebhook.validate_token(temp)
        else:
            webhooks.GitlabWebhook.validate_token(temp)


@pytest.mark.parametrize(
    "headers, payload, interested",
    [
        (
            {"X-GitHub-Event": "check_run", "X-GitHub-Delivery": "uuid"},
            {"action": "rerequested"},
            True,
        ),
        (
            {"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "uuid"},
            {"action": "opened"},
            True,
        ),
        (
            {"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "uuid"},
            {"action": "reopened"},
            True,
        ),
        (
            {"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "uuid"},
            {"action": "closed"},
            False,
        ),
        (
            {"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "uuid"},
            {"action": "edited"},
            False,
        ),
        (
            {"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "uuid"},
            {"action": "synchronize"},
            True,
        ),
        (
            {
                "X-GitHub-Event": "pull_request_review_comment",
                "X-GitHub-Delivery": "uuid",
            },
            {"action": "created"},
            False,
        ),
        (
            {"X-GitHub-Event": "issue_comment", "X-GitHub-Delivery": "uuid"},
            {"action": "created"},
            True,
        ),
        (
            {"X-GitHub-Event": "issue_comment", "X-GitHub-Delivery": "uuid"},
            {"action": "created"},
            True,
        ),
        (
            {"X-GitHub-Event": "issue_comment", "X-GitHub-Delivery": "uuid"},
            {"action": "edited"},
            True,
        ),
        (
            {"X-GitHub-Event": "release", "X-GitHub-Delivery": "uuid"},
            {"action": "created"},
            False,
        ),
        (
            {"X-GitHub-Event": "release", "X-GitHub-Delivery": "uuid"},
            {"action": "published"},
            True,
        ),
        (
            {"X-GitHub-Event": "release", "X-GitHub-Delivery": "uuid"},
            {"action": "released"},
            False,
        ),
        (
            {"X-GitHub-Event": "push", "X-GitHub-Delivery": "uuid"},
            {"deleted": False},
            True,
        ),
        (
            {"X-GitHub-Event": "push", "X-GitHub-Delivery": "uuid"},
            {"deleted": True},
            False,
        ),
        (
            {"X-GitHub-Event": "installation", "X-GitHub-Delivery": "uuid"},
            {"action": "created"},
            True,
        ),
        (
            {"X-GitHub-Event": "installation", "X-GitHub-Delivery": "uuid"},
            {"action": "deleted"},
            False,
        ),
        (
            {"X-GitHub-Event": "label"},
            {"action": "created"},
            False,
        ),
    ],
)
def test_interested(mock_config, headers, payload, interested):
    # flexmock config before import as it fails on looking for config
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(
        flexmock(ServiceConfig),
    )

    from packit_service.service.api import webhooks

    webhooks.config = mock_config

    with Flask(__name__).test_request_context(
        json=payload,
        content_type="application/json",
        headers=headers,
    ):
        assert webhooks.GithubWebhook.interested() == interested
