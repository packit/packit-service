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

from packit_service.service.events import Event
from packit_service.service.models import Installation

logger = getLogger("packit_service")

ns = Namespace("installations", description="Github App installations")


@ns.route("")
class InstallationsList(Resource):
    @ns.response(HTTPStatus.OK, "OK, installations list follows")
    def get(self):
        """List all Github App installations"""
        return [
            Event.ts2str(i["event_data"]) for i in Installation.db().get_all().values()
        ]


@ns.route("/<int:id>")
@ns.param("id", "Installation identifier")
class InstallationItem(Resource):
    @ns.response(HTTPStatus.OK, "OK, installation details follow")
    @ns.response(HTTPStatus.NO_CONTENT, "identifier not in whitelist")
    def get(self, id):
        """A specific installation details"""
        installation = Installation.db().get(id)
        no_content = ("", HTTPStatus.NO_CONTENT)
        return installation["event_data"] if installation else no_content
