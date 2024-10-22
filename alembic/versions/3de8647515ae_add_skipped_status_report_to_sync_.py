"""add skipped status report to sync release jobs

Revision ID: 3de8647515ae
Revises: 99841c3f8ba8
Create Date: 2023-11-13 11:29:10.383794

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "3de8647515ae"
down_revision = "99841c3f8ba8"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE syncreleasetargetstatus ADD VALUE 'skipped'")


def downgrade():
    op.execute(
        "ALTER TYPE syncreleasetargetstatus RENAME TO syncreleasetargetstatus_old",
    )
    op.execute(
        "CREATE TYPE syncreleasetargetstatus AS ENUM "
        "('queued', 'running', 'error', 'retry', 'submitted')",
    )
    op.execute(
        "ALTER TABLE sync_release_run_targets "
        "ALTER COLUMN status TYPE syncreleasetargetstatus USING status::syncreleasetargetstatus",
    )
    op.execute("DROP TYPE syncreleasetargetstatus_old")
