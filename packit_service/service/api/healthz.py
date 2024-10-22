# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from http import HTTPStatus
from logging import getLogger

from flask_restx import Namespace, Resource

from packit_service.service.api.utils import response_maker

logger = getLogger("packit_service")

ns = Namespace("healthz", description="Health checks")


@ns.route("")
class HealthCheck(Resource):
    @ns.response(HTTPStatus.OK.value, "Healthy")
    def get(self):
        """Health check"""
        return response_maker(
            {"status": "We are healthy!"},
        )

    @ns.response(HTTPStatus.OK.value, "Healthy")
    def head(self):
        """Health check (no body)"""
        # HEAD is identical to GET except that it MUST NOT return a message-body in the response
