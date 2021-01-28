# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
Data layer on top of PSQL using sqlalch
"""
import enum
import logging
import os
from contextlib import contextmanager
from datetime import datetime
from typing import Dict, Iterable, Optional, TYPE_CHECKING, Type, Union
from urllib.parse import urlparse

from packit.config import JobConfigTriggerType
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
)
from sqlalchemy.dialects.postgresql import array as psql_array
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, relationship, scoped_session, sessionmaker
from sqlalchemy.types import ARRAY

from packit_service.constants import WHITELIST_CONSTANTS

logger = logging.getLogger(__name__)
# SQLAlchemy session, get it with `get_sa_session`
session_instance = None


def get_pg_url() -> str:
    """ create postgresql connection string """
    return (
        f"postgres+psycopg2://{os.getenv('POSTGRESQL_USER')}"
        f":{os.getenv('POSTGRESQL_PASSWORD')}@{os.getenv('POSTGRESQL_HOST', 'postgres')}"
        f":{os.getenv('POSTGRESQL_PORT', '5432')}/{os.getenv('POSTGRESQL_DATABASE')}"
    )


engine = create_engine(get_pg_url())
ScopedSession = scoped_session(sessionmaker(bind=engine))


@contextmanager
def get_sa_session() -> Session:
    """ get SQLAlchemy session """
    session = ScopedSession()
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
    """Returns a formatted date-time string if argument is a datetime object

    :param datetime_object: datatime to be converted to string
    :param fmt: format string to be used to produce the string
    :return: formatted string or None
    """
    if datetime_object is None:
        return None
    return datetime_object.strftime(fmt)


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
        with get_sa_session() as session:
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
        with get_sa_session() as session:
            projects = session.query(GitProjectModel).order_by(
                GitProjectModel.namespace
            )[first:last]
            return projects

    @classmethod
    def get_namespace(cls, forge: str, namespace: str) -> Iterable["GitProjectModel"]:
        """Return projects of given forge and namespace"""
        with get_sa_session() as session:
            projects = (
                session.query(GitProjectModel).filter_by(namespace=namespace).all()
            )
            matched_projects = []
            for project in projects:
                forge_domain = urlparse(project.project_url).hostname
                if forge == forge_domain:
                    matched_projects.append(project)
            return matched_projects

    @classmethod
    def get_project(
        cls, forge: str, namespace: str, repo_name: str
    ) -> Optional["GitProjectModel"]:
        """Return one project which matches said criteria"""
        with get_sa_session() as session:
            project = (
                session.query(cls)
                .filter_by(instance_url=forge, namespace=namespace, repo_name=repo_name)
                .one_or_none()
            )
            return project

    @classmethod
    def get_project_prs(
        cls, first: int, last: int, forge: str, namespace: str, repo_name: str
    ) -> Optional[Iterable["PullRequestModel"]]:
        with get_sa_session() as session:
            pull_requests = (
                session.query(PullRequestModel)
                .join(GitProjectModel)
                .filter(
                    PullRequestModel.project_id == GitProjectModel.id,
                    GitProjectModel.instance_url == forge,
                    GitProjectModel.namespace == namespace,
                    GitProjectModel.repo_name == repo_name,
                )
                .order_by(desc(PullRequestModel.pr_id))[first:last]
            )
            return pull_requests

    @classmethod
    def get_project_issues(
        cls, forge: str, namespace: str, repo_name: str
    ) -> Optional[Iterable["IssueModel"]]:
        with get_sa_session() as session:
            issues = (
                session.query(IssueModel)
                .join(GitProjectModel)
                .filter(
                    IssueModel.project_id == GitProjectModel.id,
                    GitProjectModel.instance_url == forge,
                    GitProjectModel.namespace == namespace,
                    GitProjectModel.repo_name == repo_name,
                )
                .all()
            )
            return issues

    @classmethod
    def get_project_branches(
        cls, forge: str, namespace: str, repo_name: str
    ) -> Optional[Iterable["GitBranchModel"]]:

        with get_sa_session() as session:
            branches = (
                session.query(GitBranchModel)
                .join(GitProjectModel)
                .filter(
                    GitBranchModel.project_id == GitProjectModel.id,
                    GitProjectModel.instance_url == forge,
                    GitProjectModel.namespace == namespace,
                    GitProjectModel.repo_name == repo_name,
                )
                .all()
            )
            return branches

    @classmethod
    def get_project_releases(
        cls, forge: str, namespace: str, repo_name: str
    ) -> Optional[Iterable["ProjectReleaseModel"]]:
        with get_sa_session() as session:
            releases = (
                session.query(ProjectReleaseModel)
                .join(GitProjectModel)
                .filter(
                    ProjectReleaseModel.project_id == GitProjectModel.id,
                    GitProjectModel.instance_url == forge,
                    GitProjectModel.namespace == namespace,
                    GitProjectModel.repo_name == repo_name,
                )
                .all()
            )
            return releases

    def __repr__(self):
        return (
            f"GitProjectModel(name={self.namespace}/{self.repo_name}, "
            f"project_url='{self.project_url}')"
        )


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
    # CentOS Pagure only
    bugzilla = relationship("BugzillaModel", back_populates="pull_request")

    job_config_trigger_type = JobConfigTriggerType.pull_request
    job_trigger_model_type = JobTriggerModelType.pull_request

    @classmethod
    def get_or_create(
        cls, pr_id: int, namespace: str, repo_name: str, project_url: str
    ) -> "PullRequestModel":
        with get_sa_session() as session:
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

    def get_copr_builds(self):
        return JobTriggerModel.get_or_create(
            type=JobTriggerModelType.pull_request, trigger_id=self.id
        ).copr_builds

    def get_koji_builds(self):
        return JobTriggerModel.get_or_create(
            type=JobTriggerModelType.pull_request, trigger_id=self.id
        ).koji_builds

    def get_srpm_builds(self):
        return JobTriggerModel.get_or_create(
            type=JobTriggerModelType.pull_request, trigger_id=self.id
        ).srpm_builds

    def get_test_runs(self):
        return JobTriggerModel.get_or_create(
            type=JobTriggerModelType.pull_request, trigger_id=self.id
        ).test_runs

    @classmethod
    def get_by_id(cls, id_: int) -> Optional["PullRequestModel"]:
        with get_sa_session() as session:
            return session.query(PullRequestModel).filter_by(id=id_).first()

    def __repr__(self):
        return f"PullRequestModel(id={self.pr_id}, project={self.project})"


class IssueModel(Base):
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
        with get_sa_session() as session:
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
        with get_sa_session() as session:
            return session.query(IssueModel).filter_by(id=id_).first()

    def __repr__(self):
        return f"IssueModel(id={self.issue_id}, project={self.project})"


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
        cls, branch_name: str, namespace: str, repo_name: str, project_url: str
    ) -> "GitBranchModel":
        with get_sa_session() as session:
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
        with get_sa_session() as session:
            return session.query(GitBranchModel).filter_by(id=id_).first()

    def get_copr_builds(self):
        return JobTriggerModel.get_or_create(
            type=JobTriggerModelType.branch_push, trigger_id=self.id
        ).copr_builds

    def get_koji_builds(self):
        return JobTriggerModel.get_or_create(
            type=JobTriggerModelType.pull_request, trigger_id=self.id
        ).koji_builds

    def get_srpm_builds(self):
        return JobTriggerModel.get_or_create(
            type=JobTriggerModelType.branch_push, trigger_id=self.id
        ).srpm_builds

    def get_test_runs(self):
        return JobTriggerModel.get_or_create(
            type=JobTriggerModelType.branch_push, trigger_id=self.id
        ).test_runs

    def __repr__(self):
        return f"GitBranchModel(name={self.name},  project={self.project})"


class BugzillaModel(Base):
    __tablename__ = "bugzillas"
    id = Column(Integer, primary_key=True)
    bug_id = Column(Integer, index=True)
    bug_url = Column(String)
    pull_request_id = Column(Integer, ForeignKey("pull_requests.id"))
    pull_request = relationship("PullRequestModel", back_populates="bugzilla")

    @classmethod
    def get_or_create(
        cls,
        pr_id: int,
        namespace: str,
        repo_name: str,
        project_url: str,
        bug_id: int = None,
        bug_url: str = None,
    ) -> "BugzillaModel":
        with get_sa_session() as session:
            pull_request = PullRequestModel.get_or_create(
                pr_id=pr_id,
                namespace=namespace,
                repo_name=repo_name,
                project_url=project_url,
            )
            bugzilla = (
                session.query(BugzillaModel)
                .filter_by(pull_request_id=pull_request.id)
                .first()
            )
            if not bugzilla and bug_id and bug_url:
                bugzilla = BugzillaModel()
                bugzilla.bug_id = bug_id
                bugzilla.bug_url = bug_url
                bugzilla.pull_request_id = pull_request.id
                session.add(bugzilla)
            return bugzilla

    @classmethod
    def get_by_pr(
        cls,
        pr_id: int,
        namespace: str,
        repo_name: str,
        project_url: str,
    ) -> Optional["BugzillaModel"]:
        return cls.get_or_create(
            pr_id=pr_id,
            namespace=namespace,
            repo_name=repo_name,
            project_url=project_url,
        )

    def __repr__(self):
        return f"BugzillaModel(bug_id={self.bug_id}, bug_url={self.bug_url})"


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
        with get_sa_session() as session:
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
        with get_sa_session() as session:
            return session.query(ProjectReleaseModel).filter_by(id=id_).first()

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
    __tablename__ = "build_triggers"
    id = Column(Integer, primary_key=True)  # our database PK
    type = Column(Enum(JobTriggerModelType))
    trigger_id = Column(Integer)
    copr_builds = relationship("CoprBuildModel", back_populates="job_trigger")
    srpm_builds = relationship("SRPMBuildModel", back_populates="job_trigger")
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

    def set_start_time(self, start_time: DateTime):
        with get_sa_session() as session:
            self.build_start_time = start_time
            session.add(self)

    def set_end_time(self, end_time: DateTime):
        with get_sa_session() as session:
            self.build_finished_time = end_time
            session.add(self)

    def set_status(self, status: str):
        with get_sa_session() as session:
            self.status = status
            session.add(self)

    def set_build_logs_url(self, build_logs: str):
        with get_sa_session() as session:
            self.build_logs_url = build_logs
            session.add(self)

    def get_project(self) -> Optional[GitProjectModel]:
        trigger_object = self.job_trigger.get_trigger_object()
        if not trigger_object:
            return None
        return trigger_object.project

    def get_pr_id(self) -> Optional[int]:
        trigger_object = self.job_trigger.get_trigger_object()
        if isinstance(trigger_object, PullRequestModel):
            return trigger_object.pr_id
        return None

    def get_branch_name(self) -> Optional[str]:
        trigger_object = self.job_trigger.get_trigger_object()
        if isinstance(trigger_object, GitBranchModel):
            return trigger_object.name
        return None

    @classmethod
    def get_by_id(cls, id_: int) -> Optional["CoprBuildModel"]:
        with get_sa_session() as session:
            return session.query(CoprBuildModel).filter_by(id=id_).first()

    @classmethod
    def get_all(cls) -> Optional[Iterable["CoprBuildModel"]]:
        with get_sa_session() as session:
            return session.query(CoprBuildModel).order_by(desc(CoprBuildModel.id)).all()

    @classmethod
    def get_merged_chroots(
        cls, first: int, last: int
    ) -> Optional[Iterable["CoprBuildModel"]]:
        """Returns a list of unique build ids with merged status, chroots
        Details:
        https://github.com/packit/packit-service/pull/674#discussion_r439819852
        """
        with get_sa_session() as session:
            builds = (
                session.query(
                    # We need something to order our merged builds by,
                    # so set new_id to be min(ids of to-be-merged rows)
                    func.min(CoprBuildModel.id).label("new_id"),
                    # Select identical element(s)
                    CoprBuildModel.build_id,
                    # Merge chroots and statuses from different rows into one
                    func.array_agg(psql_array([CoprBuildModel.target])).label("target"),
                    func.array_agg(psql_array([CoprBuildModel.status])).label("status"),
                )
                .group_by(CoprBuildModel.build_id)  # Group by identical element(s)
                .order_by(desc("new_id"))[first:last]
            )

            return builds

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
        cls, build_id: Union[str, int], target: str = None
    ) -> Optional["CoprBuildModel"]:
        if isinstance(build_id, int):
            # PG is pesky about this:
            #   LINE 3: WHERE copr_builds.build_id = 1245767 AND copr_builds.target ...
            #   HINT:  No operator matches the given name and argument type(s).
            #   You might need to add explicit type casts.
            build_id = str(build_id)
        with get_sa_session() as session:
            query = session.query(CoprBuildModel).filter_by(build_id=build_id)
            if target:
                query = query.filter_by(target=target)
            return query.first()

    @classmethod
    def get_all_by_owner_and_project(
        cls, owner: str, project_name: str
    ) -> Optional[Iterable["CoprBuildModel"]]:
        """
        All owner/project_name builds sorted from latest to oldest
        """
        with get_sa_session() as session:
            query = (
                session.query(CoprBuildModel)
                .filter_by(owner=owner, project_name=project_name)
                .order_by(CoprBuildModel.build_id.desc())
            )
            return query.all()

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

    def set_web_url(self, web_url: str):
        with get_sa_session() as session:
            self.web_url = web_url
            session.add(self)

    def get_project(self) -> GitProjectModel:
        return self.job_trigger.get_trigger_object().project

    def set_build_start_time(self, build_start_time: Optional[DateTime]):
        with get_sa_session() as session:
            self.build_start_time = build_start_time
            session.add(self)

    def set_build_finished_time(self, build_finished_time: Optional[DateTime]):
        with get_sa_session() as session:
            self.build_finished_time = build_finished_time
            session.add(self)

    def set_build_submitted_time(self, build_submitted_time: Optional[DateTime]):
        with get_sa_session() as session:
            self.build_submitted_time = build_submitted_time
            session.add(self)

    def get_pr_id(self) -> Optional[int]:
        trigger_object = self.job_trigger.get_trigger_object()
        if isinstance(trigger_object, PullRequestModel):
            return trigger_object.pr_id
        return None

    def get_branch_name(self) -> Optional[str]:
        trigger_object = self.job_trigger.get_trigger_object()
        if isinstance(trigger_object, GitBranchModel):
            return trigger_object.name
        return None

    def get_release_tag(self) -> Optional[int]:
        trigger_object = self.job_trigger.get_trigger_object()
        if isinstance(trigger_object, ProjectReleaseModel):
            return trigger_object.tag_name
        return None

    @classmethod
    def get_by_id(cls, id_: int) -> Optional["KojiBuildModel"]:
        with get_sa_session() as session:
            return session.query(KojiBuildModel).filter_by(id=id_).first()

    @classmethod
    def get_all(cls) -> Optional[Iterable["KojiBuildModel"]]:
        with get_sa_session() as session:
            return session.query(KojiBuildModel).all()

    @classmethod
    def get_range(cls, first: int, last: int) -> Optional[Iterable["KojiBuildModel"]]:
        with get_sa_session() as session:
            return session.query(KojiBuildModel).order_by(desc(KojiBuildModel.id))[
                first:last
            ]

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
        cls, build_id: Union[str, int], target: Optional[str] = None
    ) -> Optional["KojiBuildModel"]:
        if isinstance(build_id, int):
            # PG is pesky about this:
            #   LINE 3: WHERE koji_builds.build_id = 1245767 AND koji_builds.target ...
            #   HINT:  No operator matches the given name and argument type(s).
            #   You might need to add explicit type casts.
            build_id = str(build_id)
        with get_sa_session() as session:
            if target:
                return (
                    session.query(KojiBuildModel)
                    .filter_by(build_id=build_id, target=target)
                    .first()
                )
            return session.query(KojiBuildModel).filter_by(build_id=build_id).first()

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
    build_submitted_time = Column(DateTime, default=datetime.utcnow)
    url = Column(Text)

    @classmethod
    def create(
        cls,
        logs: str,
        success: bool,
        trigger_model: AbstractTriggerDbType,
    ) -> "SRPMBuildModel":
        job_trigger = JobTriggerModel.get_or_create(
            type=trigger_model.job_trigger_model_type,
            trigger_id=trigger_model.id,
        )
        with get_sa_session() as session:
            srpm_build = cls()
            srpm_build.logs = logs
            srpm_build.success = success
            srpm_build.job_trigger = job_trigger
            session.add(srpm_build)
            return srpm_build

    @classmethod
    def get_by_id(
        cls,
        id_: int,
    ) -> Optional["SRPMBuildModel"]:
        with get_sa_session() as session:
            return session.query(SRPMBuildModel).filter_by(id=id_).first()

    @classmethod
    def get(cls, first: int, last: int) -> Optional[Iterable["SRPMBuildModel"]]:
        with get_sa_session() as session:
            return session.query(SRPMBuildModel).order_by(desc(SRPMBuildModel.id))[
                first:last
            ]

    def get_project(self) -> Optional[GitProjectModel]:
        if not self.job_trigger_id:
            return None
        trigger_object = self.job_trigger.get_trigger_object()
        if not trigger_object:
            return None
        return trigger_object.project

    def get_pr_id(self) -> Optional[int]:
        if not self.job_trigger_id:
            return None
        trigger_object = self.job_trigger.get_trigger_object()
        if isinstance(trigger_object, PullRequestModel):
            return trigger_object.pr_id
        return None

    def get_branch_name(self) -> Optional[str]:
        if not self.job_trigger_id:
            return None
        trigger_object = self.job_trigger.get_trigger_object()
        if isinstance(trigger_object, GitBranchModel):
            return trigger_object.name
        return None

    def set_url(self, url: str) -> None:
        with get_sa_session() as session:
            self.url = url
            session.add(self)

    def __repr__(self):
        return f"SRPMBuildModel(id={self.id}, job_trigger={self.job_trigger})"


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
    def get_accounts_by_status(
        cls, status: str
    ) -> Optional[Iterable["WhitelistModel"]]:
        with get_sa_session() as session:
            return session.query(WhitelistModel).filter_by(status=status)

    @classmethod
    def remove_account(cls, account_name: str) -> Optional["WhitelistModel"]:
        with get_sa_session() as session:
            account = session.query(WhitelistModel).filter_by(account_name=account_name)
            if account:
                account.delete()
            return account

    @classmethod
    def get_all(cls) -> Optional[Iterable["WhitelistModel"]]:
        with get_sa_session() as session:
            return session.query(WhitelistModel).all()

    def to_dict(self) -> dict:
        return {"account": self.account_name, "status": self.status}

    def __repr__(self):
        return f"WhitelistModel(name={self.account_name})"


class TestingFarmResult(str, enum.Enum):
    new = "new"
    queued = "queued"
    running = "running"
    passed = "passed"
    failed = "failed"
    skipped = "skipped"
    error = "error"
    unknown = "unknown"


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

    def get_project(self) -> Optional[GitProjectModel]:
        trigger_object = self.job_trigger.get_trigger_object()
        if not trigger_object:
            return None
        return trigger_object.project

    def get_pr_id(self) -> Optional[int]:
        trigger_object = self.job_trigger.get_trigger_object()
        if isinstance(trigger_object, PullRequestModel):
            return trigger_object.pr_id
        return None

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

    @classmethod
    def get_range(cls, first: int, last: int) -> Optional[Iterable["TFTTestRunModel"]]:
        with get_sa_session() as session:
            return session.query(TFTTestRunModel).order_by(desc(TFTTestRunModel.id))[
                first:last
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
        with get_sa_session() as session:
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
        with get_sa_session() as session:
            project = GitProjectModel.get_or_create(
                namespace=namespace, repo_name=repo_name, project_url=project_url
            )

            project_authentication_issue = cls()
            project_authentication_issue.issue_created = issue_created
            project_authentication_issue.project_id = project.id
            session.add(project_authentication_issue)

            return project_authentication_issue

    def __repr__(self):
        return f"ProjectAuthenticationIssueModel(project={self.project}, token={self.token})"


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
        return GitProjectModel.get_or_create(
            namespace=namespace,
            repo_name=repo_name,
            project_url=f"https://github.com/{namespace}/{repo}",
        )

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

    def to_dict(self):
        return {
            "account_login": self.account_login,
            "account_id": self.account_id,
            "account_type": self.account_type,
            "account_url": self.account_url,
            "sender_login": self.sender_login,
            "sender_id": self.sender_id,
            "created_at": optional_time(self.created_at),
        }

    def __repr__(self):
        return f"InstallationModel(id={self.id}, account={self.account_login})"
