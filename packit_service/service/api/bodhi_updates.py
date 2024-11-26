# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from http import HTTPStatus
from logging import getLogger

from flask_restx import Namespace, Resource

from packit_service.models import (
    BodhiUpdateGroupModel,
    BodhiUpdateTargetModel,
    optional_timestamp,
)
from packit_service.service.api.parsers import indices, pagination_arguments
from packit_service.service.api.utils import get_project_info_from_build, response_maker

logger = getLogger("packit_service")

ns = Namespace("bodhi-updates", description="Bodhi updates")


@ns.route("")
class BodhiUpdatesList(Resource):
    @ns.expect(pagination_arguments)
    @ns.response(HTTPStatus.PARTIAL_CONTENT, "Bodhi updates list follows")
    def get(self):
        """List all Bodhi updates."""
        first, last = indices()
        result = []

        for update in BodhiUpdateTargetModel.get_range(first, last):
            update_dict = {
                "packit_id": update.id,
                "status": update.status,
                "branch": update.target,
                "web_url": update.web_url,
                "koji_nvrs": update.koji_nvrs,
                "alias": update.alias,
                "pr_id": update.get_pr_id(),
                "branch_name": update.get_branch_name(),
                "release": update.get_release_tag(),
                "submitted_time": optional_timestamp(update.submitted_time),
                "update_creation_time": optional_timestamp(update.update_creation_time),
            }

            if project := update.get_project():
                update_dict["project_url"] = project.project_url
                update_dict["repo_namespace"] = project.namespace
                update_dict["repo_name"] = project.repo_name

            result.append(update_dict)

        resp = response_maker(
            result,
            status=HTTPStatus.PARTIAL_CONTENT,
        )
        resp.headers["Content-Range"] = f"bodhi-updates {first + 1}-{last}/*"
        return resp


@ns.route("/<int:id>")
@ns.param("id", "Packit id of the update")
class BodhiUpdateItem(Resource):
    @ns.response(HTTPStatus.OK, "OK, Bodhi update details follow")
    @ns.response(HTTPStatus.NOT_FOUND.value, "No info about Bodhi update stored in DB")
    def get(self, id):
        """A specific Bodhi updates details."""
        update = BodhiUpdateTargetModel.get_by_id(int(id))

        if not update:
            return response_maker(
                {"error": "No info about update stored in DB"},
                status=HTTPStatus.NOT_FOUND,
            )

        update_dict = {
            "status": update.status,
            "branch": update.target,
            "web_url": update.web_url,
            "koji_nvrs": update.koji_nvrs,
            "alias": update.alias,
            "submitted_time": optional_timestamp(update.submitted_time),
            "update_creation_time": optional_timestamp(update.update_creation_time),
            "run_ids": sorted(run.id for run in update.group_of_targets.runs),
            "error_message": update.data.get("error") if update.data else None,
        }

        update_dict.update(get_project_info_from_build(update))
        return response_maker(update_dict)


@ns.route("/groups/<int:id>")
@ns.param("id", "Packit id of the Bodhi update group")
class BodhiUpdateGroup(Resource):
    @ns.response(HTTPStatus.OK, "OK, Bodhi update group details follow")
    @ns.response(
        HTTPStatus.NOT_FOUND.value,
        "No info about Bodhi update group stored in DB",
    )
    def get(self, id):
        """A specific Bodhi update group details."""
        group_model = BodhiUpdateGroupModel.get_by_id(int(id))

        if not group_model:
            return response_maker(
                {"error": "No info about group stored in DB"},
                status=HTTPStatus.NOT_FOUND,
            )

        group_dict = {
            "submitted_time": optional_timestamp(group_model.submitted_time),
            "run_ids": sorted(run.id for run in group_model.runs),
            "update_target_ids": sorted(build.id for build in group_model.grouped_targets),
        }

        group_dict.update(get_project_info_from_build(group_model))
        return response_maker(group_dict)
