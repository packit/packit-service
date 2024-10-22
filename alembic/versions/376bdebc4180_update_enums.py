"""Update enums

Revision ID: 376bdebc4180
Revises: 3aa397e3adac
Create Date: 2021-11-10 17:29:41.918859

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "376bdebc4180"
down_revision = "3aa397e3adac"
branch_labels = None
depends_on = None


def upgrade():
    # 'denied' is missing from 'allowliststatus'
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE allowliststatus ADD VALUE 'denied'")

    # 'queued', 'skipped', 'unknown', 'needs_inspection' are missing from
    # 'testingfarmresult'
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE testingfarmresult ADD VALUE 'queued'")
        op.execute("ALTER TYPE testingfarmresult ADD VALUE 'skipped'")
        op.execute("ALTER TYPE testingfarmresult ADD VALUE 'unknown'")
        op.execute("ALTER TYPE testingfarmresult ADD VALUE 'needs_inspection'")


def downgrade():
    # Let's not write a downgrade here, the values above should be
    # in the DB for some time already.
    pass
