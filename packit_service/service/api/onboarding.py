# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from http import HTTPStatus
from os import getenv

from flask import request
from flask_restx import Namespace, Resource, fields

from packit_service.celerizer import celery_app
from packit_service.config import ServiceConfig
from packit_service.constants import CELERY_DEFAULT_MAIN_TASK_NAME
from packit_service.service.api.errors import ValidationFailed

logger = logging.getLogger("packit_service")

config = ServiceConfig.get_service_config()

ns = Namespace("onboarding", description="Packit dist-git onboarding")

payload = ns.model(
    "Packit dist-git onboarding request",
    {
        "package": fields.String(required=True, example="packit"),
        "open_pr": fields.Boolean(required=True, default=True),
        "token": fields.String(required=True, example="HERE-IS-A-VALID-TOKEN"),
    },
)


@ns.route("/request")
class OnboardingRequest(Resource):
    @ns.response(HTTPStatus.OK.value, "Request has been accepted")
    @ns.response(HTTPStatus.BAD_REQUEST.value, "Bad request data")
    @ns.response(HTTPStatus.UNAUTHORIZED.value, "Secret validation failed")
    @ns.expect(payload)
    def post(self):
        msg = request.json

        if not msg:
            logger.debug("/onboarding/request: we haven't received any JSON data.")
            return "We haven't received any JSON data.", HTTPStatus.BAD_REQUEST

        try:
            self.validate_onboarding_request()
        except ValidationFailed as exc:
            logger.info(f"/onboarding/request {exc}")
            return str(exc), HTTPStatus.UNAUTHORIZED

        msg["source"] = "onboarding"

        celery_app.send_task(
            name=getenv("CELERY_MAIN_TASK_NAME") or CELERY_DEFAULT_MAIN_TASK_NAME,
            kwargs={
                "event": msg,
                "source": "onboarding",
                "event_type": "request",
            },
        )

        return "Onboarding request accepted", HTTPStatus.OK

    @staticmethod
    def validate_onboarding_request():
        if not config.onboarding_secret:
            msg = "Onboarding secret not specified in config"
            logger.error(msg)
            raise ValidationFailed(msg)

        if not (token := request.json.get("token")):
            msg = "The request doesn't contain any token"
            logger.info(msg)
            raise ValidationFailed(msg)

        if token == config.onboarding_secret:
            return

        msg = "Invalid onboarding secret provided"
        logger.warning(msg)
        raise ValidationFailed(msg)
