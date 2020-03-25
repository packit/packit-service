# MIT License
#
# Copyright (c) 2018-2019 Red Hat, Inc.

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

from os import getenv

from celery import Celery
from lazy_object_proxy import Proxy

from packit_service.models import get_pg_url
from packit_service.sentry_integration import configure_sentry


class Celerizer:
    def __init__(self):
        self._celery_app = None

    @property
    def celery_app(self):
        if self._celery_app is None:
            redis_host = getenv("REDIS_SERVICE_HOST", "localhost")
            redis_port = getenv("REDIS_SERVICE_PORT", "6379")
            redis_db = getenv("REDIS_SERVICE_DB", "0")
            redis_url = "redis://{host}:{port}/{db}".format(
                host=redis_host, port=redis_port, db=redis_db
            )
            postgres_url = f"db+{get_pg_url()}"

            # http://docs.celeryproject.org/en/latest/reference/celery.html#celery.Celery
            self._celery_app = Celery(backend=postgres_url, broker=redis_url)
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
