# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from http import HTTPStatus

from flask import request
from flask_restx import Namespace, Resource, fields

from packit_service.celerizer import celery_app
from packit_service.config import ServiceConfig
from packit_service.models import TFTTestRunModel
from packit_service.service.api.errors import ValidationFailed
from packit_service.service.api.parsers import indices, pagination_arguments
from packit_service.service.api.utils import response_maker

logger = logging.getLogger("packit_service")

config = ServiceConfig.get_service_config()

ns = Namespace("testing-farm", description="Testing Farm")

payload = ns.model(
    "Testing Farm notification",
    {
        "request_id": fields.String(
            required=True, example="614d240a-1e27-4758-ad6a-ed3d34281924"
        ),
        "token": fields.String(required=True, example="HERE-IS-A-VALID-TOKEN"),
    },
)


@ns.route("/results")
class TestingFarmResults(Resource):
    @ns.response(HTTPStatus.ACCEPTED, "Test results accepted and being processed")
    @ns.response(HTTPStatus.BAD_REQUEST, "Bad request data")
    @ns.response(HTTPStatus.UNAUTHORIZED, "Testing farm secret validation failed")
    @ns.expect(payload)
    def post(self):
        """
        Submit Testing Farm results
        """
        msg = request.json

        if not msg:
            logger.debug("/testing-farm/results: we haven't received any JSON data.")
            return "We haven't received any JSON data.", HTTPStatus.BAD_REQUEST

        try:
            self.validate_testing_farm_request()
        except ValidationFailed as exc:
            logger.info(f"/testing-farm/results {exc}")
            return str(exc), HTTPStatus.UNAUTHORIZED

        # There's only one key in the msg,
        # so make sure we don't confuse this with something else
        msg["source"] = "testing-farm"
        celery_app.send_task(
            name="task.steve_jobs.process_message", kwargs={"event": msg}
        )

        return "Test results accepted", HTTPStatus.ACCEPTED

    @staticmethod
    def validate_testing_farm_request():
        """
        Validate testing farm token received in request with the one in packit-service.yaml

        Currently we use the same secret to authenticate both,
        packit service (when sending request to testing farm)
        and testing farm (when sending notification to packit service's webhook).
        We might later use a different secret for those use cases.

        :raises ValidationFailed
        """
        if not config.testing_farm_secret:
            msg = "Testing farm secret not specified in config"
            logger.error(msg)
            raise ValidationFailed(msg)

        token = request.json.get("token")
        if not token:
            msg = "The notification doesn't contain any token"
            logger.info(msg)
            raise ValidationFailed(msg)
        if token == config.testing_farm_secret:
            return

        msg = "Invalid testing farm secret provided"
        logger.warning(msg)
        raise ValidationFailed(msg)

    @ns.expect(pagination_arguments)
    @ns.response(HTTPStatus.PARTIAL_CONTENT.value, "Testing Farm Results follow")
    def get(self):
        """ List all Testing Farm  results. """

        result = []

        first, last = indices()
        # results have nothing other than ref in common, so it doesnt make sense to
        # merge them like copr builds
        for tf_result in TFTTestRunModel.get_range(first, last):
            result_dict = {
                "pipeline_id": tf_result.pipeline_id,
                "ref": tf_result.commit_sha,
                "status": tf_result.status,
                "target": tf_result.target,
                "web_url": tf_result.web_url,
                "pr_id": tf_result.get_pr_id(),
            }

            project = tf_result.get_project()
            result_dict["repo_namespace"] = project.namespace
            result_dict["repo_name"] = project.repo_name
            result_dict["project_url"] = project.project_url

            result.append(result_dict)

        resp = response_maker(
            result,
            status=HTTPStatus.PARTIAL_CONTENT.value,
        )
        resp.headers["Content-Range"] = f"test-results {first + 1}-{last}/*"
        return resp
