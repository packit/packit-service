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

from flask import Blueprint, redirect, render_template, url_for

from packit_service.log_versions import log_service_versions
from packit_service.models import (
    CoprBuildModel,
    GitBranchModel,
    KojiBuildModel,
    ProjectReleaseModel,
    PullRequestModel,
    SRPMBuildModel,
)
from packit_service.utils import pretty_time

builds_blueprint = Blueprint("builds", __name__)


@builds_blueprint.route("/srpm-build/<int:id_>/logs", methods=("GET",))
def get_srpm_build_logs_by_id(id_):
    log_service_versions()
    srpm_build = SRPMBuildModel.get_by_id(id_)
    if srpm_build:
        return render_template("srpm_logs.html", id=id_, srpm_build=srpm_build)
    return f"We can't find any info about SRPM build {id_}.\n", 404


def _get_build_info(
    build: Union[KojiBuildModel, CoprBuildModel], build_description: str
):
    project = build.get_project()

    trigger = build.get_trigger_object()
    if isinstance(trigger, PullRequestModel):
        title_identifier = f"PR #{trigger.pr_id}"
    elif isinstance(trigger, GitBranchModel):
        title_identifier = f"branch {trigger.name}"
    elif isinstance(trigger, ProjectReleaseModel):
        title_identifier = f"release {trigger.tag_name}"
    else:
        title_identifier = ""

    srpm_build = build.get_srpm_build()

    return render_template(
        "build_info.html",
        project=project,
        title_identifier=title_identifier,
        build_description=build_description,
        build=build,
        build_submitted_time=pretty_time(build.build_submitted_time),
        srpm_submitted_time=pretty_time(srpm_build.build_submitted_time)
        if srpm_build
        else None,
        owner=build.owner if isinstance(build, CoprBuildModel) else None,
        project_name=build.project_name if isinstance(build, CoprBuildModel) else None,
        is_pr=isinstance(trigger, PullRequestModel),
    )


@builds_blueprint.route("/copr-build/<int:id_>/logs", methods=("GET",))
def get_copr_build_logs_by_id(id_):
    return redirect(url_for(".copr_build_info", id_=id_, code=308))


@builds_blueprint.route("/copr-build/<int:id_>", methods=("GET",))
def copr_build_info(id_):
    log_service_versions()
    build = CoprBuildModel.get_by_id(id_)
    if build:
        return _get_build_info(build, build_description="Copr build")
    return f"We can't find any info about Copr build {id_}.\n", 404


@builds_blueprint.route("/koji-build/<int:id_>/logs", methods=("GET",))
def get_koji_build_logs_by_id(id_):
    return redirect(url_for(".koji_build_info", id_=id_, code=308))


@builds_blueprint.route("/koji-build/<int:id_>", methods=("GET",))
def koji_build_info(id_):
    log_service_versions()
    build = KojiBuildModel.get_by_id(id_)
    if build:
        return _get_build_info(build, build_description="Koji build")
    return f"We can't find any info about Koji build {id_}.\n", 404
