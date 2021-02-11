"""Add RunModel

Revision ID: 2870354b84cf
Revises: 70444197d206
Create Date: 2021-02-09 11:07:59.697152

"""
import enum
from datetime import datetime
from typing import List, TYPE_CHECKING

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    orm,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship


revision = "2870354b84cf"
down_revision = "70444197d206"
branch_labels = None
depends_on = None

if TYPE_CHECKING:
    Base = object
else:
    Base = declarative_base()


class JobTriggerModelType(str, enum.Enum):
    pull_request = "pull_request"
    branch_push = "branch_push"
    release = "release"
    issue = "issue"


class JobTriggerModel(Base):
    """
    Model representing a trigger of some packit task.

    It connects RunModel (and built/test models via that model)
    with models like PullRequestModel, GitBranchModel or ProjectReleaseModel.

    * It contains type and id of the other database_model.
      * We know table and id that we need to find in that table.
    * Each RunModel has to be connected to exactly one JobTriggerModel.
    * There can be multiple RunModels for one JobTriggerModel.
      (e.g. For each push to PR, there will be new RunModel, but same JobTriggerModel.)
    """

    __tablename__ = "build_triggers"
    id = Column(Integer, primary_key=True)  # our database PK
    type = Column(Enum(JobTriggerModelType))
    trigger_id = Column(Integer)

    runs = relationship("RunModel", back_populates="job_trigger")

    # TO-BE-REMOVED
    copr_builds = relationship("CoprBuildModel", back_populates="job_trigger")
    srpm_builds = relationship("SRPMBuildModel", back_populates="job_trigger")
    koji_builds = relationship("KojiBuildModel", back_populates="job_trigger")
    test_runs = relationship("TFTTestRunModel", back_populates="job_trigger")


class RunModel(Base):
    """
    Represents one pipeline.

    Connects JobTriggerModel (and triggers like PullRequestModel via that model) with
    build/test models like  SRPMBuildModel, CoprBuildModel, KojiBuildModel, and TFTTestRunModel.

    * One model of each build/test model can be connected.
    * Each build/test model can be connected to multiple RunModels (e.g. on retrigger).
    * Each RunModel has to be connected to exactly one JobTriggerModel.
    * There can be multiple RunModels for one JobTriggerModel.
      (e.g. For each push to PR, there will be new RunModel, but same JobTriggerModel.)
    """

    __tablename__ = "runs"
    id = Column(Integer, primary_key=True)  # our database PK
    type = Column(Enum(JobTriggerModelType))
    trigger_id = Column(Integer)

    job_trigger_id = Column(Integer, ForeignKey("build_triggers.id"))
    job_trigger = relationship("JobTriggerModel", back_populates="runs")

    srpm_build_id = Column(Integer, ForeignKey("runs.id"))
    srpm_build = relationship("SRPMBuildModel", back_populates="runs")
    copr_build_id = Column(Integer, ForeignKey("runs.id"))
    copr_build = relationship("CoprBuildModel", back_populates="runs")
    koji_build_id = Column(Integer, ForeignKey("runs.id"))
    koji_build = relationship("KojiBuildModel", back_populates="runs")
    test_run_id = Column(Integer, ForeignKey("runs.id"))
    test_run = relationship("TFTTestRunModel", back_populates="runs")


class SRPMBuildModel(Base):
    __tablename__ = "srpm_builds"
    id = Column(Integer, primary_key=True)
    # our logs we want to show to the user
    logs = Column(Text)
    success = Column(Boolean)
    build_submitted_time = Column(DateTime, default=datetime.utcnow)
    url = Column(Text)

    runs = relationship("RunModel", back_populates="job_trigger")

    # TO-BE-REMOVED
    job_trigger_id = Column(Integer, ForeignKey("build_triggers.id"))
    job_trigger = relationship("JobTriggerModel", back_populates="srpm_builds")
    copr_builds = relationship("CoprBuildModel", back_populates="srpm_build")
    koji_builds = relationship("KojiBuildModel", back_populates="srpm_build")


