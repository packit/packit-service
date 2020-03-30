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

from packit_service.models import CoprBuildModel, SRPMBuildModel

builds_blueprint = Blueprint("builds", __name__)


@builds_blueprint.route("/srpm-build/<int:id_>/logs", methods=("GET",))
def get_srpm_build_logs_by_id(id_):
    srpm_build = SRPMBuildModel.get_by_id(id_)
    if srpm_build:
        response = (
            "<html><head>"
            f"<title>SRPM Build id={id_}</title></head><body>"
            "SRPM creation logs:<br><br>"
            f"<pre>{srpm_build.logs}</pre>"
            "<br></body></html>"
        )
        return response
    return f"We can't find any info about SRPM build {id_}.\n"


@builds_blueprint.route("/copr-build/<int:id_>/logs", methods=("GET",))
def get_build_logs_by_id(id_):
    build = CoprBuildModel.get_by_id(id_)
    if build:
        response = (
            "<html><head>"
            f"<title>Build {build.pr.project.namespace}/{build.pr.project.repo_name}"
            f" #{build.pr.pr_id}</title></head><body>"
            f"COPR Build ID: {build.build_id}<br>"
            f"State: {build.status}<br><br>"
            f'Build web interface URL: <a href="{build.web_url}">{build.web_url}</a><br>'
        )
        if build.build_logs_url:
            response += (
                f'Build logs: <a href="{build.build_logs_url}">'
                f"{build.build_logs_url}</a><br>"
            )
        response += (
            "SRPM creation logs:<br><br>"
            f"<pre>{build.srpm_build.logs}</pre>"
            "<br></body></html>"
        )
        return response
    return f"We can't find any info about COPR build {id_}.\n"
