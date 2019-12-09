# MIT License
#
# Copyright (c) 2018-2019 Red Hat, Inc.

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

"""
Flask views for packit-service
"""
from flask import Blueprint

from packit_service.worker.copr_db import CoprBuildDB

builds_blueprint = Blueprint("builds", __name__)


@builds_blueprint.route(
    "/build/<string:build_id>/<string:target>/logs", methods=("GET",)
)
def get_build_logs(build_id, target):
    db = CoprBuildDB()
    build = db.get_build(build_id)
    if build:
        try:
            build_target = build["targets"][target]
        except KeyError:
            return 404
        build_state = build_target.get("state", "invalid-state")
        web_url = build.get("web_url", "no web url")
        build_logs = build_target.get("build_logs", "no build logs")
        response = (
            f"Build {build_id} is in state {build_state}<br><br>"
            + f'Build web interface URL: <a href="{web_url}">{web_url}</a><br>'
            + f'Build logs: <a href="{build_logs}">{build_logs}</a><br>'
            + "SRPM creation logs:<br><br>"
            + build.get("logs", "No logs.")
            + "<br>"
        )
        return response
    return f"Build {build_id} does not exist.\n"
