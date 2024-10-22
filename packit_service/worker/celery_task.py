# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from typing import Any, Optional

from celery import Task

logger = logging.getLogger(__name__)


class CeleryTask:
    """
    Class wrapping the Celery task object with methods related to retrying.
    """

    def __init__(self, task: Task):
        self.task = task

    @property
    def retries(self):
        """
        This is the retry number:
        """
        return self.task.request.retries

    def is_last_try(self) -> bool:
        """
        Returns True if the current celery task is run for the last try.
        More info about retries can be found here:
        https://docs.celeryq.dev/en/latest/userguide/tasks.html#retrying
        """
        return self.retries >= self.get_retry_limit()

    def get_retry_limit(self) -> int:
        """
        Returns the limit of the celery task retries. These are configured
        in task.py in the specific Task definitions
        """
        return self.task.max_retries

    def retry(
        self,
        ex: Optional[Exception] = None,
        delay: Optional[int] = None,
        max_retries: Optional[int] = None,
        kargs: Optional[dict[str, Any]] = None,
    ) -> None:
        """
        Retries the celery task.
        Argument `throw` is set to False to not retry
        the task also because of the `autoretry_for` mechanism.

        More info about retries can be found here:
        https://docs.celeryq.dev/en/latest/userguide/tasks.html#retrying

        Args:
            ex: Exception which caused the retry (will be logged).
            delay: Number of seconds the task will wait before being run again.
            max_retries: Maximum number of retries to use instead of the default within
                HandlerTaskWithRetry.
            kargs: Extra keyword arguments to pass to the task when retrying.
        """
        retries = self.retries
        delay = delay if delay is not None else 60 * 2**retries
        logger.info(f"Will retry for the {retries + 1}. time in {delay}s.")
        kargs = (kargs or self.task.request.kwargs).copy()
        self.task.retry(
            exc=ex,
            countdown=delay,
            throw=False,
            args=(),
            kwargs=kargs,
            max_retries=max_retries,
        )
