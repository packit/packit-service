"""Pull from upstream releated adjustements

Revision ID: f5792581522c
Revises: e907a93de3ef
Create Date: 2022-11-29 13:33:58.160534

"""

import sqlalchemy as sa

# revision identifiers, used by Alembic.
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "f5792581522c"
down_revision = "e907a93de3ef"
branch_labels = None
depends_on = None


def upgrade():
    # rename propose_downstream_runs table to sync_release_runs
    op.rename_table("propose_downstream_runs", "sync_release_runs")
    op.execute(
        "ALTER SEQUENCE propose_downstream_runs_id_seq RENAME TO sync_release_runs_id_seq",
    )
    op.execute(
        "ALTER INDEX propose_downstream_runs_pkey RENAME TO sync_release_runs_pkey",
    )

    # rename proposedownstreamstatus to syncreleasestatus
    op.execute("ALTER TYPE proposedownstreamstatus RENAME TO syncreleasestatus")

    # add job_type column to sync_release_runs
    sync_release_job_type = postgresql.ENUM(
        "pull_from_upstream",
        "propose_downstream",
        name="syncreleasejobtype",
    )
    sync_release_job_type.create(op.get_bind())
    op.add_column(
        "sync_release_runs",
        sa.Column(
            "job_type",
            sa.Enum(
                "pull_from_upstream",
                "propose_downstream",
                name="syncreleasejobtype",
            ),
            nullable=True,
        ),
    )
    op.execute("UPDATE sync_release_runs SET job_type = 'propose_downstream'")

    # rename propose_downstream_run_targets table to sync_release_run_targets
    op.rename_table("propose_downstream_run_targets", "sync_release_run_targets")
    op.execute(
        "ALTER SEQUENCE propose_downstream_run_targets_id_seq RENAME TO "
        "sync_release_run_targets_id_seq",
    )
    op.execute(
        "ALTER INDEX propose_downstream_run_targets_pkey RENAME TO sync_release_run_targets_pkey",
    )

    # rename proposedownstreamtargetstatus to syncreleasetargetstatus
    op.execute(
        "ALTER TYPE proposedownstreamtargetstatus RENAME TO syncreleasetargetstatus",
    )

    # rename foreign key in sync_release_run_targets
    op.alter_column(
        "sync_release_run_targets",
        "propose_downstream_id",
        new_column_name="sync_release_id",
    )
    op.drop_constraint(
        "propose_downstream_run_targets_propose_downstream_id_fkey",
        "sync_release_run_targets",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "sync_release_run_targets_sync_release_id_fkey",
        "sync_release_run_targets",
        "sync_release_runs",
        ["sync_release_id"],
        ["id"],
    )

    # rename foreign key in pipelines
    op.alter_column(
        "pipelines",
        "propose_downstream_run_id",
        new_column_name="sync_release_run_id",
    )
    op.drop_constraint(
        "pipelines_propose_downstream_run_id_fkey",
        "pipelines",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "pipelines_sync_release_run_id_fkey",
        "pipelines",
        "sync_release_runs",
        ["sync_release_run_id"],
        ["id"],
    )

    # rename the index
    op.execute(
        "ALTER INDEX ix_pipelines_propose_downstream_run_id RENAME TO "
        "ix_pipelines_sync_release_run_id",
    )


def downgrade():
    # remove job_type column from sync_release_runs
    op.drop_column("sync_release_runs", "job_type")
    sync_release_job_type = postgresql.ENUM(
        "pull_from_upstream",
        "propose_downstream",
        name="syncreleasejobtype",
    )
    sync_release_job_type.drop(op.get_bind())

    # rename syncreleasestatus to proposedownstreamstatus
    op.execute("ALTER TYPE syncreleasestatus RENAME TO proposedownstreamstatus")

    # rename sync_release_runs table to propose_downstream_runs
    op.rename_table("sync_release_runs", "propose_downstream_runs")
    op.execute(
        "ALTER SEQUENCE sync_release_runs_id_seq RENAME TO propose_downstream_runs_id_seq",
    )
    op.execute(
        "ALTER INDEX sync_release_runs_pkey RENAME TO propose_downstream_runs_pkey",
    )

    # rename  table sync_release_run_targets to propose_downstream_run_targets
    op.rename_table("sync_release_run_targets", "propose_downstream_run_targets")
    op.execute(
        "ALTER SEQUENCE sync_release_run_targets_id_seq RENAME TO "
        "propose_downstream_run_targets_id_seq",
    )
    op.execute(
        "ALTER INDEX sync_release_run_targets_pkey RENAME TO propose_downstream_run_targets_pkey",
    )
    # rename syncreleasetargetstatus to proposedownstreamtargetstatus
    op.execute(
        "ALTER TYPE syncreleasetargetstatus RENAME TO proposedownstreamtargetstatus",
    )

    # rename foreign key in propose_downstream_run_targets
    op.alter_column(
        "propose_downstream_run_targets",
        "sync_release_id",
        new_column_name="propose_downstream_id",
    )
    op.drop_constraint(
        "sync_release_run_targets_sync_release_id_fkey",
        "propose_downstream_run_targets",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "propose_downstream_run_targets_propose_downstream_id_fkey",
        "propose_downstream_run_targets",
        "propose_downstream_runs",
        ["propose_downstream_id"],
        ["id"],
    )

    # rename foreign key in pipelines
    op.alter_column(
        "pipelines",
        "sync_release_run_id",
        new_column_name="propose_downstream_run_id",
    )
    op.drop_constraint(
        "pipelines_sync_release_run_id_fkey",
        "pipelines",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "pipelines_propose_downstream_run_id_fkey",
        "pipelines",
        "propose_downstream_runs",
        ["propose_downstream_run_id"],
        ["id"],
    )

    # rename the index
    op.execute(
        "ALTER INDEX ix_pipelines_sync_release_run_id RENAME TO "
        "ix_pipelines_propose_downstream_run_id",
    )
