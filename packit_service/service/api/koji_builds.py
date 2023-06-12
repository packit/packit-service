# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from http import HTTPStatus
from logging import getLogger

from flask_restx import Namespace, Resource, fields

from packit_service.models import (
    KojiBuildTargetModel,
    optional_timestamp,
    KojiBuildGroupModel,
)
from packit_service.service.api.parsers import indices, pagination_arguments
from packit_service.service.api.utils import get_project_info_from_build, response_maker

logger = getLogger("packit_service")

koji_builds_ns = Namespace("koji-builds", description="Production builds")

koji_build_model = koji_builds_ns.model(
    "KojiBuild",
    {
        "packit_id": fields.Integer(required=True, example="189004"),
        "build_id": fields.String(required=True, example="98402378"),
        "status": fields.String(required=True, example="success"),
        "build_submitted_time": fields.Integer(required=True, example="1678228395"),
        "chroot": fields.String(required=True, example="epel8"),
        "web_url": fields.String(
            required=True,
            example="https://koji.fedoraproject.org/koji/taskinfo?taskID=98402378",
        ),
        "build_logs_url": fields.String(
            required=True,
            example="https://kojipkgs.fedoraproject.org//work/tasks/2378/98402378/build.log",
        ),
        "pr_id": fields.Integer(required=True, example="2953"),
        "branch_name": fields.String(required=True, example="null"),
        "release": fields.String(required=True, example="null"),
        "project_url": fields.String(
            required=True, example="https://github.com/rear/rear"
        ),
        "repo_namespace": fields.String(required=True, example="rear"),
        "repo_name": fields.String(required=True, example="rear"),
    },
)


@koji_builds_ns.route("")
class KojiBuildsList(Resource):
    @koji_builds_ns.marshal_list_with(koji_build_model)
    @koji_builds_ns.expect(pagination_arguments)
    @koji_builds_ns.response(HTTPStatus.PARTIAL_CONTENT, "Koji builds list follows")
    def get(self):
        """List all Koji builds."""

        first, last = indices()
        result = []

        for build in KojiBuildTargetModel.get_range(first, last):
            build_dict = {
                "packit_id": build.id,
                "build_id": build.build_id,
                "status": build.status,
                "build_submitted_time": optional_timestamp(build.build_submitted_time),
                "chroot": build.target,
                "web_url": build.web_url,
                # from old data, sometimes build_logs_url is same and sometimes different to web_url
                "build_logs_url": build.build_logs_url,
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
            headers={"Content-Range": f"koji-builds {first + 1}-{last}/*"},
        )
        return resp


koji_build_run_model = koji_builds_ns.model(
    "KojiBuildRun",
    {
        "build_id": fields.String(required=True, example="45270685"),
        "status": fields.String(required=True, example="pending"),
        "chroot": fields.String(required=True, example="f32"),
        "build_start_time": fields.Integer(required=True, example="null"),
        "build_finished_time": fields.Integer(required=True, example="null"),
        "build_submitted_time": fields.Integer(required=True, example="1678228395"),
        "commit_sha": fields.String(
            required=True, example="00f81fe880f945143293123c217f7d566439b3d9"
        ),
        "web_url": fields.String(
            required=True,
            example="https://koji.fedoraproject.org/koji/taskinfo?taskID=45270685",
        ),
        "build_logs_url": fields.String(
            required=True,
            example="null",
        ),
        "srpm_build_id": fields.Integer(required=True, example="3414"),
        "run_ids": fields.List(
            fields.Integer(example="60390"),
            required=True,
        ),
        "repo_namespace": fields.String(required=True, example="packit-service"),
        "repo_name": fields.String(required=True, example="hello-world"),
        "git_repo": fields.String(
            required=True, example="https://github.com/packit-service/hello-world"
        ),
        "pr_id": fields.Integer(required=True, example="114"),
        "issue_id": fields.Integer(required=True, example="null"),
        "branch_name": fields.String(required=True, example="null"),
        "release": fields.String(required=True, example="null"),
    },
)


@koji_builds_ns.route("/<int:id>")
@koji_builds_ns.param("id", "Packit id of the build")
class KojiBuildItem(Resource):
    @koji_builds_ns.marshal_with(koji_build_run_model)
    @koji_builds_ns.response(HTTPStatus.OK, "OK, koji build details follow")
    @koji_builds_ns.response(
        HTTPStatus.NOT_FOUND.value, "No info about build stored in DB"
    )
    def get(self, id):
        """A specific koji build details."""
        build = KojiBuildTargetModel.get_by_id(int(id))

        if not build:
            return response_maker(
                {"error": "No info about build stored in DB"},
                status=HTTPStatus.NOT_FOUND,
            )

        build_dict = {
            "build_id": build.build_id,
            "status": build.status,
            "chroot": build.target,
            "build_start_time": optional_timestamp(build.build_start_time),
            "build_finished_time": optional_timestamp(build.build_finished_time),
            "build_submitted_time": optional_timestamp(build.build_submitted_time),
            "commit_sha": build.commit_sha,
            "web_url": build.web_url,
            # from old data, sometimes build_logs_url is same and sometimes different to web_url
            "build_logs_url": build.build_logs_url,
            "srpm_build_id": build.get_srpm_build().id,
            "run_ids": sorted(run.id for run in build.group_of_targets.runs),
        }

        build_dict.update(get_project_info_from_build(build))
        return response_maker(build_dict)


koji_build_run_group_model = koji_builds_ns.model(
    "KojiBuildGroupRun",
    {
        "submitted_time": fields.Integer(required=True, example="1676343581"),
        "run_ids": fields.List(
            fields.Integer(example="281347"),
            required=True,
        ),
        "build_target_ids": fields.List(
            fields.Integer(example="494"),
            required=True,
        ),
        "repo_namespace": fields.String(required=True, example="packit"),
        "repo_name": fields.String(required=True, example="ogr"),
        "git_repo": fields.String(
            required=True, example="https://github.com/packit/ogr"
        ),
        "pr_id": fields.Integer(required=True, example="700"),
        "issue_id": fields.Integer(required=True, example="null"),
        "branch_name": fields.String(required=True, example="null"),
        "release": fields.String(required=True, example="null"),
    },
)


@koji_builds_ns.route("/groups/<int:id>")
@koji_builds_ns.param("id", "Packit id of the koji build group")
class KojiBuildGroup(Resource):
    @koji_builds_ns.marshal_with(koji_build_run_group_model)
    @koji_builds_ns.response(HTTPStatus.OK, "OK, koji build group details follow")
    @koji_builds_ns.response(
        HTTPStatus.NOT_FOUND.value, "No info about koji build group stored in DB"
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
            "build_target_ids": sorted(
                build.id for build in group_model.grouped_targets
            ),
        }

        group_dict.update(get_project_info_from_build(group_model))
        return response_maker(group_dict)
