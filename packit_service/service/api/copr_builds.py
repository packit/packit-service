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

from flask_restplus import Namespace, Resource
from persistentdict.dict_in_redis import PersistentDict

from packit_service.service.models import CoprBuild, LAST_PK

logger = getLogger("packit_service")


ns = Namespace("copr-builds", description="COPR builds")


@ns.route("/")
class CoprBuildsList(Resource):
    @ns.response(HTTPStatus.OK, "OK, Copr builds list follows")
    def get(self):
        """ List all Copr builds. From 'copr-builds' hash, filled by service. """
        result = []
        for k, cb in CoprBuild.db().get_all().items():
            if k == LAST_PK:
                # made-up keys, remove LAST_PK
                continue
            cb.pop("identifier", None)  # see PR#179
            result.append(cb)
        return result


@ns.route("/<int:id>")
@ns.param("id", "Copr build identifier")
class InstallationItem(Resource):
    @ns.response(HTTPStatus.OK, "OK, copr build details follow")
    @ns.response(HTTPStatus.NO_CONTENT, "Copr build identifier not in db/hash")
    def get(self, id):
        """A specific copr build details. From copr_build hash, filled by worker."""
        # hash name is defined in worker (CoprBuildDB), which I don't want to import from
        db = PersistentDict(hash_name="copr_build")
        no_content = ("", HTTPStatus.NO_CONTENT)
        try:
            build = db[id]
        except KeyError:
            return no_content
        return build if build else no_content
