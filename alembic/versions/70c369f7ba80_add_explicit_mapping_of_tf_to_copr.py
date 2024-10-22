"""Add explicit mapping of TF to Copr

Revision ID: 70c369f7ba80
Revises: f5792581522c
Create Date: 2022-11-27 20:20:00.136412

"""

import enum
from datetime import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
import sqlalchemy.orm
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

from alembic import op
from packit_service.models import ProjectAndEventsConnector

if TYPE_CHECKING:
    Base = object
else:
    Base = declarative_base()

# revision identifiers, used by Alembic.
revision = "70c369f7ba80"
down_revision = "f5792581522c"
branch_labels = None
depends_on = None

tf_copr_association_table = Table(
    "tf_copr_build_association_table",
    Base.metadata,  # type: ignore
    Column("copr_id", ForeignKey("copr_build_targets.id"), primary_key=True),
    Column("tft_id", ForeignKey("tft_test_run_targets.id"), primary_key=True),
)


class JobTriggerModelType(str, enum.Enum):
    pull_request = "pull_request"
    branch_push = "branch_push"
    release = "release"
    issue = "issue"


class JobTriggerModel(Base):
    __tablename__ = "job_triggers"
    id = Column(Integer, primary_key=True)  # our database PK
    type = Column(Enum(JobTriggerModelType))
    trigger_id = Column(Integer, index=True)

    runs = relationship("PipelineModel", back_populates="job_trigger")


class PipelineModel(Base):
    __tablename__ = "pipelines"
    id = Column(Integer, primary_key=True)  # our database PK
    # datetime.utcnow instead of datetime.utcnow() because it's an argument to the function,
    # so it will run when the model is initiated, not when the table is made
    datetime = Column(DateTime, default=datetime.utcnow)

    job_trigger_id = Column(Integer, ForeignKey("job_triggers.id"))
    job_trigger = relationship("JobTriggerModel", back_populates="runs")

    srpm_build_id = Column(Integer, ForeignKey("srpm_builds.id"), index=True)
    srpm_build = relationship("SRPMBuildModel", back_populates="runs")
    copr_build_id = Column(Integer, ForeignKey("copr_build_targets.id"), index=True)
    copr_build = relationship("CoprBuildTargetModel", back_populates="runs")
    koji_build_id = Column(Integer, ForeignKey("koji_build_targets.id"), index=True)
    koji_build = relationship("KojiBuildTargetModel", back_populates="runs")
    test_run_id = Column(Integer, ForeignKey("tft_test_run_targets.id"), index=True)
    test_run = relationship("TFTTestRunTargetModel", back_populates="runs")
    sync_release_run_id = Column(
        Integer,
        ForeignKey("sync_release_runs.id"),
        index=True,
    )
    sync_release_run = relationship("SyncReleaseModel", back_populates="runs")


class TestingFarmResult(str, enum.Enum):
    __test__ = False

    new = "new"
    queued = "queued"
    running = "running"
    passed = "passed"
    failed = "failed"
    skipped = "skipped"
    error = "error"
    unknown = "unknown"
    needs_inspection = "needs_inspection"


class TFTTestRunTargetModel(ProjectAndEventsConnector, Base):
    __tablename__ = "tft_test_run_targets"
    id = Column(Integer, primary_key=True)
    pipeline_id = Column(String, index=True)
    identifier = Column(String)
    commit_sha = Column(String)
    status = Column(Enum(TestingFarmResult))
    target = Column(String)
    web_url = Column(String)
    # datetime.utcnow instead of datetime.utcnow() because its an argument to the function
    # so it will run when the model is initiated, not when the table is made
    submitted_time = Column(DateTime, default=datetime.utcnow)
    data = Column(JSON)
    copr_builds = relationship(
        "CoprBuildTargetModel",
        secondary=tf_copr_association_table,
        backref="tft_test_run_targets",
    )

    runs = relationship("PipelineModel", back_populates="test_run")


class BuildStatus(str, enum.Enum):
    success = "success"
    pending = "pending"
    failure = "failure"
    error = "error"
    waiting_for_srpm = "waiting_for_srpm"


