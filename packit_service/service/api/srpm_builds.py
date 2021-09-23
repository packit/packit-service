# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from http import HTTPStatus
from logging import getLogger

from packit_service.service.urls import get_srpm_build_info_url
from flask_restx import Namespace, Resource

from packit_service.models import SRPMBuildModel, optional_timestamp
from packit_service.service.api.parsers import indices, pagination_arguments
from packit_service.service.api.utils import get_project_info_from_build, response_maker

logger = getLogger("packit_service")

ns = Namespace("srpm-builds", description="SRPM builds")


@ns.route("")
class SRPMBuildsList(Resource):
    @ns.expect(pagination_arguments)
    @ns.response(HTTPStatus.PARTIAL_CONTENT.value, "SRPM builds list follows")
    def get(self):
        """List all SRPM builds."""

        result = []

        first, last = indices()
        for build in SRPMBuildModel.get(first, last):
            build_dict = {
                "srpm_build_id": build.id,
                "success": build.success,
                "log_url": get_srpm_build_info_url(build.id),
                "build_submitted_time": optional_timestamp(build.build_submitted_time),
            }
            project = build.get_project()

            # Its possible that jobtrigger isnt stored in db
            if project:
                build_dict["repo_namespace"] = project.namespace
                build_dict["repo_name"] = project.repo_name
                build_dict["project_url"] = project.project_url
                build_dict["pr_id"] = build.get_pr_id()
                build_dict["branch_name"] = build.get_branch_name()

            result.append(build_dict)

        resp = response_maker(
            result,
            status=HTTPStatus.PARTIAL_CONTENT.value,
        )
        resp.headers["Content-Range"] = f"srpm-builds {first + 1}-{last}/*"
        return resp


@ns.route("/<int:id>")
@ns.param("id", "Packit id of the SRPM build")
class SRPMBuildItem(Resource):
    @ns.response(HTTPStatus.OK.value, "OK, SRPM build details follow")
    @ns.response(HTTPStatus.NOT_FOUND.value, "SRPM build identifier not in db/hash")
    def get(self, id):
        """A specific SRPM build details."""
        build = SRPMBuildModel.get_by_id(int(id))
        if not build:
            return response_maker(
                {"error": "No info about build stored in DB"},
                status=HTTPStatus.NOT_FOUND.value,
            )

        build_dict = {
            "success": build.success,
            "build_submitted_time": optional_timestamp(build.build_submitted_time),
            "url": build.url,
            "logs": build.logs,
            "run_ids": sorted(run.id for run in build.runs),
        }

        build_dict.update(get_project_info_from_build(build))
        return response_maker(build_dict)
