# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from http import HTTPStatus
from logging import getLogger

from flask_restx import Namespace, Resource

from packit_service.models import (
    SyncReleaseJobType,
    SyncReleaseModel,
    SyncReleaseTargetModel,
)
from packit_service.service.api.parsers import indices, pagination_arguments
from packit_service.service.api.utils import (
    get_sync_release_info,
    get_sync_release_target_info,
    response_maker,
)

logger = getLogger("packit_service")

ns = Namespace("pull-from-upstream", description="Pull from upstream")


@ns.route("")
class PullFromUpstreamList(Resource):
    @ns.expect(pagination_arguments)
    @ns.response(HTTPStatus.PARTIAL_CONTENT.value, "Pull from upstream results follow")
    def get(self):
        """List of all Pull from upstream results."""

        first, last = indices()
        result = [
            get_sync_release_info(pull_results)
            for pull_results in SyncReleaseModel.get_range(
                first,
                last,
                job_type=SyncReleaseJobType.pull_from_upstream,
            )
        ]

        resp = response_maker(result, status=HTTPStatus.PARTIAL_CONTENT)
        resp.headers["Content-Range"] = f"pull-from-upstreams {first + 1}-{last}/*"
        return resp


@ns.route("/<int:id>")
@ns.param("id", "Packit id of the pull from upstream run target")
class PullResult(Resource):
    @ns.response(
        HTTPStatus.OK.value,
        "OK, pull from upstream target details will follow",
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
