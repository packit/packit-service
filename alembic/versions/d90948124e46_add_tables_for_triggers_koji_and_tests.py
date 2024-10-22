"""Add tables for triggers, koji and tests.

Revision ID: d90948124e46
Revises: dc1beda6749e
Create Date: 2020-03-27 16:22:45.721822

"""

import enum
from collections.abc import Iterable
from typing import TYPE_CHECKING, Optional

import sqlalchemy as sa
from flexmock import flexmock
from sqlalchemy import Enum, ForeignKey, Integer, String, orm
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, relationship
from sqlalchemy.testing import config
from sqlalchemy.testing.schema import Column

from alembic import op

# revision identifiers, used by Alembic.
revision = "d90948124e46"
down_revision = "dc1beda6749e"
branch_labels = None
depends_on = None

# Very hacky but I cannot solve the problem, that `requirements` is None.
flexmock(config).should_receive("requirements").and_return(
    flexmock(foreign_key_ddl=flexmock(enabled_for_config=lambda config: True)),
)

# https://github.com/python/mypy/issues/2477#issuecomment-313984522 ^_^
if TYPE_CHECKING:
    Base = object
else:
    Base = declarative_base()


# Models for upgrade
# We don't know the state of the models in the packit_service/models.py during the update.
# Now, we have full control on it.
# https://stackoverflow.com/
#   questions/24612395/how-do-i-execute-inserts-and-updates-in-an-alembic-upgrade-script


class GitProjectUpgradeModel(Base):
    __tablename__ = "git_projects"
    id = Column(Integer, primary_key=True)
    namespace = Column(String, index=True)
    repo_name = Column(String, index=True)

    pull_requests = relationship("PullRequestUpgradeModel", back_populates="project")

    @classmethod
    def get_or_create(
        cls,
        namespace: str,
        repo_name: str,
        session: Session,
    ) -> "GitProjectUpgradeModel":
        project = (
            session.query(GitProjectUpgradeModel)
            .filter_by(namespace=namespace, repo_name=repo_name)
            .first()
        )
        if not project:
            project = cls()
            project.repo_name = repo_name
            project.namespace = namespace
            session.add(project)
        return project

    def __repr__(self):
        return f"GitProjectUpgradeModel(name={self.namespace}/{self.repo_name})"

    def __str__(self):
        return self.__repr__()


class PullRequestUpgradeModel(Base):
    __tablename__ = "pull_requests"
    id = Column(Integer, primary_key=True)  # our database PK
    pr_id = Column(Integer, index=True)
    project_id = Column(Integer, ForeignKey("git_projects.id"))
    project = relationship("GitProjectUpgradeModel", back_populates="pull_requests")
    copr_builds = relationship("CoprBuildUpgradeModel", back_populates="pr")

    @classmethod
    def get_or_create(
        cls,
        pr_id: int,
        namespace: str,
        repo_name: str,
        session: Session,
    ) -> "PullRequestUpgradeModel":
        project = GitProjectUpgradeModel.get_or_create(
            namespace=namespace,
            repo_name=repo_name,
            session=session,
        )
        pr = (
            session.query(PullRequestUpgradeModel)
            .filter_by(pr_id=pr_id, project_id=project.id)
            .first()
        )
        if not pr:
            pr = PullRequestUpgradeModel()
            pr.pr_id = pr_id
            pr.project_id = project.id
            session.add(pr)
        return pr

    def __repr__(self):
        return f"PullRequestUpgradeModel(id={self.pr_id}, project={self.project})"

    def __str__(self):
        return self.__repr__()


class CoprBuildUpgradeModel(Base):
    __tablename__ = "copr_builds"
    id = Column(Integer, primary_key=True)
    build_id = Column(String, index=True)  # copr build id
    job_trigger_id = Column(Integer, ForeignKey("build_triggers.id"))
    job_trigger = relationship("JobTriggerUpgradeModel", back_populates="copr_builds")

    # Will be removed.
    pr_id = Column(Integer, ForeignKey("pull_requests.id"))
    pr = relationship("PullRequestUpgradeModel", back_populates="copr_builds")

    @classmethod
    def get_all(cls, session: Session) -> Optional[Iterable["CoprBuildUpgradeModel"]]:
        return session.query(CoprBuildUpgradeModel).all()

    def __repr__(self):
        return f"COPRBuildUpgradeModel(id={self.id}, job_trigger={self.job_trigger})"

    def __str__(self):
        return self.__repr__()


class JobTriggerModelType(str, enum.Enum):
    pull_request = "pull_request"
    branch_push = "branch_push"
    release = "release"
    issue = "issue"


