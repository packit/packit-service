"""Update TFResult enum

Revision ID: b58f55c0112c
Revises: 5b57c5409325
Create Date: 2023-05-09 09:49:35.867918

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "b58f55c0112c"
down_revision = "5b57c5409325"
branch_labels = None
depends_on = None


def upgrade():
    # 'complete' is missing from 'testingfarmresult'
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE testingfarmresult ADD VALUE 'complete'")


def downgrade():
    pass
