# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from http import HTTPStatus
from logging import getLogger

from flask_restx import Namespace, Resource, fields

from packit_service.models import (
    CoprBuildTargetModel,
    optional_timestamp,
    BuildStatus,
    CoprBuildGroupModel,
)
from packit_service.service.api.parsers import indices, pagination_arguments
from packit_service.service.api.utils import get_project_info_from_build, response_maker

logger = getLogger("packit_service")

ns = Namespace("copr-builds", description="COPR builds")

status_per_chroot = ns.model(
    "StatusPerChroot", {"*": fields.Wildcard(fields.String, example="success")}
)
packit_id_per_chroot = ns.model(
    "PackitIdPerChroot", {"*": fields.Wildcard(fields.Integer, example="649829")}
)
copr_build_model = ns.model(
    "CoprBuild",
    {
        "packit_id": fields.Integer(required=True, example="649837"),
        "project": fields.String(required=True, example="systemd-systemd-26013"),
        "build_id": fields.String(required=True, example="5604188"),
        "status_per_chroot": fields.Nested(status_per_chroot, required=True),
        "packit_id_per_chroot": fields.Nested(packit_id_per_chroot, required=True),
        "build_submitted_time": fields.Integer(required=True, example="1678224300"),
        "web_url": fields.String(
            required=True,
            example="https://copr.fedorainfracloud.org/coprs/build/5604188/",
        ),
        "ref": fields.String(
            required=True, example="4b247d9e8046f9f7a6014e148eb441b27c72c926"
        ),
        "pr_id": fields.Integer(required=True, example="26013"),
        "branch_name": fields.String(required=True, example="null"),
        "repo_namespace": fields.String(required=True, example="systemd"),
        "repo_name": fields.String(required=True, example="systemd"),
        "project_url": fields.String(
            required=True, example="https://github.com/systemd/systemd"
        ),
    },
)


@ns.route("")
class CoprBuildsList(Resource):
    @ns.marshal_list_with(copr_build_model)
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
            project_info = build_info.get_project()
            build_dict = {
                "packit_id": build_info.id,
                "project": build_info.project_name,
                "build_id": build.build_id,
                "status_per_chroot": {},
                "packit_id_per_chroot": {},
                "build_submitted_time": optional_timestamp(
                    build_info.build_submitted_time
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
                build_dict["packit_id_per_chroot"][
                    chroot[0]
                ] = build.packit_id_per_chroot[i][0]

            result.append(build_dict)

        resp = response_maker(
            result,
            status=HTTPStatus.PARTIAL_CONTENT,
            headers={"Content-Range": f"copr-builds {first + 1}-{last}/*"},
        )
        return resp


built_package_model = ns.model(
    "BuiltPackage",
    {
        "arch": fields.String(required=True, example="src"),
        "epoch": fields.Integer(required=True, example="0"),
        "name": fields.String(required=True, example="scap-security-guide"),
        "release": fields.String(
            required=True, example="0.20221201205609771856.pr9918.5351.g74cb2c9508.el8"
        ),
        "version": fields.String(required=True, example="0.1.66"),
    },
)

copr_build_item_model = ns.model(
    "CoprBuildItem",
    {
        "build_id": fields.String(required=True, example="5078183"),
        "status": fields.String(required=True, example="success"),
        "chroot": fields.String(required=True, example="centos-stream-8-x86_64"),
        "build_submitted_time": fields.Integer(required=True, example="1669928185"),
        "build_start_time": fields.Integer(required=True, example="1669928252"),
        "build_finished_time": fields.Integer(required=True, example="1669928581"),
        "commit_sha": fields.String(
            required=True, example="a333d276ee2cd662e0ff9b2e7b82ffebe52cf648"
        ),
        "web_url": fields.String(
            required=True,
            example="https://copr.fedorainfracloud.org/coprs/build/5078183/",
        ),
        "build_logs_url": fields.String(
            required=True,
            example=(
                "https://download.copr.fedorainfracloud.org/results/packit/"
                "ComplianceAsCode-content-9918/centos-stream-8-x86_64/"
                "05078183-scap-security-guide/builder-live.log"
            ),
        ),
        "copr_project": fields.String(
            required=True, example="ComplianceAsCode-content-9918"
        ),
        "copr_owner": fields.String(required=True, example="packit"),
        "srpm_build_id": fields.Integer(required=True, example="101752"),
        "run_ids": fields.List(
            fields.Integer,
            required=True,
            example="[544716, 544717, 544718, 544719, 544720, 544721]",
        ),
        "built_packages": fields.Nested(
            built_package_model, required=True, as_list=True
        ),
        "repo_namespace": fields.String(required=True, example="ComplianceAsCode"),
        "repo_name": fields.String(required=True, example="content"),
        "git_repo": fields.String(
            required=True, example="https://github.com/ComplianceAsCode/content"
        ),
        "pr_id": fields.Integer(required=True, example="9918"),
        "issue_id": fields.Integer(required=True, example="null"),
        "branch_name": fields.String(required=True, example="null"),
        "release": fields.String(required=True, example="null"),
    },
)


@ns.route("/<int:id>")
@ns.param("id", "Packit id of the build")
class CoprBuildItem(Resource):
    @ns.marshal_with(copr_build_item_model)
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


copr_build_group_model = ns.model(
    "CoprBuildGroup",
    {
        "build_submitted_time": fields.Integer(required=True, example="1676341717"),
        "run_ids": fields.List(
            fields.Integer,
            required=True,
            example="[39761, 39765]",
        ),
        "build_target_ids": fields.List(
            fields.Integer,
            required=True,
            example="[421, 422]",
        ),
        "repo_namespace": fields.String(required=True, example="nmstate"),
        "repo_name": fields.String(required=True, example="nmstate"),
        "git_repo": fields.String(
            required=True, example="https://github.com/nmstate/nmstate"
        ),
        "pr_id": fields.Integer(required=True, example="874"),
        "issue_id": fields.Integer(required=True, example="null"),
        "branch_name": fields.String(required=True, example="null"),
        "release": fields.String(required=True, example="null"),
    },
)


@ns.route("/groups/<int:id>")
@ns.param("id", "Packit id of the copr build group")
class CoprBuildGroup(Resource):
    @ns.marshal_with(copr_build_group_model)
    @ns.response(HTTPStatus.OK, "OK, copr build group details follow")
    @ns.response(
        HTTPStatus.NOT_FOUND.value, "No info about copr build group stored in DB"
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
            "build_target_ids": sorted(
                build.id for build in group_model.grouped_targets
            ),
        }

        group_dict.update(get_project_info_from_build(group_model))
        return response_maker(group_dict)
