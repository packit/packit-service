"""link srpmbuildmodel to jobtriggermodel

Revision ID: 726cb0f70c6d
Revises: adbdc1c21d7e
Create Date: 2020-08-05 18:14:20.277673

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
    desc,
    orm,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, relationship

from alembic import op

# revision identifiers, used by Alembic.
revision = "726cb0f70c6d"
down_revision = "adbdc1c21d7e"
branch_labels = None
depends_on = None


if TYPE_CHECKING:
    Base = object
else:
    Base = declarative_base()


class CoprBuildModel(Base):
    """we create an entry for every target"""

    __tablename__ = "copr_builds"
    id = Column(Integer, primary_key=True)
    build_id = Column(String, index=True)  # copr build id
    job_trigger_id = Column(Integer, ForeignKey("build_triggers.id"))
    job_trigger = relationship("JobTriggerModel", back_populates="copr_builds")
    srpm_build_id = Column(Integer, ForeignKey("srpm_builds.id"))
    srpm_build = relationship("SRPMBuildModel", back_populates="copr_builds")
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

    def __repr__(self):
        return f"COPRBuildModel(id={self.id}, job_trigger={self.job_trigger})"


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
    copr_builds = relationship("CoprBuildModel", back_populates="job_trigger")
    srpm_builds = relationship("SRPMBuildModel", back_populates="job_trigger")
    koji_builds = relationship("KojiBuildModel", back_populates="job_trigger")
    test_runs = relationship("TFTTestRunModel", back_populates="job_trigger")

    def __repr__(self):
        return f"JobTriggerModel(type={self.type}, trigger_id={self.trigger_id})"


class KojiBuildModel(Base):
    """we create an entry for every target"""

    __tablename__ = "koji_builds"
    id = Column(Integer, primary_key=True)
    build_id = Column(String, index=True)  # koji build id
    job_trigger_id = Column(Integer, ForeignKey("build_triggers.id"))
    job_trigger = relationship("JobTriggerModel", back_populates="koji_builds")
    srpm_build_id = Column(Integer, ForeignKey("srpm_builds.id"))
    srpm_build = relationship("SRPMBuildModel", back_populates="koji_builds")
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

    def __repr__(self):
        return f"KojiBuildModel(id={self.id}, job_trigger={self.job_trigger})"


class TestingFarmResult(str, enum.Enum):
    new = "new"
    passed = "passed"
    failed = "failed"
    error = "error"
    running = "running"


class TFTTestRunModel(Base):
    __tablename__ = "tft_test_runs"
    id = Column(Integer, primary_key=True)
    pipeline_id = Column(String, index=True)
    job_trigger_id = Column(Integer, ForeignKey("build_triggers.id"))
    job_trigger = relationship("JobTriggerModel", back_populates="test_runs")
    commit_sha = Column(String)
    status = Column(Enum(TestingFarmResult))
    target = Column(String)
    web_url = Column(String)
    data = Column(JSON)


class SRPMBuildModel(Base):
    __tablename__ = "srpm_builds"
    id = Column(Integer, primary_key=True)
    # our logs we want to show to the user
    logs = Column(Text)
    success = Column(Boolean)
    job_trigger_id = Column(Integer, ForeignKey("build_triggers.id"))
    job_trigger = relationship("JobTriggerModel", back_populates="srpm_builds")
    copr_builds = relationship("CoprBuildModel", back_populates="srpm_build")
    koji_builds = relationship("KojiBuildModel", back_populates="srpm_build")

    def set_trigger(self, session: Session, trigger_id: int):
        self.job_trigger_id = trigger_id
        session.add(self)

    @classmethod
    def get_all(cls, session: Session):
        return session.query(SRPMBuildModel).order_by(desc(SRPMBuildModel.id)).all()

    def __repr__(self):
        return f"SRPMBuildModel(id={self.id} trigger={self.job_trigger_id})"


def upgrade():
    # Start schema migration

    op.add_column(
        "srpm_builds",
        sa.Column("job_trigger_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        None,
        "srpm_builds",
        "build_triggers",
        ["job_trigger_id"],
        ["id"],
    )
    # End schema migration

    # Start data migration

    bind = op.get_bind()
    session = orm.Session(bind=bind)
    srpm_builds = SRPMBuildModel.get_all(session)

    for build in srpm_builds:
        if build.job_trigger_id:
            # Trigger is already linked
            continue
        if build.copr_builds:
            # Trigger doesnt exist, but a Copr Build was found
            # all copr builds of that srpm build will have the same trigger, so copy the first one
            trigger_id = build.copr_builds[0].job_trigger_id
            # Adding trigger
            build.set_trigger(session, trigger_id)
    session.commit()

    # End data migration


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_constraint(None, "srpm_builds", type_="foreignkey")
    op.drop_column("srpm_builds", "job_trigger_id")
    # ### end Alembic commands ###
