"""Add explicit mapping of TF runs to Koji builds

Revision ID: 16219ebce6cb
Revises: eadd57289c17
Create Date: 2025-04-15 06:17:58.237601

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "16219ebce6cb"
down_revision = "eadd57289c17"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tf_koji_build_association_table",
        sa.Column("koji_id", sa.Integer(), nullable=False),
        sa.Column("tft_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["koji_id"], ["koji_build_targets.id"]),
        sa.ForeignKeyConstraint(["tft_id"], ["tft_test_run_targets.id"]),
        sa.PrimaryKeyConstraint("koji_id", "tft_id"),
    )


def downgrade():
    op.drop_table("tf_koji_build_association_table")
