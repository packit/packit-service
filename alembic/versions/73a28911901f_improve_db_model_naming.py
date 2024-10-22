"""Improve DB model naming

Revision ID: 73a28911901f
Revises: 0ad4d1c2a2d8
Create Date: 2022-02-02 09:45:20.907000

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "73a28911901f"
down_revision = "0ad4d1c2a2d8"
branch_labels = None
depends_on = None


def upgrade():
    op.rename_table("build_triggers", "job_triggers")
    op.execute("ALTER SEQUENCE build_triggers_id_seq RENAME TO job_triggers_id_seq")
    op.execute("ALTER INDEX build_triggers_pkey RENAME TO job_triggers_pkey")
    op.execute("ALTER TYPE jobtriggermodeltype RENAME TO jobtriggertype")

    op.rename_table("runs", "pipelines")
    op.execute("ALTER SEQUENCE runs_id_seq RENAME TO pipelines_id_seq")
    op.execute("ALTER INDEX runs_pkey RENAME TO pipelines_pkey")

    op.rename_table("copr_builds", "copr_build_targets")
    op.execute("ALTER SEQUENCE copr_builds_id_seq RENAME TO copr_build_targets_id_seq")
    op.execute("ALTER INDEX copr_builds_pkey RENAME TO copr_build_targets_pkey")
    op.execute(
        "ALTER INDEX ix_copr_builds_build_id RENAME TO ix_copr_build_targets_build_id",
    )

    op.rename_table("koji_builds", "koji_build_targets")
    op.execute("ALTER SEQUENCE koji_builds_id_seq RENAME TO koji_build_targets_id_seq")
    op.execute("ALTER INDEX koji_builds_pkey RENAME TO koji_build_targets_pkey")
    op.execute(
        "ALTER INDEX ix_koji_builds_build_id RENAME TO ix_koji_build_targets_build_id",
    )

    op.rename_table("tft_test_runs", "tft_test_run_targets")
    op.execute(
        "ALTER SEQUENCE tft_test_runs_id_seq RENAME TO tft_test_run_targets_id_seq",
    )
    op.execute("ALTER INDEX tft_test_runs_pkey RENAME TO tft_test_run_targets_pkey")
    op.execute(
        "ALTER INDEX ix_tft_test_runs_pipeline_id RENAME TO ix_tft_test_run_targets_pipeline_id",
    )


def downgrade():
    op.rename_table("job_triggers", "build_triggers")
    op.execute("ALTER SEQUENCE job_triggers_id_seq RENAME TO build_triggers_id_seq")
    op.execute("ALTER INDEX job_triggers_pkey RENAME TO build_triggers_pkey")
    op.execute("ALTER TYPE jobtriggertype RENAME TO jobtriggermodeltype")

    op.rename_table("pipelines", "runs")
    op.execute("ALTER SEQUENCE pipelines_id_seq RENAME TO runs_id_seq")
    op.execute("ALTER INDEX pipelines_pkey RENAME TO runs_pkey")

    op.rename_table("copr_build_targets", "copr_builds")
    op.execute("ALTER SEQUENCE copr_build_targets_id_seq RENAME TO copr_builds_id_seq")
    op.execute("ALTER INDEX copr_build_targets_pkey RENAME TO copr_builds_pkey")
    op.execute(
        "ALTER INDEX ix_copr_build_targets_build_id RENAME TO ix_copr_builds_build_id",
    )

    op.rename_table("koji_build_targets", "koji_builds")
    op.execute("ALTER SEQUENCE koji_build_targets_id_seq RENAME TO koji_builds_id_seq")
    op.execute("ALTER INDEX koji_build_targets_pkey RENAME TO koji_builds_pkey")
    op.execute(
        "ALTER INDEX ix_koji_build_targets_build_id RENAME TO ix_koji_builds_build_id",
    )

    op.rename_table("tft_test_run_targets", "tft_test_runs")
    op.execute(
        "ALTER SEQUENCE tft_test_run_targets_id_seq RENAME TO tft_test_runs_id_seq",
    )
    op.execute("ALTER INDEX tft_test_run_targets_pkey RENAME TO tft_test_runs_pkey")
    op.execute(
        "ALTER INDEX ix_tft_test_run_targets_pipeline_id RENAME TO ix_tft_test_runs_pipeline_id",
    )
