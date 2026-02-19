# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import argparse
import logging
import os
import tempfile
from argparse import RawTextHelpFormatter
from datetime import datetime, timedelta, timezone
from io import StringIO
from logging import StreamHandler
from pathlib import Path
from re import search
from typing import Optional

import requests
from cachetools.func import ttl_cache
from ogr.abstract import PullRequest
from packit.config import JobConfig, PackageConfig, aliases
from packit.config.aliases import Distro
from packit.schema import JobConfigSchema, PackageConfigSchema
from packit.utils import PackitFormatter

from packit_service import __version__ as ps_version
from packit_service.constants import (
    DEFAULT_MAPPING_INTERNAL_TF,
    DEFAULT_MAPPING_TF,
    ELN_EXTRAS_PACKAGE_LIST,
    ELN_PACKAGE_LIST,
)

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


def _create_base_parser(
    prog: Optional[str] = None,
    description: Optional[str] = None,
    epilog: Optional[str] = None,
) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description=description,
        epilog=epilog,
        formatter_class=RawTextHelpFormatter,
    )
    parser.add_argument("--package", help="Specific package from monorepo to run job for")
    return parser


def get_comment_parser(
    prog: Optional[str] = None,
    description: Optional[str] = None,
    epilog: Optional[str] = None,
) -> argparse.ArgumentParser:
    parser = _create_base_parser(prog, description, epilog)

    subparsers = parser.add_subparsers(
        dest="command",
        help="Jobs available",
    )

    build_parser = subparsers.add_parser(
        "copr-build",
        aliases=["build"],
        help="Build package(s) in Copr",
    )
    build_parser.add_argument(
        "--commit", help="Run Copr build jobs configured with the commit trigger"
    )
    build_parser.add_argument(
        "--release", help="Run Copr build jobs configured with the release trigger"
    )
    subparsers.add_parser("rebuild-failed", help="Re-build failed builds in Copr")
    subparsers.add_parser(
        "upstream-koji-build",
        help="Build package(s) in Koji (the latest commit of this PR will be targeted, not HEAD)",
    )

    test_parser = subparsers.add_parser("test", help="Run tests in Testing Farm")
    test_parser.add_argument(
        "target",
        nargs="?",
        help="Reference to a PR in a different repository containing builds to test",
    )
    test_parser.add_argument("--commit", help="Run tests configured with the commit trigger")
    test_parser.add_argument("--release", help="Run tests configured with the release trigger")
    test_parser.add_argument(
        "--identifier", "--id", "-i", help="Identifier of job for which to run tests"
    )
    test_parser.add_argument(
        "--labels",
        type=lambda s: s.split(","),
        help="Comma-separated list of labels identifying tests to run",
    )
    test_parser.add_argument("--env", action="append", help="Environment variables")

    subparsers.add_parser("retest-failed", help="Re-run failed tests in Testing Farm")
    subparsers.add_parser("vm-image-build", help="Trigger VM image build")
    subparsers.add_parser("propose-downstream", help="Trigger propose-downstream job")

    pull_from_upstream_parser = subparsers.add_parser(
        "pull-from-upstream", help="Trigger pull-from-upstream job"
    )
    pull_from_upstream_parser.add_argument(
        "--resolve-bug",
        type=lambda s: s.split(","),
        help="Override the referenced resolved bug set by Packit",
    )
    pull_from_upstream_parser.add_argument(
        "--with-pr-config",
        action="store_true",
        help="Use the configuration file from this dist-git pull request",
    )

    subparsers.add_parser(
        "koji-build",
        help="Build package(s) in Koji (the latest commit of this PR will be targeted, not HEAD)",
    )

    koji_tag_parser = subparsers.add_parser("koji-tag", help="Tag Koji build to the common sidetag")
    koji_tag_parser.add_argument("--all-branches", action="store_true", help="Target all branches")

    subparsers.add_parser("create-update", help="Trigger Bodhi update job")

    return parser


def get_comment_parser_fedora_ci(
    prog: Optional[str] = None,
    description: Optional[str] = None,
    epilog: Optional[str] = None,
) -> argparse.ArgumentParser:
    parser = _create_base_parser(prog, description, epilog)

    subparsers = parser.add_subparsers(
        dest="command",
        help="Jobs available",
    )
    test_parser = subparsers.add_parser("test", help="Run tests in Testing Farm")
    test_parser.add_argument(
        "target",
        nargs="?",
        choices=["installability", "rpmlint", "rpminspect", "custom"],
        help="Specific type of tests to run",
    )
    subparsers.add_parser("scratch-build", help="Build package in Scratch")

    return parser


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


def get_user_agent() -> str:
    return (
        os.getenv("PACKIT_USER_AGENT") or f"packit-service/{ps_version or 'dev'} (hello@packit.dev)"
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
    try:
        with requests.get(
            url,
            headers={"User-Agent": get_user_agent()},
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


@ttl_cache(maxsize=1, ttl=timedelta(hours=12).seconds)
def get_eln_packages():
    packages = []
    for url in (ELN_PACKAGE_LIST, ELN_EXTRAS_PACKAGE_LIST):
        with tempfile.NamedTemporaryFile() as tmp:
            if download_file(url, tmp.name):
                packages.extend(Path(tmp.name).read_text().splitlines())
    return packages


def get_default_tf_mapping(internal: bool = False) -> dict[str, str]:
    mapping = DEFAULT_MAPPING_INTERNAL_TF if internal else DEFAULT_MAPPING_TF
    # map branched minor versions of EL 10+ to corresponding composes
    for alias in aliases.expand_aliases("epel-all"):
        if not isinstance(alias, Distro) or "." not in alias.branch:
            continue
        version = alias.namever.split("-")[1]
        majorver = version.split(".")[0]
        [target] = aliases.get_build_targets(alias.branch)
        mapping[target.rsplit("-", 1)[0]] = (
            f"rhel-{version}-nightly" if internal else f"centos-stream-{majorver}"
        )
    return mapping
