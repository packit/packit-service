# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from datetime import datetime
from typing import Optional

from packit.config import JobConfig, PackageConfig
from packit.schema import JobConfigSchema, PackageConfigSchema

logger = logging.getLogger(__name__)


class only_once(object):
    """
    Use as a function decorator to run function only once.
    """

    def __init__(self, func):
        self.func = func
        self.configured = False

    def __call__(self, *args, **kwargs):
        if self.configured:
            logger.debug(f"Function {self.func.__name__} already called. Skipping.")
            return

        self.configured = True
        logger.debug(
            f"Function {self.func.__name__} called for the first time with "
            f"args: {args} and kwargs: {kwargs}"
        )
        return self.func(*args, **kwargs)


# wrappers for dumping/loading of configs
def load_package_config(package_config: PackageConfig):
    return PackageConfigSchema().load(package_config) if package_config else None


def dump_package_config(package_config: PackageConfig):
    return PackageConfigSchema().dump(package_config) if package_config else None


def load_job_config(job_config: JobConfig):
    return JobConfigSchema().load(job_config) if job_config else None


def dump_job_config(job_config: JobConfig):
    return JobConfigSchema().dump(job_config) if job_config else None


def pretty_time(time: datetime) -> Optional[str]:
    """
    Get a pretty string like 'an hour ago', 'yesterday', '3 months ago',
    'just now'.
    https://stackoverflow.com/questions/1551382/user-friendly-time-format-in-python
    """
    if time is None:
        return None

    now = datetime.now()
    diff = now - time
    second_diff = diff.seconds
    day_diff = diff.days

    if day_diff == 0:
        if second_diff < 10:
            return "just now"
        if second_diff < 60:
            return str(second_diff) + " seconds ago"
        if second_diff < 120:
            return "a minute ago"
        if second_diff < 3600:
            return str(second_diff // 60) + " minutes ago"
        if second_diff < 7200:
            return "an hour ago"
        if second_diff < 86400:
            return str(second_diff // 3600) + " hours ago"
    if day_diff == 1:
        return "yesterday"
    if day_diff < 7:
        return str(day_diff) + " days ago"
    if day_diff < 31:
        return str(day_diff // 7) + " weeks ago"
    if day_diff < 365:
        return str(day_diff // 30) + " months ago"
    return str(day_diff // 365) + " years ago"
