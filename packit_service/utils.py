# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
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
def load_package_config(package_config: PackageConfig):
    return PackageConfigSchema().load(package_config) if package_config else None


def dump_package_config(package_config: PackageConfig):
    return PackageConfigSchema().dump(package_config) if package_config else None


def load_job_config(job_config: JobConfig):
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
