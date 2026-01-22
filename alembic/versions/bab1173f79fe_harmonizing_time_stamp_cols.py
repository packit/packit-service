"""Harmonizing time stamp cols

Revision ID: bab1173f79fe
Revises: d408de018a66
Create Date: 2026-01-29 09:54:18.881000

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "bab1173f79fe"
down_revision = "d408de018a66"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("copr_build_targets", "build_submitted_time", new_column_name="submitted_time")


def downgrade():
    op.alter_column("copr_build_targets", "submitted_time", new_column_name="build_submitted_time")
