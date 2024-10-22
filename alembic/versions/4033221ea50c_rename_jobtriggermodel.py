"""Renamed JobTriggerModel in ProjectEventModel

Revision ID: 4033221ea50c
Revises: b58f55c0112c
Create Date: 2023-05-24 08:16:53.631139

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "4033221ea50c"
down_revision = "b58f55c0112c"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE jobtriggertype RENAME TO projecteventtype")
    op.rename_table("job_triggers", "project_events")
    op.alter_column("project_events", "trigger_id", new_column_name="event_id")

    op.execute("ALTER SEQUENCE job_triggers_id_seq RENAME TO project_events_id_seq")
    op.execute("ALTER INDEX job_triggers_pkey RENAME TO project_events_pkey")
    op.execute(
        "ALTER INDEX ix_job_triggers_trigger_id RENAME TO ix_project_events_event_id",
    )

    op.alter_column("pipelines", "job_trigger_id", new_column_name="project_event_id")
    op.drop_constraint("runs_job_trigger_id_fkey", "pipelines", type_="foreignkey")
    op.create_foreign_key(
        "runs_project_event_id_fkey",
        "pipelines",
        "project_events",
        ["project_event_id"],
        ["id"],
    )


def downgrade():
    op.execute("ALTER TYPE projecteventtype RENAME TO jobtriggertype ")
    op.rename_table("project_events", "job_triggers")
    op.alter_column("job_triggers", "event_id", new_column_name="trigger_id")

    op.execute("ALTER SEQUENCE project_events_id_seq RENAME TO job_triggers_id_seq")
    op.execute("ALTER INDEX project_events_pkey RENAME TO job_triggers_pkey")
    op.execute(
        "ALTER INDEX ix_project_events_event_id RENAME TO ix_job_triggers_trigger_id",
    )

    op.alter_column("pipelines", "project_event_id", new_column_name="job_trigger_id")
    op.drop_constraint("runs_project_event_id_fkey", "pipelines", type_="foreignkey")
    op.create_foreign_key(
        "runs_job_trigger_id_fkey",
        "pipelines",
        "job_triggers",
        ["job_trigger_id"],
        ["id"],
    )
