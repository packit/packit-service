# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
import os
from datetime import datetime, timezone
from io import StringIO
from logging import StreamHandler
from pathlib import Path
from re import search
from typing import Optional

import requests
from ogr.abstract import PullRequest
from packit.config import JobConfig, PackageConfig
from packit.schema import JobConfigSchema, PackageConfigSchema
from packit.utils import PackitFormatter

from packit_service import __version__ as ps_version

logger = logging.getLogger(__name__)

LoggingLevel = int


class only_once:
    """
    Use as a function decorator to run function only once.
    """

    def __init__(self, func):
        self.func = func
        self.configured = False

    def __call__(self, *args, **kwargs):
        if self.configured:
            logger.debug(f"Function {self.func.__name__} already called. Skipping.")
            return None

        self.configured = True
        logger.debug(
            f"Function {self.func.__name__} called for the first time with "
            f"args: {args} and kwargs: {kwargs}",
        )
        return self.func(*args, **kwargs)


# wrappers for dumping/loading of configs
def load_package_config(package_config: dict):
    package_config_obj = PackageConfigSchema().load(package_config) if package_config else None
    return PackageConfig.post_load(package_config_obj)


def dump_package_config(package_config: PackageConfig):
    return PackageConfigSchema().dump(package_config) if package_config else None


def load_job_config(job_config: dict):
    return JobConfigSchema().load(job_config) if job_config else None


def dump_job_config(job_config: JobConfig):
    return JobConfigSchema().dump(job_config) if job_config else None


def get_package_nvrs(built_packages: list[dict]) -> list[str]:
    """
    Construct package NVRs for built packages except the SRPM.

    Returns:
        list of nvrs
    """
    packages = []
    for package in built_packages:
        if package["arch"] == "src":
            continue

        epoch = f"{package['epoch']}:" if package["epoch"] else ""

        packages.append(
            f"{package['name']}-{epoch}{package['version']}-{package['release']}.{package['arch']}",
        )
    return packages


def log_package_versions(package_versions: list[tuple[str, str]]):
    """
    It does the actual logging.

    Args:
        package_versions: List of tuples having pkg name and version.
    """
    log_string = "\nPackage Versions:"
    for name, version in package_versions:
        log_string += f"\n* {name} {version}"
    logger.info(log_string)


# https://stackoverflow.com/a/41215655/14294700
def gather_packit_logs_to_buffer(
    logging_level: LoggingLevel,
) -> tuple[StringIO, StreamHandler]:
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
    git_logger = logging.getLogger("git")
    git_logger.setLevel(logging_level)
    git_logger.addHandler(handler)
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
    git_logger = logging.getLogger("git")
    git_logger.removeHandler(handler)
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
    comment: str,
    packit_comment_command_prefix: str,
) -> list[str]:
    comment_parts = comment.strip()

    if not comment_parts:
        logger.debug("Empty comment, nothing to do.")
        return []

    comment_lines = comment_parts.split("\n")

    for line in filter(None, map(str.strip, comment_lines)):
        (packit_mark, *packit_command) = line.split()
        # packit_command[0] has the cmd and other list items are the arguments
        if packit_mark == packit_comment_command_prefix and packit_command:
            return packit_command

    return []


def get_koji_task_id_and_url_from_stdout(stdout: str) -> tuple[Optional[int], Optional[str]]:
    task_id, task_url = None, None

    task_id_match = search(pattern=r"Created task: (\d+)", string=stdout)
    if task_id_match:
        task_id = int(task_id_match.group(1))

    task_url_match = search(
        pattern=r"(https://.+/koji/taskinfo\?taskID=\d+)",
        string=stdout,
    )
    if task_url_match:
        task_url = task_url_match.group(0)

    return task_id, task_url


def pr_labels_match_configuration(
    pull_request: Optional[PullRequest],
    configured_labels_present: list[str],
    configured_labels_absent: list[str],
) -> bool:
    """
    Do the PR labels match the configuration of the labels?
    """
    if not pull_request:
        logger.debug("No PR to check the labels on.")
        return True

    logger.info(
        f"About to check whether PR labels in PR {pull_request.id} "
        f"match to the labels configuration "
        f"(label.present: {configured_labels_present}, label.absent: {configured_labels_absent})",
    )

    pr_labels = [label.name for label in pull_request.labels]
    logger.info(f"Labels on PR: {pr_labels}")

    return (
        not configured_labels_present
        or any(label in pr_labels for label in configured_labels_present)
    ) and (
        not configured_labels_absent
        or all(label not in pr_labels for label in configured_labels_absent)
    )


def download_file(url: str, path: Path):
    """
    Download a file from given url to the given path.

    Returns:
        True if the download was successful, False otherwise
    """
    # TODO: use a library to make the downloads more robust (e.g. pycurl),
    # unify with packit code:
    # https://github.com/packit/packit/blob/2e75e6ff4c0cadb55da1c8daf9315e4b0a69e4a8/packit/base_git.py#L566-L583
    user_agent = os.getenv("PACKIT_USER_AGENT") or f"packit-service/{ps_version} (hello@packit.dev)"
    try:
        with requests.get(
            url,
            headers={"User-Agent": user_agent},
            # connection and read timout
            timeout=(10, 30),
            stream=True,
        ) as response:
            response.raise_for_status()
            with open(path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
    except requests.exceptions.RequestException as e:
        msg = f"Failed to download file from {url}"
        logger.debug(f"{msg}: {e!r}")
        return False

    return True
