# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
Data layer on top of PSQL using sqlalch
"""

import enum
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import (
    Dict,
    Iterable,
    List,
    Optional,
    TYPE_CHECKING,
    Tuple,
    Type,
    Union,
)
from urllib.parse import urlparse

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
    create_engine,
    desc,
    func,
    null,
    case,
)
from sqlalchemy.dialects.postgresql import array as psql_array
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import (
    Session as SQLASession,
    relationship,
    scoped_session,
    sessionmaker,
)
from sqlalchemy.types import ARRAY

from packit.config import JobConfigTriggerType
from packit.exceptions import PackitException
from packit_service.constants import ALLOWLIST_CONSTANTS

logger = logging.getLogger(__name__)


def get_pg_url() -> str:
    """create postgresql connection string"""
    return (
        f"postgresql+psycopg2://{os.getenv('POSTGRESQL_USER')}"
        f":{os.getenv('POSTGRESQL_PASSWORD')}@{os.getenv('POSTGRESQL_HOST', 'postgres')}"
        f":{os.getenv('POSTGRESQL_PORT', '5432')}/{os.getenv('POSTGRESQL_DATABASE')}"
    )


# To log SQL statements, set echo=True
engine = create_engine(get_pg_url(), echo=False)
Session = sessionmaker(bind=engine)
if Path("/usr/bin/run_worker.sh").exists():
    # Multi-(green)threaded workers can't use scoped_session()
    singleton_session = Session()
else:  # service/httpd
    Session = scoped_session(Session)
    singleton_session = None


def sa_session() -> SQLASession:
    """If we use single session, return it, otherwise return a new session from registry."""
    return singleton_session or Session()


@contextmanager
def sa_session_transaction() -> SQLASession:
    """
    Context manager for 'framing' of a transaction for cases where we
    commit data to the database. If all operations succeed
    the transaction is committed, otherwise rolled back.
    https://docs.sqlalchemy.org/en/14/orm/session_basics.html#framing-out-a-begin-commit-rollback-block
    TODO: Replace usages of this function with the sessionmaker.begin[_nested]() as described in
    https://docs.sqlalchemy.org/en/14/orm/session_basics.html#using-a-sessionmaker
    """
    session = sa_session()
    try:
        yield session
        session.commit()
    except Exception as ex:
        logger.warning(f"Exception while working with database: {ex!r}")
        session.rollback()
        raise


def optional_time(
    datetime_object: Union[datetime, None], fmt: str = "%d/%m/%Y %H:%M:%S"
) -> Union[str, None]:
    """
    Returns a formatted date-time string if argument is a datetime object.

    Args:
        datetime_object: date-time to be converted to string
        fmt: format string to be used to produce the string.

            Defaults to `"%d/%m/%Y %H:%M:%S"`.

    Returns:
        Formatted date-time or `None` if no datetime is provided.
    """
    return None if datetime_object is None else datetime_object.strftime(fmt)


def optional_timestamp(datetime_object: Optional[datetime]) -> Optional[int]:
    """
    Returns a UNIX timestamp if argument is a datetime object.

    Args:
        datetime_object: Date-time to be converted to timestamp.

    Returns:
        UNIX timestamp or `None` if no datetime object is provided.
    """
    return None if datetime_object is None else int(datetime_object.timestamp())


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


class BuildsAndTestsConnector:
    """
    Abstract class that is inherited by trigger models
    to share methods for accessing build/test models..
    """

    id: int
    job_trigger_model_type: JobTriggerModelType

    def get_runs(self) -> List["PipelineModel"]:
        trigger_list = (
            sa_session()
            .query(JobTriggerModel)
            .filter_by(type=self.job_trigger_model_type, trigger_id=self.id)
            .all()
        )
        if len(trigger_list) > 1:
            msg = (
                f"There are multiple run models for type {self.job_trigger_model_type}"
                f"and id={self.id}."
            )
            logger.error(msg)
            raise PackitException(msg)
        return trigger_list[0].runs if trigger_list else []

    def _get_run_item(
        self, model_type: Type["AbstractBuildTestDbType"]
    ) -> List["AbstractBuildTestDbType"]:
        runs = self.get_runs()
        models = []

        if model_type == CoprBuildTargetModel:
            models = [run.copr_build for run in runs]

        if model_type == KojiBuildTargetModel:
            models = [run.koji_build for run in runs]

        if model_type == SRPMBuildModel:
            models = [run.srpm_build for run in runs]

        if model_type == TFTTestRunTargetModel:
            models = [run.test_run for run in runs]

        return list({model for model in models if model is not None})

    def get_copr_builds(self):
        return self._get_run_item(model_type=CoprBuildTargetModel)

    def get_koji_builds(self):
        return self._get_run_item(model_type=KojiBuildTargetModel)

    def get_srpm_builds(self):
        return self._get_run_item(model_type=SRPMBuildModel)

    def get_test_runs(self):
        return self._get_run_item(model_type=TFTTestRunTargetModel)


class ProjectAndTriggersConnector:
    """
    Abstract class that is inherited by build/test models
    to share methods for accessing project and trigger models.
    """

    runs: Optional[List["PipelineModel"]]

    def get_job_trigger_model(self) -> Optional["JobTriggerModel"]:
        return self.runs[0].job_trigger if self.runs else None

    def get_trigger_object(self) -> Optional["AbstractTriggerDbType"]:
        job_trigger = self.get_job_trigger_model()
        return job_trigger.get_trigger_object() if job_trigger else None

    def get_project(self) -> Optional["GitProjectModel"]:
        trigger_object = self.get_trigger_object()
        return trigger_object.project if trigger_object else None

    def get_pr_id(self) -> Optional[int]:
        trigger_object = self.get_trigger_object()
        if isinstance(trigger_object, PullRequestModel):
            return trigger_object.pr_id
        return None

    def get_issue_id(self) -> Optional[int]:
        trigger_object = self.get_trigger_object()
        if isinstance(trigger_object, IssueModel):
            return trigger_object.issue_id
        return None

    def get_branch_name(self) -> Optional[str]:
        trigger_object = self.get_trigger_object()
        if isinstance(trigger_object, GitBranchModel):
            return trigger_object.name
        return None

    def get_release_tag(self) -> Optional[str]:
        trigger_object = self.get_trigger_object()
        if isinstance(trigger_object, ProjectReleaseModel):
            return trigger_object.tag_name
        return None


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
    project_authentication_issue = relationship(
        "ProjectAuthenticationIssueModel", back_populates="project"
    )

    # Git URL of the repo
    # Example: https://github.com/packit/hello-world.git
    https_url = Column(String)
    project_url = Column(String)
    instance_url = Column(String, nullable=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance_url = urlparse(self.project_url).hostname

    @classmethod
    def get_or_create(
        cls, namespace: str, repo_name: str, project_url: str
    ) -> "GitProjectModel":
        with sa_session_transaction() as session:
            project = (
                session.query(GitProjectModel)
                .filter_by(
                    namespace=namespace, repo_name=repo_name, project_url=project_url
                )
                .first()
            )
            if not project:
                project = cls(
                    repo_name=repo_name, namespace=namespace, project_url=project_url
                )
                session.add(project)
            return project

    @classmethod
    def get_projects(cls, first: int, last: int) -> Iterable["GitProjectModel"]:
        return (
            sa_session()
            .query(GitProjectModel)
            .order_by(GitProjectModel.namespace)
            .slice(first, last)
        )

    @classmethod
    def get_forge(
        cls, first: int, last: int, forge: str
    ) -> Iterable["GitProjectModel"]:
        """Return projects of given forge"""
        return (
            sa_session()
            .query(GitProjectModel)
            .filter_by(instance_url=forge)
            .order_by(GitProjectModel.namespace)
            .slice(first, last)
        )

    @classmethod
    def get_namespace(cls, forge: str, namespace: str) -> Iterable["GitProjectModel"]:
        """Return projects of given forge and namespace"""
        return (
            p
            for p in sa_session().query(GitProjectModel).filter_by(namespace=namespace)
            if forge == urlparse(p.project_url).hostname
        )

    @classmethod
    def get_project(
        cls, forge: str, namespace: str, repo_name: str
    ) -> Optional["GitProjectModel"]:
        """Return one project which matches said criteria"""
        return (
            sa_session()
            .query(cls)
            .filter_by(instance_url=forge, namespace=namespace, repo_name=repo_name)
            .one_or_none()
        )

    @classmethod
    def get_project_prs(
        cls, first: int, last: int, forge: str, namespace: str, repo_name: str
    ) -> Iterable["PullRequestModel"]:
        return (
            sa_session()
            .query(PullRequestModel)
            .join(GitProjectModel)
            .filter(
                PullRequestModel.project_id == GitProjectModel.id,
                GitProjectModel.instance_url == forge,
                GitProjectModel.namespace == namespace,
                GitProjectModel.repo_name == repo_name,
            )
            .order_by(desc(PullRequestModel.pr_id))
            .slice(first, last)
        )

    @classmethod
    def get_project_issues(
        cls, forge: str, namespace: str, repo_name: str
    ) -> Iterable["IssueModel"]:
        return (
            sa_session()
            .query(IssueModel)
            .join(GitProjectModel)
            .filter(
                IssueModel.project_id == GitProjectModel.id,
                GitProjectModel.instance_url == forge,
                GitProjectModel.namespace == namespace,
                GitProjectModel.repo_name == repo_name,
            )
        )

    @classmethod
    def get_project_branches(
        cls, forge: str, namespace: str, repo_name: str
    ) -> Iterable["GitBranchModel"]:
        return (
            sa_session()
            .query(GitBranchModel)
            .join(GitProjectModel)
            .filter(
                GitBranchModel.project_id == GitProjectModel.id,
                GitProjectModel.instance_url == forge,
                GitProjectModel.namespace == namespace,
                GitProjectModel.repo_name == repo_name,
            )
        )

    @classmethod
    def get_project_releases(
        cls, forge: str, namespace: str, repo_name: str
    ) -> Iterable["ProjectReleaseModel"]:
        return (
            sa_session()
            .query(ProjectReleaseModel)
            .join(GitProjectModel)
            .filter(
                ProjectReleaseModel.project_id == GitProjectModel.id,
                GitProjectModel.instance_url == forge,
                GitProjectModel.namespace == namespace,
                GitProjectModel.repo_name == repo_name,
            )
        )

    def __repr__(self):
        return (
            f"GitProjectModel(name={self.namespace}/{self.repo_name}, "
            f"project_url='{self.project_url}')"
        )


class PullRequestModel(BuildsAndTestsConnector, Base):
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
        cls, pr_id: int, namespace: str, repo_name: str, project_url: str
    ) -> "PullRequestModel":
        with sa_session_transaction() as session:
            project = GitProjectModel.get_or_create(
                namespace=namespace, repo_name=repo_name, project_url=project_url
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

    @classmethod
    def get_by_id(cls, id_: int) -> Optional["PullRequestModel"]:
        return sa_session().query(PullRequestModel).filter_by(id=id_).first()

    def __repr__(self):
        return f"PullRequestModel(pr_id={self.pr_id}, project={self.project})"


class IssueModel(BuildsAndTestsConnector, Base):
    __tablename__ = "project_issues"
    id = Column(Integer, primary_key=True)  # our database PK
    issue_id = Column(Integer, index=True)
    project_id = Column(Integer, ForeignKey("git_projects.id"))
    project = relationship("GitProjectModel", back_populates="issues")
    # TODO: Fix this hardcoding! This is only to make propose-downstream work!
    job_config_trigger_type = JobConfigTriggerType.release
    job_trigger_model_type = JobTriggerModelType.issue

    @classmethod
    def get_or_create(
        cls, issue_id: int, namespace: str, repo_name: str, project_url: str
    ) -> "IssueModel":
        with sa_session_transaction() as session:
            project = GitProjectModel.get_or_create(
                namespace=namespace, repo_name=repo_name, project_url=project_url
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

    @classmethod
    def get_by_id(cls, id_: int) -> Optional["IssueModel"]:
        return sa_session().query(IssueModel).filter_by(id=id_).first()

    def __repr__(self):
        return f"IssueModel(id={self.issue_id}, project={self.project})"


class GitBranchModel(BuildsAndTestsConnector, Base):
    __tablename__ = "git_branches"
    id = Column(Integer, primary_key=True)  # our database PK
    name = Column(String)
    project_id = Column(Integer, ForeignKey("git_projects.id"))
    project = relationship("GitProjectModel", back_populates="branches")

    job_config_trigger_type = JobConfigTriggerType.commit
    job_trigger_model_type = JobTriggerModelType.branch_push

    @classmethod
    def get_or_create(
        cls, branch_name: str, namespace: str, repo_name: str, project_url: str
    ) -> "GitBranchModel":
        with sa_session_transaction() as session:
            project = GitProjectModel.get_or_create(
                namespace=namespace, repo_name=repo_name, project_url=project_url
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

    @classmethod
    def get_by_id(cls, id_: int) -> Optional["GitBranchModel"]:
        return sa_session().query(GitBranchModel).filter_by(id=id_).first()

    def __repr__(self):
        return f"GitBranchModel(name={self.name},  project={self.project})"


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
        project_url: str,
        commit_hash: Optional[str] = None,
    ) -> "ProjectReleaseModel":
        with sa_session_transaction() as session:
            project = GitProjectModel.get_or_create(
                namespace=namespace, repo_name=repo_name, project_url=project_url
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

    @classmethod
    def get_by_id(cls, id_: int) -> Optional["ProjectReleaseModel"]:
        return sa_session().query(ProjectReleaseModel).filter_by(id=id_).first()

    def __repr__(self):
        return (
            f"ProjectReleaseModel("
            f"tag_name={self.tag_name}, "
            f"project={self.project})"
        )


AbstractTriggerDbType = Union[
    PullRequestModel,
    ProjectReleaseModel,
    GitBranchModel,
    IssueModel,
]


MODEL_FOR_TRIGGER: Dict[JobTriggerModelType, Type[AbstractTriggerDbType]] = {
    JobTriggerModelType.pull_request: PullRequestModel,
    JobTriggerModelType.branch_push: GitBranchModel,
    JobTriggerModelType.release: ProjectReleaseModel,
    JobTriggerModelType.issue: IssueModel,
}


class JobTriggerModel(Base):
    """
    Model representing a trigger of some packit task.

    It connects PipelineModel (and built/test models via that model)
    with models like PullRequestModel, GitBranchModel or ProjectReleaseModel.

    * It contains type and id of the other database_model.
      * We know table and id that we need to find in that table.
    * Each PipelineModel has to be connected to exactly one JobTriggerModel.
    * There can be multiple PipelineModels for one JobTriggerModel.
      (e.g. For each push to PR, there will be new PipelineModel, but same JobTriggerModel.)
    """

    __tablename__ = "job_triggers"
    id = Column(Integer, primary_key=True)  # our database PK
    type = Column(Enum(JobTriggerModelType))
    trigger_id = Column(Integer)

    runs = relationship("PipelineModel", back_populates="job_trigger")

    @classmethod
    def get_or_create(
        cls, type: JobTriggerModelType, trigger_id: int
    ) -> "JobTriggerModel":
        with sa_session_transaction() as session:
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

    @classmethod
    def get_by_id(cls, id_: int) -> Optional["JobTriggerModel"]:
        return sa_session().query(JobTriggerModel).filter_by(id=id_).first()

    def get_trigger_object(self) -> Optional[AbstractTriggerDbType]:
        return (
            sa_session()
            .query(MODEL_FOR_TRIGGER[self.type])
            .filter_by(id=self.trigger_id)
            .first()
        )

    def __repr__(self):
        return f"JobTriggerModel(type={self.type}, trigger_id={self.trigger_id})"


class PipelineModel(Base):
    """
    Represents one pipeline.

    Connects JobTriggerModel (and triggers like PullRequestModel via that model) with
    build/test models like  SRPMBuildModel, CoprBuildTargetModel, KojiBuildTargetModel,
    and TFTTestRunTargetModel.

    * One model of each build/test model can be connected.
    * Each build/test model can be connected to multiple PipelineModels (e.g. on retrigger).
    * Each PipelineModel has to be connected to exactly one JobTriggerModel.
    * There can be multiple PipelineModels for one JobTriggerModel.
      (e.g. For each push to PR, there will be new PipelineModel, but same JobTriggerModel.)
    """

    __tablename__ = "pipelines"
    id = Column(Integer, primary_key=True)  # our database PK
    # datetime.utcnow instead of datetime.utcnow() because its an argument to the function
    # so it will run when the model is initiated, not when the table is made
    datetime = Column(DateTime, default=datetime.utcnow)

    job_trigger_id = Column(Integer, ForeignKey("job_triggers.id"))
    job_trigger = relationship("JobTriggerModel", back_populates="runs")

    srpm_build_id = Column(Integer, ForeignKey("srpm_builds.id"))
    srpm_build = relationship("SRPMBuildModel", back_populates="runs")
    copr_build_id = Column(Integer, ForeignKey("copr_build_targets.id"))
    copr_build = relationship("CoprBuildTargetModel", back_populates="runs")
    koji_build_id = Column(Integer, ForeignKey("koji_build_targets.id"))
    koji_build = relationship("KojiBuildTargetModel", back_populates="runs")
    test_run_id = Column(Integer, ForeignKey("tft_test_run_targets.id"))
    test_run = relationship("TFTTestRunTargetModel", back_populates="runs")
    propose_downstream_run_id = Column(
        Integer, ForeignKey("propose_downstream_runs.id")
    )
    propose_downstream_run = relationship(
        "ProposeDownstreamModel", back_populates="runs"
    )

    @classmethod
    def create(cls, type: JobTriggerModelType, trigger_id: int) -> "PipelineModel":
        with sa_session_transaction() as session:
            run_model = PipelineModel()
            run_model.job_trigger = JobTriggerModel.get_or_create(
                type=type, trigger_id=trigger_id
            )
            session.add(run_model)
            return run_model

    def get_trigger_object(self) -> AbstractTriggerDbType:
        return self.job_trigger.get_trigger_object()

    def __repr__(self):
        return f"PipelineModel(id={self.id}, datetime='{datetime}', job_trigger={self.job_trigger})"

    @classmethod
    def __query_merged_runs(cls):
        return sa_session().query(
            func.min(PipelineModel.id).label("merged_id"),
            PipelineModel.srpm_build_id,
            func.array_agg(psql_array([PipelineModel.copr_build_id])).label(
                "copr_build_id"
            ),
            func.array_agg(psql_array([PipelineModel.koji_build_id])).label(
                "koji_build_id"
            ),
            func.array_agg(psql_array([PipelineModel.test_run_id])).label(
                "test_run_id"
            ),
            func.array_agg(psql_array([PipelineModel.propose_downstream_run_id])).label(
                "propose_downstream_run_id",
            ),
        )

    @classmethod
    def get_merged_chroots(cls, first: int, last: int) -> Iterable["PipelineModel"]:
        return (
            cls.__query_merged_runs()
            .group_by(
                PipelineModel.srpm_build_id,
                case(
                    [(PipelineModel.srpm_build_id.isnot(null()), 0)],
                    else_=PipelineModel.id,
                ),
            )
            .order_by(desc("merged_id"))
            .slice(first, last)
        )

    @classmethod
    def get_merged_run(cls, first_id: int) -> Optional[Iterable["PipelineModel"]]:
        return (
            cls.__query_merged_runs()
            .filter(PipelineModel.id >= first_id, PipelineModel.id <= first_id + 100)
            .group_by(
                PipelineModel.srpm_build_id,
                case(
                    [(PipelineModel.srpm_build_id.isnot(null()), 0)],
                    else_=PipelineModel.id,
                ),
            )
            .first()
        )

    @classmethod
    def get_run(cls, id_: int) -> Optional["PipelineModel"]:
        return sa_session().query(PipelineModel).filter_by(id=id_).first()


class CoprBuildTargetModel(ProjectAndTriggersConnector, Base):
    """
    Representation of Copr build for one target.
    """

    __tablename__ = "copr_build_targets"
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

    runs = relationship("PipelineModel", back_populates="copr_build")

    def set_built_packages(self, built_packages):
        with sa_session_transaction() as session:
            self.built_packages = built_packages
            session.add(self)

    def set_start_time(self, start_time: datetime):
        with sa_session_transaction() as session:
            self.build_start_time = start_time
            session.add(self)

    def set_end_time(self, end_time: datetime):
        with sa_session_transaction() as session:
            self.build_finished_time = end_time
            session.add(self)

    def set_status(self, status: str):
        with sa_session_transaction() as session:
            self.status = status
            session.add(self)

    def set_build_logs_url(self, build_logs: str):
        with sa_session_transaction() as session:
            self.build_logs_url = build_logs
            session.add(self)

    def get_srpm_build(self) -> Optional["SRPMBuildModel"]:
        # All SRPMBuild models for all the runs have to be same.
        return self.runs[0].srpm_build if self.runs else None

    @classmethod
    def get_by_id(cls, id_: int) -> Optional["CoprBuildTargetModel"]:
        return sa_session().query(CoprBuildTargetModel).filter_by(id=id_).first()

    @classmethod
    def get_all(cls) -> Iterable["CoprBuildTargetModel"]:
        return (
            sa_session()
            .query(CoprBuildTargetModel)
            .order_by(desc(CoprBuildTargetModel.id))
        )

    @classmethod
    def get_merged_chroots(
        cls, first: int, last: int
    ) -> Iterable["CoprBuildTargetModel"]:
        """Returns a list of unique build ids with merged status, chroots
        Details:
        https://github.com/packit/packit-service/pull/674#discussion_r439819852
        """
        return (
            sa_session()
            .query(
                # We need something to order our merged builds by,
                # so set new_id to be min(ids of to-be-merged rows)
                func.min(CoprBuildTargetModel.id).label("new_id"),
                # Select identical element(s)
                CoprBuildTargetModel.build_id,
                # Merge chroots and statuses from different rows into one
                func.array_agg(psql_array([CoprBuildTargetModel.target])).label(
                    "target"
                ),
                func.array_agg(psql_array([CoprBuildTargetModel.status])).label(
                    "status"
                ),
                func.array_agg(psql_array([CoprBuildTargetModel.id])).label(
                    "packit_id_per_chroot"
                ),
            )
            .group_by(CoprBuildTargetModel.build_id)  # Group by identical element(s)
            .order_by(desc("new_id"))
            .slice(first, last)
        )

    # Returns all builds with that build_id, irrespective of target
    @classmethod
    def get_all_by_build_id(
        cls, build_id: Union[str, int]
    ) -> Iterable["CoprBuildTargetModel"]:
        if isinstance(build_id, int):
            # See the comment in get_by_build_id()
            build_id = str(build_id)
        return sa_session().query(CoprBuildTargetModel).filter_by(build_id=build_id)

    @classmethod
    def get_all_by_status(cls, status: str) -> Iterable["CoprBuildTargetModel"]:
        """Returns all builds which currently have the given status."""
        return sa_session().query(CoprBuildTargetModel).filter_by(status=status)

    # returns the build matching the build_id and the target
    @classmethod
    def get_by_build_id(
        cls, build_id: Union[str, int], target: str = None
    ) -> Optional["CoprBuildTargetModel"]:
        if isinstance(build_id, int):
            # PG is pesky about this:
            #   LINE 3: WHERE copr_builds.build_id = 1245767 AND copr_builds.target ...
            #   HINT:  No operator matches the given name and argument type(s).
            #   You might need to add explicit type casts.
            build_id = str(build_id)
        query = sa_session().query(CoprBuildTargetModel).filter_by(build_id=build_id)
        if target:
            query = query.filter_by(target=target)
        return query.first()

    @staticmethod
    def get_all_by(
        project_name: str,
        commit_sha: str,
        owner: str = None,
        target: str = None,
    ) -> Iterable["CoprBuildTargetModel"]:
        """
        All owner/project_name builds sorted from latest to oldest
        with the given commit_sha and optional target.
        """
        non_none_args = {
            arg: value for arg, value in locals().items() if value is not None
        }

        return (
            sa_session()
            .query(CoprBuildTargetModel)
            .filter_by(**non_none_args)
            .order_by(CoprBuildTargetModel.build_id.desc())
        )

    @classmethod
    def get_all_by_commit(cls, commit_sha: str) -> Iterable["CoprBuildTargetModel"]:
        """Returns all builds that match a given commit sha"""
        return sa_session().query(CoprBuildTargetModel).filter_by(commit_sha=commit_sha)

    @classmethod
    def create(
        cls,
        build_id: str,
        commit_sha: str,
        project_name: str,
        owner: str,
        web_url: str,
        target: str,
        status: str,
        run_model: "PipelineModel",
        task_accepted_time: Optional[datetime] = None,
    ) -> "CoprBuildTargetModel":
        with sa_session_transaction() as session:
            build = cls()
            build.build_id = build_id
            build.status = status
            build.project_name = project_name
            build.owner = owner
            build.commit_sha = commit_sha
            build.web_url = web_url
            build.target = target
            build.task_accepted_time = task_accepted_time
            session.add(build)

            if run_model.copr_build:
                # Clone run model
                new_run_model = PipelineModel.create(
                    type=run_model.job_trigger.type,
                    trigger_id=run_model.job_trigger.trigger_id,
                )
                new_run_model.srpm_build = run_model.srpm_build
                new_run_model.copr_build = build
                session.add(new_run_model)
            else:
                run_model.copr_build = build
                session.add(run_model)

            return build

    @classmethod
    def get(
        cls,
        build_id: str,
        target: str,
    ) -> Optional["CoprBuildTargetModel"]:
        return cls.get_by_build_id(build_id, target)

    def __repr__(self):
        return f"COPRBuildModel(id={self.id}, build_submitted_time={self.build_submitted_time})"


class KojiBuildTargetModel(ProjectAndTriggersConnector, Base):
    """we create an entry for every target"""

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

    def set_status(self, status: str):
        with sa_session_transaction() as session:
            self.status = status
            session.add(self)

    def set_build_logs_url(self, build_logs: str):
        with sa_session_transaction() as session:
            self.build_logs_url = build_logs
            session.add(self)

    def set_web_url(self, web_url: str):
        with sa_session_transaction() as session:
            self.web_url = web_url
            session.add(self)

    def set_build_start_time(self, build_start_time: Optional[DateTime]):
        with sa_session_transaction() as session:
            self.build_start_time = build_start_time
            session.add(self)

    def set_build_finished_time(self, build_finished_time: Optional[DateTime]):
        with sa_session_transaction() as session:
            self.build_finished_time = build_finished_time
            session.add(self)

    def set_build_submitted_time(self, build_submitted_time: Optional[DateTime]):
        with sa_session_transaction() as session:
            self.build_submitted_time = build_submitted_time
            session.add(self)

    def set_scratch(self, value: bool):
        with sa_session_transaction() as session:
            self.scratch = value
            session.add(self)

    def get_srpm_build(self) -> Optional["SRPMBuildModel"]:
        # All SRPMBuild models for all the runs have to be same.
        return self.runs[0].srpm_build if self.runs else None

    @classmethod
    def get_by_id(cls, id_: int) -> Optional["KojiBuildTargetModel"]:
        return sa_session().query(KojiBuildTargetModel).filter_by(id=id_).first()

    @classmethod
    def get_all(cls) -> Iterable["KojiBuildTargetModel"]:
        return sa_session().query(KojiBuildTargetModel)

    @classmethod
    def get_range(cls, first: int, last: int) -> Iterable["KojiBuildTargetModel"]:
        return (
            sa_session()
            .query(KojiBuildTargetModel)
            .order_by(desc(KojiBuildTargetModel.id))
            .slice(first, last)
        )

    @classmethod
    def get_all_by_build_id(
        cls, build_id: Union[str, int]
    ) -> Iterable["KojiBuildTargetModel"]:
        """
        Returns all builds with that build_id, irrespective of target.
        """
        if isinstance(build_id, int):
            # See the comment in get_by_build_id()
            build_id = str(build_id)
        return sa_session().query(KojiBuildTargetModel).filter_by(build_id=build_id)

    @classmethod
    def get_by_build_id(
        cls, build_id: Union[str, int], target: Optional[str] = None
    ) -> Optional["KojiBuildTargetModel"]:
        """
        Returns the first build matching the build_id and optionally the target.
        """
        if isinstance(build_id, int):
            # PG is pesky about this:
            #   LINE 3: WHERE koji_builds.build_id = 1245767 AND koji_builds.target ...
            #   HINT:  No operator matches the given name and argument type(s).
            #   You might need to add explicit type casts.
            build_id = str(build_id)
        query = sa_session().query(KojiBuildTargetModel).filter_by(build_id=build_id)
        if target:
            query = query.filter_by(target=target)
        return query.first()

    @classmethod
    def create(
        cls,
        build_id: str,
        commit_sha: str,
        web_url: str,
        target: str,
        status: str,
        scratch: bool,
        run_model: "PipelineModel",
    ) -> "KojiBuildTargetModel":
        with sa_session_transaction() as session:
            build = cls()
            build.build_id = build_id
            build.status = status
            build.commit_sha = commit_sha
            build.web_url = web_url
            build.target = target
            build.scratch = scratch
            session.add(build)

            if run_model.koji_build:
                # Clone run model
                new_run_model = PipelineModel.create(
                    type=run_model.job_trigger.type,
                    trigger_id=run_model.job_trigger.trigger_id,
                )
                new_run_model.srpm_build = run_model.srpm_build
                new_run_model.koji_build = build
                session.add(new_run_model)
            else:
                run_model.koji_build = build
                session.add(run_model)

            return build

    @classmethod
    def get(
        cls,
        build_id: str,
        target: str,
    ) -> Optional["KojiBuildTargetModel"]:
        return cls.get_by_build_id(build_id, target)

    def __repr__(self):
        return (
            f"KojiBuildTargetModel(id={self.id}, "
            f"build_submitted_time={self.build_submitted_time})"
        )


class SRPMBuildModel(ProjectAndTriggersConnector, Base):
    __tablename__ = "srpm_builds"
    id = Column(Integer, primary_key=True)
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

    runs = relationship("PipelineModel", back_populates="srpm_build")

    @classmethod
    def create_with_new_run(
        cls,
        trigger_model: AbstractTriggerDbType,
        commit_sha: str,
        copr_build_id: Optional[str] = None,
        copr_web_url: Optional[str] = None,
    ) -> Tuple["SRPMBuildModel", "PipelineModel"]:
        """
        Create a new model for SRPM and connect it to the PipelineModel.

        * New SRPMBuildModel model will have connection to a new PipelineModel.
        * The newly created PipelineModel can reuse existing JobTriggerModel
          (e.g.: one pull-request can have multiple runs).

        More specifically:
        * On PR creation:
          -> SRPMBuildModel is created.
          -> New PipelineModel is created.
          -> JobTriggerModel is created.
        * On `/packit build` comment or new push:
          -> SRPMBuildModel is created.
          -> New PipelineModel is created.
          -> JobTriggerModel is reused.
        * On `/packit test` comment:
          -> SRPMBuildModel and CoprBuildTargetModel are reused.
          -> New TFTTestRunTargetModel is created.
          -> New PipelineModel is created and
             collects this new TFTTestRunTargetModel with old SRPMBuildModel and
             CoprBuildTargetModel.
        """
        with sa_session_transaction() as session:
            srpm_build = cls()
            srpm_build.status = "pending"
            srpm_build.commit_sha = commit_sha
            srpm_build.copr_build_id = copr_build_id
            srpm_build.copr_web_url = copr_web_url
            session.add(srpm_build)

            # Create a new run model, reuse trigger_model if it exists:
            new_run_model = PipelineModel.create(
                type=trigger_model.job_trigger_model_type, trigger_id=trigger_model.id
            )
            new_run_model.srpm_build = srpm_build
            session.add(new_run_model)

            return srpm_build, new_run_model

    @classmethod
    def get_by_id(
        cls,
        id_: int,
    ) -> Optional["SRPMBuildModel"]:
        return sa_session().query(SRPMBuildModel).filter_by(id=id_).first()

    @classmethod
    def get(cls, first: int, last: int) -> Iterable["SRPMBuildModel"]:
        return (
            sa_session()
            .query(SRPMBuildModel)
            .order_by(desc(SRPMBuildModel.id))
            .slice(first, last)
        )

    @classmethod
    def get_by_copr_build_id(
        cls, copr_build_id: Union[str, int]
    ) -> Optional["SRPMBuildModel"]:
        if isinstance(copr_build_id, int):
            copr_build_id = str(copr_build_id)
        return (
            sa_session()
            .query(SRPMBuildModel)
            .filter_by(copr_build_id=copr_build_id)
            .first()
        )

    @classmethod
    def get_older_than(cls, delta: timedelta) -> Iterable["SRPMBuildModel"]:
        """Return builds older than delta, whose logs/artifacts haven't been discarded yet."""
        delta_ago = datetime.now(timezone.utc) - delta
        return (
            sa_session()
            .query(SRPMBuildModel)
            .filter(
                SRPMBuildModel.build_submitted_time < delta_ago,
                SRPMBuildModel.logs.isnot(None),
            )
        )

    def set_url(self, url: Optional[str]) -> None:
        with sa_session_transaction() as session:
            self.url = null() if url is None else url
            session.add(self)

    def set_logs(self, logs: Optional[str]) -> None:
        with sa_session_transaction() as session:
            self.logs = null() if logs is None else logs
            session.add(self)

    def set_start_time(self, start_time: datetime) -> None:
        with sa_session_transaction() as session:
            self.build_start_time = start_time
            session.add(self)

    def set_end_time(self, end_time: datetime) -> None:
        with sa_session_transaction() as session:
            self.build_finished_time = end_time
            session.add(self)

    def set_build_logs_url(self, logs_url: str) -> None:
        with sa_session_transaction() as session:
            self.logs_url = logs_url
            session.add(self)

    def set_status(self, status: str) -> None:
        with sa_session_transaction() as session:
            self.status = status
            session.add(self)

    def __repr__(self):
        return f"SRPMBuildModel(id={self.id}, build_submitted_time={self.build_submitted_time})"


