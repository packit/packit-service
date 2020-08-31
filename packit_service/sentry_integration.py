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
import logging
from contextlib import contextmanager
from os import getenv

from packit_service.utils import only_once

logger = logging.getLogger(__name__)


@only_once
def configure_sentry(
    runner_type: str,
    celery_integration: bool = False,
    flask_integration: bool = False,
    sqlalchemy_integration: bool = False,
) -> None:
    logger.debug(
        f"Setup sentry for {runner_type}: "
        f"celery_integration={celery_integration}, "
        f"flask_integration={flask_integration}, "
        f"sqlalchemy_integration={sqlalchemy_integration}"
    )

    secret_key = getenv("SENTRY_SECRET")
    if not secret_key:
        return

    # so that we don't have to have sentry sdk installed locally
    import sentry_sdk

    integrations = []

    if celery_integration:
        # https://docs.sentry.io/platforms/python/celery/
        from sentry_sdk.integrations.celery import CeleryIntegration

        integrations.append(CeleryIntegration())

    if flask_integration:
        # https://docs.sentry.io/platforms/python/flask/
        from sentry_sdk.integrations.flask import FlaskIntegration

        integrations.append(FlaskIntegration())

    if sqlalchemy_integration:
        # https://docs.sentry.io/platforms/python/sqlalchemy/
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

        integrations.append(SqlalchemyIntegration())

    sentry_sdk.init(
        secret_key,
        integrations=integrations,
        environment=getenv("DEPLOYMENT"),
    )
    with sentry_sdk.configure_scope() as scope:
        scope.set_tag("runner-type", runner_type)


def send_to_sentry(ex):
    # so that we don't have to have sentry sdk installed locally
    import sentry_sdk

    sentry_sdk.capture_exception(ex)


@contextmanager
def push_scope_to_sentry():
    try:
        # so that we don't have to have sentry sdk installed locally
        import sentry_sdk

    except ImportError:

        class SentryMocker:
            def set_tag(self, k, v):
                pass

        yield SentryMocker()
    else:

        with sentry_sdk.push_scope() as scope:
            yield scope
