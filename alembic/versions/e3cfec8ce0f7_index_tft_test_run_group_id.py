"""Index tft_test_run_group_id

Based on the Sentry's backend insights, it appears that in the last 90d
we have spent 16.82min running the query that filters out rows based on
the «test run group id», therefore index that column to improve the speed
of the queries done.

Revision ID: e3cfec8ce0f7
Revises: d625d6c1122f
Create Date: 2025-01-17 16:06:29.833622

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "e3cfec8ce0f7"
down_revision = "d625d6c1122f"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        op.f("ix_tft_test_run_targets_tft_test_run_group_id"),
        "tft_test_run_targets",
        ["tft_test_run_group_id"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        op.f("ix_tft_test_run_targets_tft_test_run_group_id"), table_name="tft_test_run_targets"
    )