class AllowlistStatus(str, enum.Enum):
    approved_automatically = ALLOWLIST_CONSTANTS["approved_automatically"]
    waiting = ALLOWLIST_CONSTANTS["waiting"]
    approved_manually = ALLOWLIST_CONSTANTS["approved_manually"]
    denied = ALLOWLIST_CONSTANTS["denied"]


class AllowlistModel(Base):
    __tablename__ = "allowlist"
    id = Column(Integer, primary_key=True)
    namespace = Column(String, index=True)  # renamed from account_name
    status = Column(Enum(AllowlistStatus))
    fas_account = Column(String)

    @classmethod
    def add_namespace(
        cls, namespace: str, status: str, fas_account: Optional[str] = None
    ):
        """
        Adds namespace with specific status to the allowlist. If namespace is present,
        just changes the status.

        Args:
            namespace (str): Namespace to be added. Can be `github.com/namespace`
                or specific repository `github.com/namespace/repository.git`.
            status (str): Status to be set. AllowlistStatus enumeration as string.
            fas_account (Optional[str]): FAS login, in case the namespace was automatically
                approved through the FAS login of user that installed GitHub App.

                Defaults to `None`.

        Returns:
            Newly created entry or entry that represents requested namespace.
        """
        with sa_session_transaction() as session:
            namespace_entry = cls.get_namespace(namespace)
            if not namespace_entry:
                namespace_entry = cls()
                namespace_entry.namespace = namespace

            namespace_entry.status = status
            if fas_account:
                namespace_entry.fas_account = fas_account

            session.add(namespace_entry)
            return namespace_entry

    @classmethod
    def get_namespace(cls, namespace: str) -> Optional["AllowlistModel"]:
        """
        Retrieves namespace from the allowlist.

        Args:
            namespace (str): Namespace to be added. Can be `github.com/namespace`
                or specific repository `github.com/namespace/repository.git`.

        Returns:
            Entry that represents namespace or `None` if cannot be found.
        """
        return sa_session().query(AllowlistModel).filter_by(namespace=namespace).first()

    @classmethod
    def get_namespaces_by_status(cls, status: str) -> Iterable["AllowlistModel"]:
        """
        Get list of namespaces with specific status.

        Args:
            status (str): Status of the namespaces. AllowlistStatus enumeration as string.

        Returns:
            List of the namespaces with set status.
        """
        return sa_session().query(AllowlistModel).filter_by(status=status)

    @classmethod
    def remove_namespace(cls, namespace: str):
        with sa_session_transaction() as session:
            namespace_entry = session.query(AllowlistModel).filter_by(
                namespace=namespace
            )
            if namespace_entry.one_or_none():
                namespace_entry.delete()

    @classmethod
    def get_all(cls) -> Iterable["AllowlistModel"]:
        return sa_session().query(AllowlistModel)

    def to_dict(self) -> Dict[str, str]:
        return {
            "namespace": self.namespace,
            "status": self.status,
            "fas_account": self.fas_account,
        }

    def __repr__(self):
        return (
            f'<AllowlistModel(namespace="{self.namespace}", '
            f'status="{self.status}", '
            f'fas_account="{self.fas_account}")>'
        )


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


