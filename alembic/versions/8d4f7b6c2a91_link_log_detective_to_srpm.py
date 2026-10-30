"""Link Log Detective analyses to Copr SRPM builds.

Revision ID: 8d4f7b6c2a91
Revises: af67c621d3e0
"""

import sqlalchemy as sa

from alembic import op

revision = "8d4f7b6c2a91"
down_revision = "af67c621d3e0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("log_detective_run", sa.Column("srpm_build_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_log_detective_run_srpm_build_id",
        "log_detective_run",
        "srpm_builds",
        ["srpm_build_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade():
    op.drop_constraint(
        "fk_log_detective_run_srpm_build_id", "log_detective_run", type_="foreignkey"
    )
    op.drop_column("log_detective_run", "srpm_build_id")
