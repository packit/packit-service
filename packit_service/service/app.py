# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
import sys
from os import getenv
from socket import gaierror

from fastapi import FastAPI
from fastapi import __version__ as fastapi_version
from fastapi.middleware.wsgi import WSGIMiddleware
from flask import Flask

# Mypy errors out with Module 'flask' has no attribute '__version__'.
# Python can find flask's version but mypy cannot.
# So we use "type: ignore" to cause mypy to ignore that line.
from flask import __version__ as flask_version  # type: ignore
from flask_cors import CORS
from flask_restx import __version__ as restx_version
from flask_talisman import Talisman
from packit.utils import set_logging
from prometheus_client import make_asgi_app
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import RedirectResponse
from syslog_rfc5424_formatter import RFC5424Formatter

from packit_service import __version__ as ps_version
from packit_service.config import ServiceConfig
from packit_service.sentry_integration import configure_sentry
from packit_service.service.api import blueprint
from packit_service.service.api_v1 import routers
from packit_service.utils import log_package_versions

set_logging(logger_name="packit_service", level=logging.DEBUG)


def setup_logging_and_sentry():
    configure_sentry(
        runner_type="packit-service",
        celery_integration=True,
        sqlalchemy_integration=True,
        flask_integration=True,
        fastapi_integration=True,
    )
    logger = logging.getLogger("packit_service")
    logger.setLevel(logging.DEBUG)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)
    logger.addHandler(console_handler)

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

    package_versions = [
        ("Flask", flask_version),
        ("Flask RestX", restx_version),
        ("FastAPI", fastapi_version),
        ("Packit Service", ps_version),
    ]
    log_package_versions(package_versions)

    return logger


def get_flask_application():
    app = Flask(__name__)
    app.register_blueprint(blueprint)
    service_config = ServiceConfig.get_service_config()
    # https://flask.palletsprojects.com/en/1.1.x/config/#SERVER_NAME
    # also needs to contain port if it's not 443
    # TODO local deployment fails without uncommenting
    # app.config["SERVER_NAME"] = service_config.server_name
    app.config["PREFERRED_URL_SCHEME"] = "https"
    if getenv("DEPLOYMENT") in ("dev", "stg"):
        app.config["DEBUG"] = True

    app.logger.setLevel(logging.DEBUG)

    logger.info(
        f"server name = {service_config.server_name}, all HTTP requests need to use this URL!",
    )
    # no need to thank me, just buy me a beer
    logger.debug(f"URL map = {app.url_map}")
    return app


logger = setup_logging_and_sentry()

flask_app = get_flask_application()

CORS(flask_app)
INLINE = ["'unsafe-inline'", "'self'"]
# https://github.com/wntrblm/flask-talisman#options
# https://infosec.mozilla.org/guidelines/web_security#implementation-notes
Talisman(
    flask_app,
    content_security_policy={
        "default-src": "'self'",
        "object-src": "'none'",
        "img-src": ["'self'", "data:"],
        "style-src": INLINE,
        "script-src": INLINE,
    },
)

app = FastAPI(
    title="Packit Service API",
    version="1.0.0",
    docs_url="/v1/docs",
    redoc_url="/v1/redoc",
    openapi_url="/v1/openapi.json",
)

# CORS settings
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# mount Flask inside FastAPI
# https://fastapi.tiangolo.com/advanced/wsgi/
app.mount("/api", app=WSGIMiddleware(flask_app))


# Prometheus metrics
# https://prometheus.github.io/client_python/exporting/http/fastapi-gunicorn/
app.mount("/metrics", make_asgi_app())

# mount the endpoints
for router in routers:
    app.include_router(router, prefix="/v1")


@app.middleware("http")
async def security_and_https_middleware(request, call_next):
    if request.url.scheme != "https":
        url = request.url.replace(scheme="https", port=443)
        return RedirectResponse(url, status_code=301)

    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "object-src 'none'; "
        "img-src 'self' data:; "
        # Swagger and ReDoc don't work without these
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "worker-src 'self' blob:"
    )

    return response


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