class TFTTestRunTargetModel(ProjectAndTriggersConnector, Base):
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

    runs = relationship("PipelineModel", back_populates="test_run")

    def set_status(self, status: TestingFarmResult, created: Optional[DateTime] = None):
        """
        set status of the TF run and optionally set the created datetime as well
        """
        with sa_session_transaction() as session:
            self.status = status
            if created and not self.submitted_time:
                self.submitted_time = created
            session.add(self)

    def set_web_url(self, web_url: str):
        with sa_session_transaction() as session:
            self.web_url = web_url
            session.add(self)

    @classmethod
    def create(
        cls,
        pipeline_id: str,
        commit_sha: str,
        status: TestingFarmResult,
        target: str,
        run_model: "PipelineModel",
        web_url: Optional[str] = None,
        data: dict = None,
        identifier: Optional[str] = None,
    ) -> "TFTTestRunTargetModel":
        with sa_session_transaction() as session:
            test_run = cls()
            test_run.pipeline_id = pipeline_id
            test_run.identifier = identifier
            test_run.commit_sha = commit_sha
            test_run.status = status
            test_run.target = target
            test_run.web_url = web_url
            test_run.data = data
            session.add(test_run)

            if run_model.test_run:
                # Clone run model
                new_run_model = PipelineModel.create(
                    type=run_model.job_trigger.type,
                    trigger_id=run_model.job_trigger.trigger_id,
                )
                new_run_model.srpm_build = run_model.srpm_build
                new_run_model.copr_build = run_model.copr_build
                new_run_model.test_run = test_run
                session.add(new_run_model)
            else:
                run_model.test_run = test_run
                session.add(run_model)

            return test_run

    @classmethod
    def get_by_pipeline_id(cls, pipeline_id: str) -> Optional["TFTTestRunTargetModel"]:
        return (
            sa_session()
            .query(TFTTestRunTargetModel)
            .filter_by(pipeline_id=pipeline_id)
            .first()
        )

    @classmethod
    def get_all_by_status(
        cls, *status: TestingFarmResult
    ) -> Iterable["TFTTestRunTargetModel"]:
        """Returns all runs which currently have their status set to one
        of the requested statuses."""
        return (
            sa_session()
            .query(TFTTestRunTargetModel)
            .filter(TFTTestRunTargetModel.status.in_(status))
        )

    @classmethod
    def get_by_id(cls, id: int) -> Optional["TFTTestRunTargetModel"]:
        return sa_session().query(TFTTestRunTargetModel).filter_by(id=id).first()

    @staticmethod
    def get_all_by_commit_target(
        commit_sha: str,
        target: str = None,
    ) -> Iterable["TFTTestRunTargetModel"]:
        """
        All tests with the given commit_sha and optional target.
        """
        non_none_args = {
            arg: value for arg, value in locals().items() if value is not None
        }

        return sa_session().query(TFTTestRunTargetModel).filter_by(**non_none_args)

    @classmethod
    def get_range(cls, first: int, last: int) -> Iterable["TFTTestRunTargetModel"]:
        return (
            sa_session()
            .query(TFTTestRunTargetModel)
            .order_by(desc(TFTTestRunTargetModel.id))
            .slice(first, last)
        )

    def __repr__(self):
        return f"TFTTestRunTargetModel(id={self.id}, pipeline_id={self.pipeline_id})"


class ProposeDownstreamTargetStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    error = "error"
    retry = "retry"
    submitted = "submitted"


class ProposeDownstreamTargetModel(ProjectAndTriggersConnector, Base):
    __tablename__ = "propose_downstream_run_targets"
    id = Column(Integer, primary_key=True)
    branch = Column(String, default="unknown")
    downstream_pr_url = Column(String)
    status = Column(Enum(ProposeDownstreamTargetStatus))
    submitted_time = Column(DateTime, default=datetime.utcnow)
    start_time = Column(DateTime)
    finished_time = Column(DateTime)
    logs = Column(Text)
    propose_downstream_id = Column(Integer, ForeignKey("propose_downstream_runs.id"))

    propose_downstream = relationship(
        "ProposeDownstreamModel", back_populates="propose_downstream_targets"
    )

    def __repr__(self) -> str:
        return f"ProposeDownstreamTargetModel(id={self.id})"

    @classmethod
    def create(
        cls, status: ProposeDownstreamTargetStatus, branch: str
    ) -> "ProposeDownstreamTargetModel":
        with sa_session_transaction() as session:
            propose_downstream_target = cls()
            propose_downstream_target.status = status
            propose_downstream_target.branch = branch
            session.add(propose_downstream_target)
            return propose_downstream_target

    def set_status(self, status: ProposeDownstreamTargetStatus) -> None:
        with sa_session_transaction() as session:
            self.status = status
            session.add(self)

    def set_downstream_pr_url(self, downstream_pr_url: str) -> None:
        with sa_session_transaction() as session:
            self.downstream_pr_url = downstream_pr_url
            session.add(self)

    def set_start_time(self, start_time: DateTime) -> None:
        with sa_session_transaction() as session:
            self.start_time = start_time
            session.add(self)

    def set_finished_time(self, finished_time: DateTime) -> None:
        with sa_session_transaction() as session:
            self.finished_time = finished_time
            session.add(self)

    def set_logs(self, logs: str) -> None:
        with sa_session_transaction() as session:
            self.logs = logs
            session.add(self)

    @classmethod
    def get_by_id(cls, id_: int) -> Optional["ProposeDownstreamTargetModel"]:
        return (
            sa_session().query(ProposeDownstreamTargetModel).filter_by(id=id_).first()
        )


