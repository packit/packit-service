"""initial schema

Revision ID: 258490f6e667
Revises:
Create Date: 2020-01-08 15:18:39.722683

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "258490f6e667"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "github_projects",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("namespace", sa.String),
        sa.Column("repo_name", sa.String),
    )


def downgrade():
    op.drop_table("github_projects")
