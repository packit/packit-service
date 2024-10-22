# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from contextlib import contextmanager
from os import getenv

from sentry_sdk.integrations import Integration
from sentry_sdk.integrations.logging import LoggingIntegration

from packit_service.utils import only_once

logger = logging.getLogger(__name__)


def traces_sampler(sampling_context: dict) -> float:
    """
    Compute sample rate or sampling decision for a transaction.
    https://docs.sentry.io/platforms/python/performance/
    https://docs.sentry.io/platforms/python/configuration/sampling

    Args:
        sampling_context: context data

    Returns: traces sample rate (between 0 and 1)
    """
    if rate := getenv("SENTRY_TRACES_SAMPLE_RATE"):
        return float(rate)
    # TODO: Take sampling_context into account
    return 0.1 if getenv("DEPLOYMENT") == "prod" else 0.25


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
        f"sqlalchemy_integration={sqlalchemy_integration}",
    )

    secret_key = getenv("SENTRY_SECRET")
    if not secret_key:
        return

    # so that we don't have to have sentry sdk installed locally
    import sentry_sdk

    integrations: list[Integration] = []

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

    # https://docs.sentry.io/platforms/python/guides/logging/
    sentry_logging = LoggingIntegration(
        level=logging.DEBUG,  # Log everything, from DEBUG and above
        event_level=logging.ERROR,  # Send errors as events
    )
    integrations.append(sentry_logging)

    sentry_sdk.init(
        secret_key,
        integrations=integrations,
        environment=getenv("DEPLOYMENT"),
        traces_sampler=traces_sampler,
        # Do not report crawlers sending requests with wrong method.
        ignore_errors=["MethodNotAllowed"],
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
