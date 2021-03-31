# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from http import HTTPStatus
from logging import getLogger

from flask_restx import Namespace, Resource

from packit_service.models import AllowlistModel

logger = getLogger("packit_service")

ns = Namespace("allowlist", description="Allowlisted FAS accounts")


@ns.route("")
class Allowlist(Resource):
    @ns.response(HTTPStatus.OK.value, "OK, allowlist follows")
    def get(self):
        """List all Allowlisted FAS accounts"""
        return [account.to_dict() for account in AllowlistModel.get_all()]


@ns.route("/<path:namespace>")
@ns.param("namespace", "Namespace to be queried")
class AllowlistItem(Resource):
    @ns.response(HTTPStatus.OK.value, "OK, allowlisted namespace details follow")
    @ns.response(HTTPStatus.NO_CONTENT.value, "namespace not in allowlist")
    def get(self, namespace):
        """A specific allowlist item details"""
        entry = AllowlistModel.get_namespace(namespace)
        return entry.to_dict() if entry else ("", HTTPStatus.NO_CONTENT.value)
