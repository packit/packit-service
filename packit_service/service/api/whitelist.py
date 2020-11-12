# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from http import HTTPStatus
from logging import getLogger

from flask_restx import Namespace, Resource

from packit_service.models import WhitelistModel

logger = getLogger("packit_service")

ns = Namespace("whitelist", description="Whitelisted FAS accounts")


@ns.route("")
class WhiteList(Resource):
    @ns.response(HTTPStatus.OK.value, "OK, whitelist follows")
    def get(self):
        """List all Whitelisted FAS accounts"""
        return [account.to_dict() for account in WhitelistModel.get_all()]


@ns.route("/<string:login>")
@ns.param("login", "Account login")
class WhiteListItem(Resource):
    @ns.response(HTTPStatus.OK.value, "OK, whitelisted account details follow")
    @ns.response(HTTPStatus.NO_CONTENT.value, "login not in whitelist")
    def get(self, login):
        """A specific whitelist item details"""
        account = WhitelistModel.get_account(login)
        return account.to_dict() if account else ("", HTTPStatus.NO_CONTENT.value)
