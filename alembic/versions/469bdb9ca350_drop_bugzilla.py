"""Drop Bugzilla

Revision ID: 469bdb9ca350
Revises: 482dc393678a
Create Date: 2022-08-18 15:51:00.416417

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "469bdb9ca350"
down_revision = "482dc393678a"
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index("ix_bugzillas_bug_id", table_name="bugzillas")
    op.drop_table("bugzillas")
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "bugzillas",
        sa.Column("id", sa.INTEGER(), autoincrement=True, nullable=False),
        sa.Column("bug_id", sa.INTEGER(), autoincrement=False, nullable=True),
        sa.Column("bug_url", sa.VARCHAR(), autoincrement=False, nullable=True),
        sa.Column("pull_request_id", sa.INTEGER(), autoincrement=False, nullable=True),
        sa.ForeignKeyConstraint(
            ["pull_request_id"],
            ["pull_requests.id"],
            name="bugzillas_pull_request_id_fkey",
        ),
        sa.PrimaryKeyConstraint("id", name="bugzillas_pkey"),
    )
    op.create_index("ix_bugzillas_bug_id", "bugzillas", ["bug_id"], unique=False)
    # ### end Alembic commands ###