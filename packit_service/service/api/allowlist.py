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


@ns.route("/<string:login>")
@ns.param("login", "Account login")
class AllowlistItem(Resource):
    @ns.response(HTTPStatus.OK.value, "OK, allowlisted account details follow")
    @ns.response(HTTPStatus.NO_CONTENT.value, "login not in allowlist")
    def get(self, login):
        """A specific allowlist item details"""
        account = AllowlistModel.get_account(login)
        return account.to_dict() if account else ("", HTTPStatus.NO_CONTENT.value)
