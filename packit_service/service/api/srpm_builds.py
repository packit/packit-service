# MIT License
#
# Copyright (c) 2020 Red Hat, Inc.
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
from packit_service.models import SRPMBuildModel, optional_time

# from packit_service.service.urls import get_srpm_log_url_from_flask
from flask import url_for

logger = getLogger("packit_service")

ns = Namespace("srpm-builds", description="SRPM builds")


@ns.route("")
class SRPMBuildsList(Resource):
    @ns.expect(pagination_arguments)
    @ns.response(HTTPStatus.PARTIAL_CONTENT.value, "SRPM builds list follows")
    def get(self):
        """ List all SRPM builds. """

        result = []

        first, last = indices()
        for build in SRPMBuildModel.get(first, last):
            build_dict = {
                "srpm_build_id": build.id,
                "success": build.success,
                "log_url": url_for(
                    "builds.get_srpm_build_logs_by_id", id_=build.id, _external=True
                ),
                "build_submitted_time": optional_time(build.build_submitted_time),
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
