# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from http import HTTPStatus
from logging import getLogger

from flask import request
from flask_restx import Namespace, Resource

from packit_service.models import (
    KojiBuildGroupModel,
    KojiBuildTargetModel,
    optional_timestamp,
)
from packit_service.service.api.parsers import indices, pagination_arguments
from packit_service.service.api.utils import get_project_info_from_build, response_maker

logger = getLogger("packit_service")

koji_builds_ns = Namespace("koji-builds", description="Production builds")


@koji_builds_ns.route("")
class KojiBuildsList(Resource):
    @koji_builds_ns.expect(pagination_arguments)
    @koji_builds_ns.response(HTTPStatus.PARTIAL_CONTENT, "Koji builds list follows")
    def get(self):
        """List all Koji builds."""
        scratch = (
            request.args.get("scratch").lower() == "true" if "scratch" in request.args else None
        )
        first, last = indices()
        result = []

        for build in KojiBuildTargetModel.get_range(first, last, scratch):
            build_dict = {
                "packit_id": build.id,
                "task_id": build.task_id,
                "scratch": build.scratch,
                "status": build.status,
                "build_submitted_time": optional_timestamp(build.build_submitted_time),
                "chroot": build.target,
                "web_url": build.web_url,
                # from old data, sometimes build_logs_url is same and sometimes different to web_url
                "build_logs_urls": build.build_logs_urls,
                "pr_id": build.get_pr_id(),
                "branch_name": build.get_branch_name(),
                "release": build.get_release_tag(),
            }

            if project := build.get_project():
                build_dict["project_url"] = project.project_url
                build_dict["repo_namespace"] = project.namespace
                build_dict["repo_name"] = project.repo_name

            result.append(build_dict)

        resp = response_maker(
            result,
            status=HTTPStatus.PARTIAL_CONTENT,
        )
        resp.headers["Content-Range"] = f"koji-builds {first + 1}-{last}/*"
        return resp


@koji_builds_ns.route("/<int:id>")
@koji_builds_ns.param("id", "Packit id of the build")
class KojiBuildItem(Resource):
    @koji_builds_ns.response(HTTPStatus.OK, "OK, koji build details follow")
    @koji_builds_ns.response(
        HTTPStatus.NOT_FOUND.value,
        "No info about build stored in DB",
    )
    def get(self, id):
        """A specific koji build details."""
        build = KojiBuildTargetModel.get_by_id(int(id))

        if not build:
            return response_maker(
                {"error": "No info about build stored in DB"},
                status=HTTPStatus.NOT_FOUND,
            )

        srpm_build = build.get_srpm_build()

        build_dict = {
            "task_id": build.task_id,
            "status": build.status,
            "chroot": build.target,
            "scratch": build.scratch,
            "build_start_time": optional_timestamp(build.build_start_time),
            "build_finished_time": optional_timestamp(build.build_finished_time),
            "build_submitted_time": optional_timestamp(build.build_submitted_time),
            "commit_sha": build.commit_sha,
            "web_url": build.web_url,
            # from old data, sometimes build_logs_url is same and sometimes different to web_url
            "build_logs_urls": build.build_logs_urls,
            "srpm_build_id": srpm_build.id if srpm_build else None,
            "run_ids": sorted(run.id for run in build.group_of_targets.runs),
            "build_submission_stdout": build.build_submission_stdout,
            "error_message": build.data.get("error") if build.data else None,
        }

        build_dict.update(get_project_info_from_build(build))
        return response_maker(build_dict)


@koji_builds_ns.route("/groups/<int:id>")
@koji_builds_ns.param("id", "Packit id of the koji build group")
class KojiBuildGroup(Resource):
    @koji_builds_ns.response(HTTPStatus.OK, "OK, koji build group details follow")
    @koji_builds_ns.response(
        HTTPStatus.NOT_FOUND.value,
        "No info about koji build group stored in DB",
    )
    def get(self, id):
        """A specific test run details."""
        group_model = KojiBuildGroupModel.get_by_id(int(id))

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
