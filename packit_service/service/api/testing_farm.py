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

import logging
from http import HTTPStatus

from flask import request

try:
    from flask_restx import Namespace, Resource, fields
except ModuleNotFoundError:
    from flask_restplus import Namespace, Resource, fields

from packit_service.service.api.utils import response_maker
from packit_service.celerizer import celery_app
from packit_service.config import ServiceConfig
from packit_service.service.api.errors import ValidationFailed
from packit_service.models import TFTTestRunModel
from packit_service.service.api.parsers import indices, pagination_arguments

logger = logging.getLogger("packit_service")

config = ServiceConfig.get_service_config()

ns = Namespace("testing-farm", description="Testing Farm")

payload_artifact = ns.model(
    "Testing Farm results artifact",
    {
        "commit-sha": fields.String(
            required=True, example="08bfc38f15082bdf9ba964c3bbd04878666d1d56"
        ),
        "copr-chroot": fields.String(required=True, example="fedora-30-x86_64"),
        "copr-repo-name": fields.String(
            required=True, example="packit/packit-service-hello-world-14"
        ),
        "git-ref": fields.String(
            required=True, example="08bfc38f15082bdf9ba964c3bbd04878666d1d56"
        ),
        "git-url": fields.Url(
            required=True, example="https://github.com/packit-service/hello-world"
        ),
        "repo-name": fields.String(required=True, example="hello-world"),
        "repo-namespace": fields.String(required=True, example="packit-service"),
    },
)
payload_pipeline = ns.model(
    "Testing Farm results pipeline",
    {
        "id": fields.String(
            required=True, example="614d240a-1e27-4758-ad6a-ed3d34281924"
        )
    },
)
payload = ns.model(
    "Testing Farm results",
    {
        "artifact": fields.Nested(payload_artifact),
        "message": fields.String(required=True, example="Command 'git' not found"),
        "pipeline": fields.Nested(payload_pipeline),
        "result": fields.String(required=True, example="error"),
        "token": fields.String(required=True, example="HERE-IS-A-VALID-TOKEN"),
        "url": fields.Url(
            required=True,
            example="https://console-testing-farm.apps.ci.centos.org/pipeline/<ID>",
        ),
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

        celery_app.send_task(
            name="task.steve_jobs.process_message", kwargs={"event": msg}
        )

        return "Test results accepted", HTTPStatus.ACCEPTED

    @staticmethod
    def validate_testing_farm_request():
        """
        Validate testing farm token received in request with the one in packit-service.yaml
        :raises ValidationFailed
        """
        if not config.testing_farm_secret:
            msg = "Testing farm secret not specified in config"
            logger.error(msg)
            raise ValidationFailed(msg)

        token = request.json.get("token")
        if not token:
            msg = "The request doesn't contain any token"
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

            result.append(result_dict)

        resp = response_maker(result, status=HTTPStatus.PARTIAL_CONTENT.value,)
        resp.headers["Content-Range"] = f"test-results {first + 1}-{last}/*"
        return resp
