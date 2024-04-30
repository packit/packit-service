# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from os import getenv

from celery import Celery
from lazy_object_proxy import Proxy

from packit_service.sentry_integration import configure_sentry


class Celerizer:
    def __init__(self):
        self._celery_app = None

    @property
    def celery_app(self):
        if self._celery_app is None:
            host = getenv("REDIS_SERVICE_HOST", "redis")
            password = getenv("REDIS_PASSWORD", "")
            port = getenv("REDIS_SERVICE_PORT", "6379")
            db = getenv("REDIS_SERVICE_DB", "0")
            celery_beckend = getenv("REDIS_CELERY_BECKEND", "1")
            broker_url = f"redis://:{password}@{host}:{port}/{db}"
            backend_url = f"redis://:{password}@{host}:{port}/{celery_beckend}"

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