class ProposeDownstreamStatus(str, enum.Enum):
    running = "running"
    finished = "finished"
    error = "error"


class ProposeDownstreamModel(ProjectAndTriggersConnector, Base):
    __tablename__ = "propose_downstream_runs"
    id = Column(Integer, primary_key=True)
    status = Column(Enum(ProposeDownstreamStatus))
    submitted_time = Column(DateTime, default=datetime.utcnow)

    runs = relationship("PipelineModel", back_populates="propose_downstream_run")
    propose_downstream_targets = relationship(
        "ProposeDownstreamTargetModel", back_populates="propose_downstream"
    )

    def __repr__(self) -> str:
        return f"ProposeDownstreamModel(id={self.id}, submitted_time={self.submitted_time})"

    @classmethod
    def create_with_new_run(
        cls,
        status: ProposeDownstreamStatus,
        trigger_model: AbstractTriggerDbType,
    ) -> Tuple["ProposeDownstreamModel", "PipelineModel"]:
        """
        Create a new model for ProposeDownstream and connect it to the PipelineModel.

        * New ProposeDownstreamModel model will have connection to a new PipelineModel.
        * The newly created PipelineModel can reuse existing JobTriggerModel
          (e.g.: one IssueModel can have multiple runs).

        More specifically:
        * On `/packit propose-downstream` issue comment:
          -> ProposeDownstreamModel is created.
          -> New PipelineModel is created.
          -> JobTriggerModel is created.
        * Something went wrong, after correction and another `/packit propose-downstream` comment:
          -> ProposeDownstreamModel is created.
          -> PipelineModel is created.
          -> JobTriggerModel is reused.
        * TODO: we will use propose-downstream in commit-checks - fill in once it's implemented
        """
        with sa_session_transaction() as session:
            propose_downstream = cls()
            propose_downstream.status = status
            session.add(propose_downstream)

            # Create a pipeline, reuse trigger_model if it exists:
            pipeline = PipelineModel.create(
                type=trigger_model.job_trigger_model_type, trigger_id=trigger_model.id
            )
            pipeline.propose_downstream_run = propose_downstream
            session.add(pipeline)

            return propose_downstream, pipeline

    def set_status(self, status: ProposeDownstreamStatus) -> None:
        with sa_session_transaction() as session:
            self.status = status
            session.add(self)

    @classmethod
    def get_by_id(cls, id_: int) -> Optional["ProposeDownstreamModel"]:
        return sa_session().query(ProposeDownstreamModel).filter_by(id=id_).first()

    @classmethod
    def get_all_by_status(cls, status: str) -> Iterable["ProposeDownstreamModel"]:
        return sa_session().query(ProposeDownstreamModel).filter_by(status=status)

    @classmethod
    def get_range(cls, first: int, last: int) -> Iterable["ProposeDownstreamModel"]:
        return (
            sa_session()
            .query(ProposeDownstreamModel)
            .order_by(desc(ProposeDownstreamModel.id))
            .slice(first, last)
        )


