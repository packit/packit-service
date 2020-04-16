# MIT License
#
# Copyright (c) 2019 Red Hat, Inc.
#
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

import logging
from os import getenv

from flask import Flask
from lazy_object_proxy import Proxy
from packit.utils import set_logging

from packit_service.config import ServiceConfig

# from packit_service.sentry_integration import configure_sentry
from packit_service.service.api import blueprint
from packit_service.log_versions import log_service_versions
from packit_service.service.views import builds_blueprint

set_logging(logger_name="packit_service", level=logging.DEBUG)


def get_flask_application():

    # Sentry does not work in the service for now
    # SENTRY_SECRET is not passed to the service pod or container
    # https://github.com/packit-service/deployment/blob/master/openshift/packit-service.yml.j2

    # configure_sentry(
    #     runner_type="packit-service",
    #     celery_integration=True,
    #     sqlalchemy_integration=True,
    #     flask_integration=True,
    # )

    app = Flask(__name__)
    app.register_blueprint(blueprint)
    app.register_blueprint(builds_blueprint)
    s = ServiceConfig.get_service_config()
    # https://flask.palletsprojects.com/en/1.1.x/config/#SERVER_NAME
    # also needs to contain port if it's not 443
    app.config["SERVER_NAME"] = s.server_name
    app.config["PREFERRED_URL_SCHEME"] = "https"
    if getenv("DEPLOYMENT") in ("dev", "stg"):
        app.config["DEBUG"] = True
    app.logger.setLevel(logging.DEBUG)
    logger = logging.getLogger("packit_service")
    logger.info(
        f"server name = {s.server_name}, all HTTP requests need to use this URL!"
    )
    log_service_versions()
    # no need to thank me, just buy me a beer
    logger.debug(f"URL map = {app.url_map}")
    return app


application = Proxy(get_flask_application)


# With the code below, you can debug ALL requests coming to flask
# @application.before_request
# def log_request():
#     from flask import request, url_for
#     import logging
#     logger = logging.getLogger(__name__)
#     logger.info("Request Headers %s", request.headers)
#     logger.info("sample URL: %s", url_for(
#         "api.doc",
#         _external=True,  # _external = generate a URL with FQDN, not a relative one
#     ))
