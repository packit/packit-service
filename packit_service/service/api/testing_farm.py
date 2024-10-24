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
from packit_service.models import (
    TFTTestRunGroupModel,
    TFTTestRunTargetModel,
    optional_timestamp,
)
from packit_service.service.api.errors import ValidationFailed
from packit_service.service.api.parsers import indices, pagination_arguments
from packit_service.service.api.utils import get_project_info_from_build, response_maker

logger = logging.getLogger("packit_service")

config = ServiceConfig.get_service_config()

ns = Namespace("testing-farm", description="Testing Farm")

payload = ns.model(
    "Testing Farm notification",
    {
        "request_id": fields.String(
            required=True,
            example="614d240a-1e27-4758-ad6a-ed3d34281924",
        ),
        "token": fields.String(required=True, example="HERE-IS-A-VALID-TOKEN"),
    },
)


@ns.route("/results")
class TestingFarmResults(Resource):
    @ns.response(HTTPStatus.OK.value, "Notification has been accepted")
    @ns.response(HTTPStatus.BAD_REQUEST.value, "Bad request data")
    @ns.response(HTTPStatus.UNAUTHORIZED.value, "Testing farm secret validation failed")
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

        msg["source"] = "testing-farm"  # TODO: remove me
        celery_app.send_task(
            name=getenv("CELERY_MAIN_TASK_NAME") or CELERY_DEFAULT_MAIN_TASK_NAME,
            kwargs={
                "event": msg,
                "source": "testing-farm",
                "event_type": "results",
            },
        )

        return "Test results accepted", HTTPStatus.OK

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

        if token == config.testing_farm_secret or (
            config.internal_testing_farm_secret and token == config.internal_testing_farm_secret
        ):
            return

        msg = "Invalid testing farm secret provided"
        logger.warning(msg)
        raise ValidationFailed(msg)

    @ns.expect(pagination_arguments)
    @ns.response(HTTPStatus.PARTIAL_CONTENT.value, "Testing Farm Results follow")
    def get(self):
        """List all Testing Farm results."""

        result = []

        first, last = indices()
        # results have nothing other than ref in common, so it doesn't make sense to
        # merge them like copr builds
        for tf_result in TFTTestRunTargetModel.get_range(first, last):
            result_dict = {
                "packit_id": tf_result.id,
                "pipeline_id": tf_result.pipeline_id,
                "ref": tf_result.commit_sha,
                "status": tf_result.status,
                "target": tf_result.target,
                "web_url": tf_result.web_url,
                "pr_id": tf_result.get_pr_id(),
                "submitted_time": optional_timestamp(tf_result.submitted_time),
            }

            project = tf_result.get_project()
            result_dict["repo_namespace"] = project.namespace if project else None
            result_dict["repo_name"] = project.repo_name if project else None
            result_dict["project_url"] = project.project_url if project else None

            result.append(result_dict)

        resp = response_maker(
            result,
            status=HTTPStatus.PARTIAL_CONTENT,
        )
        resp.headers["Content-Range"] = f"test-results {first + 1}-{last}/*"
        return resp


@ns.route("/<int:id>")
@ns.param("id", "Packit id of the test run")
class TestingFarmResult(Resource):
    @ns.response(HTTPStatus.OK.value, "OK, test run details follow")
    @ns.response(HTTPStatus.NOT_FOUND.value, "No info about test run stored in DB")
    def get(self, id):
        """A specific test run details."""
        test_run_model = TFTTestRunTargetModel.get_by_id(int(id))

        if not test_run_model:
            return response_maker(
                {"error": "No info about build stored in DB"},
                status=HTTPStatus.NOT_FOUND,
            )

        test_result_dict = {
            "pipeline_id": test_run_model.pipeline_id,
            "status": test_run_model.status,
            "chroot": test_run_model.target,
            "commit_sha": test_run_model.commit_sha,
            "web_url": test_run_model.web_url,
            "copr_build_ids": [build.id for build in test_run_model.copr_builds],
            "run_ids": sorted(run.id for run in test_run_model.group_of_targets.runs),
            "submitted_time": optional_timestamp(test_run_model.submitted_time),
        }

        test_result_dict.update(get_project_info_from_build(test_run_model))
        return response_maker(test_result_dict)


@ns.route("/groups/<int:id>")
@ns.param("id", "Packit id of the test run group")
class TestingFarmGroup(Resource):
    @ns.response(HTTPStatus.OK, "OK, test run group details follow")
    @ns.response(
        HTTPStatus.NOT_FOUND.value,
        "No info about test run group stored in DB",
    )
    def get(self, id):
        """A specific test run details."""
        group_model = TFTTestRunGroupModel.get_by_id(int(id))

        if not group_model:
            return response_maker(
                {"error": "No info about group stored in DB"},
                status=HTTPStatus.NOT_FOUND,
            )

        group_dict = {
            "submitted_time": optional_timestamp(group_model.submitted_time),
            "run_ids": sorted(run.id for run in group_model.runs),
            "test_target_ids": sorted(test.id for test in group_model.grouped_targets),
        }

        group_dict.update(get_project_info_from_build(group_model))
        return response_maker(group_dict)