AbstractBuildTestDbType = Union[
    CoprBuildTargetModel,
    KojiBuildTargetModel,
    SRPMBuildModel,
    TFTTestRunTargetModel,
    ProposeDownstreamModel,
]


class ProjectAuthenticationIssueModel(Base):
    __tablename__ = "project_authentication_issue"

    id = Column(Integer, primary_key=True)
    project = relationship(
        "GitProjectModel", back_populates="project_authentication_issue"
    )
    # Check to know if we created a issue for the repo.
    issue_created = Column(Boolean)
    project_id = Column(Integer, ForeignKey("git_projects.id"))

    @classmethod
    def get_project(
        cls, namespace: str, repo_name: str, project_url: str
    ) -> Optional["ProjectAuthenticationIssueModel"]:
        with sa_session_transaction() as session:
            project = GitProjectModel.get_or_create(
                namespace=namespace, repo_name=repo_name, project_url=project_url
            )
            return (
                session.query(ProjectAuthenticationIssueModel)
                .filter_by(project_id=project.id)
                .first()
            )

    @classmethod
    def create(
        cls, namespace: str, repo_name: str, project_url: str, issue_created: bool
    ) -> "ProjectAuthenticationIssueModel":
        with sa_session_transaction() as session:
            project = GitProjectModel.get_or_create(
                namespace=namespace, repo_name=repo_name, project_url=project_url
            )

            project_authentication_issue = cls()
            project_authentication_issue.issue_created = issue_created
            project_authentication_issue.project_id = project.id
            session.add(project_authentication_issue)

            return project_authentication_issue

    def __repr__(self):
        return (
            f"ProjectAuthenticationIssueModel(project={self.project}, "
            f"issue_created={self.issue_created})"
        )


