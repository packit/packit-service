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
    get_sync_release_target_info,
    get_sync_release_info,
)

logger = getLogger("packit_service")

ns = Namespace("pull-from-upstream", description="Pull from upstream")

status_per_downstream = ns.model(
    "StatusPerDownstream", {"*": fields.Wildcard(fields.String, example="submitted")}
)
packit_id_per_downstream = ns.model(
    "PackitIdPerChroot", {"*": fields.Wildcard(fields.Integer, example="1893")}
)

pull_from_upstream_model = ns.model(
    "PullFromUpstream",
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
    },
)


@ns.route("")
class PullFromUpstreamList(Resource):
    @ns.marshal_list_with(pull_from_upstream_model)
    @ns.expect(pagination_arguments)
    @ns.response(HTTPStatus.PARTIAL_CONTENT.value, "Pull from upstream results follow")
    def get(self):
        """List of all Pull from upstream results."""

        result = []
        first, last = indices()
        for pull_results in SyncReleaseModel.get_range(
            first, last, job_type=SyncReleaseJobType.pull_from_upstream
        ):
            result.append(get_sync_release_info(pull_results))

        resp = response_maker(
            result,
            status=HTTPStatus.PARTIAL_CONTENT,
            headers={"Content-Range": f"pull-from-upstreams {first + 1}-{last}/*"},
        )
        return resp


pull_from_upstream_run_model = ns.model(
    "PullFromUpstreamRun",
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
        "git_repo": fields.String(
            required=True, example="https://github.com/tumic0/GPXSee"
        ),
        "pr_id": fields.Integer(required=True, example="null"),
        "issue_id": fields.Integer(required=True, example="null"),
        "branch_name": fields.String(required=True, example="null"),
        "release": fields.String(required=True, example="12.1"),
    },
)


@ns.route("/<int:id>")
@ns.param("id", "Packit id of the pull from upstream run target")
class PullResult(Resource):
    @ns.marshal_with(pull_from_upstream_run_model)
    @ns.response(
        HTTPStatus.OK.value, "OK, pull from upstream target details will follow"
    )
    @ns.response(
        HTTPStatus.NOT_FOUND.value,
        "No info about pull from upstream target stored in DB",
    )
    def get(self, id):
        """A specific pull from upstream job details"""
        sync_release_target_model = SyncReleaseTargetModel.get_by_id(id_=int(id))
        if (
            not sync_release_target_model
            or sync_release_target_model.sync_release.job_type
            != SyncReleaseJobType.pull_from_upstream
        ):
            return response_maker(
                {"error": "No info about pull from upstream target stored in DB"},
                status=HTTPStatus.NOT_FOUND,
            )

        return response_maker(get_sync_release_target_info(sync_release_target_model))
