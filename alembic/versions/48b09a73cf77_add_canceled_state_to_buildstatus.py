"""Add canceled state to BuildStatus

Revision ID: 48b09a73cf77
Revises: 16219ebce6cb
Create Date: 2025-04-23 13:44:56.196348

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "48b09a73cf77"
down_revision = "16219ebce6cb"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE buildstatus ADD VALUE 'canceled'")


def downgrade():
    pass