class GithubInstallationModel(Base):
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
    def get_project(cls, repository: str) -> "GitProjectModel":
        namespace, repo_name = repository.split("/")
        return GitProjectModel.get_or_create(
            namespace=namespace,
            repo_name=repo_name,
            project_url=f"https://github.com/{namespace}/{repo_name}",
        )

    @classmethod
    def get_by_id(cls, id: int) -> Optional["GithubInstallationModel"]:
        return sa_session().query(GithubInstallationModel).filter_by(id=id).first()

    @classmethod
    def get_by_account_login(
        cls, account_login: str
    ) -> Optional["GithubInstallationModel"]:
        return (
            sa_session()
            .query(GithubInstallationModel)
            .filter_by(account_login=account_login)
            .first()
        )

    @classmethod
    def get_all(cls) -> Iterable["GithubInstallationModel"]:
        return sa_session().query(GithubInstallationModel)

    @classmethod
    def create_or_update(cls, event):
        with sa_session_transaction() as session:
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

    def to_dict(self):
        return {
            "account_login": self.account_login,
            "account_id": self.account_id,
            "account_type": self.account_type,
            "account_url": self.account_url,
            "sender_login": self.sender_login,
            "sender_id": self.sender_id,
            # Inconsistent with other API endpoints, kept for readability for
            # internal use, if necessary
            "created_at": optional_time(self.created_at),
        }

    def __repr__(self):
        return f"GithubInstallationModel(id={self.id}, account={self.account_login})"


