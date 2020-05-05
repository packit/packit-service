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
from itertools import islice
from json import dumps
from logging import getLogger

from flask import make_response

try:
    from flask_restx import Namespace, Resource
except ModuleNotFoundError:
    from flask_restplus import Namespace, Resource

from packit_service.service.api.parsers import indices, pagination_arguments
from packit_service.models import CoprBuildModel, optional_time


logger = getLogger("packit_service")

ns = Namespace("copr-builds", description="COPR builds")


@ns.route("")
class CoprBuildsList(Resource):
    @ns.expect(pagination_arguments)
    @ns.response(HTTPStatus.PARTIAL_CONTENT, "Copr builds list follows")
    def get(self):
        """ List all Copr builds. """

        # Return relevant info thats concise
        # Usecases like the packit-dashboard copr-builds table

        result = []
        checklist = []
        first, last = indices()
        for build in islice(CoprBuildModel.get_all(), first, last):
            if int(build.build_id) not in checklist:

                build_dict = {
                    "project": build.project_name,
                    "owner": build.owner,
                    "build_id": build.build_id,
                    "status": build.status,  # Legacy, remove later.
                    "status_per_chroot": {},
                    "chroots": [],
                    "build_submitted_time": optional_time(build.build_submitted_time),
                    "web_url": build.web_url,
                }

                project = build.get_project()
                if project:
                    build_dict["repo_namespace"] = project.namespace
                    build_dict["repo_name"] = project.repo_name

                # same_buildid_builds are copr builds created due to the same trigger
                # multiple identical builds are created which differ only in target
                # so we merge them into one
                same_buildid_builds = CoprBuildModel.get_all_by_build_id(
                    str(build.build_id)
                )
                for sbid_build in same_buildid_builds:
                    build_dict["chroots"].append(sbid_build.target)
                    # Get status per chroot as well
                    build_dict["status_per_chroot"][
                        sbid_build.target
                    ] = sbid_build.status

                checklist.append(int(build.build_id))
                result.append(build_dict)

        resp = make_response(dumps(result), HTTPStatus.PARTIAL_CONTENT)
        resp.headers["Content-Range"] = f"copr-builds {first + 1}-{last}/{len(result)}"
        resp.headers["Content-Type"] = "application/json"

        return resp


@ns.route("/<int:id>")
@ns.param("id", "Copr build identifier")
class InstallationItem(Resource):
    @ns.response(HTTPStatus.OK, "OK, copr build details follow")
    @ns.response(HTTPStatus.NO_CONTENT, "Copr build identifier not in db/hash")
    def get(self, id):
        """A specific copr build details. From copr_build hash, filled by worker."""
        builds_list = CoprBuildModel.get_all_by_build_id(str(id))
        if bool(builds_list.first()):
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
                build_dict[
                    "git_repo"
                ] = f"https://github.com/{project.namespace}/{project.repo_name}"
                build_dict[
                    "https_url"
                ] = f"https://github.com/{project.namespace}/{project.repo_name}.git"
                build_dict["pr_id"] = build.get_pr_id()

            # merge chroots into one
            for sbid_build in builds_list:
                build_dict["chroots"].append(sbid_build.target)
                # Get status per chroot as well
                build_dict["status_per_chroot"][sbid_build.target] = sbid_build.status

            build = make_response(dumps(build_dict))
            build.headers["Content-Type"] = "application/json"
            return build if build else ("", HTTPStatus.NO_CONTENT)

        else:
            return "", HTTPStatus.NO_CONTENT
