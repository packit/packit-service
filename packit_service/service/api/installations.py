# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from http import HTTPStatus
from logging import getLogger

from flask_restx import Namespace, Resource

from packit_service.models import GithubInstallationModel

logger = getLogger("packit_service")

ns = Namespace("installations", description="Github App installations")


@ns.route("")
class InstallationsList(Resource):
    @ns.response(HTTPStatus.OK.value, "OK, installations list follows")
    def get(self):
        """List all Github App installations"""
        return [installation.to_dict() for installation in GithubInstallationModel.get_all()]


@ns.route("/<int:id>")
@ns.param("id", "Installation identifier")
class InstallationItem(Resource):
    @ns.response(HTTPStatus.OK.value, "OK, installation details follow")
    @ns.response(HTTPStatus.NO_CONTENT.value, "identifier not in allowlist")
    def get(self, id):
        """A specific installation details"""
        installation = GithubInstallationModel.get_by_id(id)
        return installation.to_dict() if installation else ("", HTTPStatus.NO_CONTENT.value)