class CoprBuildModel(Base):
    """
    Representation of Copr build for one target.
    """

    __tablename__ = "copr_builds"
    id = Column(Integer, primary_key=True)
    build_id = Column(String, index=True)  # copr build id
    runs = relationship("RunModel", back_populates="job_trigger")

    # commit sha of the PR (or a branch, release) we used for a build
    commit_sha = Column(String)
    # what's the build status?
    status = Column(String)
    # chroot, but we use the word target in our docs
    target = Column(String)
    # URL to copr web ui for the particular build
    web_url = Column(String)
    # url to copr build logs
    build_logs_url = Column(String)
    # datetime.utcnow instead of datetime.utcnow() because its an argument to the function
    # so it will run when the copr build is initiated, not when the table is made
    build_submitted_time = Column(DateTime, default=datetime.utcnow)
    build_start_time = Column(DateTime)
    build_finished_time = Column(DateTime)

    # project name as shown in copr
    project_name = Column(String)
    owner = Column(String)

    # metadata for the build which didn't make it to schema yet
    # metadata is reserved to sqlalch
    data = Column(JSON)

    # TO-BE-REMOVED
    job_trigger_id = Column(Integer, ForeignKey("build_triggers.id"))
    job_trigger = relationship("JobTriggerModel", back_populates="copr_builds")

    srpm_build_id = Column(Integer, ForeignKey("srpm_builds.id"))
    srpm_build = relationship("SRPMBuildModel", back_populates="copr_builds")


class KojiBuildModel(Base):
    """ we create an entry for every target """

    __tablename__ = "koji_builds"
    id = Column(Integer, primary_key=True)
    build_id = Column(String, index=True)  # koji build id
    runs = relationship("RunModel", back_populates="job_trigger")

    # commit sha of the PR (or a branch, release) we used for a build
    commit_sha = Column(String)
    # what's the build status?
    status = Column(String)
    # chroot, but we use the word target in our docs
    target = Column(String)
    # URL to koji web ui for the particular build
    web_url = Column(String)
    # url to koji build logs
    build_logs_url = Column(String)
    # datetime.utcnow instead of datetime.utcnow() because its an argument to the function
    # so it will run when the koji build is initiated, not when the table is made
    build_submitted_time = Column(DateTime, default=datetime.utcnow)
    build_start_time = Column(DateTime)
    build_finished_time = Column(DateTime)

    # metadata for the build which didn't make it to schema yet
    # metadata is reserved to sqlalch
    data = Column(JSON)

    # TO-BE-REMOVED
    job_trigger_id = Column(Integer, ForeignKey("build_triggers.id"))
    job_trigger = relationship("JobTriggerModel", back_populates="copr_builds")

    srpm_build_id = Column(Integer, ForeignKey("srpm_builds.id"))
    srpm_build = relationship("SRPMBuildModel", back_populates="copr_builds")


class TFTTestRunModel(Base):
    __tablename__ = "tft_test_runs"
    id = Column(Integer, primary_key=True)
    pipeline_id = Column(String, index=True)
    commit_sha = Column(String)
    target = Column(String)
    web_url = Column(String)
    data = Column(JSON)

    runs = relationship("RunModel", back_populates="job_trigger")

    # TO-BE-REMOVED
    job_trigger_id = Column(Integer, ForeignKey("build_triggers.id"))
    job_trigger = relationship("JobTriggerModel", back_populates="test_runs")


