"""Adjust KojiBuildTargetModel

Revision ID: 99841c3f8ba8
Revises: db64a37ff1c6
Create Date: 2023-11-02 15:19:28.923208

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "99841c3f8ba8"
down_revision = "db64a37ff1c6"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("koji_build_targets", "build_id", new_column_name="task_id")
    op.execute(
        "ALTER INDEX ix_koji_build_targets_build_id RENAME TO ix_koji_build_targets_task_id",
    )
    op.add_column(
        "koji_build_targets",
        sa.Column("build_logs_urls", sa.JSON(), nullable=True),
    )
    # we can drop this, the logs URLs pointed to invalid URLs
    op.drop_column("koji_build_targets", "build_logs_url")


def downgrade():
    op.alter_column("koji_build_targets", "task_id", new_column_name="build_id")
    op.execute(
        "ALTER INDEX ix_koji_build_targets_task_id RENAME TO ix_koji_build_targets_build_id",
    )
    op.add_column(
        "koji_build_targets",
        sa.Column("build_logs_url", sa.VARCHAR(), autoincrement=False, nullable=True),
    )
    op.drop_column("koji_build_targets", "build_logs_urls")
