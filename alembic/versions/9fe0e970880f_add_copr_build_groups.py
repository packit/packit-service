"""Add copr build groups

Revision ID: 9fe0e970880f
Revises: a9c49475e9c7
Create Date: 2022-11-28 20:37:50.780407

"""

import collections
import enum
import itertools
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
revision = "9fe0e970880f"
down_revision = "a9c49475e9c7"
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
    copr_build_group_id = Column(
        Integer,
        ForeignKey("copr_build_groups.id"),
        index=True,
    )
    copr_build_group = relationship("CoprBuildGroupModel", back_populates="runs")
    koji_build_id = Column(Integer, ForeignKey("koji_build_targets.id"), index=True)
    koji_build = relationship("KojiBuildTargetModel", back_populates="runs")
    test_run_group_id = Column(
        Integer,
        ForeignKey("tft_test_run_groups.id"),
        index=True,
    )
    test_run_group = relationship("TFTTestRunGroupModel", back_populates="runs")
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
    tft_test_run_group_id = Column(Integer, ForeignKey("tft_test_run_groups.id"))
    copr_builds = relationship(
        "CoprBuildTargetModel",
        secondary=tf_copr_association_table,
        backref="tft_test_run_targets",
    )

    group_of_targets = relationship(
        "TFTTestRunGroupModel",
        back_populates="tft_test_run_targets",
    )


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
    copr_build_group_id = Column(Integer, ForeignKey("copr_build_groups.id"))

    group_of_targets = relationship(
        "CoprBuildGroupModel",
        back_populates="copr_build_targets",
    )
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


class GroupModel:
    @property
    def grouped_targets(self):
        raise NotImplementedError


class TFTTestRunGroupModel(ProjectAndEventsConnector, GroupModel, Base):
    __tablename__ = "tft_test_run_groups"
    id = Column(Integer, primary_key=True)
    submitted_time = Column(DateTime, default=datetime.utcnow)

    runs = relationship("PipelineModel", back_populates="test_run_group")
    tft_test_run_targets = relationship(
        "TFTTestRunTargetModel",
        back_populates="group_of_targets",
    )

    @property
    def grouped_targets(self) -> list["TFTTestRunTargetModel"]:
        return self.tft_test_run_targets


class CoprBuildGroupModel(ProjectAndEventsConnector, GroupModel, Base):
    __tablename__ = "copr_build_groups"
    id = Column(Integer, primary_key=True)
    submitted_time = Column(DateTime, default=datetime.utcnow)

    runs = relationship("PipelineModel", back_populates="copr_build_group")
    copr_build_targets = relationship(
        "CoprBuildTargetModel",
        back_populates="group_of_targets",
    )

    @property
    def grouped_targets(self) -> list["CoprBuildTargetModel"]:
        return self.copr_build_targets


def upgrade():
    bind = op.get_bind()
    session = sa.orm.Session(bind=bind)

    op.create_table(
        "copr_build_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("submitted_time", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "copr_build_targets",
        sa.Column("copr_build_group_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        None,
        "copr_build_targets",
        "copr_build_groups",
        ["copr_build_group_id"],
        ["id"],
    )
    op.add_column(
        "pipelines",
        sa.Column("copr_build_group_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        None,
        "pipelines",
        "copr_build_groups",
        ["copr_build_group_id"],
        ["id"],
    )

    groups = collections.defaultdict(list)
    for copr_build in session.query(CoprBuildTargetModel):
        groups[copr_build.build_id].append(copr_build.id)

    for ids in groups.values():
        group = CoprBuildGroupModel()

        for copr_model_id in ids:
            copr_build = (
                session.query(CoprBuildTargetModel)
                .filter(CoprBuildTargetModel.id == copr_model_id)
                .one()
            )
            group.grouped_targets.append(copr_build)
            # Link the pipeline to groups
            # TODO: should we merge the groups? This would result in deletion and possibly some mess
            for pipeline in session.query(PipelineModel).filter(
                PipelineModel.copr_build_id == copr_model_id,
            ):
                pipeline.copr_build_group = group
                session.add(pipeline)

        session.add(group)

    op.drop_constraint("runs_copr_build_id_fkey", "pipelines")
    op.drop_column("pipelines", "copr_build_id")
    session.commit()


def downgrade():
    bind = op.get_bind()
    session = sa.orm.Session(bind=bind)

    op.add_column("pipelines", sa.Column("copr_build_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "runs_copr_build_id_fkey",
        "pipelines",
        "copr_build_targets",
        ["copr_build_id"],
        ["id"],
    )

    # Split the groups back, this may not fully produce the same thing.
    for group in session.query(CoprBuildGroupModel):
        for pipeline, copr_build in itertools.zip_longest(
            group.runs,
            group.copr_build_targets,
        ):
            if not pipeline:
                # Not enough pipelines, create a new one
                pipeline = PipelineModel()
            if not copr_build:
                continue
            pipeline.copr_build = copr_build
            session.add(pipeline)

    op.drop_constraint(
        "copr_build_targets_copr_build_group_id_fkey",
        "copr_build_targets",
    )
    op.drop_column("copr_build_targets", "copr_build_group_id")
    op.drop_constraint("pipelines_copr_build_group_id_fkey", "pipelines")
    op.drop_column("pipelines", "copr_build_group_id")
    op.drop_table("copr_build_groups")
    session.commit()
