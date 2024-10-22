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

ns = Namespace("propose-downstream", description="Propose Downstream")


@ns.route("")
class ProposeDownstreamList(Resource):
    @ns.expect(pagination_arguments)
    @ns.response(HTTPStatus.PARTIAL_CONTENT.value, "Propose Downstreams results follow")
    def get(self):
        """List of all Propose Downstreams results."""

        first, last = indices()
        result = [
            get_sync_release_info(propose_downstream_results)
            for propose_downstream_results in SyncReleaseModel.get_range(
                first,
                last,
                job_type=SyncReleaseJobType.propose_downstream,
            )
        ]

        resp = response_maker(result, status=HTTPStatus.PARTIAL_CONTENT)
        resp.headers["Content-Range"] = f"propose-downstreams {first + 1}-{last}/*"
        return resp


@ns.route("/<int:id>")
@ns.param("id", "Packit id of the propose downstream run target")
class ProposeResult(Resource):
    @ns.response(
        HTTPStatus.OK.value,
        "OK, propose downstream target details will follow",
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
