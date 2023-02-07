# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from http import HTTPStatus
from logging import getLogger

from flask_restx import Namespace, Resource

from packit_service.models import (
    SyncReleaseTargetModel,
    SyncReleaseModel,
    optional_timestamp,
)
from packit_service.service.api.parsers import indices, pagination_arguments
from packit_service.service.api.utils import get_project_info_from_build, response_maker

logger = getLogger("packit_service")

ns = Namespace("propose-downstream", description="Propose Downstream")


@ns.route("")
class ProposeDownstreamList(Resource):
    @ns.expect(pagination_arguments)
    @ns.response(HTTPStatus.PARTIAL_CONTENT.value, "Propose Downstreams results follow")
    def get(self):
        """List of all Propose Downstreams results."""

        result = []
        first, last = indices()
        for propose_downstream_results in SyncReleaseModel.get_range_propose_downstream(
            first, last
        ):
            result_dict = {
                "packit_id": propose_downstream_results.id,
                "status": propose_downstream_results.status,
                "submitted_time": optional_timestamp(
                    propose_downstream_results.submitted_time
                ),
                "status_per_downstream_pr": {
                    pr.branch: pr.status
                    for pr in propose_downstream_results.sync_release_targets
                },
                "packit_id_per_downstream_pr": {
                    pr.branch: pr.id
                    for pr in propose_downstream_results.sync_release_targets
                },
                "pr_id": propose_downstream_results.get_pr_id(),
                "issue_id": propose_downstream_results.get_issue_id(),
                "release": propose_downstream_results.get_release_tag(),
            }

            project = propose_downstream_results.get_project()
            result_dict["repo_namespace"] = project.namespace
            result_dict["repo_name"] = project.repo_name
            result_dict["project_url"] = project.project_url

            result.append(result_dict)

        resp = response_maker(result, status=HTTPStatus.PARTIAL_CONTENT)
        resp.headers["Content-Range"] = f"propose-downstreams {first + 1}-{last}/*"
        return resp


@ns.route("/<int:id>")
@ns.param("id", "Packit id of the propose downstream run target")
class ProposeResult(Resource):
    @ns.response(
        HTTPStatus.OK.value, "OK, propose downstream target details will follow"
    )
    @ns.response(
        HTTPStatus.NOT_FOUND.value,
        "No info about propose downstream target stored in DB",
    )
    def get(self, id):
        """A specific propose-downstream job details"""
        dowstream_pr = SyncReleaseTargetModel.get_by_id(id_=int(id))

        if not dowstream_pr:
            return response_maker(
                {"error": "No info about propose downstream target stored in DB"},
                status=HTTPStatus.NOT_FOUND,
            )

        job_result_dict = {
            "status": dowstream_pr.status,
            "branch": dowstream_pr.branch,
            "downstream_pr_url": dowstream_pr.downstream_pr_url,
            "submitted_time": optional_timestamp(dowstream_pr.submitted_time),
            "start_time": optional_timestamp(dowstream_pr.start_time),
            "finished_time": optional_timestamp(dowstream_pr.finished_time),
            "logs": dowstream_pr.logs,
        }

        job_result_dict.update(get_project_info_from_build(dowstream_pr.sync_release))
        return response_maker(job_result_dict)