def upgrade():

    op.create_table(
        "runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_trigger_id", sa.Integer(), nullable=True),
        sa.Column("srpm_build_id", sa.Integer(), nullable=True),
        sa.Column("copr_build_id", sa.Integer(), nullable=True),
        sa.Column("koji_build_id", sa.Integer(), nullable=True),
        sa.Column("test_run_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["copr_build_id"],
            ["runs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["job_trigger_id"],
            ["build_triggers.id"],
        ),
        sa.ForeignKeyConstraint(
            ["koji_build_id"],
            ["runs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["srpm_build_id"],
            ["runs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["test_run_id"],
            ["runs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Start data migration

    bind = op.get_bind()
    session = orm.Session(bind=bind)
    for copr_build in session.query(CoprBuildModel).all():
        run_model = RunModel()
        run_model.job_trigger = copr_build.job_trigger
        run_model.srpm_build = copr_build.srpm_build
        run_model.copr_build = copr_build
        session.add(run_model)

    for koji_build in session.query(KojiBuildModel).all():
        run_model = RunModel()
        run_model.job_trigger = koji_build.job_trigger
        run_model.srpm_build = koji_build.srpm_build
        run_model.koji_build = koji_build
        session.add(run_model)

    for test_run in session.query(TFTTestRunModel):
        matching_runs: List[CoprBuildModel] = []
        for copr_build in test_run.job_trigger.copr_builds:
            if (
                copr_build.commit_sha == test_run.commit_sha
                and copr_build.target == test_run.target
            ):
                # TODO: match only successful Copr builds
                matching_runs += copr_build.runs

        if not matching_runs:
            # Leave it as is, bad data
            pass
        else:
            if len(matching_runs) != 1:
                # This is the problematic part of the previous schema.
                # We don't know the matching between between test runs and builds.
                pass

            for run_model in matching_runs:
                if not run_model.test_run:
                    run_model.test_run = test_run
                    session.add(run_model)
                    break
            else:
                # Create new RunModel
                run_to_copy = matching_runs[-1]
                new_run = RunModel()
                new_run.srpm_build = run_to_copy.srpm_build
                new_run.copr_build = run_to_copy.copr_build
                new_run.test_run = test_run
                session.add(new_run)

    session.commit()

    # Remove direct connections:

    op.drop_constraint(
        "copr_builds_job_trigger_id_fkey", "copr_builds", type_="foreignkey"
    )
    op.drop_constraint(
        "copr_builds_srpm_build_id_fkey1", "copr_builds", type_="foreignkey"
    )
    op.drop_column("copr_builds", "srpm_build_id")
    op.drop_column("copr_builds", "job_trigger_id")
    op.drop_constraint(
        "koji_builds_job_trigger_id_fkey", "koji_builds", type_="foreignkey"
    )
    op.drop_constraint(
        "koji_builds_srpm_build_id_fkey", "koji_builds", type_="foreignkey"
    )
    op.drop_column("koji_builds", "srpm_build_id")
    op.drop_column("koji_builds", "job_trigger_id")
    op.drop_constraint(
        "srpm_builds_job_trigger_id_fkey", "srpm_builds", type_="foreignkey"
    )
    op.drop_column("srpm_builds", "job_trigger_id")
    op.drop_constraint(
        "tft_test_runs_job_trigger_id_fkey", "tft_test_runs", type_="foreignkey"
    )
    op.drop_column("tft_test_runs", "job_trigger_id")


def downgrade():

    # Recreated direct connections:

    op.add_column(
        "tft_test_runs",
        sa.Column("job_trigger_id", sa.INTEGER(), autoincrement=False, nullable=True),
    )
    op.create_foreign_key(
        "tft_test_runs_job_trigger_id_fkey",
        "tft_test_runs",
        "build_triggers",
        ["job_trigger_id"],
        ["id"],
    )
    op.add_column(
        "srpm_builds",
        sa.Column("job_trigger_id", sa.INTEGER(), autoincrement=False, nullable=True),
    )
    op.create_foreign_key(
        "srpm_builds_job_trigger_id_fkey",
        "srpm_builds",
        "build_triggers",
        ["job_trigger_id"],
        ["id"],
    )
    op.add_column(
        "koji_builds",
        sa.Column("job_trigger_id", sa.INTEGER(), autoincrement=False, nullable=True),
    )
    op.add_column(
        "koji_builds",
        sa.Column("srpm_build_id", sa.INTEGER(), autoincrement=False, nullable=True),
    )
    op.create_foreign_key(
        "koji_builds_srpm_build_id_fkey",
        "koji_builds",
        "srpm_builds",
        ["srpm_build_id"],
        ["id"],
    )
    op.create_foreign_key(
        "koji_builds_job_trigger_id_fkey",
        "koji_builds",
        "build_triggers",
        ["job_trigger_id"],
        ["id"],
    )
    op.add_column(
        "copr_builds",
        sa.Column("job_trigger_id", sa.INTEGER(), autoincrement=False, nullable=True),
    )
    op.add_column(
        "copr_builds",
        sa.Column("srpm_build_id", sa.INTEGER(), autoincrement=False, nullable=True),
    )
    op.create_foreign_key(
        "copr_builds_srpm_build_id_fkey1",
        "copr_builds",
        "srpm_builds",
        ["srpm_build_id"],
        ["id"],
    )
    op.create_foreign_key(
        "copr_builds_job_trigger_id_fkey",
        "copr_builds",
        "build_triggers",
        ["job_trigger_id"],
        ["id"],
    )

    # Migrate data:

    bind = op.get_bind()
    session = orm.Session(bind=bind)
    for run_model in session.query(RunModel).all():
        run_model.srpm_build.job_trigger = run_model.job_trigger

        if run_model.copr_build:
            run_model.copr_build.job_trigger = run_model.job_trigger
            run_model.copr_build.srpm_build = run_model.srpm_build

        if run_model.koji_build:
            run_model.koji_build.job_trigger = run_model.job_trigger
            run_model.koji_build.srpm_build = run_model.srpm_build

        if run_model.test_run:
            run_model.test_run.job_trigger = run_model.job_trigger

    session.commit()

    op.drop_table("runs")
