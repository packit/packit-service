# MIT License
#
# Copyright (c) 2018-2019 Red Hat, Inc.

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""
Data layer on top of PSQL using sqlalch
"""
import enum
import logging
import os
from contextlib import contextmanager
from datetime import datetime
from typing import TYPE_CHECKING, Optional, Union, Iterable, Dict, Type

from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    ForeignKey,
    Text,
    Enum,
    desc,
    JSON,
    create_engine,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship
from sqlalchemy.types import PickleType, ARRAY

from packit.config import JobConfigTriggerType
from packit_service.constants import WHITELIST_CONSTANTS

logger = logging.getLogger(__name__)
# SQLAlchemy session, get it with `get_sa_session`
session_instance = None


def get_pg_url() -> str:
    """ create postgresql connection string """
    return (
        f"postgres+psycopg2://{os.getenv('POSTGRESQL_USER')}"
        f":{os.getenv('POSTGRESQL_PASSWORD')}@{os.getenv('POSTGRES_SERVICE_HOST', 'postgres')}"
        f":{os.getenv('POSTGRESQL_PORT', '5432')}/{os.getenv('POSTGRESQL_DATABASE')}"
    )


@contextmanager
def get_sa_session() -> Session:
    """ get SQLAlchemy session """
    # we need to keep one session for all the operations b/c SA objects
    # are bound to this session and we can use them, otherwise we'd need
    # add objects into all newly created sessions:
    #   Instance <PullRequest> is not bound to a Session; attribute refresh operation cannot proceed
    global session_instance
    if session_instance is None:
        engine = create_engine(get_pg_url())
        Session = sessionmaker(bind=engine)
        session_instance = Session()
    try:
        yield session_instance
        session_instance.commit()
    except Exception as ex:
        logger.warning(f"Exception while working with database: {ex!r}")
        session_instance.rollback()
        raise


# https://github.com/python/mypy/issues/2477#issuecomment-313984522 ^_^
if TYPE_CHECKING:
    Base = object
else:
    Base = declarative_base()


class JobTriggerModelType(str, enum.Enum):
    pull_request = "pull_request"
    branch_push = "branch_push"
    release = "release"
    issue = "issue"


class GitProjectModel(Base):
    __tablename__ = "git_projects"
    id = Column(Integer, primary_key=True)
    # github.com/NAMESPACE/REPO_NAME
    # git.centos.org/NAMESPACE/REPO_NAME
    namespace = Column(String, index=True)
    repo_name = Column(String, index=True)
    pull_requests = relationship("PullRequestModel", back_populates="project")
    branches = relationship("GitBranchModel", back_populates="project")
    releases = relationship("ProjectReleaseModel", back_populates="project")
    issues = relationship("IssueModel", back_populates="project")

    # Git URL of the repo
    # Example: https://github.com/packit-service/hello-world.git
    https_url = Column(String)

    @classmethod
    def get_or_create(cls, namespace: str, repo_name: str) -> "GitProjectModel":
        with get_sa_session() as session:
            project = (
                session.query(GitProjectModel)
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
        return f"GitProjectModel(name={self.namespace}/{self.repo_name})"

    def __str__(self):
        return self.__repr__()


class PullRequestModel(Base):
    __tablename__ = "pull_requests"
    id = Column(Integer, primary_key=True)  # our database PK
    # GitHub PR ID
    # this is not our PK b/c:
    #   1) we don't control it
    #   2) we want sensible auto-incremented ID, not random numbers
    #   3) it's not unique across projects obviously, so why am I even writing this?
    pr_id = Column(Integer, index=True)
    project_id = Column(Integer, ForeignKey("git_projects.id"))
    project = relationship("GitProjectModel", back_populates="pull_requests")

    job_config_trigger_type = JobConfigTriggerType.pull_request
    job_trigger_model_type = JobTriggerModelType.pull_request

    @classmethod
    def get_or_create(
        cls, pr_id: int, namespace: str, repo_name: str
    ) -> "PullRequestModel":
        with get_sa_session() as session:
            project = GitProjectModel.get_or_create(
                namespace=namespace, repo_name=repo_name
            )
            pr = (
                session.query(PullRequestModel)
                .filter_by(pr_id=pr_id, project_id=project.id)
                .first()
            )
            if not pr:
                pr = PullRequestModel()
                pr.pr_id = pr_id
                pr.project_id = project.id
                session.add(pr)
            return pr

    def __repr__(self):
        return f"PullRequestModel(id={self.pr_id}, project={self.project})"

    def __str__(self):
        return self.__repr__()


class IssueModel(Base):
    __tablename__ = "project_issues"
    id = Column(Integer, primary_key=True)  # our database PK
    issue_id = Column(Integer, index=True)
    project_id = Column(Integer, ForeignKey("git_projects.id"))
    project = relationship("GitProjectModel", back_populates="issues")
    job_config_trigger_type = None
    job_trigger_model_type = JobTriggerModelType.issue

    @classmethod
    def get_or_create(
        cls, issue_id: int, namespace: str, repo_name: str
    ) -> "IssueModel":
        with get_sa_session() as session:
            project = GitProjectModel.get_or_create(
                namespace=namespace, repo_name=repo_name
            )
            issue = (
                session.query(IssueModel)
                .filter_by(issue_id=issue_id, project_id=project.id)
                .first()
            )
            if not issue:
                issue = IssueModel()
                issue.issue_id = issue_id
                issue.project_id = project.id
                session.add(issue)
            return issue

    def __repr__(self):
        return f"IssueModel(id={self.issue_id}, project={self.project})"

    def __str__(self):
        return self.__repr__()


class GitBranchModel(Base):
    __tablename__ = "git_branches"
    id = Column(Integer, primary_key=True)  # our database PK
    name = Column(String)
    project_id = Column(Integer, ForeignKey("git_projects.id"))
    project = relationship("GitProjectModel", back_populates="branches")

    job_config_trigger_type = JobConfigTriggerType.commit
    job_trigger_model_type = JobTriggerModelType.branch_push

    @classmethod
    def get_or_create(
        cls, branch_name: str, namespace: str, repo_name: str
    ) -> "GitBranchModel":
        with get_sa_session() as session:
            project = GitProjectModel.get_or_create(
                namespace=namespace, repo_name=repo_name
            )
            git_branch = (
                session.query(GitBranchModel)
                .filter_by(name=branch_name, project_id=project.id)
                .first()
            )
            if not git_branch:
                git_branch = GitBranchModel()
                git_branch.name = branch_name
                git_branch.project_id = project.id
                session.add(git_branch)
            return git_branch

    def __repr__(self):
        return f"GitBranchModel(name={self.name},  project={self.project})"

    def __str__(self):
        return self.__repr__()


class ProjectReleaseModel(Base):
    __tablename__ = "project_releases"
    id = Column(Integer, primary_key=True)  # our database PK
    tag_name = Column(String)
    commit_hash = Column(String)
    project_id = Column(Integer, ForeignKey("git_projects.id"))
    project = relationship("GitProjectModel", back_populates="releases")

    job_config_trigger_type = JobConfigTriggerType.release
    job_trigger_model_type = JobTriggerModelType.release

    @classmethod
    def get_or_create(
        cls,
        tag_name: str,
        namespace: str,
        repo_name: str,
        commit_hash: Optional[str] = None,
    ) -> "ProjectReleaseModel":
        with get_sa_session() as session:
            project = GitProjectModel.get_or_create(
                namespace=namespace, repo_name=repo_name
            )
            project_release = (
                session.query(ProjectReleaseModel)
                .filter_by(tag_name=tag_name, project_id=project.id)
                .first()
            )
            if not project_release:
                project_release = ProjectReleaseModel()
                project_release.tag_name = tag_name
                project_release.project = project
                project_release.commit_hash = commit_hash
                session.add(project_release)
            return project_release

    def __repr__(self):
        return (
            f"ProjectReleaseModel("
            f"tag_name={self.tag_name}, "
            f"project={self.project})"
        )

    def __str__(self):
        return self.__repr__()


AbstractTriggerDbType = Union[
    PullRequestModel, ProjectReleaseModel, GitBranchModel, IssueModel
]

MODEL_FOR_TRIGGER: Dict[JobTriggerModelType, Type[AbstractTriggerDbType]] = {
    JobTriggerModelType.pull_request: PullRequestModel,
    JobTriggerModelType.branch_push: GitBranchModel,
    JobTriggerModelType.release: ProjectReleaseModel,
    JobTriggerModelType.issue: IssueModel,
}


class JobTriggerModel(Base):
    __tablename__ = "build_triggers"
    id = Column(Integer, primary_key=True)  # our database PK
    type = Column(Enum(JobTriggerModelType))
    trigger_id = Column(Integer)
    copr_builds = relationship("CoprBuildModel", back_populates="job_trigger")
    koji_builds = relationship("KojiBuildModel", back_populates="job_trigger")
    test_runs = relationship("TFTTestRunModel", back_populates="job_trigger")

    @classmethod
    def get_or_create(
        cls, type: JobTriggerModelType, trigger_id: int
    ) -> "JobTriggerModel":
        with get_sa_session() as session:
            trigger = (
                session.query(JobTriggerModel)
                .filter_by(type=type, trigger_id=trigger_id)
                .first()
            )
            if not trigger:
                trigger = JobTriggerModel()
                trigger.type = type
                trigger.trigger_id = trigger_id
                session.add(trigger)
            return trigger

    def get_trigger_object(self) -> AbstractTriggerDbType:
        with get_sa_session() as session:
            return (
                session.query(MODEL_FOR_TRIGGER[self.type])
                .filter_by(id=self.trigger_id)
                .first()
            )

    def __repr__(self):
        return f"JobTriggerModel(type={self.type}, trigger_id={self.trigger_id})"

    def __str__(self):
        return self.__repr__()


class CoprBuildModel(Base):
    """ we create an entry for every target """

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

    def set_status(self, status: str):
        with get_sa_session() as session:
            self.status = status
            session.add(self)

    def set_build_logs_url(self, build_logs: str):
        with get_sa_session() as session:
            self.build_logs_url = build_logs
            session.add(self)

    def get_project(self) -> GitProjectModel:
        return self.job_trigger.get_trigger_object().project

    @classmethod
    def get_by_id(cls, id_: int) -> Optional["CoprBuildModel"]:
        with get_sa_session() as session:
            return session.query(CoprBuildModel).filter_by(id=id_).first()

    @classmethod
    def get_all(cls) -> Optional[Iterable["CoprBuildModel"]]:
        with get_sa_session() as session:
            return session.query(CoprBuildModel).order_by(desc(CoprBuildModel.id)).all()

    # Returns all builds with that build_id, irrespective of target
    @classmethod
    def get_all_by_build_id(
        cls, build_id: Union[str, int]
    ) -> Optional[Iterable["CoprBuildModel"]]:
        if isinstance(build_id, int):
            # See the comment in get_by_build_id()
            build_id = str(build_id)
        with get_sa_session() as session:
            return session.query(CoprBuildModel).filter_by(build_id=build_id)

    # returns the build matching the build_id and the target
    @classmethod
    def get_by_build_id(
        cls, build_id: Union[str, int], target: str
    ) -> Optional["CoprBuildModel"]:
        if isinstance(build_id, int):
            # PG is pesky about this:
            #   LINE 3: WHERE copr_builds.build_id = 1245767 AND copr_builds.target ...
            #   HINT:  No operator matches the given name and argument type(s).
            #   You might need to add explicit type casts.
            build_id = str(build_id)
        with get_sa_session() as session:
            return (
                session.query(CoprBuildModel)
                .filter_by(build_id=build_id, target=target)
                .first()
            )

    @classmethod
    def get_or_create(
        cls,
        build_id: str,
        commit_sha: str,
        project_name: str,
        owner: str,
        web_url: str,
        target: str,
        status: str,
        srpm_build: "SRPMBuildModel",
        trigger_model: AbstractTriggerDbType,
    ) -> "CoprBuildModel":
        job_trigger = JobTriggerModel.get_or_create(
            type=trigger_model.job_trigger_model_type, trigger_id=trigger_model.id
        )
        with get_sa_session() as session:
            build = cls.get_by_build_id(build_id, target)
            if not build:
                build = cls()
                build.build_id = build_id
                build.job_trigger = job_trigger
                build.srpm_build_id = srpm_build.id
                build.status = status
                build.project_name = project_name
                build.owner = owner
                build.commit_sha = commit_sha
                build.web_url = web_url
                build.target = target
                session.add(build)
            return build

    def __repr__(self):
        return f"COPRBuildModel(id={self.id}, job_trigger={self.job_trigger})"

    def __str__(self):
        return self.__repr__()


class KojiBuildModel(Base):
    """ we create an entry for every target """

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

    def set_status(self, status: str):
        with get_sa_session() as session:
            self.status = status
            session.add(self)

    def set_build_logs_url(self, build_logs: str):
        with get_sa_session() as session:
            self.build_logs_url = build_logs
            session.add(self)

    def get_project(self) -> GitProjectModel:
        return self.job_trigger.get_trigger_object().project

    @classmethod
    def get_by_id(cls, id_: int) -> Optional["KojiBuildModel"]:
        with get_sa_session() as session:
            return session.query(KojiBuildModel).filter_by(id=id_).first()

    @classmethod
    def get_all(cls) -> Optional[Iterable["KojiBuildModel"]]:
        with get_sa_session() as session:
            return session.query(KojiBuildModel).all()

    # Returns all builds with that build_id, irrespective of target
    @classmethod
    def get_all_by_build_id(
        cls, build_id: Union[str, int]
    ) -> Optional[Iterable["KojiBuildModel"]]:
        if isinstance(build_id, int):
            # See the comment in get_by_build_id()
            build_id = str(build_id)
        with get_sa_session() as session:
            return session.query(KojiBuildModel).filter_by(build_id=build_id)

    # returns the build matching the build_id and the target
    @classmethod
    def get_by_build_id(
        cls, build_id: Union[str, int], target: str
    ) -> Optional["KojiBuildModel"]:
        if isinstance(build_id, int):
            # PG is pesky about this:
            #   LINE 3: WHERE koji_builds.build_id = 1245767 AND koji_builds.target ...
            #   HINT:  No operator matches the given name and argument type(s).
            #   You might need to add explicit type casts.
            build_id = str(build_id)
        with get_sa_session() as session:
            return (
                session.query(KojiBuildModel)
                .filter_by(build_id=build_id, target=target)
                .first()
            )

    @classmethod
    def get_or_create(
        cls,
        build_id: str,
        commit_sha: str,
        web_url: str,
        target: str,
        status: str,
        srpm_build: "SRPMBuildModel",
        trigger_model: AbstractTriggerDbType,
    ) -> "KojiBuildModel":
        job_trigger = JobTriggerModel.get_or_create(
            type=trigger_model.job_trigger_model_type, trigger_id=trigger_model.id
        )
        with get_sa_session() as session:
            build = cls.get_by_build_id(build_id, target)
            if not build:
                build = cls()
                build.build_id = build_id
                build.job_trigger = job_trigger
                build.srpm_build_id = srpm_build.id
                build.status = status
                build.commit_sha = commit_sha
                build.web_url = web_url
                build.target = target
                session.add(build)
            return build

    def __repr__(self):
        return f"KojiBuildModel(id={self.id}, job_trigger={self.job_trigger})"

    def __str__(self):
        return self.__repr__()


class SRPMBuildModel(Base):
    __tablename__ = "srpm_builds"
    id = Column(Integer, primary_key=True)
    # our logs we want to show to the user
    logs = Column(Text)
    copr_builds = relationship("CoprBuildModel", back_populates="srpm_build")
    koji_builds = relationship("KojiBuildModel", back_populates="srpm_build")

    @classmethod
    def create(cls, logs: str) -> "SRPMBuildModel":
        with get_sa_session() as session:
            srpm_build = cls()
            srpm_build.logs = logs
            session.add(srpm_build)
            return srpm_build

    @classmethod
    def get_by_id(cls, id_: int,) -> Optional["SRPMBuildModel"]:
        with get_sa_session() as session:
            return session.query(SRPMBuildModel).filter_by(id=id_).first()

    def __repr__(self):
        return f"SRPMBuildModel(id={self.id})"

    def __str__(self):
        return self.__repr__()


class WhitelistStatus(str, enum.Enum):
    approved_automatically = WHITELIST_CONSTANTS["approved_automatically"]
    waiting = WHITELIST_CONSTANTS["waiting"]
    approved_manually = WHITELIST_CONSTANTS["approved_manually"]


class WhitelistModel(Base):
    __tablename__ = "whitelist"
    id = Column(Integer, primary_key=True)
    account_name = Column(String, index=True)
    status = Column(Enum(WhitelistStatus))

    # add new account or change status if it already exists
    @classmethod
    def add_account(cls, account_name: str, status: str):
        with get_sa_session() as session:
            account = cls.get_account(account_name)
            if not account:
                account = cls()
                account.account_name = account_name
            account.status = status
            session.add(account)
            return account

    @classmethod
    def get_account(cls, account_name: str) -> Optional["WhitelistModel"]:
        with get_sa_session() as session:
            return (
                session.query(WhitelistModel)
                .filter_by(account_name=account_name)
                .first()
            )

    @classmethod
    def get_accounts_by_status(cls, status: str) -> Optional["WhitelistModel"]:
        with get_sa_session() as session:
            return session.query(WhitelistModel).filter_by(status=status)

    @classmethod
    def remove_account(cls, account_name: str) -> Optional["WhitelistModel"]:
        with get_sa_session() as session:
            account = session.query(WhitelistModel).filter_by(account_name=account_name)
            if account:
                account.delete()
            return account

    def __repr__(self):
        return f"WhitelistModel(name={self.account_name})"

    def __str__(self):
        return self.__repr__()


class TaskResultModel(Base):
    __tablename__ = "task_results"
    task_id = Column(String, primary_key=True)
    jobs = Column(PickleType)
    event = Column(PickleType)

    @classmethod
    def get_by_id(cls, task_id: str) -> Optional["TaskResultModel"]:
        with get_sa_session() as session:
            return session.query(TaskResultModel).filter_by(task_id=task_id).first()

    @classmethod
    def get_all(cls) -> Optional[Iterable["TaskResultModel"]]:
        with get_sa_session() as session:
            return session.query(TaskResultModel).all()

    @classmethod
    def add_task_result(cls, task_id, task_result_dict):
        with get_sa_session() as session:
            task_result = cls.get_by_id(task_id)
            if not task_result:
                task_result = cls()
                task_result.task_id = task_id
                task_result.jobs = task_result_dict.get("jobs")
                task_result.event = task_result_dict.get("event")
                session.add(task_result)
            return task_result

    def to_dict(self):
        return {
            "task_id": self.task_id,
            "jobs": self.jobs,
            "event": self.event,
        }

    def __repr__(self):
        return f"TaskResult(id={self.task_id})"

    def __str__(self):
        return self.__repr__()


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

    def set_status(self, status: TestingFarmResult):
        with get_sa_session() as session:
            self.status = status
            session.add(self)

    def set_web_url(self, web_url: str):
        with get_sa_session() as session:
            self.web_url = web_url
            session.add(self)

    @classmethod
    def create(
        cls,
        pipeline_id: str,
        commit_sha: str,
        status: TestingFarmResult,
        target: str,
        trigger_model: AbstractTriggerDbType,
        web_url: Optional[str] = None,
    ) -> "TFTTestRunModel":
        job_trigger = JobTriggerModel.get_or_create(
            type=trigger_model.job_trigger_model_type, trigger_id=trigger_model.id
        )

        with get_sa_session() as session:
            test_run = cls()
            test_run.pipeline_id = pipeline_id
            test_run.commit_sha = commit_sha
            test_run.status = status
            test_run.target = target
            test_run.web_url = web_url
            test_run.job_trigger = job_trigger
            session.add(test_run)
            return test_run

    @classmethod
    def get_by_pipeline_id(cls, pipeline_id: str) -> Optional["TFTTestRunModel"]:
        with get_sa_session() as session:
            return (
                session.query(TFTTestRunModel)
                .filter_by(pipeline_id=pipeline_id)
                .first()
            )


class InstallationModel(Base):
    __tablename__ = "github_installations"
    id = Column(Integer, primary_key=True, autoincrement=True)
    # information about account (user/organization) into which the app has been installed
    account_login = Column(String)
    account_id = Column(Integer)
    account_url = Column(String)
    account_type = Column(String)
    # information about user who installed the app into 'account'
    sender_id = Column(Integer)
    sender_login = Column(String)

    created_at = Column(DateTime, default=datetime.utcnow)
    repositories = Column(ARRAY(Integer, ForeignKey("git_projects.id")))

    @classmethod
    def get_project(cls, repo: str):
        namespace, repo_name = repo.split("/")
        return GitProjectModel.get_or_create(namespace, repo_name)

    @classmethod
    def get_by_id(cls, id: int) -> Optional["InstallationModel"]:
        with get_sa_session() as session:
            return session.query(InstallationModel).filter_by(id=id).first()

    @classmethod
    def get_by_account_login(cls, account_login: str) -> Optional["InstallationModel"]:
        with get_sa_session() as session:
            return (
                session.query(InstallationModel)
                .filter_by(account_login=account_login)
                .first()
            )

    @classmethod
    def get_all(cls) -> Optional[Iterable["InstallationModel"]]:
        with get_sa_session() as session:
            return session.query(InstallationModel).all()

    @classmethod
    def create(cls, event):
        with get_sa_session() as session:
            installation = cls.get_by_account_login(event.account_login)
            if not installation:
                installation = cls()
                installation.account_login = event.account_login
                installation.account_id = event.account_id
                installation.account_url = event.account_url
                installation.account_type = event.account_type
                installation.sender_login = event.sender_login
                installation.sender_id = event.sender_id
                installation.created_at = event.created_at
                installation.repositories = [
                    cls.get_project(repo).id for repo in event.repositories
                ]
                session.add(installation)
            return installation

    def __repr__(self):
        return f"InstallationModel(id={self.id}, account={self.account_login})"

    def __str__(self):
        return self.__repr__()
