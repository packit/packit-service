# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from datetime import timedelta
from gzip import open as gzip_open
from logging import DEBUG, INFO, getLogger
from os import getenv
from pathlib import Path
from shutil import copyfileobj

from boto3 import client as boto3_client
from botocore.exceptions import ClientError
from packit.utils.commands import run_command

from packit_service.constants import (
    PACKAGE_CONFIGS_OUTDATED_AFTER_DAYS,
    SRPMBUILDS_OUTDATED_AFTER_DAYS,
)
from packit_service.models import ProjectEventModel, SRPMBuildModel, get_pg_url

logger = getLogger(__name__)

DB_NAME = getenv("POSTGRESQL_DATABASE")


def discard_old_srpm_build_logs():
    """Called periodically (see celery_config.py) to discard logs of old SRPM builds."""
    logger.info("About to discard old SRPM build logs & artifact urls.")
    outdated_after_days = getenv(
        "SRPMBUILDS_OUTDATED_AFTER_DAYS",
        SRPMBUILDS_OUTDATED_AFTER_DAYS,
    )
    ago = timedelta(days=int(outdated_after_days))
    for build in SRPMBuildModel.get_older_than(ago):
        logger.debug(
            f"SRPM build {build.id} is older than '{ago}'. Discarding log and artifact url.",
        )
        build.set_logs(None)
        build.set_url(None)


def discard_old_package_configs():
    """Called periodically (see celery_config.py) to discard package configs of old events."""
    logger.info("About to discard old package configs.")
    outdated_after_days = getenv(
        "PACKAGE_CONFIGS_OUTDATED_AFTER_DAYS",
        PACKAGE_CONFIGS_OUTDATED_AFTER_DAYS,
    )
    ago = timedelta(days=int(outdated_after_days))
    events = ProjectEventModel.get_and_reset_older_than_with_packages_config(ago)
    event_ids = "".join([" " + str(event.id) for event in events])

    logger.debug(
        f"ProjectEventModels with ids [{event_ids}] have all runs older than '{ago}'. "
        "Discarded package configs.",
    )


def gzip_file(file: Path) -> Path:
    """Gzip compress given file into {file}.gz

    Args:
        file: File to be compressed.
    Returns:
        Compressed file.
    Raises:
        OSError: If the 'file' can't be opened.
    """
    compressed_file = Path(f"{file}.gz")
    try:
        with (
            file.open(mode="rb") as f_in,
            gzip_open(
                compressed_file,
                mode="wb",
            ) as f_out,
        ):
            logger.info(f"Compressing {file} into {compressed_file}")
            copyfileobj(f_in, f_out)
    except OSError as e:
        logger.error(e)
        raise
    return compressed_file


def upload_to_s3(
    file: Path,
    bucket: str = f"arr-packit-{getenv('DEPLOYMENT', 'dev')}",
) -> None:
    """Upload a file to an S3 bucket.

    Args:
        file: File to upload.
        bucket: Bucket to upload to.
    """

    s3_client = boto3_client("s3")
    try:
        logger.info(f"Uploading {file} to S3 ({bucket})")
        s3_client.upload_file(str(file), bucket, file.name)
    except ClientError as e:
        logger.error(e)
        raise


def is_aws_configured() -> bool:
    # https://boto3.amazonaws.com/v1/documentation/api/latest/guide/credentials.html#environment-variables
    return bool(getenv("AWS_ACCESS_KEY_ID") and getenv("AWS_SECRET_ACCESS_KEY"))


def dump_to(file: Path):
    """Dump 'packit' database into a file.

    To restore db from this file, run:
    psql -d packit < database_packit.sql

    Args:
        file: File where to put the dump.
    Raises:
        PackitCommandFailedError: When pg_dump fails.
    """
    # We have to specify libpq connection string to be able to pass the
    # password to the pg_dump. Luckily get_pg_url() does almost what we need.
    pg_connection = get_pg_url().replace("+psycopg2", "")
    cmd = ["pg_dump", f"--file={file}", f"--dbname={pg_connection}"]
    packit_logger = getLogger("packit")
    was_debug = packit_logger.level == DEBUG

    logger.info(f"Running pg_dump to create '{DB_NAME}' database backup")
    try:
        if was_debug:
            # Temporarily increase log level to avoid password leaking into logs
            packit_logger.setLevel(INFO)
        run_command(cmd=cmd)
    finally:
        if was_debug:
            packit_logger.setLevel(DEBUG)


def backup():
    """Dump the 'packit' database into a file, compress and upload to S3."""
    if not is_aws_configured():
        logger.info("Not backing up database since AWS is not configured.")
        # probably dev/test deployment
        return

    project = getenv("PROJECT", "packit")
    file = Path(f"/tmp/{project}_database_{DB_NAME}.sql")
    compressed_file = None
    try:
        logger.info("About to backup database")
        dump_to(file)
        compressed_file = gzip_file(file)
        upload_to_s3(compressed_file)
        logger.info("Backup complete")
    finally:
        file.unlink(missing_ok=True)
        if compressed_file:
            compressed_file.unlink()
