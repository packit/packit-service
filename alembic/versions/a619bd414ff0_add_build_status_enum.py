"""Add build status enum

Revision ID: a619bd414ff0
Revises: 320c791746f0
Create Date: 2022-09-21 08:12:47.752110

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "a619bd414ff0"
down_revision = "320c791746f0"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE TYPE buildstatus AS ENUM "
        "('success', 'pending', 'failure', 'error', 'waiting_for_srpm')",
    )
    op.execute(
        "ALTER TABLE copr_build_targets "
        "ALTER COLUMN status TYPE buildstatus USING status::buildstatus",
    )
    op.execute(
        "ALTER TABLE srpm_builds ALTER COLUMN status TYPE buildstatus USING status::buildstatus",
    )


def downgrade():
    op.execute("ALTER TABLE copr_build_targets ALTER COLUMN status TYPE VARCHAR")
    op.execute("ALTER TABLE srpm_builds ALTER COLUMN status TYPE VARCHAR")
    op.execute("DROP TYPE buildstatus")
