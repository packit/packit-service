# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from datetime import timedelta
from os import getenv

from celery import Celery
from lazy_object_proxy import Proxy

from packit_service.constants import CELERY_DEFAULT_QUEUE_NAME
from packit_service.models import get_pg_url
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
            broker_url = f"redis://:{password}@{host}:{port}/{db}"

            # https://docs.celeryproject.org/en/stable/userguide/configuration.html#database-url-examples
            postgres_url = f"db+{get_pg_url()}"

            # http://docs.celeryproject.org/en/latest/reference/celery.html#celery.Celery
            self._celery_app = Celery(backend=postgres_url, broker=broker_url)

            days = int(getenv("CELERY_RESULT_EXPIRES", "30"))
            # https://docs.celeryproject.org/en/latest/userguide/configuration.html#result-expires
            self._celery_app.conf.result_expires = timedelta(days=days)
            # https://docs.celeryproject.org/en/latest/userguide/configuration.html#std-setting-task_default_queue
            self._celery_app.conf.task_default_queue = CELERY_DEFAULT_QUEUE_NAME

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
