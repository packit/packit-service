"""Persist Log Detective API request inputs and acceptance.

Revision ID: af67c621d3e0
Revises: b4e11a52ea52
"""

import sqlalchemy as sa

from alembic import op

revision = "af67c621d3e0"
down_revision = "b4e11a52ea52"
branch_labels = None
depends_on = None


def upgrade():
    """Add nullable request inputs and acceptance time for API runs."""
    op.add_column(
        "log_detective_run", sa.Column("selected_logs", sa.JSON(none_as_null=True), nullable=True)
    )
    op.add_column("log_detective_run", sa.Column("analysis_commentary", sa.Text(), nullable=True))
    op.add_column("log_detective_run", sa.Column("accepted_time", sa.DateTime(), nullable=True))


def downgrade():
    """Remove API request fields after active requests have been drained."""
    op.drop_column("log_detective_run", "accepted_time")
    op.drop_column("log_detective_run", "analysis_commentary")
    op.drop_column("log_detective_run", "selected_logs")