class CoprBuildTargetModel(ProjectAndEventsConnector, Base):
    __tablename__ = "copr_build_targets"
    id = Column(Integer, primary_key=True)
    build_id = Column(String, index=True)  # copr build id

    # commit sha of the PR (or a branch, release) we used for a build
    commit_sha = Column(String, index=True)
    # what's the build status?
    status = Column(Enum(BuildStatus))
    # chroot, but we use the word target in our docs
    target = Column(String)
    # URL to copr web ui for the particular build
    web_url = Column(String)
    # url to copr build logs
    build_logs_url = Column(String)
    # for monitoring: time when we set the status about accepted task
    task_accepted_time = Column(DateTime)
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

    # info about built packages we get from Copr, e.g.
    # [
    #   {
    #       "arch": "noarch",
    #       "epoch": 0,
    #       "name": "python3-packit",
    #       "release": "1.20210930124525726166.main.0.g0b7b36b.fc36",
    #       "version": "0.38.0",
    #   }
    # ]
    built_packages = Column(JSON)

    runs = relationship("PipelineModel", back_populates="copr_build")


class SRPMBuildModel(ProjectAndEventsConnector, Base):
    __tablename__ = "srpm_builds"
    id = Column(Integer, primary_key=True)
    status = Column(Enum(BuildStatus))
    # our logs we want to show to the user
    logs = Column(Text)
    build_submitted_time = Column(DateTime, default=datetime.utcnow)
    build_start_time = Column(DateTime)
    build_finished_time = Column(DateTime)
    commit_sha = Column(String)
    # url for downloading the SRPM
    url = Column(Text)
    # attributes for SRPM built by Copr
    logs_url = Column(Text)
    copr_build_id = Column(String, index=True)
    copr_web_url = Column(Text)

    runs = relationship("PipelineModel", back_populates="srpm_build")


class KojiBuildTargetModel(ProjectAndEventsConnector, Base):
    __tablename__ = "koji_build_targets"
    id = Column(Integer, primary_key=True)
    build_id = Column(String, index=True)  # koji build id

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

    # it is a scratch build?
    scratch = Column(Boolean)

    runs = relationship("PipelineModel", back_populates="koji_build")


class SyncReleaseTargetStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    error = "error"
    retry = "retry"
    submitted = "submitted"


class SyncReleaseTargetModel(ProjectAndEventsConnector, Base):
    __tablename__ = "sync_release_run_targets"
    id = Column(Integer, primary_key=True)
    branch = Column(String, default="unknown")
    downstream_pr_url = Column(String)
    status = Column(Enum(SyncReleaseTargetStatus))
    submitted_time = Column(DateTime, default=datetime.utcnow)
    start_time = Column(DateTime)
    finished_time = Column(DateTime)
    logs = Column(Text)
    sync_release_id = Column(Integer, ForeignKey("sync_release_runs.id"))

    sync_release = relationship(
        "SyncReleaseModel",
        back_populates="sync_release_targets",
    )


class SyncReleaseStatus(str, enum.Enum):
    running = "running"
    finished = "finished"
    error = "error"


class SyncReleaseJobType(str, enum.Enum):
    pull_from_upstream = "pull_from_upstream"
    propose_downstream = "propose_downstream"


class SyncReleaseModel(ProjectAndEventsConnector, Base):
    __tablename__ = "sync_release_runs"
    id = Column(Integer, primary_key=True)
    status = Column(Enum(SyncReleaseStatus))
    submitted_time = Column(DateTime, default=datetime.utcnow)
    job_type = Column(
        Enum(SyncReleaseJobType),
        default=SyncReleaseJobType.propose_downstream,
    )

    runs = relationship("PipelineModel", back_populates="sync_release_run")
    sync_release_targets = relationship(
        "SyncReleaseTargetModel",
        back_populates="sync_release",
    )


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "tf_copr_build_association_table",
        sa.Column("copr_id", sa.Integer(), nullable=False),
        sa.Column("tft_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["copr_id"],
            ["copr_build_targets.id"],
        ),
        sa.ForeignKeyConstraint(
            ["tft_id"],
            ["tft_test_run_targets.id"],
        ),
        sa.PrimaryKeyConstraint("copr_id", "tft_id"),
    )

    # Add the data
    bind = op.get_bind()
    session = sa.orm.Session(bind=bind)

    for tf_run in session.query(TFTTestRunTargetModel):
        if tf_run.copr_builds:
            continue
        builds = [run.copr_build for run in tf_run.runs if run and run.copr_build]
        tf_run.copr_builds.extend(builds)
        session.add(tf_run)

    session.commit()


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table("tf_copr_build_association_table")
    # ### end Alembic commands ###
