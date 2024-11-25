# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from http import HTTPStatus
from logging import getLogger

from flask_restx import Namespace, Resource

from packit_service.models import (
    KojiTagRequestGroupModel,
    KojiTagRequestTargetModel,
    optional_timestamp,
)
from packit_service.service.api.parsers import indices, pagination_arguments
from packit_service.service.api.utils import get_project_info_from_build, response_maker

logger = getLogger("packit_service")

koji_tag_requests_ns = Namespace("koji-tag-requests", description="Koji tagging requests")


@koji_tag_requests_ns.route("")
class KojiTagRequestsList(Resource):
    @koji_tag_requests_ns.expect(pagination_arguments)
    @koji_tag_requests_ns.response(HTTPStatus.PARTIAL_CONTENT, "Koji tagging requests list follows")
    def get(self):
        """List all Koji tagging requests."""
        first, last = indices()
        result = []

        for tag_request in KojiTagRequestTargetModel.get_range(first, last):
            tag_request_dict = {
                "packit_id": tag_request.id,
                "task_id": tag_request.task_id,
                "tag_request_submitted_time": optional_timestamp(
                    tag_request.tag_request_submitted_time
                ),
                "chroot": tag_request.target,
                "sidetag": tag_request.sidetag,
                "nvr": tag_request.nvr,
                "web_url": tag_request.web_url,
                "pr_id": tag_request.get_pr_id(),
                "branch_name": tag_request.get_branch_name(),
                "release": tag_request.get_release_tag(),
            }

            if project := tag_request.get_project():
                tag_request_dict["project_url"] = project.project_url
                tag_request_dict["repo_namespace"] = project.namespace
                tag_request_dict["repo_name"] = project.repo_name

            result.append(tag_request_dict)

        resp = response_maker(
            result,
            status=HTTPStatus.PARTIAL_CONTENT,
        )
        resp.headers["Content-Range"] = f"koji-tag-requests {first + 1}-{last}/*"
        return resp


@koji_tag_requests_ns.route("/<int:id>")
@koji_tag_requests_ns.param("id", "Packit id of the tagging request")
class KojiTagRequestItem(Resource):
    @koji_tag_requests_ns.response(HTTPStatus.OK, "OK, koji tagging request details follow")
    @koji_tag_requests_ns.response(
        HTTPStatus.NOT_FOUND.value,
        "No info about tagging request stored in DB",
    )
    def get(self, id):
        """A specific koji tagging request details."""
        tag_request = KojiTagRequestTargetModel.get_by_id(int(id))

        if not tag_request:
            return response_maker(
                {"error": "No info about tagging request stored in DB"},
                status=HTTPStatus.NOT_FOUND,
            )

        tag_request_dict = {
            "task_id": tag_request.task_id,
            "chroot": tag_request.target,
            "sidetag": tag_request.sidetag,
            "nvr": tag_request.nvr,
            "tag_request_submitted_time": optional_timestamp(
                tag_request.tag_request_submitted_time
            ),
            "commit_sha": tag_request.commit_sha,
            "web_url": tag_request.web_url,
            "run_ids": sorted(run.id for run in tag_request.group_of_targets.runs),
        }

        tag_request_dict.update(get_project_info_from_build(tag_request))
        return response_maker(tag_request_dict)


@koji_tag_requests_ns.route("/groups/<int:id>")
@koji_tag_requests_ns.param("id", "Packit id of the koji tagging request group")
class KojiTagRequestGroup(Resource):
    @koji_tag_requests_ns.response(HTTPStatus.OK, "OK, koji tagging request group details follow")
    @koji_tag_requests_ns.response(
        HTTPStatus.NOT_FOUND.value,
        "No info about koji tagging request group stored in DB",
    )
    def get(self, id):
        """A specific test run details."""
        group_model = KojiTagRequestGroupModel.get_by_id(int(id))

        if not group_model:
            return response_maker(
                {"error": "No info about group stored in DB"},
                status=HTTPStatus.NOT_FOUND,
            )

        group_dict = {
            "submitted_time": optional_timestamp(group_model.submitted_time),
            "run_ids": sorted(run.id for run in group_model.runs),
            "tag_request_target_ids": sorted(
                tag_request.id for tag_request in group_model.grouped_targets
            ),
        }

        group_dict.update(get_project_info_from_build(group_model))
        return response_maker(group_dict)
