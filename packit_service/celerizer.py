# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from os import getenv

from celery import Celery
from lazy_object_proxy import Proxy

from packit_service.constants import (
    REDIS_DEFAULT_CELERY_BACKEND,
    REDIS_DEFAULT_DB,
    REDIS_DEFAULT_HOST,
    REDIS_DEFAULT_PASSWORD,
    REDIS_DEFAULT_PORT,
)
from packit_service.sentry_integration import configure_sentry


def get_redis_config():
    """
    Get Redis connection configuration from environment variables.

    Returns:
        dict: Redis configuration with keys: host, password, port, db, celery_backend
    """
    return {
        "host": getenv("REDIS_SERVICE_HOST", REDIS_DEFAULT_HOST),
        "password": getenv("REDIS_PASSWORD", REDIS_DEFAULT_PASSWORD),
        "port": getenv("REDIS_SERVICE_PORT", REDIS_DEFAULT_PORT),
        "db": getenv("REDIS_SERVICE_DB", REDIS_DEFAULT_DB),
        "celery_backend": getenv("REDIS_CELERY_BACKEND")
        or getenv("REDIS_CELERY_BECKEND", REDIS_DEFAULT_CELERY_BACKEND),
    }


class Celerizer:
    def __init__(self):
        self._celery_app = None

    @property
    def celery_app(self):
        if self._celery_app is None:
            redis_config = get_redis_config()
            broker_url = f"redis://:{redis_config['password']}@{redis_config['host']}:{redis_config['port']}/{redis_config['db']}"
            backend_url = f"redis://:{redis_config['password']}@{redis_config['host']}:{redis_config['port']}/{redis_config['celery_backend']}"

            # http://docs.celeryq.dev/en/stable/reference/celery.html#celery.Celery
            self._celery_app = Celery(backend=backend_url, broker=broker_url)

            # https://docs.celeryq.dev/en/stable/getting-started/first-steps-with-celery.html#configuration
            self._celery_app.config_from_object("packit_service.celery_config")

        return self._celery_app


def get_celery_application():
    celerizer = Celerizer()
    app = celerizer.celery_app
    configure_sentry(
        runner_type="packit-worker",
        celery_integration=True,
        sqlalchemy_integration=True,
    )
    return app


celery_app: Celery = Proxy(get_celery_application)
