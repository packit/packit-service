# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from os import getenv
from socket import gaierror

from flask import Flask

# Mypy errors out with Module 'flask' has no attribute '__version__'.
# Python can find flask's version but mypy cannot.
# So we use "type: ignore" to cause mypy to ignore that line.
from flask import __version__ as flask_version  # type: ignore
from flask_cors import CORS
from flask_restx import __version__ as restx_version
from flask_talisman import Talisman
from lazy_object_proxy import Proxy
from packit.utils import set_logging
from prometheus_client import make_wsgi_app as prometheus_app
from syslog_rfc5424_formatter import RFC5424Formatter
from werkzeug.middleware.dispatcher import DispatcherMiddleware

from packit_service import __version__ as ps_version
from packit_service.config import ServiceConfig
from packit_service.sentry_integration import configure_sentry
from packit_service.service.api import blueprint
from packit_service.utils import log_package_versions

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

    syslog_host = getenv("SYSLOG_HOST", "fluentd")
    syslog_port = int(getenv("SYSLOG_PORT", 5140))
    logger.info(f"Setup logging to syslog -> {syslog_host}:{syslog_port}")
    try:
        handler = logging.handlers.SysLogHandler(address=(syslog_host, syslog_port))
    except (ConnectionRefusedError, gaierror):
        logger.info(f"{syslog_host}:{syslog_port} not available")
    else:
        handler.setLevel(logging.DEBUG)
        project = getenv("PROJECT", "packit")
        handler.setFormatter(RFC5424Formatter(msgid=project))
        logger.addHandler(handler)

    logger.info(
        f"server name = {service_config.server_name}, all HTTP requests need to use this URL!",
    )

    package_versions = [
        ("Flask", flask_version),
        ("Flask RestX", restx_version),
        ("Packit Service", ps_version),
    ]
    log_package_versions(package_versions)

    # no need to thank me, just buy me a beer
    logger.debug(f"URL map = {app.url_map}")
    return app


packit_as_a_service = Proxy(get_flask_application)

CORS(packit_as_a_service)

INLINE = [
    "'unsafe-inline'",
    "'self'",
]
Talisman(
    packit_as_a_service,
    # https://github.com/wntrblm/flask-talisman#options
    # https://infosec.mozilla.org/guidelines/web_security#implementation-notes
    content_security_policy={
        "default-src": "'self'",
        "object-src": "'none'",
        "img-src": ["'self'", "data:"],
        # https://github.com/python-restx/flask-restx/issues/252
        "style-src": INLINE,
        "script-src": INLINE,
    },
)

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
