# MIT License
#
# Copyright (c) 2019 Red Hat, Inc.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
from http import HTTPStatus
from logging import getLogger

try:
    from flask_restx import Namespace, Resource
except ModuleNotFoundError:
    from flask_restplus import Namespace, Resource

from packit_service.service.api.utils import response_maker
from packit_service.service.api.parsers import indices, pagination_arguments
from packit_service.models import CoprBuildModel, optional_time


logger = getLogger("packit_service")

ns = Namespace("copr-builds", description="COPR builds")


@ns.route("")
class CoprBuildsList(Resource):
    @ns.expect(pagination_arguments)
    @ns.response(HTTPStatus.PARTIAL_CONTENT.value, "Copr builds list follows")
    def get(self):
        """ List all Copr builds. """

        # Return relevant info thats concise
        # Usecases like the packit-dashboard copr-builds table

        result = []

        first, last = indices()
        for build in CoprBuildModel.get_merged_chroots(first, last):
            build_info = CoprBuildModel.get_by_build_id(build.build_id, None)
            project_info = build_info.get_project()
            build_dict = {
                "project": build_info.project_name,
                "build_id": build.build_id,
                "status_per_chroot": {},
                "build_submitted_time": optional_time(build_info.build_submitted_time),
                "web_url": build_info.web_url,
                "ref": build_info.commit_sha,
                "pr_id": build_info.get_pr_id(),
                "branch_name": build_info.get_branch_name(),
                "repo_namespace": project_info.namespace,
                "repo_name": project_info.repo_name,
                "project_url": project_info.project_url,
            }

            for count, chroot in enumerate(build.target):
                # [0] because sqlalchemy returns a single element sub-list
                build_dict["status_per_chroot"][chroot[0]] = build.status[count][0]

            result.append(build_dict)

        resp = response_maker(
            result,
            status=HTTPStatus.PARTIAL_CONTENT.value,
        )
        resp.headers["Content-Range"] = f"copr-builds {first + 1}-{last}/*"
        return resp


@ns.route("/<int:id>")
@ns.param("id", "Copr build identifier")
class CoprBuildItem(Resource):
    @ns.response(HTTPStatus.OK.value, "OK, copr build details follow")
    @ns.response(HTTPStatus.NOT_FOUND.value, "Copr build identifier not in db/hash")
    def get(self, id):
        """A specific copr build details. From copr_build hash, filled by worker."""
        builds_list = CoprBuildModel.get_all_by_build_id(str(id))
        if not bool(builds_list.first()):
            return response_maker(
                {"error": "No info about build stored in DB"},
                status=HTTPStatus.NOT_FOUND.value,
            )

        build = builds_list[0]

        build_dict = {
            "project": build.project_name,
            "owner": build.owner,
            "build_id": build.build_id,
            "status": build.status,  # Legacy, remove later.
            "status_per_chroot": {},
            "chroots": [],
            "build_submitted_time": optional_time(build.build_submitted_time),
            "build_start_time": optional_time(build.build_start_time),
            "build_finished_time": optional_time(build.build_finished_time),
            "commit_sha": build.commit_sha,
            "web_url": build.web_url,
            "srpm_logs": build.srpm_build.logs if build.srpm_build else None,
            # For backwards compatability with the old redis based API
            "ref": build.commit_sha,
        }

        project = build.get_project()
        if project:
            build_dict["repo_namespace"] = project.namespace
            build_dict["repo_name"] = project.repo_name
            build_dict["git_repo"] = project.project_url
            build_dict["pr_id"] = build.get_pr_id()
            build_dict["branch_name"] = build.get_branch_name()

        # merge chroots into one
        for sbid_build in builds_list:
            build_dict["chroots"].append(sbid_build.target)
            # Get status per chroot as well
            build_dict["status_per_chroot"][sbid_build.target] = sbid_build.status

        return response_maker(build_dict)
