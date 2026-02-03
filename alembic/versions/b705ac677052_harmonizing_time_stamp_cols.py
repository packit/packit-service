"""Harmonizing time stamp cols

Build submission time columns were synced across build target models.

Revision ID: b705ac677052
Revises: d408de018a66
Create Date: 2026-02-03 10:07:02.345848

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "b705ac677052"
down_revision = "d408de018a66"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("copr_build_targets", "build_submitted_time", new_column_name="submitted_time")
    op.alter_column("koji_build_targets", "build_submitted_time", new_column_name="submitted_time")
    op.alter_column("srpm_builds", "build_submitted_time", new_column_name="submitted_time")
    op.alter_column(
        "vm_image_build_targets", "build_submitted_time", new_column_name="submitted_time"
    )


def downgrade():
    op.alter_column("copr_build_targets", "submitted_time", new_column_name="build_submitted_time")
    op.alter_column("koji_build_targets", "submitted_time", new_column_name="build_submitted_time")
    op.alter_column("srpm_builds", "submitted_time", new_column_name="build_submitted_time")
    op.alter_column(
        "vm_image_build_targets", "submitted_time", new_column_name="build_submitted_time"
    )
