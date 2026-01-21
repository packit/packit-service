# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from datetime import timedelta
from gzip import open as gzip_open
from logging import DEBUG, INFO, getLogger
from os import getenv
from pathlib import Path
from shutil import copyfileobj
from typing import Optional

from boto3 import client as boto3_client
from botocore.exceptions import ClientError
from packit.utils.commands import run_command
from sqlalchemy import create_engine, delete, distinct, func, select, union

from packit_service.constants import (
    PACKAGE_CONFIGS_OUTDATED_AFTER_DAYS,
    PIPELINES_OUTDATED_AFTER_DAYS,
    SRPMBUILDS_OUTDATED_AFTER_DAYS,
)
from packit_service.models import (
    BodhiUpdateGroupModel,
    BodhiUpdateTargetModel,
    CoprBuildGroupModel,
    CoprBuildTargetModel,
    GitBranchModel,
    GitProjectModel,
    IssueModel,
    KojiBuildGroupModel,
    KojiBuildTargetModel,
    KojiTagRequestGroupModel,
    KojiTagRequestTargetModel,
    OSHScanModel,
    PipelineModel,
    ProjectAuthenticationIssueModel,
    ProjectEventModel,
    ProjectEventModelType,
    ProjectReleaseModel,
    PullRequestModel,
    SRPMBuildModel,
    SyncReleaseModel,
    SyncReleasePullRequestModel,
    SyncReleaseTargetModel,
    TFTTestRunGroupModel,
    TFTTestRunTargetModel,
    VMImageBuildTargetModel,
    get_pg_url,
    sync_release_pr_association_table,
    tf_copr_association_table,
    tf_koji_association_table,
)

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


