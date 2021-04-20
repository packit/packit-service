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

from flask import Blueprint, redirect

from packit_service.service.urls import (
    get_srpm_build_info_url,
    get_copr_build_info_url,
    get_koji_build_info_url,
)

builds_blueprint = Blueprint("builds", __name__)


@builds_blueprint.route("/srpm-build/<int:id_>/logs", methods=("GET",))
def get_srpm_build_logs_by_id(id_):
    return redirect(get_srpm_build_info_url(id_), code=308)


@builds_blueprint.route("/copr-build/<int:id_>/logs", methods=("GET",))
def get_copr_build_logs_by_id(id_):
    return redirect(get_copr_build_info_url(id_), code=308)


@builds_blueprint.route("/copr-build/<int:id_>", methods=("GET",))
def copr_build_info(id_):
    return redirect(get_copr_build_info_url(id_), code=308)


@builds_blueprint.route("/koji-build/<int:id_>/logs", methods=("GET",))
def get_koji_build_logs_by_id(id_):
    return redirect(get_koji_build_info_url(id_), code=308)


@builds_blueprint.route("/koji-build/<int:id_>", methods=("GET",))
def koji_build_info(id_):
    return redirect(get_koji_build_info_url(id_), code=308)