class JobTriggerUpgradeModel(Base):
    __tablename__ = "build_triggers"
    id = Column(Integer, primary_key=True)  # our database PK
    type = Column(Enum(JobTriggerModelType))
    trigger_id = Column(Integer)
    copr_builds = relationship("CoprBuildUpgradeModel", back_populates="job_trigger")

    @classmethod
    def get_or_create(
        cls,
        type: JobTriggerModelType,
        trigger_id: int,
        session: Session,
    ) -> "JobTriggerUpgradeModel":
        trigger = (
            session.query(JobTriggerUpgradeModel)
            .filter_by(type=type, trigger_id=trigger_id)
            .first()
        )
        if not trigger:
            trigger = JobTriggerUpgradeModel()
            trigger.type = type
            trigger.trigger_id = trigger_id
            session.add(trigger)
        return trigger

    def __repr__(self):
        return (
            f"JobTriggerUpgradeModel(id={self.pr_id}, "
            f"type={self.type}, "
            f"trigger_id={self.trigger_id})"
        )

    def __str__(self):
        return self.__repr__()


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "build_triggers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "pull_request",
                "branch_push",
                "release",
                "issue",
                name="jobtriggermodeltype",
            ),
            nullable=True,
        ),
        sa.Column("trigger_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "git_branches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["git_projects.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "koji_builds",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("build_id", sa.String(), nullable=True),
        sa.Column("job_trigger_id", sa.Integer(), nullable=True),
        sa.Column("srpm_build_id", sa.Integer(), nullable=True),
        sa.Column("commit_sha", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("target", sa.String(), nullable=True),
        sa.Column("web_url", sa.String(), nullable=True),
        sa.Column("build_logs_url", sa.String(), nullable=True),
        sa.Column("build_submitted_time", sa.DateTime(), nullable=True),
        sa.Column("build_start_time", sa.DateTime(), nullable=True),
        sa.Column("build_finished_time", sa.DateTime(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["job_trigger_id"],
            ["build_triggers.id"],
        ),
        sa.ForeignKeyConstraint(
            ["srpm_build_id"],
            ["srpm_builds.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_koji_builds_build_id"),
        "koji_builds",
        ["build_id"],
        unique=False,
    )
    op.create_table(
        "project_issues",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("issue_id", sa.Integer(), nullable=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["git_projects.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_project_issues_issue_id"),
        "project_issues",
        ["issue_id"],
        unique=False,
    )
    op.create_table(
        "project_releases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tag_name", sa.String(), nullable=True),
        sa.Column("commit_hash", sa.String(), nullable=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["git_projects.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "tft_test_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pipeline_id", sa.String(), nullable=True),
        sa.Column("job_trigger_id", sa.Integer(), nullable=True),
        sa.Column("commit_sha", sa.String(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "new",
                "passed",
                "failed",
                "error",
                "running",
                name="testingfarmresult",
            ),
            nullable=True,
        ),
        sa.Column("target", sa.String(), nullable=True),
        sa.Column("web_url", sa.String(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["job_trigger_id"],
            ["build_triggers.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_tft_test_runs_pipeline_id"),
        "tft_test_runs",
        ["pipeline_id"],
        unique=False,
    )
    op.add_column(
        "copr_builds",
        sa.Column("job_trigger_id", sa.Integer(), nullable=True),
    )
    op.drop_constraint("copr_builds_pr_id_fkey1", "copr_builds", type_="foreignkey")
    op.create_foreign_key(
        None,
        "copr_builds",
        "build_triggers",
        ["job_trigger_id"],
        ["id"],
    )

    # ### start of data migration, pause the alembic auto-generate ###

    bind = op.get_bind()
    session = orm.Session(bind=bind)

    for copr_build in CoprBuildUpgradeModel.get_all(session=session):
        trigger = JobTriggerUpgradeModel.get_or_create(
            type=JobTriggerModelType.pull_request,
            trigger_id=copr_build.pr.id,
            session=session,
        )
        copr_build.job_trigger = trigger
        session.add(trigger)

    session.commit()

    # ### end of data migration, continue with alembic auto-generate ###

    op.drop_column("copr_builds", "pr_id")
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column(
        "copr_builds",
        sa.Column("pr_id", sa.INTEGER(), autoincrement=False, nullable=True),
    )
    op.drop_constraint(None, "copr_builds", type_="foreignkey")
    op.create_foreign_key(
        "copr_builds_pr_id_fkey1",
        "copr_builds",
        "pull_requests",
        ["pr_id"],
        ["id"],
    )
    op.drop_column("copr_builds", "job_trigger_id")
    op.drop_index(op.f("ix_tft_test_runs_pipeline_id"), table_name="tft_test_runs")
    op.drop_table("tft_test_runs")
    op.drop_table("project_releases")
    op.drop_index(op.f("ix_project_issues_issue_id"), table_name="project_issues")
    op.drop_table("project_issues")
    op.drop_index(op.f("ix_koji_builds_build_id"), table_name="koji_builds")
    op.drop_table("koji_builds")
    op.drop_table("git_branches")
    op.drop_table("build_triggers")
    # ### end Alembic commands ###
