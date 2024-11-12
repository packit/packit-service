"""fix(tf): fix typo in canceled-request

Revision ID: 4387d6ab90e9
Revises: 9f84a235ccbf
Create Date: 2024-11-12 11:46:17.354580

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "4387d6ab90e9"
down_revision = "9f84a235ccbf"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE testingfarmresult ADD VALUE 'cancel_requested'")


def downgrade():
    pass
