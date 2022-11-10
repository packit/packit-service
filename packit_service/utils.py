# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from datetime import datetime, timezone
from io import StringIO
from logging import StreamHandler
from typing import List, Tuple

from packit.config import JobConfig, PackageConfig
from packit.schema import JobConfigSchema, PackageConfigSchema
from packit.utils import PackitFormatter

logger = logging.getLogger(__name__)

LoggingLevel = int


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
def load_package_config(package_config: dict):
    return PackageConfigSchema().load(package_config) if package_config else None


def dump_package_config(package_config: PackageConfig):
    return PackageConfigSchema().dump(package_config) if package_config else None


def load_job_config(job_config: dict):
    return JobConfigSchema().load(job_config) if job_config else None


def dump_job_config(job_config: JobConfig):
    return JobConfigSchema().dump(job_config) if job_config else None


def get_package_nvrs(built_packages: List[dict]) -> List[str]:
    """
    Construct package NVRs for built packages except the SRPM.

    Returns:
        list of nvrs
    """
    packages = []
    for package in built_packages:
        if package["arch"] == "src":
            continue

        epoch = f"{package['epoch']}:" if package["epoch"] != 0 else ""
        packages.append(
            f"{package['name']}-{epoch}{package['version']}-{package['release']}.{package['arch']}"
        )
    return packages


# https://stackoverflow.com/a/41215655/14294700
def gather_packit_logs_to_buffer(
    logging_level: LoggingLevel,
) -> Tuple[StringIO, StreamHandler]:
    """
    Redirect packit logs into buffer with a given logging level to collect them later.

    To collect the buffer, you must use `collect_packit_logs()` later.

    Args:
        logging_level: Logs with this logging level will be collected.

    Returns:
        A tuple of values which you have to pass them to `collect_packit_logs()` function later.

        buffer: A StringIO buffer - storing logs here
        handler: StreamHandler

    """
    buffer = StringIO()
    handler = StreamHandler(buffer)
    packit_logger = logging.getLogger("packit")
    packit_logger.setLevel(logging_level)
    packit_logger.addHandler(handler)
    handler.setFormatter(PackitFormatter())
    return buffer, handler


def collect_packit_logs(buffer: StringIO, handler: StreamHandler) -> str:
    """
    Collect buffer of packit logs with specific logging level.

    To collect the buffer, you must firstly use `gather_packit_logs_to_buffer()` and pass
    its return values as parameters to this function.

    Args:
        buffer: A StringIO buffer - logs are stored here
        handler: StreamHandler

    Returns:
        String of packit logs.

    """
    packit_logger = logging.getLogger("packit")
    packit_logger.removeHandler(handler)
    buffer.seek(0)
    return buffer.read()


def is_timezone_naive_datetime(datetime_to_check: datetime) -> bool:
    """
    Check whether the given datetime is timezone naive.

    Args:
        datetime_to_check: datetime to check for timezone naiveness

    Returns:
        bool: whether the given datetime is timezone naive
    """
    # https://docs.python.org/3/library/datetime.html#determining-if-an-object-is-aware-or-naive
    return (
        datetime_to_check.tzinfo is None
        or datetime_to_check.tzinfo.utcoffset(datetime_to_check) is None
    )


def get_timezone_aware_datetime(datetime_to_update: datetime) -> datetime:
    """
    Make the datetime object timezone aware (utc) if needed.

    Args:
        datetime_to_update: datetime to check and update

    Result:
        timezone-aware datetime
    """
    if is_timezone_naive_datetime(datetime_to_update):
        return datetime_to_update.replace(tzinfo=timezone.utc)
    return datetime_to_update


def elapsed_seconds(begin: datetime, end: datetime) -> float:
    """
    Make the datetime objects timezone aware (utc) if needed
    and measure time between them in seconds.

    Returns:
        elapsed seconds between begin and end
    """
    begin = get_timezone_aware_datetime(begin)
    end = get_timezone_aware_datetime(end)

    return (end - begin).total_seconds()


def get_packit_commands_from_comment(
    comment: str, packit_comment_command_prefix: str
) -> List[str]:
    comment_parts = comment.strip()

    if not comment_parts:
        logger.debug("Empty comment, nothing to do.")
        return []

    comment_lines = comment_parts.split("\n")

    for line in filter(None, map(str.strip, comment_lines)):
        (packit_mark, *packit_command) = line.split(maxsplit=3)
        # packit_command[0] has the first cmd and [1] has the second, if needed.
        if packit_mark == packit_comment_command_prefix and packit_command:
            return packit_command

    return []
