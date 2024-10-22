"""initial schema

Revision ID: 258490f6e667
Revises:
Create Date: 2020-01-08 15:18:39.722683

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "258490f6e667"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "git_projects",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("namespace", sa.String, index=True),
        sa.Column("repo_name", sa.String, index=True),
    )
    op.create_table(
        "pull_requests",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("pr_id", sa.Integer, index=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("git_projects.id")),
        sa.ForeignKeyConstraint(
            ("project_id",),
            ["git_projects.id"],
        ),
    )
    op.create_table(
        "srpm_builds",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("logs", sa.Text),
    )
    op.create_table(
        "copr_builds",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("build_id", sa.String, index=True),
        sa.Column("pr_id", sa.Integer, sa.ForeignKey("pull_requests.id")),
        sa.ForeignKeyConstraint(
            ("pr_id",),
            ["pull_requests.id"],
        ),
        sa.Column("srpm_build_id", sa.Integer, sa.ForeignKey("srpm_builds.id")),
        sa.ForeignKeyConstraint(
            ("srpm_build_id",),
            ["srpm_builds.id"],
        ),
        sa.Column("logs", sa.Text),
        sa.Column("commit_sha", sa.String),
        sa.Column("status", sa.String),
        sa.Column("target", sa.String),
        sa.Column("web_url", sa.String),
        sa.Column("build_logs_url", sa.String),
        sa.Column("data", sa.JSON),
    )


def downgrade():
    op.drop_table("git_projects")
    op.drop_table("pull_requests")
    op.drop_table("copr_builds")
    op.drop_table("srpm_builds")