class SourceGitPRDistGitPRModel(Base):
    __tablename__ = "source_git_pr_dist_git_pr"
    id = Column(Integer, primary_key=True)  # our database PK
    source_git_pull_request_id = Column(
        Integer, ForeignKey("pull_requests.id"), unique=True, index=True
    )
    dist_git_pull_request_id = Column(
        Integer, ForeignKey("pull_requests.id"), unique=True, index=True
    )
    source_git_pull_request = relationship(
        "PullRequestModel",
        primaryjoin="SourceGitPRDistGitPRModel.source_git_pull_request_id==PullRequestModel.id",
        uselist=False,
    )
    dist_git_pull_request = relationship(
        "PullRequestModel",
        primaryjoin="SourceGitPRDistGitPRModel.dist_git_pull_request_id==PullRequestModel.id",
        uselist=False,
    )

    @classmethod
    def get_or_create(
        cls,
        source_git_pr_id: int,
        source_git_namespace: str,
        source_git_repo_name: str,
        source_git_project_url: str,
        dist_git_pr_id: int,
        dist_git_namespace: str,
        dist_git_repo_name: str,
        dist_git_project_url: str,
    ) -> "SourceGitPRDistGitPRModel":
        with sa_session_transaction() as session:
            source_git_pull_request = PullRequestModel.get_or_create(
                pr_id=source_git_pr_id,
                namespace=source_git_namespace,
                repo_name=source_git_repo_name,
                project_url=source_git_project_url,
            )
            dist_git_pull_request = PullRequestModel.get_or_create(
                pr_id=dist_git_pr_id,
                namespace=dist_git_namespace,
                repo_name=dist_git_repo_name,
                project_url=dist_git_project_url,
            )
            rel = (
                session.query(SourceGitPRDistGitPRModel)
                .filter_by(source_git_pull_request_id=source_git_pull_request.id)
                .filter_by(dist_git_pull_request_id=dist_git_pull_request.id)
                .one_or_none()
            )
            if not rel:
                rel = SourceGitPRDistGitPRModel()
                rel.source_git_pull_request_id = source_git_pull_request.id
                rel.dist_git_pull_request_id = dist_git_pull_request.id
                session.add(rel)
            return rel

    @classmethod
    def get_by_id(cls, id_: int) -> Optional["SourceGitPRDistGitPRModel"]:
        return (
            sa_session()
            .query(SourceGitPRDistGitPRModel)
            .filter_by(id=id_)
            .one_or_none()
        )

    @classmethod
    def get_by_source_git_id(cls, id_: int) -> Optional["SourceGitPRDistGitPRModel"]:
        return (
            sa_session()
            .query(SourceGitPRDistGitPRModel)
            .filter_by(source_git_pull_request_id=id_)
            .one_or_none()
        )

    @classmethod
    def get_by_dist_git_id(cls, id_: int) -> Optional["SourceGitPRDistGitPRModel"]:
        return (
            sa_session()
            .query(SourceGitPRDistGitPRModel)
            .filter_by(dist_git_pull_request_id=id_)
            .one_or_none()
        )
