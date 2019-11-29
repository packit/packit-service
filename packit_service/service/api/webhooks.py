# MIT License
#
# Copyright (c) 2019 Red Hat, Inc.
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
import hmac
from hashlib import sha1
from http import HTTPStatus
from logging import getLogger

from flask import request
from flask_restplus import Namespace, Resource, fields

from packit_service.celerizer import celery_app
from packit_service.config import ServiceConfig
from packit_service.service.api.errors import ValidationFailed

logger = getLogger("packit_service")
config = ServiceConfig.get_service_config()

ns = Namespace("webhooks", description="Webhooks")

# Just to be able to specify some payload in Swagger UI
ping_payload = ns.model(
    "Github webhook ping",
    {
        "zen": fields.String(required=False),
        "hook_id": fields.String(required=False),
        "hook": fields.String(required=False),
    },
)


@ns.route("/github")
class GithubWebhook(Resource):
    @ns.response(HTTPStatus.OK, "Webhook accepted, returning reply")
    @ns.response(HTTPStatus.ACCEPTED, "Webhook accepted, request is being processed")
    @ns.response(HTTPStatus.BAD_REQUEST, "Bad request data")
    @ns.response(HTTPStatus.UNAUTHORIZED, "X-Hub-Signature validation failed")
    # Just to be able to specify some payload in Swagger UI
    @ns.expect(ping_payload)
    def post(self):
        """
        A webhook used by Packit-as-a-Service GitHub App.
        """
        msg = request.json

        if not msg:
            logger.debug("/webhooks/github: we haven't received any JSON data.")
            return "We haven't received any JSON data.", HTTPStatus.BAD_REQUEST

        if all([msg.get("zen"), msg.get("hook_id"), msg.get("hook")]):
            logger.debug(f"/webhooks/github received ping event: {msg['hook']}")
            return "Pong!", HTTPStatus.OK

        try:
            self.validate_signature()
        except ValidationFailed as exc:
            logger.info(f"/webhooks/github {exc}")
            return str(exc), HTTPStatus.UNAUTHORIZED

        if not self.interested():
            return "Thanks but we don't care about this event", HTTPStatus.ACCEPTED

        # TODO: define task names at one place
        celery_app.send_task(
            name="task.steve_jobs.process_message", kwargs={"event": msg}
        )

        return "Webhook accepted. We thank you, Github.", HTTPStatus.ACCEPTED

    @staticmethod
    def validate_signature():
        """
        https://developer.github.com/webhooks/securing/#validating-payloads-from-github
        https://developer.github.com/webhooks/#delivery-headers
        """
        if "X-Hub-Signature" not in request.headers:
            if config.validate_webhooks:
                msg = "X-Hub-Signature not in request.headers"
                logger.warning(msg)
                raise ValidationFailed(msg)
            else:
                # don't validate signatures when testing locally
                logger.debug("Ain't validating signatures")
                return

        sig = request.headers["X-Hub-Signature"]
        if not sig.startswith("sha1="):
            msg = f"Digest mode in X-Hub-Signature {sig!r} is not sha1"
            logger.warning(msg)
            raise ValidationFailed(msg)

        webhook_secret = config.webhook_secret.encode()
        if not webhook_secret:
            msg = "webhook_secret not specified in config"
            logger.error(msg)
            raise ValidationFailed(msg)

        signature = sig.split("=")[1]
        mac = hmac.new(webhook_secret, msg=request.get_data(), digestmod=sha1)
        digest_is_valid = hmac.compare_digest(signature, mac.hexdigest())
        if digest_is_valid:
            logger.debug("payload signature OK.")
        else:
            msg = "payload signature validation failed."
            logger.warning(msg)
            logger.debug(f"X-Hub-Signature: {sig!r} != computed: {mac.hexdigest()}")
            raise ValidationFailed(msg)

    @staticmethod
    def interested():
        """
        Check X-GitHub-Event header for events we know we give a f...
        ...finely prepared response to.
        :return: False if we are not interested in this kind of event
        """
        uninteresting_events = {
            "integration_installation",
            "integration_installation_repositories",
        }
        event_type = request.headers.get("X-GitHub-Event")
        uuid = request.headers.get("X-GitHub-Delivery")
        _interested = event_type not in uninteresting_events

        logger.debug(
            f"{event_type} {uuid}{' (not interested)' if not _interested else ''}"
        )
        return _interested
