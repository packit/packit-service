"""Add canceled state to BuildStatus

Revision ID: 48b09a73cf77
Revises: eadd57289c17
Create Date: 2025-04-23 13:44:56.196348

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "48b09a73cf77"
down_revision = "eadd57289c17"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE buildstatus ADD VALUE 'canceled'")


def downgrade():
    pass
