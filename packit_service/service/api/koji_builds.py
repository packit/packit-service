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
from itertools import islice

try:
    from flask_restx import Namespace, Resource
except ModuleNotFoundError:
    from flask_restplus import Namespace, Resource

from packit_service.service.api.parsers import indices, pagination_arguments
from packit_service.models import KojiBuildModel

logger = getLogger("packit_service")

koji_builds_ns = Namespace("koji-builds", description="Production builds")


@koji_builds_ns.route("")
class KojiBuildsList(Resource):
    @koji_builds_ns.expect(pagination_arguments)
    @koji_builds_ns.response(HTTPStatus.PARTIAL_CONTENT, "Koji builds list follows")
    def get(self):
        """ List all Koji builds. """

        result = []
        first, last = indices()
        for build in islice(KojiBuildModel.get_all(), first, last):
            result.append(build.api_structure)

        resp = make_response(dumps(result), HTTPStatus.PARTIAL_CONTENT)
        resp.headers["Content-Range"] = f"koji-builds {first + 1}-{last}/{len(result)}"
        resp.headers["Content-Type"] = "application/json"

        return resp


@koji_builds_ns.route("/<int:id>")
@koji_builds_ns.param("id", "Koji build identifier")
class KojiBuildItem(Resource):
    @koji_builds_ns.response(HTTPStatus.OK, "OK, koji build details follow")
    @koji_builds_ns.response(
        HTTPStatus.NO_CONTENT, "Koji build identifier not in db/hash"
    )
    def get(self, id):
        """A specific koji build details. From koji_build hash, filled by worker."""
        builds_list = KojiBuildModel.get_all_by_build_id(str(id))
        if bool(builds_list.first()):
            build = builds_list[0]

            build_dict = build.api_structure.copy()
            build_dict["srpm_logs"] = (
                build.srpm_build.logs if build.srpm_build else None
            )
            build = make_response(dumps(build_dict))
            build.headers["Content-Type"] = "application/json"
            return build if build else ("", HTTPStatus.NO_CONTENT)

        else:
            return "", HTTPStatus.NO_CONTENT
