"""Add RunModel

Revision ID: a5c06aa9ef30
Revises: 70444197d206
Create Date: 2021-03-11 17:14:26.240507

"""

import enum
from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from packit.exceptions import PackitException
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

if TYPE_CHECKING:
    Base = object
else:
    Base = declarative_base()

# revision identifiers, used by Alembic.
revision = "a5c06aa9ef30"
down_revision = "70444197d206"
branch_labels = None
depends_on = None


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

    def __repr__(self):
        return f"JobTriggerModel(type={self.type}, trigger_id={self.trigger_id})"


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

    def __repr__(self):
        return f"RunModel(id={self.id}, datetime='{datetime}', job_trigger={self.job_trigger})"


class SRPMBuildModel(Base):
    __tablename__ = "srpm_builds"
    id = Column(Integer, primary_key=True)
    # our logs we want to show to the user
    logs = Column(Text)
    success = Column(Boolean)
    build_submitted_time = Column(DateTime, default=datetime.utcnow)
    url = Column(Text)

    runs = relationship("RunModel", back_populates="srpm_build")

    # TO-BE-REMOVED
    job_trigger_id = Column(Integer, ForeignKey("build_triggers.id"))
    job_trigger = relationship("JobTriggerModel", back_populates="srpm_builds")
    copr_builds = relationship("CoprBuildModel", back_populates="srpm_build")
    koji_builds = relationship("KojiBuildModel", back_populates="srpm_build")

    def __repr__(self):
        return f"SRPMBuildModel(id={self.id}, build_submitted_time={self.build_submitted_time})"


class CoprBuildModel(Base):
    """
    Representation of Copr build for one target.
    """

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

    runs = relationship("RunModel", back_populates="copr_build")

    # TO-BE-REMOVED
    job_trigger_id = Column(Integer, ForeignKey("build_triggers.id"))
    job_trigger = relationship("JobTriggerModel", back_populates="copr_builds")

    srpm_build_id = Column(Integer, ForeignKey("srpm_builds.id"))
    srpm_build = relationship("SRPMBuildModel", back_populates="copr_builds")

    def __repr__(self):
        return f"COPRBuildModel(id={self.id}, build_submitted_time={self.build_submitted_time})"


class KojiBuildModel(Base):
    """we create an entry for every target"""

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

    # TO-BE-REMOVED
    job_trigger_id = Column(Integer, ForeignKey("build_triggers.id"))
    job_trigger = relationship("JobTriggerModel", back_populates="koji_builds")

    srpm_build_id = Column(Integer, ForeignKey("srpm_builds.id"))
    srpm_build = relationship("SRPMBuildModel", back_populates="koji_builds")

    def __repr__(self):
        return f"KojiBuildModel(id={self.id}, build_submitted_time={self.build_submitted_time})"


class TFTTestRunModel(Base):
    __tablename__ = "tft_test_runs"
    id = Column(Integer, primary_key=True)
    pipeline_id = Column(String, index=True)
    commit_sha = Column(String)
    target = Column(String)
    web_url = Column(String)
    # datetime.utcnow instead of datetime.utcnow() because its an argument to the function
    # so it will run when the model is initiated, not when the table is made
    submitted_time = Column(DateTime)
    data = Column(JSON)

    runs = relationship("RunModel", back_populates="test_run")

    # TO-BE-REMOVED
    job_trigger_id = Column(Integer, ForeignKey("build_triggers.id"))
    job_trigger = relationship("JobTriggerModel", back_populates="test_runs")

    def __repr__(self):
        return f"TFTTestRunModel(id={self.id}, pipeline_id={self.pipeline_id})"


