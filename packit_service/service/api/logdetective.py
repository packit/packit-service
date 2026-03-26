# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from http import HTTPStatus

from flask_restx import Namespace, Resource

from packit_service.models import (
    LogDetectiveRunGroupModel,
    LogDetectiveRunModel,
    optional_timestamp,
)
from packit_service.service.api.parsers import indices, pagination_arguments
from packit_service.service.api.utils import get_project_info_from_build, response_maker

logger = logging.getLogger("packit_service")

ns = Namespace("log-detective", description="Log Detective")


@ns.route("/<int:id>")
@ns.param("id", "Packit id of the Log Detective run")
class LogDetectiveResult(Resource):
    @ns.response(HTTPStatus.OK.value, "OK, Log Detective run details follow")
    @ns.response(HTTPStatus.NOT_FOUND.value, "No info about Log Detective run stored in DB")
    def get(self, id):
        """A specific Log Detective run details."""
        log_detective_run_model = LogDetectiveRunModel.get_by_id(int(id))

        if not log_detective_run_model:
            return response_maker(
                {"error": "No info about Log Detective run stored in DB"},
                status=HTTPStatus.NOT_FOUND,
            )
        run_ids = []
        if log_detective_run_model.group_of_targets.runs:
            run_ids = sorted(run.id for run in log_detective_run_model.group_of_targets.runs)

        log_detective_result_dict = {
            "packit_id": log_detective_run_model.id,
            "analysis_id": log_detective_run_model.analysis_id,
            "status": log_detective_run_model.status.value,
            "chroot": log_detective_run_model.target,
            "commit_sha": log_detective_run_model.commit_sha,
            "log_detective_response": log_detective_run_model.log_detective_response,
            "target_build": log_detective_run_model.target_build,
            "run_ids": run_ids,
            "submitted_time": optional_timestamp(log_detective_run_model.submitted_time),
        }

        log_detective_result_dict.update(get_project_info_from_build(log_detective_run_model))
        return response_maker(log_detective_result_dict)


@ns.route("/groups/<int:id>")
@ns.param("id", "Packit id of the Log Detective run group")
class LogDetectiveGroup(Resource):
    @ns.response(HTTPStatus.OK, "OK, Log Detective run group details follow")
    @ns.response(
        HTTPStatus.NOT_FOUND.value,
        "No info about Log Detective run group stored in DB",
    )
    def get(self, id):
        """A specific Log Detective run group details."""
        group_model = LogDetectiveRunGroupModel.get_by_id(int(id))

        if not group_model:
            return response_maker(
                {"error": "No info about group stored in DB"},
                status=HTTPStatus.NOT_FOUND,
            )
        run_ids = []
        if group_model.runs:
            run_ids = sorted(run.id for run in group_model.runs)
        group_dict = {
            "packit_id": group_model.id,
            "submitted_time": optional_timestamp(group_model.submitted_time),
            "run_ids": run_ids,
            "log_detective_target_ids": sorted(ld_run.id for ld_run in group_model.grouped_targets),
        }

        group_dict.update(get_project_info_from_build(group_model))
        return response_maker(group_dict)


@ns.route("")
class LogDetectiveResultList(Resource):
    @ns.expect(pagination_arguments)
    @ns.response(HTTPStatus.PARTIAL_CONTENT, "Log Detective result list follows")
    def get(self):
        """List all Log Detective results."""

        first, last = indices()
        result = []

        for log_detective_run_model in LogDetectiveRunModel.get_range(first, last):
            run_ids = []
            if log_detective_run_model.group_of_targets.runs:
                run_ids = sorted(run.id for run in log_detective_run_model.group_of_targets.runs)
            log_detective_result_dict = {
                "packit_id": log_detective_run_model.id,
                "analysis_id": log_detective_run_model.analysis_id,
                "status": log_detective_run_model.status.value,
                "chroot": log_detective_run_model.target,
                "commit_sha": log_detective_run_model.commit_sha,
                "log_detective_response": log_detective_run_model.log_detective_response,
                "target_build": log_detective_run_model.target_build,
                "run_ids": run_ids,
                "submitted_time": optional_timestamp(log_detective_run_model.submitted_time),
            }
            result.append(log_detective_result_dict)

        resp = response_maker(
            result,
            status=HTTPStatus.PARTIAL_CONTENT,
        )
        resp.headers["Content-Range"] = f"log-detective-results {first + 1}-{last}/*"
        return resp
