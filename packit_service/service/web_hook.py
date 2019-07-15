# MIT License
#
# Copyright (c) 2018-2019 Red Hat, Inc.

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

import hmac
import logging
import os
from hashlib import sha1

from flask import Flask, abort, request, jsonify

from packit.config import Config
from packit.utils import set_logging
from packit_service.celerizer import celery_app


class PackitWebhookReceiver(Flask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        set_logging(level=logging.DEBUG)


app = PackitWebhookReceiver(__name__)
config = Config.get_user_config()
logger = logging.getLogger("packit_service")


@app.route("/healthz", methods=["GET", "HEAD", "POST"])
def get_health():
    # TODO: add some interesting stats here
    return jsonify({"msg": "We are healthy!"})


@app.route("/webhooks/github", methods=["POST"])
def github_webhook():
    msg = request.get_json()

    if not msg:
        logger.debug("/webhooks/github: we haven't received any JSON data.")
        return "We haven't received any JSON data."

    if all([msg.get("zen"), msg.get("hook_id"), msg.get("hook")]):
        logger.debug(f"/webhooks/github received ping event: {msg['hook']}")
        return "Pong!"

    if not validate_signature():
        abort(401)  # Unauthorized

    # TODO: define task names at one place
    celery_app.send_task(name="task.steve_jobs.process_message", kwargs={"event": msg})

    return "Webhook accepted. We thank you, Github."


def validate_signature() -> bool:
    """
    https://developer.github.com/webhooks/securing/#validating-payloads-from-github
    https://developer.github.com/webhooks/#delivery-headers
    """
    if "X-Hub-Signature" not in request.headers:
        # no signature -> no validation
        # don't validate signatures when testing locally
        return os.getenv("DEPLOYMENT") == "dev"

    sig = request.headers["X-Hub-Signature"]
    if not sig.startswith("sha1="):
        logger.warning(f"Digest mode in X-Hub-Signature {sig!r} is not sha1")
        return False

    webhook_secret = config.webhook_secret.encode()
    if not webhook_secret:
        logger.error("webhook_secret not specified in config")
        return False

    signature = sig.split("=")[1]
    mac = hmac.new(webhook_secret, msg=request.get_data(), digestmod=sha1)
    digest_is_valid = hmac.compare_digest(signature, mac.hexdigest())
    if digest_is_valid:
        logger.debug(f"/webhooks/github payload signature OK.")
    else:
        logger.warning(f"/webhooks/github payload signature validation failed.")
        logger.debug(f"X-Hub-Signature: {sig!r} != computed: {mac.hexdigest()}")
    return digest_is_valid
