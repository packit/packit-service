"""Add vm image builds table

Revision ID: e907a93de3ef
Revises: a619bd414ff0
Create Date: 2022-11-18 08:39:32.198275

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e907a93de3ef"
down_revision = "a619bd414ff0"
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "vm_image_build_targets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("build_id", sa.String(), nullable=True),
        sa.Column("project_url", sa.String(), nullable=True),
        sa.Column("project_name", sa.String(), nullable=True),
        sa.Column("owner", sa.String(), nullable=True),
        sa.Column("commit_sha", sa.String(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "success",
                "pending",
                "building",
                "uploading",
                "registering",
                "failure",
                "error",
                name="vmimagebuildstatus",
            ),
            nullable=True,
        ),
        sa.Column("target", sa.String(), nullable=True),
        sa.Column("pr_id", sa.String(), nullable=True),
        sa.Column("task_accepted_time", sa.DateTime(), nullable=True),
        sa.Column("build_submitted_time", sa.DateTime(), nullable=True),
        sa.Column("build_start_time", sa.DateTime(), nullable=True),
        sa.Column("build_finished_time", sa.DateTime(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_vm_image_build_targets_build_id"),
        "vm_image_build_targets",
        ["build_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_vm_image_build_targets_commit_sha"),
        "vm_image_build_targets",
        ["commit_sha"],
        unique=False,
    )
    op.add_column(
        "pipelines",
        sa.Column("vm_image_build_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        op.f("ix_pipelines_vm_image_build_id"),
        "pipelines",
        ["vm_image_build_id"],
        unique=False,
    )
    op.create_foreign_key(
        None,
        "pipelines",
        "vm_image_build_targets",
        ["vm_image_build_id"],
        ["id"],
    )
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_constraint(None, "pipelines", type_="foreignkey")
    op.drop_index(op.f("ix_pipelines_vm_image_build_id"), table_name="pipelines")
    op.drop_column("pipelines", "vm_image_build_id")
    op.drop_index(
        op.f("ix_vm_image_build_targets_commit_sha"),
        table_name="vm_image_build_targets",
    )
    op.drop_index(
        op.f("ix_vm_image_build_targets_build_id"),
        table_name="vm_image_build_targets",
    )
    op.drop_table("vm_image_build_targets")
    # ### end Alembic commands ###