def upgrade():
    op.create_table(
        "runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("datetime", sa.DateTime(), nullable=True),
        sa.Column("job_trigger_id", sa.Integer(), nullable=True),
        sa.Column("srpm_build_id", sa.Integer(), nullable=True),
        sa.Column("copr_build_id", sa.Integer(), nullable=True),
        sa.Column("koji_build_id", sa.Integer(), nullable=True),
        sa.Column("test_run_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["copr_build_id"],
            ["copr_builds.id"],
        ),
        sa.ForeignKeyConstraint(
            ["job_trigger_id"],
            ["build_triggers.id"],
        ),
        sa.ForeignKeyConstraint(
            ["koji_build_id"],
            ["koji_builds.id"],
        ),
        sa.ForeignKeyConstraint(
            ["srpm_build_id"],
            ["srpm_builds.id"],
        ),
        sa.ForeignKeyConstraint(
            ["test_run_id"],
            ["tft_test_runs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.add_column(
        "tft_test_runs",
        sa.Column("submitted_time", sa.DateTime(), nullable=True),
    )

    # Start data migration

    bind = op.get_bind()
    session = orm.Session(bind=bind)

    all_run_models = 0
    (
        deleted_copr_builds_for_no_srpm,
        all_copr_builds,
        fixed_srpm_matching_from_copr_build,
    ) = (0, 0, 0)
    (
        deleted_koji_builds_for_no_srpm,
        all_koji_builds,
        fixed_srpm_matching_from_koji_build,
    ) = (0, 0, 0)

    # Removing the builds without SRPMBuildModel set in JobTriggerModel.
    # Add matching between SRPMBuildModel and JobTriggerModel
    #     if we have srpm_build set as a build property.
    for job_trigger_model in session.query(JobTriggerModel).all():
        if not job_trigger_model.srpm_builds:
            for copr_build in job_trigger_model.copr_builds:
                if copr_build.srpm_build:
                    print(
                        "Fixing SRPM matching:",
                        f"{copr_build.srpm_build} -> {copr_build.job_trigger}",
                    )
                    fixed_srpm_matching_from_copr_build += 1
                    copr_build.srpm_build.job_trigger = job_trigger_model
                    session.add(copr_build.srpm_build)
                else:
                    deleted_copr_builds_for_no_srpm += 1
                    all_copr_builds += 1
                    session.delete(copr_build)
            for koji_build in job_trigger_model.koji_builds:
                if koji_build.srpm_build:
                    print(
                        "Fixing SRPM matching:",
                        f"{koji_build.srpm_build} -> {koji_build.job_trigger}",
                    )
                    fixed_srpm_matching_from_koji_build += 1
                    koji_build.srpm_build.job_trigger = job_trigger_model
                    session.add(koji_build.srpm_build)
                else:
                    deleted_koji_builds_for_no_srpm += 1
                    all_koji_builds += 1
                    session.delete(koji_build)

    # Remove the CoprBuildModel if there is no SRPMBuildModel set as a CoprBuildModel property.
    copr_builds_without_srpm = 0
    for copr_build in session.query(CoprBuildModel).all():
        all_copr_builds += 1
        if not copr_build.srpm_build:
            copr_builds_without_srpm += 1
            session.delete(copr_build)
            continue

        all_run_models += 1
        run_model = RunModel()
        run_model.job_trigger = copr_build.job_trigger
        run_model.srpm_build = copr_build.srpm_build
        run_model.copr_build = copr_build
        run_model.datetime = copr_build.srpm_build.build_submitted_time
        session.add(run_model)

    # Remove the KojiBuildModel if there is no SRPMBuildModeland set as a KojiBuildModel property.
    koji_builds_without_srpm = 0
    for koji_build in session.query(KojiBuildModel).all():
        all_koji_builds += 1
        if not koji_build.srpm_build:
            koji_builds_without_srpm += 1
            continue

        all_run_models += 1
        run_model = RunModel()
        run_model.job_trigger = koji_build.job_trigger
        run_model.srpm_build = koji_build.srpm_build
        run_model.datetime = koji_build.srpm_build.build_submitted_time
        run_model.koji_build = koji_build
        session.add(run_model)

    all_test_runs = 0
    test_runs_deleted = 0
    test_runs_attached = 0

    number_of_builds_and_tests_differ = 0
    run_models_successful = 0

    for job_trigger_model in session.query(JobTriggerModel).order_by(
        JobTriggerModel.id,
    ):
        copr_builds = defaultdict(list)
        for copr_build in job_trigger_model.copr_builds:
            if copr_build.status != "success":
                break
            copr_builds[(copr_build.commit_sha, copr_build.target)].append(copr_build)

        test_runs = defaultdict(list)
        for test_run in job_trigger_model.test_runs:
            all_test_runs += 1
            test_runs[(test_run.commit_sha, test_run.target)].append(test_run)

        for (commit, target), test_group in test_runs.items():
            matching_builds = copr_builds[(commit, target)]
            if len(matching_builds) != len(test_group):
                number_of_builds_and_tests_differ += 1
                for test_run in test_group:
                    test_runs_deleted += 1
                    session.delete(test_run)
            else:
                run_models_successful += 1
                for test, build in zip(test_group, matching_builds):
                    if len(build.runs) != 1:
                        PackitException(
                            f"Build {build} does not have exactly one run:\n{build.runs}",
                        )
                    test_runs_attached += 1
                    build.runs[-1].test_run = test
                    session.add(build.runs[-1])

    srpm_builds_removed_for_no_job_trigger = 0
    for srpm_build in session.query(SRPMBuildModel).all():
        if not srpm_build.job_trigger:
            srpm_builds_removed_for_no_job_trigger += 1
            session.delete(srpm_build)

    srpms_without_build = 0
    # Create RunModel for SRPMBuildModels without any build.
    for job_trigger_model in session.query(JobTriggerModel).all():
        if job_trigger_model.id == 5504:
            print(
                f"job_trigger_model={job_trigger_model}\n"
                f"runs={job_trigger_model.runs}\n"
                f"srpm_builds={job_trigger_model.srpm_builds}",
            )
        if not job_trigger_model.copr_builds and not job_trigger_model.koji_builds:
            for srpm_build in job_trigger_model.srpm_builds:
                print(
                    f"Creating RunModel for SRPMBuildModel without any build: {srpm_build}",
                )
                all_run_models += 1
                srpms_without_build += 1
                run_model = RunModel()
                run_model.job_trigger = srpm_build.job_trigger
                run_model.datetime = srpm_build.build_submitted_time
                run_model.srpm_build = srpm_build
                session.add(run_model)
                assert srpm_build.runs

    srpms_without_run = 0
    for srpm_build in session.query(SRPMBuildModel).all():
        if not srpm_build.runs:
            print(
                f"Creating RunModel for SRPMBuildModel without any RunModel: {srpm_build}",
            )
            all_run_models += 1
            srpms_without_run += 1
            run_model = RunModel()
            run_model.job_trigger = srpm_build.job_trigger
            run_model.datetime = srpm_build.build_submitted_time
            run_model.srpm_build = srpm_build
            session.add(run_model)
            assert srpm_build.runs

    print("================================")
    print(f"SRPM models without any build: {srpms_without_build}")
    print(f"SRPM models without any run (RunModel created): {srpms_without_run}")
    print(
        f"SRPM models removed because of no connection to any job trigger: "
        f"{srpm_builds_removed_for_no_job_trigger}",
    )
    print("================================")
    print(f"All Copr builds: {all_copr_builds}")
    print(
        f"Copr builds deleted for no SRPM for trigger: {deleted_copr_builds_for_no_srpm}",
    )
    print(f"Copr builds deleted for no SRPM set: {copr_builds_without_srpm}")
    print(
        f"Fixed SRPM matching to trigger model from Copr build: "
        f"{fixed_srpm_matching_from_copr_build}",
    )
    print("================================")
    print(f"All Koji builds: {all_koji_builds}")
    print(
        f"Koji builds deleted for no SRPM for trigger: {deleted_koji_builds_for_no_srpm}",
    )
    print(f"Koji builds deleted for no SRPM set: {koji_builds_without_srpm}")
    print(
        f"Fixed SRPM matching to trigger model from Koji build: "
        f"{fixed_srpm_matching_from_koji_build}",
    )
    print("================================")
    print(f"All Test runs: {all_test_runs}")
    print(f"Attached correctly to build: {test_runs_attached}")
    print(f"All Run models: {all_run_models}")
    print(
        "Run models with different number of tests and builds:",
        f"{number_of_builds_and_tests_differ}",
    )
    print(f"Run models with test run correctly set: {run_models_successful}")
    print("================================")

    # Check:
    for srpm_build in session.query(SRPMBuildModel).all():
        if not srpm_build.runs:
            raise PackitException(f"SRPMBuildModel without any run: {srpm_build}")

    for copr_build in session.query(CoprBuildModel).all():
        srpm_builds = {run.srpm_build for run in copr_build.runs}
        if len(srpm_builds) != 1:
            raise PackitException(
                f"More SRPM builds for one copr_build {copr_build}:\n{srpm_builds}",
            )

    for koji_build in session.query(KojiBuildModel).all():
        srpm_builds = {run.srpm_build for run in koji_build.runs}
        if len(srpm_builds) != 1:
            raise PackitException(
                f"More SRPM builds for one koji_build {koji_build}:\n{srpm_builds}",
            )

    for run_model in session.query(RunModel).all():
        if not run_model.srpm_build:
            raise PackitException(
                f"Run model does not have SRPM build set: {run_model}",
            )

    session.commit()

    # Remove direct connections:

    op.drop_constraint(
        "copr_builds_job_trigger_id_fkey",
        "copr_builds",
        type_="foreignkey",
    )
    op.drop_constraint(
        "copr_builds_srpm_build_id_fkey1",
        "copr_builds",
        type_="foreignkey",
    )
    op.drop_column("copr_builds", "job_trigger_id")
    op.drop_column("copr_builds", "srpm_build_id")
    op.drop_constraint(
        "koji_builds_srpm_build_id_fkey",
        "koji_builds",
        type_="foreignkey",
    )
    op.drop_constraint(
        "koji_builds_job_trigger_id_fkey",
        "koji_builds",
        type_="foreignkey",
    )
    op.drop_column("koji_builds", "job_trigger_id")
    op.drop_column("koji_builds", "srpm_build_id")
    op.drop_constraint(
        "srpm_builds_job_trigger_id_fkey",
        "srpm_builds",
        type_="foreignkey",
    )
    op.drop_column("srpm_builds", "job_trigger_id")
    op.drop_constraint(
        "tft_test_runs_job_trigger_id_fkey",
        "tft_test_runs",
        type_="foreignkey",
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
    op.drop_column("tft_test_runs", "submitted_time")
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
        sa.Column("srpm_build_id", sa.INTEGER(), autoincrement=False, nullable=True),
    )
    op.add_column(
        "koji_builds",
        sa.Column("job_trigger_id", sa.INTEGER(), autoincrement=False, nullable=True),
    )
    op.create_foreign_key(
        "koji_builds_job_trigger_id_fkey",
        "koji_builds",
        "build_triggers",
        ["job_trigger_id"],
        ["id"],
    )
    op.create_foreign_key(
        "koji_builds_srpm_build_id_fkey",
        "koji_builds",
        "srpm_builds",
        ["srpm_build_id"],
        ["id"],
    )
    op.add_column(
        "copr_builds",
        sa.Column("srpm_build_id", sa.INTEGER(), autoincrement=False, nullable=True),
    )
    op.add_column(
        "copr_builds",
        sa.Column("job_trigger_id", sa.INTEGER(), autoincrement=False, nullable=True),
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
            session.add(run_model.copr_build)

        if run_model.koji_build:
            run_model.koji_build.job_trigger = run_model.job_trigger
            run_model.koji_build.srpm_build = run_model.srpm_build
            session.add(run_model.koji_build)

        if run_model.test_run:
            run_model.test_run.job_trigger = run_model.job_trigger
            session.add(run_model.test_run)

    session.commit()

    op.drop_table("runs")
