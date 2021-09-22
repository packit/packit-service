# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from os import getenv

from flask import Flask
from lazy_object_proxy import Proxy
from prometheus_client import make_wsgi_app as prometheus_app
from werkzeug.middleware.dispatcher import DispatcherMiddleware

from packit.utils import set_logging
from packit_service.config import ServiceConfig
from packit_service.log_versions import log_service_versions
from packit_service.sentry_integration import configure_sentry
from packit_service.service.api import blueprint

set_logging(logger_name="packit_service", level=logging.DEBUG)


def get_flask_application():
    configure_sentry(
        runner_type="packit-service",
        celery_integration=True,
        sqlalchemy_integration=True,
        flask_integration=True,
    )
    app = Flask(__name__)
    app.register_blueprint(blueprint)
    service_config = ServiceConfig.get_service_config()
    # https://flask.palletsprojects.com/en/1.1.x/config/#SERVER_NAME
    # also needs to contain port if it's not 443
    app.config["SERVER_NAME"] = service_config.server_name
    app.config["PREFERRED_URL_SCHEME"] = "https"
    if getenv("DEPLOYMENT") in ("dev", "stg"):
        app.config["DEBUG"] = True

    app.logger.setLevel(logging.DEBUG)
    logger = logging.getLogger("packit_service")
    logger.info(
        f"server name = {service_config.server_name}, all HTTP requests need to use this URL!"
    )
    log_service_versions()
    # no need to thank me, just buy me a beer
    logger.debug(f"URL map = {app.url_map}")
    return app


packit_as_a_service = Proxy(get_flask_application)

# Make Prometheus Client serve the /metrics endpoint
application = DispatcherMiddleware(packit_as_a_service, {"/metrics": prometheus_app()})

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
