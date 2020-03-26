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
from json import dumps
from logging import getLogger

from flask import make_response
from flask_restplus import Namespace, Resource

from packit_service.service.api.parsers import indices, pagination_arguments
from packit_service.models import CoprBuild

from typing import Union


logger = getLogger("packit_service")


ns = Namespace("copr-builds", description="COPR builds")


def optional_time(datetime_object) -> Union[str, None]:
    """Returns a string if argument is a datetime object."""
    if datetime_object is None:
        return None
    else:
        return datetime_object.strftime("%d/%m/%Y %H:%M:%S")


@ns.route("")
class CoprBuildsList(Resource):
    @ns.expect(pagination_arguments)
    @ns.response(HTTPStatus.PARTIAL_CONTENT, "Copr builds list follows")
    def get(self):
        """ List all Copr builds. """

        # Return relevant info thats concise
        # Usecases like the packit-dashboard copr-builds table

        builds_list = CoprBuild.get_all()
        result = []
        checklist = []
        for build in builds_list:
            if int(build.build_id) not in checklist:
                build_dict = {
                    "project": build.project_name,
                    "owner": build.owner,
                    "repo_name": build.pr.project.repo_name,
                    "build_id": build.build_id,
                    "status": build.status,
                    "chroots": [],
                    "build_submitted_time": optional_time(build.build_submitted_time),
                    "repo_namespace": build.pr.project.namespace,
                    "web_url": build.web_url,
                }
                # same_buildid_builds are copr builds created due to the same trigger
                # multiple identical builds are created which differ only in target
                # so we merge them into one
                same_buildid_builds = CoprBuild.get_all_by_build_id(str(build.build_id))
                for sbid_build in same_buildid_builds:
                    build_dict["chroots"].append(sbid_build.target)

                checklist.append(int(build.build_id))
                result.append(build_dict)

        first, last = indices()
        resp = make_response(dumps(result[first:last]), HTTPStatus.PARTIAL_CONTENT)
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
        builds_list = CoprBuild.get_all_by_build_id(str(id))
        if bool(builds_list.first()):
            build = builds_list[0]
            build_dict = {
                "project": build.project_name,
                "owner": build.owner,
                "repo_name": build.pr.project.repo_name,
                "build_id": build.build_id,
                "status": build.status,
                "chroots": [],
                "build_submitted_time": optional_time(build.build_submitted_time),
                "build_start_time": build.build_start_time,
                "build_finished_time": build.build_finished_time,
                "pr_id": build.pr.pr_id,
                "commit_sha": build.commit_sha,
                "repo_namespace": build.pr.project.namespace,
                "web_url": build.web_url,
                "srpm_logs": build.srpm_build.logs,
                "git_repo": f"https://github.com/{build.pr.project.namespace}/"
                f"{build.pr.project.repo_name}",
                # For backwards compatability with the old redis based API
                "ref": build.commit_sha,
                "https_url": f"https://github.com/{build.pr.project.namespace}/"
                f"{build.pr.project.repo_name}.git",
            }
            # merge chroots into one
            for sbid_build in builds_list:
                build_dict["chroots"].append(sbid_build.target)

            build = make_response(dumps(build_dict))
            build.headers["Content-Type"] = "application/json"
            return build if build else ("", HTTPStatus.NO_CONTENT)

        else:
            return ("", HTTPStatus.NO_CONTENT)
