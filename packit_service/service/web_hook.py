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
import json
import logging
from hashlib import sha1

from flask import Flask, abort, request, jsonify

from packit.utils import set_logging
from packit_service.celerizer import celery_app
from packit_service.config import Config


class PackitWebhookReceiver(Flask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        set_logging(level=logging.DEBUG)


app = PackitWebhookReceiver(__name__)
config = Config.get_service_config()
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
        logger.info("webhook secret is not correct")
        abort(401)  # Unauthorized

    # TODO: define task names at one place
    celery_app.send_task(name="task.steve_jobs.process_message", kwargs={"event": msg})

    return "Webhook accepted. We thank you, Github."


@app.route("/testing-farm/results", methods=["POST"])
def testing_farm_results():
    msg = request.get_json()

    if not msg:
        logger.debug("/testing-farm/results: we haven't received any JSON data.")
        return "We haven't received any JSON data."

    if not validate_testing_farm_request():
        abort(401)  # Unauthorized

    celery_app.send_task(name="task.steve_jobs.process_message", kwargs={"event": msg})

    return json.dumps({"status": 200, "message": "Test results accepted"})


def validate_testing_farm_request():
    testing_farm_secret = config.testing_farm_secret
    if not testing_farm_secret:
        logger.error("testing_farm_secret not specified in config")
        return False

    token = request.get_json().get("token")
    if not token:
        logger.info("the request doesn't contain any token")
        return False
    if token == testing_farm_secret:
        return True

    logger.warning("Invalid testing farm secret provided!")
    return False


def validate_signature() -> bool:
    """
    https://developer.github.com/webhooks/securing/#validating-payloads-from-github
    https://developer.github.com/webhooks/#delivery-headers
    """
    if "X-Hub-Signature" not in request.headers:
        # no signature -> no validation
        # don't validate signatures when testing locally
        return not config.validate_webhooks

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
