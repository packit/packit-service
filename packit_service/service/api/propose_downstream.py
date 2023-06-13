# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from http import HTTPStatus
from logging import getLogger

from flask_restx import Namespace, Resource, fields

from packit_service.models import (
    SyncReleaseTargetModel,
    SyncReleaseModel,
    SyncReleaseJobType,
)
from packit_service.service.api.parsers import indices, pagination_arguments
from packit_service.service.api.utils import (
    response_maker,
    get_sync_release_info,
    get_sync_release_target_info,
)

logger = getLogger("packit_service")

ns = Namespace("propose-downstream", description="Propose Downstream")

status_per_downstream = ns.model(
    "StatusPerDownstream", {"*": fields.Wildcard(fields.String, example="submitted")}
)
packit_id_per_downstream = ns.model(
    "PackitIdPerChroot", {"*": fields.Wildcard(fields.Integer, example="1893")}
)

propose_downstream_model = ns.model(
    "ProposeDownstream",
    {
        "packit_id": fields.Integer(required=True, example="562"),
        "status": fields.String(required=True, example="finished"),
        "submitted_time": fields.Integer(required=True, example="1678165588"),
        "status_per_downstream_pr": fields.Nested(status_per_downstream, required=True),
        "packit_id_per_downstream_pr": fields.Nested(
            packit_id_per_downstream, required=True
        ),
        "pr_id": fields.Integer(required=True, example="null"),
        "issue_id": fields.Integer(required=True, example="null"),
        "release": fields.String(required=True, example="anaconda-39.4-1"),
        "repo_namespace": fields.String(required=True, example="rhinstaller"),
        "repo_name": fields.String(required=True, example="anaconda"),
        "project_url": fields.String(
            required=True, example="https://github.com/rhinstaller/anaconda"
        ),
        "branch_name": fields.String(required=True, example="null"),
    },
)


@ns.route("")
class ProposeDownstreamList(Resource):
    @ns.marshal_list_with(propose_downstream_model)
    @ns.expect(pagination_arguments)
    @ns.response(HTTPStatus.PARTIAL_CONTENT.value, "Propose Downstreams results follow")
    def get(self):
        """List of all Propose Downstreams results."""

        result = []
        first, last = indices()
        for propose_downstream_results in SyncReleaseModel.get_range(
            first, last, job_type=SyncReleaseJobType.propose_downstream
        ):
            result.append(get_sync_release_info(propose_downstream_results))

        resp = response_maker(
            result,
            status=HTTPStatus.PARTIAL_CONTENT,
            headers={"Content-Range": f"propose-downstreams {first + 1}-{last}/*"},
        )
        return resp


propose_downstream_run_model = ns.model(
    "ProposeDownstreamRun",
    {
        "status": fields.String(required=True, example="submitted"),
        "branch": fields.String(required=True, example="f36"),
        "downstream_pr_url": fields.String(
            required=True,
            example="https://src.fedoraproject.org/rpms/osbuild-composer/pull-request/18",
        ),
        "submitted_time": fields.Integer(required=True, example="1652953153"),
        "start_time": fields.Integer(required=True, example="1652953252"),
        "finished_time": fields.Integer(required=True, example="1652953275"),
        "logs": fields.String(
            required=True,
            example=(
                "2022-05-19 09:40:52.854 upstream.py       "
                "DEBUG  No ref given or is not glob pattern"
                "\n2022-05-19 09:40:52.854 local_project.py  "
                "INFO   Checking out upstream version v52."
                "\n2022-05-19 09:40:53.149 base_git.py       "
                "DEBUG  Running ActionName.post_upstream_clone hook."
                "\n2022-05-19 09:40:53.149 base_git.py       "
                "DEBUG  Running ActionName.post_upstream_clone."
            ),
        ),
        "repo_namespace": fields.String(required=True, example="osbuild"),
        "repo_name": fields.String(required=True, example="osbuild-composer"),
        "project_url": fields.String(
            required=True, example="https://github.com/osbuild/osbuild-composer"
        ),
        "pr_id": fields.Integer(required=True, example="null"),
        "issue_id": fields.Integer(required=True, example="null"),
        "branch_name": fields.String(required=True, example="null"),
        "release": fields.String(required=True, example="v52"),
    },
)


@ns.route("/<int:id>")
@ns.param("id", "Packit id of the propose downstream run target")
class ProposeResult(Resource):
    @ns.marshal_with(propose_downstream_run_model)
    @ns.response(
        HTTPStatus.OK.value, "OK, propose downstream target details will follow"
    )
    @ns.response(
        HTTPStatus.NOT_FOUND.value,
        "No info about propose downstream target stored in DB",
    )
    def get(self, id):
        """A specific propose-downstream job details"""
        sync_release_target_model = SyncReleaseTargetModel.get_by_id(id_=int(id))

        if (
            not sync_release_target_model
            or sync_release_target_model.sync_release.job_type
            != SyncReleaseJobType.propose_downstream
        ):
            return response_maker(
                {"error": "No info about propose downstream target stored in DB"},
                status=HTTPStatus.NOT_FOUND,
            )

        return response_maker(get_sync_release_target_info(sync_release_target_model))
