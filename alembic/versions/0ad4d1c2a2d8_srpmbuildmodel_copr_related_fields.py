"""SRPMBuildModel Copr related fields

Revision ID: 0ad4d1c2a2d8
Revises: 376bdebc4180
Create Date: 2022-01-17 13:51:01.783926

"""

import enum
from datetime import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    orm,
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
revision = "0ad4d1c2a2d8"
down_revision = "376bdebc4180"
branch_labels = None
depends_on = None


class JobTriggerModelType(str, enum.Enum):
    pull_request = "pull_request"
    branch_push = "branch_push"
    release = "release"
    issue = "issue"


class JobTriggerModel(Base):
    __tablename__ = "build_triggers"
    id = Column(Integer, primary_key=True)  # our database PK
    type = Column(Enum(JobTriggerModelType))
    trigger_id = Column(Integer)

    runs = relationship("RunModel", back_populates="job_trigger")


class CoprBuildModel(ProjectAndEventsConnector, Base):
    __tablename__ = "copr_builds"
    id = Column(Integer, primary_key=True)
    build_id = Column(String, index=True)  # copr build id

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

    runs = relationship("RunModel", back_populates="copr_build")


class TestingFarmResult(str, enum.Enum):
    new = "new"
    queued = "queued"
    running = "running"
    passed = "passed"
    failed = "failed"
    skipped = "skipped"
    error = "error"
    unknown = "unknown"
    needs_inspection = "needs_inspection"


class TFTTestRunModel(ProjectAndEventsConnector, Base):
    __tablename__ = "tft_test_runs"
    id = Column(Integer, primary_key=True)
    pipeline_id = Column(String, index=True)
    commit_sha = Column(String)
    status = Column(Enum(TestingFarmResult))
    target = Column(String)
    web_url = Column(String)
    # datetime.utcnow instead of datetime.utcnow() because its an argument to the function
    # so it will run when the model is initiated, not when the table is made
    submitted_time = Column(DateTime, default=datetime.utcnow)
    data = Column(JSON)

    runs = relationship("RunModel", back_populates="test_run")


class KojiBuildModel(ProjectAndEventsConnector, Base):
    __tablename__ = "koji_builds"
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

    runs = relationship("RunModel", back_populates="koji_build")


class RunModel(Base):
    __tablename__ = "runs"
    id = Column(Integer, primary_key=True)  # our database PK
    # datetime.utcnow instead of datetime.utcnow() because its an argument to the function
    # so it will run when the model is initiated, not when the table is made
    datetime = Column(DateTime, default=datetime.utcnow)

    job_trigger_id = Column(Integer, ForeignKey("build_triggers.id"))
    job_trigger = relationship("JobTriggerModel", back_populates="runs")

    srpm_build_id = Column(Integer, ForeignKey("srpm_builds.id"))
    srpm_build = relationship("SRPMBuildModel", back_populates="runs")
    copr_build_id = Column(Integer, ForeignKey("copr_builds.id"))
    copr_build = relationship("CoprBuildModel", back_populates="runs")
    koji_build_id = Column(Integer, ForeignKey("koji_builds.id"))
    koji_build = relationship("KojiBuildModel", back_populates="runs")
    test_run_id = Column(Integer, ForeignKey("tft_test_runs.id"))
    test_run = relationship("TFTTestRunModel", back_populates="runs")


class SRPMBuildModel(ProjectAndEventsConnector, Base):
    __tablename__ = "srpm_builds"
    id = Column(Integer, primary_key=True)
    success = Column(Boolean)
    status = Column(String)
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

    runs = relationship("RunModel", back_populates="srpm_build")


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column(
        "srpm_builds",
        sa.Column("build_finished_time", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "srpm_builds",
        sa.Column("build_start_time", sa.DateTime(), nullable=True),
    )
    op.add_column("srpm_builds", sa.Column("commit_sha", sa.String(), nullable=True))
    op.add_column("srpm_builds", sa.Column("copr_build_id", sa.String(), nullable=True))
    op.add_column("srpm_builds", sa.Column("copr_web_url", sa.Text(), nullable=True))
    op.add_column("srpm_builds", sa.Column("logs_url", sa.Text(), nullable=True))
    op.add_column("srpm_builds", sa.Column("status", sa.String(), nullable=True))
    op.create_index(
        op.f("ix_srpm_builds_copr_build_id"),
        "srpm_builds",
        ["copr_build_id"],
        unique=False,
    )

    bind = op.get_bind()
    session = orm.Session(bind=bind)

    for srpm_model in session.query(SRPMBuildModel).all():
        if srpm_model.success is None:
            continue

        srpm_model.status = "success" if srpm_model.success else "failure"
        session.add(srpm_model)

    session.commit()

    op.drop_column("srpm_builds", "success")
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column(
        "srpm_builds",
        sa.Column("success", sa.BOOLEAN(), autoincrement=False, nullable=True),
    )
    op.drop_index(op.f("ix_srpm_builds_copr_build_id"), table_name="srpm_builds")
    op.drop_column("srpm_builds", "status")
    op.drop_column("srpm_builds", "logs_url")
    op.drop_column("srpm_builds", "copr_web_url")
    op.drop_column("srpm_builds", "copr_build_id")
    op.drop_column("srpm_builds", "commit_sha")
    op.drop_column("srpm_builds", "build_start_time")
    op.drop_column("srpm_builds", "build_finished_time")
    # ### end Alembic commands ###