def delete_old_data(age: Optional[str] = None):
    """
    Remove old data from the DB.

    Args:
        age: PostgreSQL interval string (e.g., '1 year', '6 months', '365 days').
             If not provided, reads from PIPELINES_OUTDATED_AFTER_DAYS env var.
    """
    if age is None:
        outdated_after_days = getenv(
            "PIPELINES_OUTDATED_AFTER_DAYS",
            PIPELINES_OUTDATED_AFTER_DAYS,
        )
        age = f"{outdated_after_days} days"

    logger.info(f"About to delete data older than {age}")

    engine = create_engine(get_pg_url(), echo=True)

    with engine.begin() as conn:
        # Delete the pipelines older than AGE
        stmt = delete(PipelineModel).where(func.age(PipelineModel.datetime) >= age)
        result = conn.execute(stmt)
        logger.info(f"Deleted {result.rowcount} pipelines older than {age}")

        # Delete ProjectEventModels which don't belong to any pipeline
        orphaned_events = (
            select(distinct(ProjectEventModel.id))
            .outerjoin(PipelineModel, PipelineModel.project_event_id == ProjectEventModel.id)
            .filter(PipelineModel.id == None)  # noqa
        )
        stmt = delete(ProjectEventModel).where(ProjectEventModel.id.in_(orphaned_events))
        result = conn.execute(stmt)
        logger.info(f"Deleted {result.rowcount} orphaned ProjectEventModels")

        # Delete SRPMBuilds and VMImageBuilds which don't belong to a pipeline
        attr = [
            (SRPMBuildModel, PipelineModel.srpm_build_id),
            (VMImageBuildTargetModel, PipelineModel.vm_image_build_id),
        ]
        for model, field in attr:
            orphaned = (
                select(distinct(model.id))  # type: ignore
                .outerjoin(PipelineModel, field == model.id)  # type: ignore
                .filter(PipelineModel.id == None)  # noqa
            )
            stmt = delete(model).where(model.id.in_(orphaned))  # type: ignore
            result = conn.execute(stmt)
            logger.info(f"Deleted {result.rowcount} orphaned {model.__name__}")

        # Delete CoprBuildTargets and tf-copr associations which don't belong to a pipeline
        orphaned = (
            select(distinct(CoprBuildTargetModel.id))
            .outerjoin_from(
                CoprBuildGroupModel,
                CoprBuildTargetModel,
                CoprBuildTargetModel.copr_build_group_id == CoprBuildGroupModel.id,
            )
            .outerjoin(
                PipelineModel,
                PipelineModel.copr_build_group_id == CoprBuildGroupModel.id,
            )
            .filter(PipelineModel.id == None)  # noqa
        )
        stmt = delete(tf_copr_association_table).where(
            tf_copr_association_table.c.copr_id.in_(orphaned),
        )
        result = conn.execute(stmt)
        logger.info(f"Deleted {result.rowcount} orphaned tf-copr associations")

        # Delete OpenScanHub scans that reference orphaned copr build targets
        stmt = delete(OSHScanModel).where(OSHScanModel.copr_build_target_id.in_(orphaned))
        result = conn.execute(stmt)
        logger.info(f"Deleted {result.rowcount} orphaned OSHScanModel records")

        stmt = delete(CoprBuildTargetModel).where(CoprBuildTargetModel.id.in_(orphaned))
        result = conn.execute(stmt)
        logger.info(f"Deleted {result.rowcount} orphaned CoprBuildTargets")

        # Delete KojiBuildTargets and tf-koji associations which don't belong to a pipeline
        orphaned_koji = (
            select(distinct(KojiBuildTargetModel.id))
            .outerjoin_from(
                KojiBuildGroupModel,
                KojiBuildTargetModel,
                KojiBuildTargetModel.koji_build_group_id == KojiBuildGroupModel.id,
            )
            .outerjoin(
                PipelineModel,
                PipelineModel.koji_build_group_id == KojiBuildGroupModel.id,
            )
            .filter(PipelineModel.id == None)  # noqa
        )
        stmt = delete(tf_koji_association_table).where(
            tf_koji_association_table.c.koji_id.in_(orphaned_koji),
        )
        result = conn.execute(stmt)
        logger.info(f"Deleted {result.rowcount} orphaned tf-koji associations")

        stmt = delete(KojiBuildTargetModel).where(KojiBuildTargetModel.id.in_(orphaned_koji))
        result = conn.execute(stmt)
        logger.info(f"Deleted {result.rowcount} orphaned KojiBuildTargets")

        # Delete TFTTestRunTargets and their associations
        logger.info("Working on TFTTestRunTargetModel...")
        orphaned_tft = (
            select(distinct(TFTTestRunTargetModel.id))
            .outerjoin_from(
                TFTTestRunGroupModel,
                TFTTestRunTargetModel,
                TFTTestRunTargetModel.tft_test_run_group_id == TFTTestRunGroupModel.id,
            )
            .outerjoin(
                PipelineModel,
                PipelineModel.test_run_group_id == TFTTestRunGroupModel.id,
            )
            .filter(PipelineModel.id == None)  # noqa
        )

        # Delete associations referencing these orphaned TFT targets
        stmt = delete(tf_copr_association_table).where(
            tf_copr_association_table.c.tft_id.in_(orphaned_tft)
        )
        result = conn.execute(stmt)
        logger.info(f"Deleted {result.rowcount} tf-copr associations (by tft_id)")

        stmt = delete(tf_koji_association_table).where(
            tf_koji_association_table.c.tft_id.in_(orphaned_tft)
        )
        result = conn.execute(stmt)
        logger.info(f"Deleted {result.rowcount} tf-koji associations (by tft_id)")

        stmt = delete(TFTTestRunTargetModel).where(TFTTestRunTargetModel.id.in_(orphaned_tft))
        result = conn.execute(stmt)
        logger.info(f"Deleted {result.rowcount} orphaned TFTTestRunTargets")

        # Delete SyncReleaseTargets and their associations
        logger.info("Working on SyncReleaseTargetModel...")
        orphaned_sync = (
            select(distinct(SyncReleaseTargetModel.id))
            .outerjoin_from(
                SyncReleaseModel,
                SyncReleaseTargetModel,
                SyncReleaseTargetModel.sync_release_id == SyncReleaseModel.id,
            )
            .outerjoin(
                PipelineModel,
                PipelineModel.sync_release_run_id == SyncReleaseModel.id,
            )
            .filter(PipelineModel.id == None)  # noqa
        )

        # Delete associations referencing these orphaned SyncRelease targets
        stmt = delete(sync_release_pr_association_table).where(
            sync_release_pr_association_table.c.sync_release_target_id.in_(orphaned_sync)
        )
        result = conn.execute(stmt)
        logger.info(f"Deleted {result.rowcount} sync-release-pr associations")

        stmt = delete(SyncReleaseTargetModel).where(SyncReleaseTargetModel.id.in_(orphaned_sync))
        result = conn.execute(stmt)
        logger.info(f"Deleted {result.rowcount} orphaned SyncReleaseTargets")

        # Delete remaining target types (BodhiUpdate and KojiTagRequest) using generic logic
        attr = [
            (  # type: ignore
                BodhiUpdateTargetModel,
                BodhiUpdateGroupModel,
                PipelineModel.bodhi_update_group_id,
                BodhiUpdateTargetModel.bodhi_update_group_id,
            ),
            (  # type: ignore
                KojiTagRequestTargetModel,
                KojiTagRequestGroupModel,
                PipelineModel.koji_tag_request_group_id,
                KojiTagRequestTargetModel.koji_tag_request_group_id,
            ),
        ]
        for target_m, group_m, id_f, model_group_id in attr:  # type: ignore
            logger.info(f"Working on {target_m.__name__}...")
            orphaned = (
                select(distinct(target_m.id))  # type: ignore
                .outerjoin_from(group_m, target_m, model_group_id == group_m.id)  # type: ignore
                .outerjoin(PipelineModel, id_f == group_m.id)  # type: ignore
                .filter(PipelineModel.id == None)  # noqa
            )
            stmt = delete(target_m).where(target_m.id.in_(orphaned))  # type: ignore
            result = conn.execute(stmt)
            logger.info(f"Deleted {result.rowcount} orphaned {target_m.__name__}")

        # Delete orphaned Groups
        groups = [  # type: ignore
            (
                CoprBuildGroupModel,
                CoprBuildTargetModel,
                CoprBuildTargetModel.copr_build_group_id,
                PipelineModel.copr_build_group_id,
            ),
            (
                KojiBuildGroupModel,
                KojiBuildTargetModel,
                KojiBuildTargetModel.koji_build_group_id,
                PipelineModel.koji_build_group_id,
            ),
            (
                TFTTestRunGroupModel,
                TFTTestRunTargetModel,
                TFTTestRunTargetModel.tft_test_run_group_id,
                PipelineModel.test_run_group_id,
            ),
            (
                BodhiUpdateGroupModel,
                BodhiUpdateTargetModel,
                BodhiUpdateTargetModel.bodhi_update_group_id,
                PipelineModel.bodhi_update_group_id,
            ),
            (
                KojiTagRequestGroupModel,
                KojiTagRequestTargetModel,
                KojiTagRequestTargetModel.koji_tag_request_group_id,
                PipelineModel.koji_tag_request_group_id,
            ),
        ]
        for group, target, target_group_id, pipeline_group_id in groups:  # type: ignore
            orphaned_groups = (
                select(distinct(group.id))  # type: ignore
                .outerjoin(target, group.id == target_group_id)  # type: ignore
                .outerjoin(PipelineModel, pipeline_group_id == group.id)  # type: ignore
                .filter(target.id == None)  # type: ignore  # noqa
                .filter(PipelineModel.id == None)  # noqa
            )
            stmt = delete(group).where(group.id.in_(orphaned_groups))  # type: ignore
            result = conn.execute(stmt)
            logger.info(f"Deleted {result.rowcount} orphaned {group.__name__}")

        # Delete orphaned project event trigger objects
        trigger_types = [
            (ProjectEventModelType.pull_request, PullRequestModel),
            (ProjectEventModelType.branch_push, GitBranchModel),
            (ProjectEventModelType.release, ProjectReleaseModel),
            (ProjectEventModelType.issue, IssueModel),
        ]
        for event_type, trigger_model in trigger_types:
            # Find trigger objects not referenced by any ProjectEventModel
            project_events = (
                select(ProjectEventModel).filter(ProjectEventModel.type == event_type).subquery()
            )
            orphaned_triggers = (
                select(trigger_model.id)  # type: ignore
                .outerjoin(project_events, trigger_model.id == project_events.c.event_id)  # type: ignore
                .filter(project_events.c.event_id == None)  # noqa
            )
            stmt = delete(trigger_model).where(trigger_model.id.in_(orphaned_triggers))  # type: ignore
            result = conn.execute(stmt)
            logger.info(f"Deleted {result.rowcount} orphaned {trigger_model.__name__}")

        # Delete orphaned GitProjectModel records
        referenced_projects = union(
            select(PullRequestModel.project_id),
            select(GitBranchModel.project_id),
            select(ProjectReleaseModel.project_id),
            select(IssueModel.project_id),
            select(ProjectAuthenticationIssueModel.project_id),
            select(SyncReleasePullRequestModel.project_id),
        )
        stmt = delete(GitProjectModel).where(
            GitProjectModel.id.not_in(referenced_projects),
        )
        result = conn.execute(stmt)
        logger.info(f"Deleted {result.rowcount} orphaned GitProjectModels")

    logger.info("Finished deleting old data from database")
