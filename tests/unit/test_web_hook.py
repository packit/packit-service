from flexmock import flexmock
from flask import Flask, request
import pytest

from packit_service.config import Config


@pytest.fixture()
def mock_config():
    config = flexmock(Config)
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
    flexmock(Config).should_receive("get_service_config").and_return(flexmock(Config))
    from packit_service.service import web_hook

    web_hook.config = mock_config

    with Flask(__name__).test_request_context():
        request._cached_data = request.data = payload
        request.headers = headers
        assert web_hook.validate_signature() is is_good
