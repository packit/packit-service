"""Bodhi updates from sidetags


Revision ID: 64a51b961c28
Revises: e05e1b04de87
Create Date: 2024-07-23 14:47:21.313545

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "64a51b961c28"
down_revision = "e05e1b04de87"
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.alter_column("bodhi_update_targets", "koji_nvr", new_column_name="koji_nvrs")
    op.add_column(
        "bodhi_update_targets",
        sa.Column("sidetag", sa.String(), nullable=True),
    )
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column("bodhi_update_targets", "sidetag")
    op.alter_column("bodhi_update_targets", "koji_nvrs", new_column_name="koji_nvr")
    # ### end Alembic commands ###
