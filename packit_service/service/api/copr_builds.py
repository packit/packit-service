# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from http import HTTPStatus
from logging import getLogger

from flask_restx import Namespace, Resource

from packit_service.models import (
    BuildStatus,
    CoprBuildGroupModel,
    CoprBuildTargetModel,
    optional_timestamp,
)
from packit_service.service.api.parsers import indices, pagination_arguments
from packit_service.service.api.utils import get_project_info_from_build, response_maker

logger = getLogger("packit_service")

ns = Namespace("copr-builds", description="COPR builds")


@ns.route("")
class CoprBuildsList(Resource):
    @ns.expect(pagination_arguments)
    @ns.response(HTTPStatus.PARTIAL_CONTENT.value, "Copr builds list follows")
    def get(self):
        """List all Copr builds."""

        # Return relevant info that's concise
        # Use-cases like the packit-dashboard copr-builds table

        result = []

        first, last = indices()
        for build in CoprBuildTargetModel.get_merged_chroots(first, last):
            build_info = CoprBuildTargetModel.get_by_build_id(build.build_id, None)
            if build_info.status == BuildStatus.waiting_for_srpm:
                continue
            if (
                build_info.status == BuildStatus.failure
                and not build_info.build_start_time
                and not build_info.build_logs_url
            ):
                # SRPM build failed, it doesn't make sense to list this build
                continue
            project_info = build_info.get_project()
            build_dict = {
                "packit_id": build_info.id,
                "project": build_info.project_name,
                "build_id": build.build_id,
                "status_per_chroot": {},
                "packit_id_per_chroot": {},
                "build_submitted_time": optional_timestamp(
                    build_info.build_submitted_time,
                ),
                "web_url": build_info.web_url,
                "ref": build_info.commit_sha,
                "pr_id": build_info.get_pr_id(),
                "branch_name": build_info.get_branch_name(),
                "repo_namespace": project_info.namespace if project_info else "",
                "repo_name": project_info.repo_name if project_info else "",
                "project_url": project_info.project_url if project_info else "",
            }

            for i, chroot in enumerate(build.target):
                # [0] because sqlalchemy returns a single element sub-list
                build_dict["status_per_chroot"][chroot[0]] = build.status[i][0]
                build_dict["packit_id_per_chroot"][chroot[0]] = build.packit_id_per_chroot[i][0]

            result.append(build_dict)

        resp = response_maker(
            result,
            status=HTTPStatus.PARTIAL_CONTENT,
        )
        resp.headers["Content-Range"] = f"copr-builds {first + 1}-{last}/*"
        return resp


@ns.route("/<int:id>")
@ns.param("id", "Packit id of the build")
class CoprBuildItem(Resource):
    @ns.response(HTTPStatus.OK.value, "OK, copr build details follow")
    @ns.response(HTTPStatus.NOT_FOUND.value, "Copr build identifier not in db/hash")
    def get(self, id):
        """A specific copr build details for one chroot."""
        build = CoprBuildTargetModel.get_by_id(int(id))
        if not build:
            return response_maker(
                {"error": "No info about build stored in DB"},
                status=HTTPStatus.NOT_FOUND,
            )

        build_dict = {
            "build_id": build.build_id,
            "status": build.status,
            "chroot": build.target,
            "build_submitted_time": optional_timestamp(build.build_submitted_time),
            "build_start_time": optional_timestamp(build.build_start_time),
            "build_finished_time": optional_timestamp(build.build_finished_time),
            "commit_sha": build.commit_sha,
            "web_url": build.web_url,
            "build_logs_url": build.build_logs_url,
            "copr_project": build.project_name,
            "copr_owner": build.owner,
            "srpm_build_id": build.get_srpm_build().id,
            "run_ids": sorted(run.id for run in build.group_of_targets.runs),
            "built_packages": build.built_packages,
        }

        build_dict.update(get_project_info_from_build(build))
        return response_maker(build_dict)


@ns.route("/groups/<int:id>")
@ns.param("id", "Packit id of the copr build group")
class CoprBuildGroup(Resource):
    @ns.response(HTTPStatus.OK, "OK, copr build group details follow")
    @ns.response(
        HTTPStatus.NOT_FOUND.value,
        "No info about koji build group stored in DB",
    )
    def get(self, id):
        """A specific test run details."""
        group_model = CoprBuildGroupModel.get_by_id(int(id))

        if not group_model:
            return response_maker(
                {"error": "No info about group stored in DB"},
                status=HTTPStatus.NOT_FOUND,
            )

        group_dict = {
            "submitted_time": optional_timestamp(group_model.submitted_time),
            "run_ids": sorted(run.id for run in group_model.runs),
            "build_target_ids": sorted(build.id for build in group_model.grouped_targets),
        }

        group_dict.update(get_project_info_from_build(group_model))
        return response_maker(group_dict)
