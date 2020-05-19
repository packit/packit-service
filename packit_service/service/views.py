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
from typing import Union

from flask import Blueprint

from packit_service import models
from packit_service.log_versions import log_service_versions
from packit_service.models import (
    CoprBuildModel,
    SRPMBuildModel,
    PullRequestModel,
    GitBranchModel,
    ProjectReleaseModel,
    KojiBuildModel,
)

builds_blueprint = Blueprint("builds", __name__)


@builds_blueprint.route("/srpm-build/<int:id_>/logs", methods=("GET",))
def get_srpm_build_logs_by_id(id_):
    log_service_versions()
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


def _get_build_logs_for_build(
    build: Union[KojiBuildModel, CoprBuildModel], build_description: str
):
    project = build.get_project()

    trigger = build.job_trigger.get_trigger_object()
    if isinstance(trigger, PullRequestModel):
        title_identifier = f"PR #{trigger.pr_id}"
    elif isinstance(trigger, GitBranchModel):
        title_identifier = f"branch {trigger.name}"
    elif isinstance(trigger, ProjectReleaseModel):
        title_identifier = f"release {trigger.tag_name}"
    else:
        title_identifier = ""

    response = (
        "<html><head>"
        f"<title>{build_description} {project.namespace}/{project.repo_name}:"
        f" {title_identifier}</title></head><body>"
        f"{build_description} ID: {build.build_id}<br>"
        f"Submitted: {models.optional_time(build.build_submitted_time)}<br>"
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


@builds_blueprint.route("/copr-build/<int:id_>/logs", methods=("GET",))
def get_copr_build_logs_by_id(id_):
    log_service_versions()
    build = CoprBuildModel.get_by_id(id_)
    if build:
        return _get_build_logs_for_build(build, build_description="COPR build")
    return f"We can't find any info about COPR build {id_}.\n"


@builds_blueprint.route("/koji-build/<int:id_>/logs", methods=("GET",))
def get_koji_build_logs_by_id(id_):
    log_service_versions()
    build = KojiBuildModel.get_by_id(id_)
    if build:
        return _get_build_logs_for_build(build, build_description="Koji build")
    return f"We can't find any info about Koji build {id_}.\n"
