# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from http import HTTPStatus
from logging import getLogger

from packit_service.service.urls import get_srpm_build_info_url
from flask_restx import Namespace, Resource, fields

from packit_service.models import SRPMBuildModel, optional_timestamp
from packit_service.service.api.parsers import indices, pagination_arguments
from packit_service.service.api.utils import get_project_info_from_build, response_maker

logger = getLogger("packit_service")

ns = Namespace("srpm-builds", description="SRPM builds")

srpm_build_model = ns.model(
    "SRPMBuild",
    {
        "srpm_build_id": fields.Integer(required=True, example="118888"),
        "status": fields.String(required=True, example="success"),
        "log_url": fields.String(
            required=True,
            example="https://dashboard.localhost/results/srpm-builds/118888",
        ),
        "build_submitted_time": fields.Integer(required=True, example="1678229975"),
        "repo_namespace": fields.String(required=True, example="rear"),
        "repo_name": fields.String(required=True, example="rear"),
        "project_url": fields.String(
            required=True, example="https://github.com/rear/rear"
        ),
        "pr_id": fields.Integer(required=True, example="61"),
        "branch_name": fields.String(required=True, example="null"),
    },
)


@ns.route("")
class SRPMBuildsList(Resource):
    @ns.marshal_list_with(srpm_build_model)
    @ns.expect(pagination_arguments)
    @ns.response(HTTPStatus.PARTIAL_CONTENT.value, "SRPM builds list follows")
    def get(self):
        """List all SRPM builds."""

        result = []

        first, last = indices()
        for build in SRPMBuildModel.get_range(first, last):
            build_dict = {
                "srpm_build_id": build.id,
                "status": build.status,
                "log_url": get_srpm_build_info_url(build.id),
                "build_submitted_time": optional_timestamp(build.build_submitted_time),
            }

            # It's possible that jobtrigger isn't stored in db
            if project := build.get_project():
                build_dict["repo_namespace"] = project.namespace
                build_dict["repo_name"] = project.repo_name
                build_dict["project_url"] = project.project_url
                build_dict["pr_id"] = build.get_pr_id()
                build_dict["branch_name"] = build.get_branch_name()

            result.append(build_dict)

        resp = response_maker(
            result,
            status=HTTPStatus.PARTIAL_CONTENT,
            headers={"Content-Range": f"srpm-builds {first + 1}-{last}/*"},
        )
        return resp


srpm_build_run_model = ns.model(
    "SRPMBuildRun",
    {
        "status": fields.String(required=True, example="success"),
        "build_submitted_time": fields.Integer(required=True, example="1678222061"),
        "build_start_time": fields.Integer(required=True, example="1678222090"),
        "build_finished_time": fields.Integer(required=True, example="1678222238"),
        "url": fields.String(
            required=True,
            example=(
                "https://download.copr.fedorainfracloud.org/results/packit/"
                "OpenSCAP-openscap-1954/srpm-builds/05604030/"
                "openscap-1.3.8-0.20230307205027846228.pr1954.33.gf5ac85d5c.src.rpm"
            ),
        ),
        "logs": fields.String(required=True, example="null"),
        "logs_url": fields.String(
            required=True,
            example=(
                "https://download.copr.fedorainfracloud.org/results/"
                "packit/OpenSCAP-openscap-1954/srpm-builds/05604030/"
                "builder-live.log"
            ),
        ),
        "copr_build_id": fields.String(required=True, example="5604030"),
        "copr_web_url": fields.String(
            required=True,
            example="https://copr.fedorainfracloud.org/coprs/build/5604030",
        ),
        "run_ids": fields.List(
            fields.Integer(example="631038"),
            required=True,
        ),
        "repo_namespace": fields.String(required=True, example="OpenSCAP"),
        "repo_name": fields.String(required=True, example="openscap"),
        "git_repo": fields.String(
            required=True, example="https://github.com/OpenSCAP/openscap"
        ),
        "pr_id": fields.Integer(required=True, example="1954"),
        "issue_id": fields.Integer(required=True, example="null"),
        "branch_name": fields.String(required=True, example="null"),
        "release": fields.String(required=True, example="null"),
    },
)


@ns.route("/<int:id>")
@ns.param("id", "Packit id of the SRPM build")
class SRPMBuildItem(Resource):
    @ns.marshal_with(srpm_build_run_model)
    @ns.response(HTTPStatus.OK.value, "OK, SRPM build details follow")
    @ns.response(HTTPStatus.NOT_FOUND.value, "SRPM build identifier not in db/hash")
    def get(self, id):
        """A specific SRPM build details."""
        build = SRPMBuildModel.get_by_id(int(id))
        if not build:
            return response_maker(
                {"error": "No info about build stored in DB"},
                status=HTTPStatus.NOT_FOUND,
            )

        build_dict = {
            "status": build.status,
            "build_submitted_time": optional_timestamp(build.build_submitted_time),
            "build_start_time": optional_timestamp(build.build_start_time),
            "build_finished_time": optional_timestamp(build.build_finished_time),
            "url": build.url,
            "logs": build.logs,
            "logs_url": build.logs_url,
            "copr_build_id": build.copr_build_id,
            "copr_web_url": build.copr_web_url,
            "run_ids": sorted(run.id for run in build.runs),
        }

        build_dict.update(get_project_info_from_build(build))
        return response_maker(build_dict)
