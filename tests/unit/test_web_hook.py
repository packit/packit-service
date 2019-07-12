from flexmock import flexmock
from flask import Flask, request
import pytest

from packit.config import Config
from packit_service.service import web_hook


@pytest.fixture()
def mock_config():
    web_hook.config = flexmock(Config)
    web_hook.config.webhook_secret = "testing-secret"


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

    with Flask(__name__).test_request_context():
        request._cached_data = request.data = payload
        request.headers = headers
        assert web_hook.validate_signature() is is_good
