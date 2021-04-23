# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

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
