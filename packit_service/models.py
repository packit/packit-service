# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
Data layer on top of PSQL using sqlalch
"""

import enum
import logging
import re
from collections import Counter
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from os import getenv
from typing import (
    TYPE_CHECKING,
    Optional,
    Union,
    overload,
)
from urllib.parse import urlparse

from cachetools import TTLCache, cached
from cachetools.func import ttl_cache
from packit.config import JobConfigTriggerType
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
    asc,
    case,
    create_engine,
    desc,
    func,
    null,
    select,
)
from sqlalchemy.dialects.postgresql import array as psql_array
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import (
    Session as SQLASession,
)
from sqlalchemy.orm import (
    relationship,
    scoped_session,
    sessionmaker,
)
from sqlalchemy.sql.functions import count
from sqlalchemy.types import ARRAY

from packit_service.constants import ALLOWLIST_CONSTANTS

logger = logging.getLogger(__name__)

_CACHE_MAXSIZE = 100
_CACHE_TTL = timedelta(hours=1).total_seconds()


def get_pg_url() -> str:
    """create postgresql connection string"""
    return (
        f"postgresql+psycopg2://{getenv('POSTGRESQL_USER')}"
        f":{getenv('POSTGRESQL_PASSWORD')}@{getenv('POSTGRESQL_HOST', 'postgres')}"
        f":{getenv('POSTGRESQL_PORT', '5432')}/{getenv('POSTGRESQL_DATABASE')}"
    )


# To log SQL statements, set SQLALCHEMY_ECHO env. var. to True|T|Yes|Y|1
sqlalchemy_echo = getenv("SQLALCHEMY_ECHO", "False").lower() in (
    "true",
    "t",
    "yes",
    "y",
    "1",
)
engine = create_engine(get_pg_url(), echo=sqlalchemy_echo)
Session = sessionmaker(bind=engine)


def is_multi_threaded() -> bool:
    # See run_worker.sh
    return getenv("POOL", "solo") in ("gevent", "eventlet") and int(getenv("CONCURRENCY", 1)) > 1


if is_multi_threaded():
    # Multi-(green)threaded workers can't use scoped_session()
    # Downside of a single session is that if postgres is (oom)killed and a transaction
    # fails to rollback you have to restart the workers so that they pick another session.
    singleton_session = Session()
    logger.debug("Going to use a single SQLAlchemy session.")
else:  # service/httpd
    Session = scoped_session(Session)
    singleton_session = None


@contextmanager
def sa_session_transaction(commit: bool = False) -> SQLASession:
    """
    Context manager for 'framing' of a transaction for cases where we
    query or commit data to the database. If an error occurs the transaction is rolled back.
    https://docs.sqlalchemy.org/en/14/orm/session_basics.html#framing-out-a-begin-commit-rollback-block
    TODO: Replace usages of this function with the sessionmaker.begin[_nested]() as described in
    https://docs.sqlalchemy.org/en/14/orm/session_basics.html#using-a-sessionmaker

    Args:
        commit: Whether to call `Session.commit()` upon exiting the context. Should be set to True
            if any changes are made within the context. Defaults to False.
    """
    # if we use single session, use it, otherwise get a new session from registry
    session = singleton_session or Session()
    try:
        yield session
        if commit:
            session.commit()
    except Exception as ex:
        logger.warning(f"Exception while working with database: {ex!r}")
        session.rollback()
        raise


def optional_time(
    datetime_object: Union[datetime, None],
    fmt: str = "%d/%m/%Y %H:%M:%S",
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


def get_submitted_time_from_model(
    model: Union["CoprBuildTargetModel", "TFTTestRunTargetModel"],
) -> datetime:
    # TODO: unify `submitted_name` (or better -> create for both models `task_accepted_time`)
    # to delete this mess plz
    try:
        return model.build_submitted_time  # type: ignore[union-attr]
    except AttributeError:
        return model.submitted_time  # type: ignore[union-attr]


@overload
def get_most_recent_targets(
    models: Iterable["CoprBuildTargetModel"],
) -> list["CoprBuildTargetModel"]:
    """Overload for type-checking"""


@overload
def get_most_recent_targets(
    models: Iterable["TFTTestRunTargetModel"],
) -> list["TFTTestRunTargetModel"]:
    """Overload for type-checking"""


def get_most_recent_targets(
    models: Union[
        Iterable["CoprBuildTargetModel"],
        Iterable["TFTTestRunTargetModel"],
    ],
) -> Union[list["CoprBuildTargetModel"], list["TFTTestRunTargetModel"]]:
    """
    Gets most recent models from an iterable (regarding submission time).

    Args:
        models: Copr or TF models - if there are any duplicates in them then use the most
         recent model

    Returns:
        list of the most recent target models
    """
    most_recent_models: dict = {}
    for model in models:
        submitted_time_of_current_model = get_submitted_time_from_model(model)
        if (
            most_recent_models.get((model.target, model.identifier)) is None
            or get_submitted_time_from_model(most_recent_models[(model.target, model.identifier)])
            < submitted_time_of_current_model
        ):
            most_recent_models[(model.target, model.identifier)] = model

    return list(most_recent_models.values())


@overload
def filter_most_recent_target_models_by_status(
    models: Iterable["CoprBuildTargetModel"],
    statuses_to_filter_with: list[str],
) -> set["CoprBuildTargetModel"]:
    """Overload for type-checking"""


@overload
def filter_most_recent_target_models_by_status(
    models: Iterable["TFTTestRunTargetModel"],
    statuses_to_filter_with: list[str],
) -> set["TFTTestRunTargetModel"]:
    """Overload for type-checking"""


def filter_most_recent_target_models_by_status(
    models: Union[
        Iterable["CoprBuildTargetModel"],
        Iterable["TFTTestRunTargetModel"],
    ],
    statuses_to_filter_with: list[str],
) -> Union[set["CoprBuildTargetModel"], set["TFTTestRunTargetModel"]]:
    logger.info(
        f"Trying to filter targets with possible status: {statuses_to_filter_with} in {models}",
    )

    filtered_target_models = {
        model
        for model in get_most_recent_targets(models)
        if model.status in statuses_to_filter_with
    }

    logger.info(f"Models found: {filtered_target_models}")
    return filtered_target_models  # type: ignore


def filter_most_recent_target_names_by_status(
    models: Union[
        Iterable["CoprBuildTargetModel"],
        Iterable["TFTTestRunTargetModel"],
    ],
    statuses_to_filter_with: list[str],
) -> Optional[set[tuple[str, str]]]:
    filtered_models = filter_most_recent_target_models_by_status(
        models,
        statuses_to_filter_with,
    )
    return (
        {(model.target, model.identifier) for model in filtered_models} if filtered_models else None
    )


# https://github.com/python/mypy/issues/2477#issuecomment-313984522 ^_^
if TYPE_CHECKING:
    Base = object
else:
    Base = declarative_base()


class ProjectEventModelType(str, enum.Enum):
    pull_request = "pull_request"
    branch_push = "branch_push"
    release = "release"
    issue = "issue"
    koji_build_tag = "koji_build_tag"
    anitya_version = "anitya_version"
    anitya_multiple_versions = "anitya_multiple_versions"


class BuildsAndTestsConnector:
    """
    Abstract class that is inherited by project events models
    to share methods for accessing build/test models..
    """

    id: int
    project_event_model_type: ProjectEventModelType

    def get_project_event_models(self) -> Iterable["ProjectEventModel"]:
        with sa_session_transaction() as session:
            return session.query(ProjectEventModel).filter_by(
                type=self.project_event_model_type,
                event_id=self.id,
            )

    def get_runs(self) -> list["PipelineModel"]:
        project_events = self.get_project_event_models()
        return [run for project_event in project_events for run in project_event.runs]

    def _get_run_item(
        self,
        model_type: type["AbstractBuildTestDbType"],
    ) -> list["AbstractBuildTestDbType"]:
        runs = self.get_runs()
        models = []

        for run in runs:
            if model_type == CoprBuildTargetModel:
                if not run.copr_build_group:
                    continue
                models.extend(run.copr_build_group.copr_build_targets)

            elif model_type == KojiBuildTargetModel:
                if not run.koji_build_group:
                    continue
                models.extend(run.koji_build_group.koji_build_targets)

            elif model_type == SRPMBuildModel:
                models.append(run.srpm_build)

            elif model_type == TFTTestRunTargetModel:
                if not run.test_run_group:
                    continue
                models.extend(run.test_run_group.tft_test_run_targets)

        return list({model for model in models if model is not None})

    def get_copr_builds(self):
        return self._get_run_item(model_type=CoprBuildTargetModel)

    def get_koji_builds(self):
        return self._get_run_item(model_type=KojiBuildTargetModel)

    def get_srpm_builds(self):
        return self._get_run_item(model_type=SRPMBuildModel)

    def get_test_runs(self):
        return self._get_run_item(model_type=TFTTestRunTargetModel)


class ProjectAndEventsConnector:
    """
    Abstract class that is inherited by build/test group models
    to share methods for accessing project and project events models.
    """

    runs: Optional[list["PipelineModel"]]

    def get_project_event_model(self) -> Optional["ProjectEventModel"]:
        return self.runs[0].project_event if self.runs else None

    def get_package_name(self) -> Optional[str]:
        return self.runs[0].package_name if self.runs else None

    def get_project_event_object(self) -> Optional["AbstractProjectObjectDbType"]:
        project_event = self.get_project_event_model()
        return project_event.get_project_event_object() if project_event else None

    def get_project(self) -> Optional[Union["AnityaProjectModel", "GitProjectModel"]]:
        project_event_object = self.get_project_event_object()
        return project_event_object.project if project_event_object else None

    @property
    def commit_sha(self) -> Optional[str]:
        project_event_model = self.get_project_event_model()
        return project_event_model.commit_sha if project_event_model else None

    @commit_sha.setter
    def commit_sha(self, value: str) -> None:
        project_event_model = self.get_project_event_model()
        if project_event_model:
            project_event_model.commit_sha = value

    def get_pr_id(self) -> Optional[int]:
        project_event_object = self.get_project_event_object()
        if isinstance(project_event_object, PullRequestModel):
            return project_event_object.pr_id
        return None

    def get_issue_id(self) -> Optional[int]:
        project_event_object = self.get_project_event_object()
        if isinstance(project_event_object, IssueModel):
            return project_event_object.issue_id
        return None

    def get_branch_name(self) -> Optional[str]:
        project_event_object = self.get_project_event_object()
        if isinstance(project_event_object, GitBranchModel):
            return project_event_object.name
        if isinstance(project_event_object, KojiBuildTagModel):
            return project_event_object.target
        return None

    def get_release_tag(self) -> Optional[str]:
        project_event_object = self.get_project_event_object()
        if isinstance(project_event_object, ProjectReleaseModel):
            return project_event_object.tag_name
        return None

    def get_anitya_version(self) -> Optional[str]:
        project_event_object = self.get_project_event_object()
        if isinstance(project_event_object, AnityaVersionModel):
            return project_event_object.version
        return None


class GroupAndTargetModelConnector:
    """
    Abstract class that is inherited by build/test models
    to share methods for accessing project and project events models.
    """

    group_of_targets: ProjectAndEventsConnector

    def get_project_event_model(self) -> Optional["ProjectEventModel"]:
        return self.group_of_targets.get_project_event_model()

    def get_project_event_object(self) -> Optional["AbstractProjectObjectDbType"]:
        return self.group_of_targets.get_project_event_object()

    def get_project(self) -> Optional[Union["AnityaProjectModel", "GitProjectModel"]]:
        return self.group_of_targets.get_project()

    def get_pr_id(self) -> Optional[int]:
        return self.group_of_targets.get_pr_id()

    def get_issue_id(self) -> Optional[int]:
        return self.group_of_targets.get_issue_id()

    def get_branch_name(self) -> Optional[str]:
        return self.group_of_targets.get_branch_name()

    def get_release_tag(self) -> Optional[str]:
        return self.group_of_targets.get_release_tag()

    def get_anitya_version(self) -> Optional[str]:
        return self.group_of_targets.get_anitya_version()

    def get_package_name(self) -> Optional[str]:
        return self.group_of_targets.get_package_name()

    @property
    def commit_sha(self) -> str:
        return self.group_of_targets.commit_sha

    @commit_sha.setter
    def commit_sha(self, value: str):
        self.group_of_targets.commit_sha = value


class GroupModel:
    """An abstract class that all models grouping targets should inherit from."""

    @property
    def grouped_targets(self):
        """Returns the list of grouped targets."""
        raise NotImplementedError


class AnityaProjectModel(Base):
    __tablename__ = "anitya_projects"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, index=True)
    project_name = Column(String)
    package = Column(String)
    versions = relationship("AnityaVersionModel", back_populates="project")
    multiple_versions = relationship(
        "AnityaMultipleVersionsModel",
        back_populates="project",
    )

    @classmethod
    def get_by_id(cls, id_: int) -> Optional["AnityaProjectModel"]:
        with sa_session_transaction() as session:
            return session.query(AnityaProjectModel).filter_by(id=id_).first()

    @classmethod
    def get_or_create(
        cls,
        project_name: str,
        project_id: int,
        package: str,
    ) -> "AnityaProjectModel":
        with sa_session_transaction(commit=True) as session:
            project = (
                session.query(AnityaProjectModel)
                .filter_by(
                    project_name=project_name,
                    project_id=project_id,
                    package=package,
                )
                .first()
            )
            if not project:
                project = AnityaProjectModel()
                project.project_id = project_id
                project.project_name = project_name
                project.package = package
                session.add(project)
            return project


class AnityaMultipleVersionsModel(BuildsAndTestsConnector, Base):
    __tablename__ = "anitya_multiple_versions"
    id = Column(Integer, primary_key=True)  # our database PK
    versions = Column(ARRAY(String), nullable=False)
    project_id = Column(Integer, ForeignKey("anitya_projects.id"), index=True)
    project = relationship("AnityaProjectModel", back_populates="multiple_versions")

    job_config_trigger_type = JobConfigTriggerType.release
    project_event_model_type = ProjectEventModelType.anitya_multiple_versions

    @classmethod
    def get_or_create(
        cls,
        versions: list[str],
        project_id: int,
        project_name: str,
        package: str,
    ) -> "AnityaMultipleVersionsModel":
        with sa_session_transaction(commit=True) as session:
            project = AnityaProjectModel.get_or_create(
                project_id=project_id,
                project_name=project_name,
                package=package,
            )
            project_version = (
                session.query(AnityaMultipleVersionsModel)
                .filter_by(versions=versions, project_id=project.id)
                .first()
            )
            if not project_version:
                project_version = AnityaMultipleVersionsModel()
                project_version.versions = versions
                project_version.project = project
                session.add(project_version)
            return project_version

    @classmethod
    def get_by_id(cls, id_: int) -> Optional["AnityaMultipleVersionsModel"]:
        with sa_session_transaction() as session:
            return session.query(AnityaVersionModel).filter_by(id=id_).first()

    def __repr__(self):
        return f"AnityaMultipleVersionsModel(versions={self.versions}, project={self.project})"


class AnityaVersionModel(BuildsAndTestsConnector, Base):
    __tablename__ = "anitya_versions"
    id = Column(Integer, primary_key=True)  # our database PK
    version = Column(String)
    project_id = Column(Integer, ForeignKey("anitya_projects.id"), index=True)
    project = relationship("AnityaProjectModel", back_populates="versions")

    job_config_trigger_type = JobConfigTriggerType.release
    project_event_model_type = ProjectEventModelType.anitya_version

    @classmethod
    def get_or_create(
        cls,
        version: str,
        project_id: int,
        project_name: str,
        package: str,
    ) -> "AnityaVersionModel":
        with sa_session_transaction(commit=True) as session:
            project = AnityaProjectModel.get_or_create(
                project_id=project_id,
                project_name=project_name,
                package=package,
            )
            project_version = (
                session.query(AnityaVersionModel)
                .filter_by(version=version, project_id=project.id)
                .first()
            )
            if not project_version:
                project_version = AnityaVersionModel()
                project_version.version = version
                project_version.project = project
                session.add(project_version)
            return project_version

    @classmethod
    def get_by_id(cls, id_: int) -> Optional["AnityaVersionModel"]:
        with sa_session_transaction() as session:
            return session.query(AnityaVersionModel).filter_by(id=id_).first()

    def __repr__(self):
        return f"AnityaVersionModel(version={self.version}, project={self.project})"


class GitProjectModel(Base):
    __tablename__ = "git_projects"
    id = Column(Integer, primary_key=True)
    # github.com/NAMESPACE/REPO_NAME
    namespace = Column(String, index=True)
    repo_name = Column(String, index=True)
    pull_requests = relationship("PullRequestModel", back_populates="project")
    branches = relationship("GitBranchModel", back_populates="project")
    releases = relationship("ProjectReleaseModel", back_populates="project")
    issues = relationship("IssueModel", back_populates="project")
    koji_build_tags = relationship("KojiBuildTagModel", back_populates="project")
    sync_release_pull_requests = relationship(
        "SyncReleasePullRequestModel",
        back_populates="project",
    )
    project_authentication_issue = relationship(
        "ProjectAuthenticationIssueModel",
        back_populates="project",
    )

    project_url = Column(String)
    instance_url = Column(String, nullable=False)

    # we checked that exists at least a bodhi update or a koji build
    # or a merged packit downstream pull request for it.
    onboarded_downstream = Column(Boolean, default=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance_url = urlparse(self.project_url).hostname

    def set_onboarded_downstream(self, onboarded: bool):
        with sa_session_transaction(commit=True) as session:
            self.onboarded_downstream = onboarded
            session.add(self)

    @classmethod
    def get_or_create(
        cls,
        namespace: str,
        repo_name: str,
        project_url: str,
    ) -> "GitProjectModel":
        with sa_session_transaction(commit=True) as session:
            project = (
                session.query(GitProjectModel)
                .filter_by(
                    namespace=namespace,
                    repo_name=repo_name,
                    project_url=project_url,
                )
                .first()
            )
            if not project:
                project = cls(
                    repo_name=repo_name,
                    namespace=namespace,
                    project_url=project_url,
                )
                session.add(project)
            return project

    @classmethod
    def get_all(cls) -> Iterable["GitProjectModel"]:
        """Return projects of given forge"""
        with sa_session_transaction() as session:
            query = session.query(GitProjectModel).order_by(GitProjectModel.namespace)
            return query.all()

    @classmethod
    def get_by_id(cls, id_: int) -> Optional["GitProjectModel"]:
        with sa_session_transaction() as session:
            return session.query(GitProjectModel).filter_by(id=id_).first()

    @classmethod
    def get_range(cls, first: int, last: int) -> Iterable["GitProjectModel"]:
        with sa_session_transaction() as session:
            return (
                session.query(GitProjectModel)
                .order_by(GitProjectModel.namespace)
                .slice(first, last)
            )

    @classmethod
    def get_by_forge(
        cls,
        first: int,
        last: int,
        forge: str,
    ) -> Iterable["GitProjectModel"]:
        """Return projects of given forge"""
        with sa_session_transaction() as session:
            return (
                session.query(GitProjectModel)
                .filter_by(instance_url=forge)
                .order_by(GitProjectModel.namespace)
                .slice(first, last)
            )

    @classmethod
    def get_by_forge_namespace(
        cls,
        first: int,
        last: int,
        forge: str,
        namespace: str,
    ) -> Iterable["GitProjectModel"]:
        """Return projects of given forge and namespace"""
        with sa_session_transaction() as session:
            return (
                session.query(GitProjectModel)
                .filter_by(instance_url=forge, namespace=namespace)
                .slice(first, last)
            )

    @classmethod
    def get_project(
        cls,
        forge: str,
        namespace: str,
        repo_name: str,
    ) -> Optional["GitProjectModel"]:
        """Return one project which matches said criteria"""
        with sa_session_transaction() as session:
            return (
                session.query(cls)
                .filter_by(instance_url=forge, namespace=namespace, repo_name=repo_name)
                .one_or_none()
            )

    @classmethod
    def get_project_prs(
        cls,
        first: int,
        last: int,
        forge: str,
        namespace: str,
        repo_name: str,
    ) -> Iterable["PullRequestModel"]:
        with sa_session_transaction() as session:
            return (
                session.query(PullRequestModel)
                .join(PullRequestModel.project)
                .filter(
                    GitProjectModel.instance_url == forge,
                    GitProjectModel.namespace == namespace,
                    GitProjectModel.repo_name == repo_name,
                )
                .order_by(desc(PullRequestModel.pr_id))
                .slice(first, last)
            )

    @classmethod
    def get_project_issues(
        cls,
        first: int,
        last: int,
        forge: str,
        namespace: str,
        repo_name: str,
    ) -> Iterable["IssueModel"]:
        with sa_session_transaction() as session:
            return (
                session.query(IssueModel)
                .join(IssueModel.project)
                .filter(
                    GitProjectModel.instance_url == forge,
                    GitProjectModel.namespace == namespace,
                    GitProjectModel.repo_name == repo_name,
                )
                .slice(first, last)
            )

    @classmethod
    def get_project_branches(
        cls,
        first: int,
        last: int,
        forge: str,
        namespace: str,
        repo_name: str,
    ) -> Iterable["GitBranchModel"]:
        with sa_session_transaction() as session:
            return (
                session.query(GitBranchModel)
                .join(GitBranchModel.project)
                .filter(
                    GitProjectModel.instance_url == forge,
                    GitProjectModel.namespace == namespace,
                    GitProjectModel.repo_name == repo_name,
                )
                .slice(first, last)
            )

    @classmethod
    def get_project_releases(
        cls,
        first: int,
        last: int,
        forge: str,
        namespace: str,
        repo_name: str,
    ) -> Iterable["ProjectReleaseModel"]:
        with sa_session_transaction() as session:
            return (
                session.query(ProjectReleaseModel)
                .join(ProjectReleaseModel.project)
                .filter(
                    GitProjectModel.instance_url == forge,
                    GitProjectModel.namespace == namespace,
                    GitProjectModel.repo_name == repo_name,
                )
                .slice(first, last)
            )

    # ACTIVE PROJECTS

    @classmethod
    @ttl_cache(maxsize=_CACHE_MAXSIZE, ttl=_CACHE_TTL)
    def get_active_projects(
        cls,
        top: Optional[int] = None,
        datetime_from=None,
        datetime_to=None,
    ) -> list[str]:
        """
        Active project is the one with at least one activity (=one pipeline)
        during the given period.
        """
        return list(
            cls.get_active_projects_usage_numbers(
                top=top,
                datetime_from=datetime_from,
                datetime_to=datetime_to,
            ).keys(),
        )

    @classmethod
    @ttl_cache(maxsize=_CACHE_MAXSIZE, ttl=_CACHE_TTL)
    def get_active_projects_count(cls, datetime_from=None, datetime_to=None) -> int:
        """
        Active project is the one with at least one activity (=one pipeline)
        during the given period.
        """
        return len(
            cls.get_active_projects_usage_numbers(
                top=None,
                datetime_from=datetime_from,
                datetime_to=datetime_to,
            ),
        )

    @classmethod
    @ttl_cache(maxsize=_CACHE_MAXSIZE, ttl=_CACHE_TTL)
    def get_active_projects_usage_numbers(
        cls,
        top: Optional[int] = 10,
        datetime_from=None,
        datetime_to=None,
    ) -> dict[str, int]:
        """
        Get the most active projects sorted by the number of related pipelines.
        """
        all_usage_numbers: dict[str, int] = Counter()
        for project_event_type in ProjectEventModelType:
            all_usage_numbers.update(
                cls.get_project_event_usage_numbers(
                    datetime_from=datetime_from,
                    datetime_to=datetime_to,
                    top=None,
                    project_event_type=project_event_type,
                ),
            )
        return dict(
            sorted(all_usage_numbers.items(), key=lambda x: x[1], reverse=True)[:top],
        )

    # ALL PROJECTS

    @classmethod
    @ttl_cache(maxsize=_CACHE_MAXSIZE, ttl=_CACHE_TTL)
    def get_project_count(
        cls,
    ) -> list[str]:
        """
        Number of project models in the database.
        """
        with sa_session_transaction() as session:
            return session.query(GitProjectModel).count()

    @classmethod
    @ttl_cache(maxsize=_CACHE_MAXSIZE, ttl=_CACHE_TTL)
    def get_instance_numbers(cls) -> dict[str, int]:
        """
        Get the number of projects per each GIT instances.
        """
        with sa_session_transaction() as session:
            return dict(
                session.query(
                    GitProjectModel.instance_url,
                    func.count(GitProjectModel.instance_url),
                )
                .group_by(GitProjectModel.instance_url)
                .all(),
            )

    @classmethod
    @ttl_cache(maxsize=_CACHE_MAXSIZE, ttl=_CACHE_TTL)
    def get_instance_numbers_for_active_projects(
        cls,
        datetime_from=None,
        datetime_to=None,
    ) -> dict[str, int]:
        """
        Get the number of projects (at least one pipeline during the time period)
        per each GIT instances.
        """
        projects_per_instance: dict[str, set[str]] = {}

        with sa_session_transaction() as session:
            for project_event_type in ProjectEventModelType:
                project_event_model = MODEL_FOR_PROJECT_EVENT[project_event_type]
                query = (
                    session.query(
                        GitProjectModel.instance_url,
                        GitProjectModel.project_url,
                    )
                    .join(
                        project_event_model,
                        GitProjectModel.id == project_event_model.project_id,
                    )
                    .join(
                        ProjectEventModel,
                        ProjectEventModel.event_id == project_event_model.id,
                    )
                    .join(
                        PipelineModel,
                        PipelineModel.project_event_id == ProjectEventModel.id,
                    )
                    .filter(ProjectEventModel.type == project_event_type)
                )
                if datetime_from:
                    query = query.filter(PipelineModel.datetime >= datetime_from)
                if datetime_to:
                    query = query.filter(PipelineModel.datetime <= datetime_to)

                query = query.group_by(
                    GitProjectModel.project_url,
                    GitProjectModel.instance_url,
                )
                for instance, project in query.all():
                    projects_per_instance.setdefault(instance, set())
                    projects_per_instance[instance].add(project)

        return {instance: len(projects) for instance, projects in projects_per_instance.items()}

    @classmethod
    @ttl_cache(maxsize=_CACHE_MAXSIZE, ttl=_CACHE_TTL)
    def get_project_event_usage_count(
        cls,
        project_event_type: ProjectEventModelType,
        datetime_from=None,
        datetime_to=None,
    ):
        """
        Get the number of triggers of a given type with at least one pipeline from the given period.
        """
        # TODO: share the computation with _get_trigger_usage_numbers
        #       (one query with top and one without)
        return sum(
            cls.get_project_event_usage_numbers(
                datetime_from=datetime_from,
                datetime_to=datetime_to,
                project_event_type=project_event_type,
                top=None,
            ).values(),
        )

    @classmethod
    @ttl_cache(maxsize=_CACHE_MAXSIZE, ttl=_CACHE_TTL)
    def get_project_event_usage_numbers(
        cls,
        project_event_type,
        datetime_from=None,
        datetime_to=None,
        top=None,
    ) -> dict[str, int]:
        """
        For each project, get the number of triggers of a given type with at least one pipeline
        from the given period.

        Order from the highest numbers.
        All if `top` not set, the first `top` projects returned otherwise.
        """
        project_event_model = MODEL_FOR_PROJECT_EVENT[project_event_type]
        with sa_session_transaction() as session:
            query = (
                session.query(
                    GitProjectModel.project_url,
                    count(project_event_model.id).over(
                        partition_by=GitProjectModel.project_url,
                    ),
                )
                .join(
                    project_event_model,
                    GitProjectModel.id == project_event_model.project_id,
                )
                .join(
                    ProjectEventModel,
                    ProjectEventModel.event_id == project_event_model.id,
                )
                .join(
                    PipelineModel,
                    PipelineModel.project_event_id == ProjectEventModel.id,
                )
                .filter(ProjectEventModel.type == project_event_type)
            )
            if datetime_from:
                query = query.filter(PipelineModel.datetime >= datetime_from)
            if datetime_to:
                query = query.filter(PipelineModel.datetime <= datetime_to)

            query = (
                query.group_by(GitProjectModel.project_url, project_event_model.id)
                .distinct()
                .order_by(
                    desc(
                        count(project_event_model.id).over(
                            partition_by=GitProjectModel.project_url,
                        ),
                    ),
                )
            )

            if top:
                query = query.limit(top)

            return dict(query.all())

    @classmethod
    @ttl_cache(maxsize=_CACHE_MAXSIZE, ttl=_CACHE_TTL)
    def get_job_usage_numbers_count(
        cls,
        job_result_model,
        project_event_type,
        datetime_from=None,
        datetime_to=None,
    ) -> int:
        """
        Get the number of jobs of a given type with at least one pipeline
        from the given period and given project event.
        """
        return sum(
            cls.get_job_usage_numbers(
                datetime_from=datetime_from,
                datetime_to=datetime_to,
                job_result_model=job_result_model,
                top=None,
                project_event_type=project_event_type,
            ).values(),
        )

    @classmethod
    @ttl_cache(maxsize=_CACHE_MAXSIZE, ttl=_CACHE_TTL)
    def get_job_usage_numbers_count_all_project_events(
        cls,
        job_result_model,
        datetime_from=None,
        datetime_to=None,
    ) -> int:
        """
        Get the number of all the jobs of a given type with at least one pipeline
        from the given period.
        """
        return sum(
            cls.get_job_usage_numbers_all_project_events(
                datetime_from=datetime_from,
                datetime_to=datetime_to,
                job_result_model=job_result_model,
                top=None,
            ).values(),
        )

    @classmethod
    @ttl_cache(maxsize=_CACHE_MAXSIZE, ttl=_CACHE_TTL)
    def get_job_usage_numbers(
        cls,
        job_result_model,
        project_event_type,
        datetime_from=None,
        datetime_to=None,
        top: Optional[int] = 10,
    ) -> dict[str, int]:
        """
        For each project, get the number of jobs of a given type with at least one pipeline
        from the given period.

        Order from the highest numbers.
        All if `top` not set, the first `top` projects returned otherwise.
        """
        project_event_model = MODEL_FOR_PROJECT_EVENT[project_event_type]
        pipeline_attribute = {
            SRPMBuildModel: PipelineModel.srpm_build_id,
            CoprBuildGroupModel: PipelineModel.copr_build_group_id,
            KojiBuildGroupModel: PipelineModel.koji_build_group_id,
            VMImageBuildTargetModel: PipelineModel.vm_image_build_id,
            TFTTestRunGroupModel: PipelineModel.test_run_group_id,
            SyncReleaseModel: PipelineModel.sync_release_run_id,
        }[job_result_model]

        with sa_session_transaction() as session:
            query = (
                session.query(
                    GitProjectModel.project_url,
                    count(job_result_model.id).over(
                        partition_by=GitProjectModel.project_url,
                    ),
                )
                .join(
                    project_event_model,
                    GitProjectModel.id == project_event_model.project_id,
                )
                .join(
                    ProjectEventModel,
                    ProjectEventModel.event_id == project_event_model.id,
                )
                .join(
                    PipelineModel,
                    PipelineModel.project_event_id == ProjectEventModel.id,
                )
                .join(job_result_model, job_result_model.id == pipeline_attribute)
                .filter(ProjectEventModel.type == project_event_type)
            )
            if datetime_from:
                query = query.filter(PipelineModel.datetime >= datetime_from)
            if datetime_to:
                query = query.filter(PipelineModel.datetime <= datetime_to)
            return dict(
                query.group_by(GitProjectModel.project_url, job_result_model.id)
                .distinct()
                .order_by(
                    desc(
                        count(job_result_model.id).over(
                            partition_by=GitProjectModel.project_url,
                        ),
                    ),
                )
                .limit(top)
                .all(),
            )

    @classmethod
    @ttl_cache(maxsize=_CACHE_MAXSIZE, ttl=_CACHE_TTL)
    def get_job_usage_numbers_all_project_events(
        cls,
        job_result_model,
        datetime_from=None,
        datetime_to=None,
        top: Optional[int] = None,
    ) -> dict[str, int]:
        """
        For each job, get the per-project number of jobs from the given period.
        """
        all_usage_numbers: dict[str, int] = Counter()
        for project_event_type in ProjectEventModelType:
            all_usage_numbers.update(
                cls.get_job_usage_numbers(
                    datetime_from=datetime_from,
                    datetime_to=datetime_to,
                    top=None,
                    job_result_model=job_result_model,
                    project_event_type=project_event_type,
                ),
            )
        return dict(
            sorted(all_usage_numbers.items(), key=lambda x: x[1], reverse=True)[:top],
        )

    @classmethod
    def get_known_onboarded_downstream_projects(
        cls,
    ) -> set["GitProjectModel"]:
        """
        List already known onboarded projects.
        An onboarded project is a project with a bodhi update or a koji build
        or a merged downstream packit pull request.

        We already checked them.
        """
        with sa_session_transaction() as session:
            query = session.query(GitProjectModel).filter(
                GitProjectModel.onboarded_downstream == True  # noqa
            )
            return set(query.all())

    def __repr__(self):
        return (
            f"GitProjectModel(name={self.namespace}/{self.repo_name}, "
            f"project_url='{self.project_url}')"
        )


sync_release_pr_association_table = Table(
    "sync_release_pr_association",
    Base.metadata,  # type: ignore
    Column(
        "sync_release_target_id",
        Integer,
        ForeignKey("sync_release_run_targets.id"),
        primary_key=True,
    ),
    Column(
        "sync_release_pr_id", Integer, ForeignKey("sync_release_pull_request.id"), primary_key=True
    ),
)


class SyncReleasePullRequestModel(Base):
    __tablename__ = "sync_release_pull_request"

    # Here are collected references to the downstream pull requests
    # created by Packit during the sync_release process.
    # This is not a subtype of ProjectEventModel,
    # this is not a pull request event!
    # sync_release_pull_request and pull_request table may have the
    # same data when for example a retriggering-command comment is
    # written inside a Packit created PR...
    # @todo properly handle the duplication!

    id = Column(Integer, primary_key=True)  # our database PK
    # GitHub PR ID
    # this is not our PK b/c:
    #   1) we don't control it
    #   2) we want sensible auto-incremented ID, not random numbers
    #   3) it's not unique across projects obviously, so why am I even writing this?
    pr_id = Column(Integer, index=True)
    project_id = Column(Integer, ForeignKey("git_projects.id"), index=True)
    project = relationship(
        "GitProjectModel",
        back_populates="sync_release_pull_requests",
    )
    is_fast_forward = Column(Boolean, default=False)
    target_branch = Column(String)
    url = Column(String)

    @classmethod
    def get_or_create(
        cls,
        pr_id: int,
        namespace: str,
        repo_name: str,
        project_url: str,
        target_branch: str,
        url: str,
        is_fast_forward: bool = False,
    ) -> "SyncReleasePullRequestModel":
        with sa_session_transaction(commit=True) as session:
            project = GitProjectModel.get_or_create(
                namespace=namespace,
                repo_name=repo_name,
                project_url=project_url,
            )
            pr = (
                session.query(SyncReleasePullRequestModel)
                .filter_by(pr_id=pr_id, project_id=project.id)
                .first()
            )
            if not pr:
                pr = SyncReleasePullRequestModel()
                pr.pr_id = pr_id
                pr.project_id = project.id
                pr.target_branch = target_branch
                pr.url = url
                pr.is_fast_forward = is_fast_forward
                session.add(pr)
            return pr

    @classmethod
    def get(
        cls,
        pr_id: int,
        namespace: str,
        repo_name: str,
        project_url: str,
    ) -> Optional["SyncReleasePullRequestModel"]:
        with sa_session_transaction(commit=True) as session:
            project = GitProjectModel.get_or_create(
                namespace=namespace,
                repo_name=repo_name,
                project_url=project_url,
            )
            return (
                session.query(SyncReleasePullRequestModel)
                .filter_by(pr_id=pr_id, project_id=project.id)
                .first()
            )

    @classmethod
    def get_by_id(cls, id_: int) -> Optional["SyncReleasePullRequestModel"]:
        with sa_session_transaction() as session:
            return session.query(SyncReleasePullRequestModel).filter_by(id=id_).first()

    def __repr__(self):
        return f"SyncReleasePullRequestModel(pr_id={self.pr_id}, project={self.project})"


class PullRequestModel(BuildsAndTestsConnector, Base):
    __tablename__ = "pull_requests"
    id = Column(Integer, primary_key=True)  # our database PK
    # GitHub PR ID
    # this is not our PK b/c:
    #   1) we don't control it
    #   2) we want sensible auto-incremented ID, not random numbers
    #   3) it's not unique across projects obviously, so why am I even writing this?
    pr_id = Column(Integer, index=True)
    project_id = Column(Integer, ForeignKey("git_projects.id"), index=True)
    project = relationship("GitProjectModel", back_populates="pull_requests")

    job_config_trigger_type = JobConfigTriggerType.pull_request
    project_event_model_type = ProjectEventModelType.pull_request

    @classmethod
    def get_or_create(
        cls,
        pr_id: int,
        namespace: str,
        repo_name: str,
        project_url: str,
    ) -> "PullRequestModel":
        with sa_session_transaction(commit=True) as session:
            project = GitProjectModel.get_or_create(
                namespace=namespace,
                repo_name=repo_name,
                project_url=project_url,
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
    def get(
        cls,
        pr_id: int,
        namespace: str,
        repo_name: str,
        project_url: str,
    ) -> Optional["PullRequestModel"]:
        with sa_session_transaction(commit=True) as session:
            project = GitProjectModel.get_or_create(
                namespace=namespace,
                repo_name=repo_name,
                project_url=project_url,
            )
            return (
                session.query(PullRequestModel)
                .filter_by(pr_id=pr_id, project_id=project.id)
                .first()
            )

    @classmethod
    def get_by_id(cls, id_: int) -> Optional["PullRequestModel"]:
        with sa_session_transaction() as session:
            return session.query(PullRequestModel).filter_by(id=id_).first()

    def __repr__(self):
        return f"PullRequestModel(pr_id={self.pr_id}, project={self.project})"


class IssueModel(BuildsAndTestsConnector, Base):
    __tablename__ = "project_issues"
    id = Column(Integer, primary_key=True)  # our database PK
    issue_id = Column(Integer, index=True)
    project_id = Column(Integer, ForeignKey("git_projects.id"), index=True)
    project = relationship("GitProjectModel", back_populates="issues")
    # TODO: Fix this hardcoding! This is only to make propose-downstream work!
    job_config_trigger_type = JobConfigTriggerType.release
    project_event_model_type = ProjectEventModelType.issue

    @classmethod
    def get_or_create(
        cls,
        issue_id: int,
        namespace: str,
        repo_name: str,
        project_url: str,
    ) -> "IssueModel":
        with sa_session_transaction(commit=True) as session:
            project = GitProjectModel.get_or_create(
                namespace=namespace,
                repo_name=repo_name,
                project_url=project_url,
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
        with sa_session_transaction() as session:
            return session.query(IssueModel).filter_by(id=id_).first()

    def __repr__(self):
        return f"IssueModel(id={self.issue_id}, project={self.project})"


class GitBranchModel(BuildsAndTestsConnector, Base):
    __tablename__ = "git_branches"
    id = Column(Integer, primary_key=True)  # our database PK
    name = Column(String)
    project_id = Column(Integer, ForeignKey("git_projects.id"), index=True)
    project = relationship("GitProjectModel", back_populates="branches")

    job_config_trigger_type = JobConfigTriggerType.commit
    project_event_model_type = ProjectEventModelType.branch_push

    @classmethod
    def get_or_create(
        cls,
        branch_name: str,
        namespace: str,
        repo_name: str,
        project_url: str,
    ) -> "GitBranchModel":
        with sa_session_transaction(commit=True) as session:
            project = GitProjectModel.get_or_create(
                namespace=namespace,
                repo_name=repo_name,
                project_url=project_url,
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
        with sa_session_transaction() as session:
            return session.query(GitBranchModel).filter_by(id=id_).first()

    def __repr__(self):
        return f"GitBranchModel(name={self.name},  project={self.project})"


class ProjectReleaseModel(BuildsAndTestsConnector, Base):
    __tablename__ = "project_releases"
    id = Column(Integer, primary_key=True)  # our database PK
    tag_name = Column(String)
    commit_hash = Column(String)
    project_id = Column(Integer, ForeignKey("git_projects.id"), index=True)
    project = relationship("GitProjectModel", back_populates="releases")

    job_config_trigger_type = JobConfigTriggerType.release
    project_event_model_type = ProjectEventModelType.release

    @classmethod
    def get_or_create(
        cls,
        tag_name: str,
        namespace: str,
        repo_name: str,
        project_url: str,
        commit_hash: Optional[str] = None,
    ) -> "ProjectReleaseModel":
        with sa_session_transaction(commit=True) as session:
            project = GitProjectModel.get_or_create(
                namespace=namespace,
                repo_name=repo_name,
                project_url=project_url,
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
        with sa_session_transaction() as session:
            return session.query(ProjectReleaseModel).filter_by(id=id_).first()

    def __repr__(self):
        return f"ProjectReleaseModel(tag_name={self.tag_name}, project={self.project})"


class KojiBuildTagModel(BuildsAndTestsConnector, Base):
    __tablename__ = "koji_build_tags"
    id = Column(Integer, primary_key=True)  # our database PK
    task_id = Column(String, index=True)
    koji_tag_name = Column(String, index=True)
    target = Column(String)
    project_id = Column(Integer, ForeignKey("git_projects.id"), index=True)
    project = relationship("GitProjectModel", back_populates="koji_build_tags")

    job_config_trigger_type = JobConfigTriggerType.koji_build
    project_event_model_type = ProjectEventModelType.koji_build_tag

    @classmethod
    def get_or_create(
        cls,
        task_id: str,
        koji_tag_name: str,
        target: Optional[str],
        namespace: str,
        repo_name: str,
        project_url: str,
    ) -> "KojiBuildTagModel":
        with sa_session_transaction(commit=True) as session:
            project = GitProjectModel.get_or_create(
                namespace=namespace,
                repo_name=repo_name,
                project_url=project_url,
            )
            koji_build_tag = (
                session.query(KojiBuildTagModel)
                .filter_by(
                    task_id=task_id,
                    koji_tag_name=koji_tag_name,
                    project_id=project.id,
                )
                .first()
            )
            if not koji_build_tag:
                koji_build_tag = KojiBuildTagModel()
                koji_build_tag.task_id = task_id
                koji_build_tag.koji_tag_name = koji_tag_name
                koji_build_tag.target = target
                koji_build_tag.project_id = project.id
                session.add(koji_build_tag)
            return koji_build_tag

    @classmethod
    def get_by_id(cls, id_: int) -> Optional["KojiBuildTagModel"]:
        with sa_session_transaction() as session:
            return session.query(KojiBuildTagModel).filter_by(id=id_).first()

    def __repr__(self):
        return (
            f"KojiBuildTagModel(task_id={self.task_id}, koji_tag_name={self.koji_tag_name}, "
            f"target={self.target}, project={self.project})"
        )


AbstractProjectObjectDbType = Union[
    PullRequestModel,
    ProjectReleaseModel,
    GitBranchModel,
    IssueModel,
    KojiBuildTagModel,
    AnityaVersionModel,
    AnityaMultipleVersionsModel,
]

MODEL_FOR_PROJECT_EVENT: dict[
    ProjectEventModelType,
    type[AbstractProjectObjectDbType],
] = {
    ProjectEventModelType.pull_request: PullRequestModel,
    ProjectEventModelType.branch_push: GitBranchModel,
    ProjectEventModelType.release: ProjectReleaseModel,
    ProjectEventModelType.issue: IssueModel,
    ProjectEventModelType.koji_build_tag: KojiBuildTagModel,
    ProjectEventModelType.anitya_version: AnityaVersionModel,
    ProjectEventModelType.anitya_multiple_versions: AnityaMultipleVersionsModel,
}


class ProjectEventModel(Base):
    """
    Model representing a "project event" which triggers some packit task.
    Like a push into a pull request: the push is a "project event" with a
    given commit sha into a specific "project object" which is a pull request.

    It connects PipelineModel (and built/test models via that model)
    with "project objects" models: IssueModel, PullRequestModel,
    GitBranchModel or ProjectReleaseModel.

    * It contains type and id of the other database_model.
      * We know table and id that we need to find in that table.
    * Each PipelineModel has to be connected to exactly one ProjectEventModel.
    * There can be multiple PipelineModels for one ProjectEventModel.
      (e.g. For each push to PR, there will be new PipelineModel, but same ProjectEventModel.)
    """

    __tablename__ = "project_events"
    id = Column(Integer, primary_key=True)  # our database PK
    type = Column(Enum(ProjectEventModelType))
    event_id = Column(Integer, index=True)
    commit_sha = Column(String, index=True)
    packages_config = Column(JSON)

    runs = relationship("PipelineModel", back_populates="project_event")

    @classmethod
    def add_pull_request_event(
        cls,
        pr_id: int,
        namespace: str,
        repo_name: str,
        project_url: str,
        commit_sha: str,
    ) -> tuple[PullRequestModel, "ProjectEventModel"]:
        pull_request = PullRequestModel.get_or_create(
            pr_id=pr_id,
            namespace=namespace,
            repo_name=repo_name,
            project_url=project_url,
        )
        event = ProjectEventModel.get_or_create(
            type=pull_request.project_event_model_type,
            event_id=pull_request.id,
            commit_sha=commit_sha,
        )
        return (pull_request, event)

    @classmethod
    def add_branch_push_event(
        cls,
        branch_name: str,
        namespace: str,
        repo_name: str,
        project_url: str,
        commit_sha: str,
    ) -> tuple[GitBranchModel, "ProjectEventModel"]:
        branch_push = GitBranchModel.get_or_create(
            branch_name=branch_name,
            namespace=namespace,
            repo_name=repo_name,
            project_url=project_url,
        )
        event = ProjectEventModel.get_or_create(
            type=branch_push.project_event_model_type,
            event_id=branch_push.id,
            commit_sha=commit_sha,
        )
        return (branch_push, event)

    @classmethod
    def add_release_event(
        cls,
        tag_name: str,
        namespace: str,
        repo_name: str,
        project_url: str,
        commit_hash: str,
    ) -> tuple[ProjectReleaseModel, "ProjectEventModel"]:
        release = ProjectReleaseModel.get_or_create(
            tag_name=tag_name,
            namespace=namespace,
            repo_name=repo_name,
            project_url=project_url,
            commit_hash=commit_hash,
        )
        event = ProjectEventModel.get_or_create(
            type=release.project_event_model_type,
            event_id=release.id,
            commit_sha=commit_hash,
        )
        return (release, event)

    @classmethod
    def add_anitya_version_event(
        cls,
        version: str,
        project_name: str,
        project_id: int,
        package: str,
    ) -> tuple[AnityaVersionModel, "ProjectEventModel"]:
        project_version = AnityaVersionModel.get_or_create(
            version=version,
            project_name=project_name,
            project_id=project_id,
            package=package,
        )
        event = ProjectEventModel.get_or_create(
            type=project_version.project_event_model_type,
            event_id=project_version.id,
            commit_sha=None,
        )
        return (project_version, event)

    @classmethod
    def add_anitya_multiple_versions_event(
        cls,
        versions: list[str],
        project_name: str,
        project_id: int,
        package: str,
    ) -> tuple[AnityaMultipleVersionsModel, "ProjectEventModel"]:
        project_version = AnityaMultipleVersionsModel.get_or_create(
            versions=versions,
            project_name=project_name,
            project_id=project_id,
            package=package,
        )
        event = ProjectEventModel.get_or_create(
            type=project_version.project_event_model_type,
            event_id=project_version.id,
            commit_sha=None,
        )
        return (project_version, event)

    @classmethod
    def add_issue_event(
        cls,
        issue_id: int,
        namespace: str,
        repo_name: str,
        project_url: str,
    ) -> tuple[IssueModel, "ProjectEventModel"]:
        issue = IssueModel.get_or_create(
            issue_id=issue_id,
            namespace=namespace,
            repo_name=repo_name,
            project_url=project_url,
        )
        event = ProjectEventModel.get_or_create(
            type=issue.project_event_model_type,
            event_id=issue.id,
            commit_sha=None,
        )
        return (issue, event)

    @classmethod
    def add_koji_build_tag_event(
        cls,
        task_id: str,
        koji_tag_name: str,
        namespace: str,
        repo_name: str,
        project_url: str,
    ) -> tuple[KojiBuildTagModel, "ProjectEventModel"]:
        target = None
        if sidetag := SidetagModel.get_by_koji_name(koji_tag_name):
            target = sidetag.target
        koji_build_tag = KojiBuildTagModel.get_or_create(
            task_id=task_id,
            koji_tag_name=koji_tag_name,
            target=target,
            namespace=namespace,
            repo_name=repo_name,
            project_url=project_url,
        )
        event = ProjectEventModel.get_or_create(
            type=koji_build_tag.project_event_model_type,
            event_id=koji_build_tag.id,
            commit_sha=None,
        )
        return (koji_build_tag, event)

    @classmethod
    def get_or_create(
        cls,
        type: ProjectEventModelType,
        event_id: int,
        commit_sha: str,
    ) -> "ProjectEventModel":
        with sa_session_transaction(commit=True) as session:
            project_event = (
                session.query(ProjectEventModel)
                .filter_by(type=type, event_id=event_id, commit_sha=commit_sha)
                .first()
            )
            if not project_event:
                project_event = ProjectEventModel()
                project_event.type = type
                project_event.event_id = event_id
                project_event.commit_sha = commit_sha
                session.add(project_event)
            return project_event

    @classmethod
    def get_by_id(cls, id_: int) -> Optional["ProjectEventModel"]:
        with sa_session_transaction() as session:
            return session.query(ProjectEventModel).filter_by(id=id_).first()

    @classmethod
    def get_and_reset_older_than_with_packages_config(
        cls,
        delta: timedelta,
    ) -> Iterable["ProjectEventModel"]:
        """Return project events with all runs older than delta
        and set to null their stored packages config.
        Cleanup project events here to speed up the process."""
        delta_ago = datetime.now(timezone.utc) - delta
        with sa_session_transaction(commit=True) as session:
            events = (
                session.query(ProjectEventModel)
                .filter(ProjectEventModel.packages_config.isnot(null()))
                .filter(
                    ~ProjectEventModel.runs.any(PipelineModel.datetime >= delta_ago),
                )
            )
            # After we reset the packages config
            # the query will be empty.
            # Store the query result in a new list
            events_list = list(events)
            for event in events:
                event.packages_config = null()
                session.add(event)
            return events_list

    def set_packages_config(self, packages_config: dict):
        with sa_session_transaction(commit=True) as session:
            self.packages_config = packages_config
            session.add(self)

    def get_project_event_object(self) -> Optional[AbstractProjectObjectDbType]:
        with sa_session_transaction() as session:
            return (
                session.query(MODEL_FOR_PROJECT_EVENT[self.type])
                .filter_by(id=self.event_id)
                .first()
            )

    def __repr__(self):
        return (
            f"ProjectEventModel(type={self.type}, event_id={self.event_id}, "
            f"commit_sha={self.commit_sha})"
        )


class PipelineModel(Base):
    """
    Represents one pipeline.

    Connects ProjectEventModel (and project events like PullRequestModel via that model) with
    build/test models like  SRPMBuildModel, CoprBuildTargetModel, KojiBuildTargetModel,
    and TFTTestRunGroupModel.

    * One model of each build/test target/group model can be connected.
    * Each build/test model can be connected to multiple PipelineModels (e.g. on retrigger).
    * Each PipelineModel has to be connected to exactly one ProjectEventModel.
    * There can be multiple PipelineModels for one ProjectEventModel.
      (e.g. For each push to PR, there will be new PipelineModel, but same ProjectEventModel.)
    """

    __tablename__ = "pipelines"
    id = Column(Integer, primary_key=True)  # our database PK
    # datetime.utcnow instead of datetime.utcnow() because it's an argument to the function,
    # so it will run when the model is initiated, not when the table is made
    datetime = Column(DateTime, default=datetime.utcnow)

    project_event_id = Column(Integer, ForeignKey("project_events.id"), index=True)
    package_name = Column(String, index=True)

    project_event = relationship("ProjectEventModel", back_populates="runs")

    srpm_build_id = Column(Integer, ForeignKey("srpm_builds.id"), index=True)
    srpm_build = relationship("SRPMBuildModel", back_populates="runs")
    copr_build_group_id = Column(
        Integer,
        ForeignKey("copr_build_groups.id"),
        index=True,
    )
    copr_build_group = relationship("CoprBuildGroupModel", back_populates="runs")
    koji_build_group_id = Column(
        Integer,
        ForeignKey("koji_build_groups.id"),
        index=True,
    )
    koji_build_group = relationship("KojiBuildGroupModel", back_populates="runs")
    koji_tag_request_group_id = Column(
        Integer,
        ForeignKey("koji_tag_request_groups.id"),
        index=True,
    )
    koji_tag_request_group = relationship("KojiTagRequestGroupModel", back_populates="runs")
    vm_image_build_id = Column(
        Integer,
        ForeignKey("vm_image_build_targets.id"),
        index=True,
    )
    vm_image_build = relationship("VMImageBuildTargetModel", back_populates="runs")
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
    bodhi_update_group_id = Column(
        Integer,
        ForeignKey("bodhi_update_groups.id"),
        index=True,
    )
    bodhi_update_group = relationship("BodhiUpdateGroupModel", back_populates="runs")

    @classmethod
    def create(
        cls,
        project_event: ProjectEventModel,
        package_name: Optional[str] = None,
    ) -> "PipelineModel":
        """Create a pipeline triggered by the given project_event.
        If project is a monorepo, then specify for which
        package the pipeline is run. Otherwise the package name
        can be None.
        """
        with sa_session_transaction(commit=True) as session:
            run_model = PipelineModel()
            run_model.project_event = project_event
            run_model.package_name = package_name
            session.add(run_model)
            return run_model

    def get_project_event_object(self) -> AbstractProjectObjectDbType:
        return self.project_event.get_project_event_object()

    def __repr__(self):
        return (
            f"PipelineModel(id={self.id}, datetime='{datetime}', "
            f"project_event={self.project_event})"
        )

    @classmethod
    def __query_merged_runs(cls):
        with sa_session_transaction() as session:
            return session.query(
                func.min(PipelineModel.id).label("merged_id"),
                PipelineModel.srpm_build_id,
                func.array_agg(psql_array([PipelineModel.copr_build_group_id])).label(
                    "copr_build_group_id",
                ),
                func.array_agg(psql_array([PipelineModel.koji_build_group_id])).label(
                    "koji_build_group_id",
                ),
                func.array_agg(psql_array([PipelineModel.test_run_group_id])).label(
                    "test_run_group_id",
                ),
                func.array_agg(psql_array([PipelineModel.sync_release_run_id])).label(
                    "sync_release_run_id",
                ),
                func.array_agg(psql_array([PipelineModel.bodhi_update_group_id])).label(
                    "bodhi_update_group_id",
                ),
                func.array_agg(psql_array([PipelineModel.vm_image_build_id])).label(
                    "vm_image_build_id",
                ),
            )

    @classmethod
    def get_merged_chroots(cls, first: int, last: int) -> Iterable["PipelineModel"]:
        return (
            cls.__query_merged_runs()
            .group_by(
                PipelineModel.srpm_build_id,
                case(
                    (PipelineModel.srpm_build_id.isnot(null()), 0),
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
                    (PipelineModel.srpm_build_id.isnot(null()), 0),
                    else_=PipelineModel.id,
                ),
            )
            .order_by(asc("merged_id"))
            .first()
        )

    @classmethod
    def get_run(cls, id_: int) -> Optional["PipelineModel"]:
        with sa_session_transaction() as session:
            return session.query(PipelineModel).filter_by(id=id_).first()


class CoprBuildGroupModel(ProjectAndEventsConnector, GroupModel, Base):
    __tablename__ = "copr_build_groups"
    id = Column(Integer, primary_key=True)
    submitted_time = Column(DateTime, default=datetime.utcnow)

    runs = relationship("PipelineModel", back_populates="copr_build_group")
    copr_build_targets = relationship(
        "CoprBuildTargetModel",
        back_populates="group_of_targets",
    )

    def __repr__(self) -> str:
        return f"CoprBuildGroupModel(id={self.id}, submitted_time={self.submitted_time})"

    @property
    def grouped_targets(self) -> list["CoprBuildTargetModel"]:
        return self.copr_build_targets

    @classmethod
    def create(cls, run_model: "PipelineModel") -> "CoprBuildGroupModel":
        with sa_session_transaction(commit=True) as session:
            build_group = cls()
            session.add(build_group)
            if run_model.copr_build_group:
                # Clone run model
                new_run_model = PipelineModel.create(
                    project_event=run_model.project_event,
                    package_name=run_model.package_name,
                )
                new_run_model.srpm_build = run_model.srpm_build
                new_run_model.copr_build_group = build_group
                session.add(new_run_model)
            else:
                run_model.copr_build_group = build_group
                session.add(run_model)

            return build_group

    @classmethod
    def get_by_id(cls, group_id: int) -> Optional["CoprBuildGroupModel"]:
        with sa_session_transaction() as session:
            return session.query(CoprBuildGroupModel).filter_by(id=group_id).first()

    @classmethod
    def get_running(cls, commit_sha: str) -> Iterable[tuple["CoprBuildTargetModel"]]:
        """Get list of currently running Copr builds matching the passed
        arguments.

        Args:
            commit_sha: Commit hash that is used for filtering the running jobs.

        Returns:
            An iterable over Copr target models that are curently in queue
            (running) or waiting for an SRPM.
        """
        q = (
            select(CoprBuildTargetModel)
            .join(CoprBuildGroupModel)
            .join(PipelineModel)
            .join(ProjectEventModel)
            .filter(
                ProjectEventModel.commit_sha == commit_sha,
                CoprBuildTargetModel.status.in_(
                    (BuildStatus.pending, BuildStatus.waiting_for_srpm)
                ),
            )
        )
        with sa_session_transaction() as session:
            return session.execute(q)


class BuildStatus(str, enum.Enum):
    """An enum of all possible build statuses"""

    success = "success"
    pending = "pending"
    failure = "failure"
    error = "error"
    waiting_for_srpm = "waiting_for_srpm"
    retry = "retry"
    canceled = "canceled"

    @staticmethod
    def is_final_state(status: "BuildStatus"):
        return status in {
            BuildStatus.success,
            BuildStatus.failure,
            BuildStatus.error,
            BuildStatus.canceled,
        }


class CoprBuildTargetModel(GroupAndTargetModelConnector, Base):
    """
    Representation of Copr build for one target.
    """

    __tablename__ = "copr_build_targets"
    id = Column(Integer, primary_key=True)
    build_id = Column(String, index=True)  # copr build id

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
    copr_build_group_id = Column(
        Integer,
        ForeignKey("copr_build_groups.id"),
        index=True,
    )

    group_of_targets = relationship(
        "CoprBuildGroupModel",
        back_populates="copr_build_targets",
    )

    scan = relationship("OSHScanModel", back_populates="copr_build_target")

    identifier = Column(String)

    def set_built_packages(self, built_packages):
        with sa_session_transaction(commit=True) as session:
            self.built_packages = built_packages
            session.add(self)

    def set_start_time(self, start_time: datetime):
        with sa_session_transaction(commit=True) as session:
            self.build_start_time = start_time
            session.add(self)

    def set_end_time(self, end_time: datetime):
        with sa_session_transaction(commit=True) as session:
            self.build_finished_time = end_time
            session.add(self)

    def set_status(self, status: BuildStatus):
        with sa_session_transaction(commit=True) as session:
            self.status = status
            session.add(self)

    def set_build_logs_url(self, build_logs: str):
        with sa_session_transaction(commit=True) as session:
            self.build_logs_url = build_logs
            session.add(self)

    def set_web_url(self, web_url: str):
        with sa_session_transaction(commit=True) as session:
            self.web_url = web_url
            session.add(self)

    def set_build_id(self, build_id: str):
        with sa_session_transaction(commit=True) as session:
            self.build_id = build_id
            session.add(self)

    def get_srpm_build(self) -> Optional["SRPMBuildModel"]:
        # All SRPMBuild models for all the runs have to be same.
        return self.group_of_targets.runs[0].srpm_build if self.group_of_targets.runs else None

    @classmethod
    def get_by_id(cls, id_: int) -> Optional["CoprBuildTargetModel"]:
        with sa_session_transaction() as session:
            return session.query(CoprBuildTargetModel).filter_by(id=id_).first()

    @classmethod
    def get_all(cls) -> Iterable["CoprBuildTargetModel"]:
        with sa_session_transaction() as session:
            return session.query(CoprBuildTargetModel).order_by(
                desc(CoprBuildTargetModel.id),
            )

    @classmethod
    def get_merged_chroots(
        cls,
        first: int,
        last: int,
    ) -> Iterable["CoprBuildTargetModel"]:
        """Returns a list of unique build ids with merged status, chroots
        Details:
        https://github.com/packit/packit-service/pull/674#discussion_r439819852
        """
        with sa_session_transaction() as session:
            return (
                session.query(
                    # We need something to order our merged builds by,
                    # so set new_id to be min(ids of to-be-merged rows)
                    func.min(CoprBuildTargetModel.id).label("new_id"),
                    # Select identical element(s)
                    CoprBuildTargetModel.build_id,
                    # Merge chroots and statuses from different rows into one
                    func.array_agg(psql_array([CoprBuildTargetModel.target])).label(
                        "target",
                    ),
                    func.json_agg(psql_array([CoprBuildTargetModel.status])).label(
                        "status",
                    ),
                    func.array_agg(psql_array([CoprBuildTargetModel.id])).label(
                        "packit_id_per_chroot",
                    ),
                )
                .group_by(
                    CoprBuildTargetModel.build_id,
                )  # Group by identical element(s)
                .order_by(desc("new_id"))
                .slice(first, last)
            )

    # Returns all builds with that build_id, irrespective of target
    @classmethod
    def get_all_by_build_id(
        cls,
        build_id: Union[str, int],
    ) -> Iterable["CoprBuildTargetModel"]:
        if isinstance(build_id, int):
            # See the comment in get_by_task_id()
            build_id = str(build_id)
        with sa_session_transaction() as session:
            return session.query(CoprBuildTargetModel).filter_by(build_id=build_id)

    @classmethod
    def get_all_by_status(cls, status: BuildStatus) -> Iterable["CoprBuildTargetModel"]:
        """Returns all builds which currently have the given status."""
        with sa_session_transaction() as session:
            return session.query(CoprBuildTargetModel).filter_by(status=status)

    # returns the build matching the build_id and the target
    @classmethod
    def get_by_build_id(
        cls,
        build_id: Union[str, int],
        target: Optional[str] = None,
    ) -> Optional["CoprBuildTargetModel"]:
        if isinstance(build_id, int):
            # PG is pesky about this:
            #   LINE 3: WHERE copr_builds.build_id = 1245767 AND copr_builds.target ...
            #   HINT:  No operator matches the given name and argument type(s).
            #   You might need to add explicit type casts.
            build_id = str(build_id)
        with sa_session_transaction() as session:
            query = session.query(CoprBuildTargetModel).filter_by(build_id=build_id)
            if target:
                query = query.filter_by(target=target)
            return query.first()

    @staticmethod
    def get_all_by(
        commit_sha: str,
        project_name: Optional[str] = None,
        owner: Optional[str] = None,
        target: Optional[str] = None,
        status: BuildStatus = None,
    ) -> Iterable["CoprBuildTargetModel"]:
        """
        All owner/project_name builds sorted from latest to oldest
        with the given commit_sha and optional target.
        """
        with sa_session_transaction() as session:
            query = (
                session.query(CoprBuildTargetModel)
                .join(
                    CoprBuildTargetModel.group_of_targets,
                )
                .join(
                    PipelineModel,
                    PipelineModel.copr_build_group_id == CoprBuildGroupModel.id,
                )
                .join(
                    ProjectEventModel,
                    PipelineModel.project_event_id == ProjectEventModel.id,
                )
                .filter(CoprBuildTargetModel.project_name == project_name)
                .filter(ProjectEventModel.commit_sha == commit_sha)
                .order_by(CoprBuildTargetModel.build_id.desc())
            )

            if owner:
                query = query.filter(CoprBuildTargetModel.owner == owner)
            if target:
                query = query.filter(CoprBuildTargetModel.target == target)
            if status:
                query = query.filter(CoprBuildTargetModel.status == status)

            return query

    @classmethod
    def get_all_by_commit(cls, commit_sha: str) -> Iterable["CoprBuildTargetModel"]:
        """Returns all builds that match a given commit sha"""
        with sa_session_transaction() as session:
            return (
                session.query(CoprBuildTargetModel)
                .join(
                    CoprBuildTargetModel.group_of_targets,
                )
                .join(
                    PipelineModel,
                    PipelineModel.copr_build_group_id == CoprBuildGroupModel.id,
                )
                .join(
                    ProjectEventModel,
                    PipelineModel.project_event_id == ProjectEventModel.id,
                )
                .filter(ProjectEventModel.commit_sha == commit_sha)
            )

    @classmethod
    def create(
        cls,
        build_id: Optional[str],
        project_name: str,
        owner: str,
        web_url: Optional[str],
        target: str,
        status: BuildStatus,
        copr_build_group: "CoprBuildGroupModel",
        task_accepted_time: Optional[datetime] = None,
        identifier: Optional[str] = None,
    ) -> "CoprBuildTargetModel":
        with sa_session_transaction(commit=True) as session:
            build = cls()
            build.build_id = build_id
            build.status = status
            build.project_name = project_name
            build.owner = owner
            build.web_url = web_url
            build.target = target
            build.task_accepted_time = task_accepted_time
            build.identifier = identifier
            session.add(build)

            copr_build_group.copr_build_targets.append(build)
            session.add(copr_build_group)

            return build

    @classmethod
    def get(
        cls,
        build_id: str,
        target: str,
    ) -> Optional["CoprBuildTargetModel"]:
        return cls.get_by_build_id(build_id, target)

    def __repr__(self):
        return (
            f"CoprBuildTargetModel(id={self.id}, build_submitted_time={self.build_submitted_time})"
        )

    def add_scan(self, task_id: int) -> "OSHScanModel":
        with sa_session_transaction(commit=True) as session:
            scan = OSHScanModel.get_or_create(task_id)
            scan.copr_build_target = self
            session.add(scan)
            return scan

    @contextmanager
    def add_scan_transaction(self) -> Generator["OSHScanModel"]:
        """
        Context manager that creates a ScanModel upon entering the context,
        provides a corresponding instance of `ScanModel` to be updated within the context
        and commits the changes upon exiting the context, all within a single transaction.

        This locking mechanism is working on the assumption that just a single scan model
        for build can exist.

        raise: IntegrityError if the scan model already exists
        """
        session = singleton_session or Session()
        try:
            scan = OSHScanModel()
            scan.copr_build_target = self
            session.add(scan)
            session.commit()
        except Exception as ex:
            logger.warning(f"Exception while working with database: {ex!r}")
            session.rollback()
            raise

        try:
            yield scan
        except Exception as ex:
            logger.warning(f"{ex!r}")
            session.rollback()
            raise

        try:
            session.add(scan)
            session.commit()
        except Exception as ex:
            logger.warning(f"Exception while working with database: {ex!r}")
            session.rollback()
            raise


class KojiBuildGroupModel(ProjectAndEventsConnector, GroupModel, Base):
    __tablename__ = "koji_build_groups"
    id = Column(Integer, primary_key=True)
    submitted_time = Column(DateTime, default=datetime.utcnow)

    runs = relationship("PipelineModel", back_populates="koji_build_group")
    koji_build_targets = relationship(
        "KojiBuildTargetModel",
        back_populates="group_of_targets",
    )

    @property
    def grouped_targets(self):
        return self.koji_build_targets

    def __repr__(self) -> str:
        return f"KojiBuildGroupModel(id={self.id}, submitted_time={self.submitted_time})"

    @classmethod
    def get_by_id(cls, id_: int) -> Optional["KojiBuildGroupModel"]:
        with sa_session_transaction() as session:
            return session.query(KojiBuildGroupModel).filter_by(id=id_).first()

    @classmethod
    def create(cls, run_model: "PipelineModel") -> "KojiBuildGroupModel":
        with sa_session_transaction(commit=True) as session:
            build_group = cls()
            session.add(build_group)
            if run_model.koji_build_group:
                # Clone run model
                new_run_model = PipelineModel.create(
                    project_event=run_model.project_event,
                    package_name=run_model.package_name,
                )
                new_run_model.srpm_build = run_model.srpm_build
                new_run_model.koji_build_group = build_group
                session.add(new_run_model)
            else:
                run_model.koji_build_group = build_group
                session.add(run_model)
            return build_group


class BodhiUpdateTargetModel(GroupAndTargetModelConnector, Base):
    __tablename__ = "bodhi_update_targets"
    id = Column(Integer, primary_key=True)
    status = Column(String)
    target = Column(String)
    web_url = Column(String)
    koji_nvrs = Column(String)
    sidetag = Column(String)
    alias = Column(String)
    submitted_time = Column(DateTime, default=datetime.utcnow)
    update_creation_time = Column(DateTime)
    data = Column(JSON)
    bodhi_update_group_id = Column(Integer, ForeignKey("bodhi_update_groups.id"))

    group_of_targets = relationship(
        "BodhiUpdateGroupModel",
        back_populates="bodhi_update_targets",
    )

    def set_status(self, status: str):
        with sa_session_transaction(commit=True) as session:
            self.status = status
            session.add(self)

    def set_web_url(self, web_url: str):
        with sa_session_transaction(commit=True) as session:
            self.web_url = web_url
            session.add(self)

    def set_alias(self, alias: str):
        with sa_session_transaction(commit=True) as session:
            self.alias = alias
            session.add(self)

    def set_data(self, data: dict):
        with sa_session_transaction(commit=True) as session:
            self.data = data
            session.add(self)

    def set_update_creation_time(self, time: datetime):
        with sa_session_transaction(commit=True) as session:
            self.update_creation_time = time
            session.add(self)

    @classmethod
    def create(
        cls,
        target: str,
        status: str,
        koji_nvrs: str,
        bodhi_update_group: "BodhiUpdateGroupModel",
        sidetag: Optional[str] = None,
    ) -> "BodhiUpdateTargetModel":
        with sa_session_transaction(commit=True) as session:
            update = cls()
            update.status = status
            update.target = target
            update.koji_nvrs = koji_nvrs
            update.sidetag = sidetag
            session.add(update)

            bodhi_update_group.bodhi_update_targets.append(update)
            session.add(bodhi_update_group)

            return update

    @classmethod
    def get_by_id(cls, id_: int) -> Optional["BodhiUpdateTargetModel"]:
        with sa_session_transaction() as session:
            return session.query(BodhiUpdateTargetModel).filter_by(id=id_).first()

    @classmethod
    def get_all(cls) -> Iterable["BodhiUpdateTargetModel"]:
        with sa_session_transaction() as session:
            return session.query(BodhiUpdateTargetModel)

    @classmethod
    def get_range(cls, first: int, last: int) -> Iterable["BodhiUpdateTargetModel"]:
        with sa_session_transaction() as session:
            return (
                session.query(BodhiUpdateTargetModel)
                .order_by(desc(BodhiUpdateTargetModel.id))
                .slice(first, last)
            )

    @classmethod
    def get_all_projects(cls) -> set["GitProjectModel"]:
        """Get all git projects with a saved successfull bodhi update."""
        with sa_session_transaction() as session:
            query = (
                session.query(ProjectEventModel)
                .join(
                    PipelineModel,
                    PipelineModel.project_event_id == ProjectEventModel.id,
                )
                .join(
                    BodhiUpdateGroupModel,
                    PipelineModel.bodhi_update_group_id == BodhiUpdateGroupModel.id,
                )
                .join(
                    BodhiUpdateTargetModel,
                    BodhiUpdateGroupModel.id == BodhiUpdateTargetModel.bodhi_update_group_id,
                )
            ).filter(BodhiUpdateTargetModel.status == "success")

            project_event_branches = [
                project_event.get_project_event_object() for project_event in query
            ]
            projects = [branch.project for branch in project_event_branches]
            return set(projects)

    @classmethod
    def get_last_successful_by_sidetag(
        cls,
        sidetag: str,
    ) -> Optional["BodhiUpdateTargetModel"]:
        with sa_session_transaction() as session:
            return (
                session.query(BodhiUpdateTargetModel)
                .filter(
                    BodhiUpdateTargetModel.status == "success",
                    BodhiUpdateTargetModel.sidetag == sidetag,
                )
                .order_by(BodhiUpdateTargetModel.update_creation_time.desc())
                .first()
            )

    @classmethod
    def get_all_successful_or_in_progress_by_nvrs(
        cls,
        koji_nvrs: str,
    ) -> set["BodhiUpdateTargetModel"]:
        regexp = "|".join(re.escape(nvr) for nvr in set(koji_nvrs.split()))
        with sa_session_transaction() as session:
            return set(
                session.query(BodhiUpdateTargetModel)
                .filter(
                    BodhiUpdateTargetModel.status.in_(("queued", "retry", "success")),
                    BodhiUpdateTargetModel.koji_nvrs.regexp_match(regexp),
                )
                .all(),
            )


class BodhiUpdateGroupModel(ProjectAndEventsConnector, GroupModel, Base):
    __tablename__ = "bodhi_update_groups"
    id = Column(Integer, primary_key=True)
    submitted_time = Column(DateTime, default=datetime.utcnow)

    runs = relationship("PipelineModel", back_populates="bodhi_update_group")
    bodhi_update_targets = relationship(
        "BodhiUpdateTargetModel",
        back_populates="group_of_targets",
    )

    @property
    def grouped_targets(self):
        return self.bodhi_update_targets

    def __repr__(self) -> str:
        return f"BodhiUpdateGroupModel(id={self.id}, submitted_time={self.submitted_time})"

    @classmethod
    def get_by_id(cls, id_: int) -> Optional["BodhiUpdateGroupModel"]:
        with sa_session_transaction() as session:
            return session.query(BodhiUpdateGroupModel).filter_by(id=id_).first()

    @classmethod
    def create(cls, run_model: "PipelineModel") -> "BodhiUpdateGroupModel":
        with sa_session_transaction(commit=True) as session:
            update_group = cls()
            session.add(update_group)
            if run_model.bodhi_update_group:
                # Clone run model
                new_run_model = PipelineModel.create(
                    project_event=run_model.project_event,
                    package_name=run_model.package_name,
                )
                new_run_model.bodhi_update_group = update_group
                session.add(new_run_model)
            else:
                run_model.bodhi_update_group = update_group
                session.add(run_model)
            return update_group


class KojiBuildTargetModel(GroupAndTargetModelConnector, Base):
    """we create an entry for every target"""

    __tablename__ = "koji_build_targets"
    id = Column(Integer, primary_key=True)
    task_id = Column(String, index=True)  # ID of the Koji build task

    # what's the build status?
    status = Column(String)
    # chroot, but we use the word target in our docs
    target = Column(String)
    # URL to koji web ui for the particular build
    web_url = Column(String)
    # url to koji build logs
    # dictionary with archs and links, e.g. {"x86_64": "my-url"}
    build_logs_urls = Column(JSON)
    # datetime.utcnow instead of datetime.utcnow() because its an argument to the function
    # so it will run when the koji build is initiated, not when the table is made
    build_submitted_time = Column(DateTime, default=datetime.utcnow)
    build_start_time = Column(DateTime)
    build_finished_time = Column(DateTime)
    # stdout from the Koji build submission command
    build_submission_stdout = Column(Text)

    # metadata for the build which didn't make it to schema yet
    # metadata is reserved to sqlalch
    data = Column(JSON)

    sidetag = Column(String)
    nvr = Column(String)

    # it is a scratch build?
    scratch = Column(Boolean)
    koji_build_group_id = Column(Integer, ForeignKey("koji_build_groups.id"))

    group_of_targets = relationship(
        "KojiBuildGroupModel",
        back_populates="koji_build_targets",
    )

    def set_status(self, status: str):
        with sa_session_transaction(commit=True) as session:
            self.status = status
            session.add(self)

    def set_build_logs_urls(self, build_logs: dict):
        with sa_session_transaction(commit=True) as session:
            self.build_logs_urls = build_logs
            session.add(self)

    def set_web_url(self, web_url: str):
        with sa_session_transaction(commit=True) as session:
            self.web_url = web_url
            session.add(self)

    def set_task_id(self, task_id: str):
        with sa_session_transaction(commit=True) as session:
            self.task_id = task_id
            session.add(self)

    def set_build_start_time(self, build_start_time: Optional[DateTime]):
        with sa_session_transaction(commit=True) as session:
            self.build_start_time = build_start_time
            session.add(self)

    def set_build_finished_time(self, build_finished_time: Optional[DateTime]):
        with sa_session_transaction(commit=True) as session:
            self.build_finished_time = build_finished_time
            session.add(self)

    def set_build_submitted_time(self, build_submitted_time: Optional[DateTime]):
        with sa_session_transaction(commit=True) as session:
            self.build_submitted_time = build_submitted_time
            session.add(self)

    def set_scratch(self, value: bool):
        with sa_session_transaction(commit=True) as session:
            self.scratch = value
            session.add(self)

    def set_data(self, data: dict):
        with sa_session_transaction(commit=True) as session:
            self.data = data
            session.add(self)

    def set_build_submission_stdout(self, build_submission_stdout: str):
        with sa_session_transaction(commit=True) as session:
            self.build_submission_stdout = build_submission_stdout
            session.add(self)

    def get_srpm_build(self) -> Optional["SRPMBuildModel"]:
        # All SRPMBuild models for all the runs have to be same.
        return self.group_of_targets.runs[0].srpm_build if self.group_of_targets.runs else None

    @classmethod
    def get_by_id(cls, id_: int) -> Optional["KojiBuildTargetModel"]:
        with sa_session_transaction() as session:
            return session.query(KojiBuildTargetModel).filter_by(id=id_).first()

    @classmethod
    def get_all(cls) -> Iterable["KojiBuildTargetModel"]:
        with sa_session_transaction() as session:
            return session.query(KojiBuildTargetModel)

    @classmethod
    def get_range(
        cls,
        first: int,
        last: int,
        scratch: Optional[bool] = None,
    ) -> Iterable["KojiBuildTargetModel"]:
        with sa_session_transaction() as session:
            query = session.query(KojiBuildTargetModel).order_by(
                desc(KojiBuildTargetModel.id),
            )

            if scratch is not None:
                query = query.filter_by(scratch=scratch)

            return query.slice(first, last)

    @classmethod
    def get_by_task_id(
        cls,
        task_id: Union[str, int],
        target: Optional[str] = None,
    ) -> Optional["KojiBuildTargetModel"]:
        """
        Returns the first build matching the build_id and optionally the target.
        """
        if isinstance(task_id, int):
            # PG is pesky about this:
            #   LINE 3: WHERE koji_builds.build_id = 1245767 AND koji_builds.target ...
            #   HINT:  No operator matches the given name and argument type(s).
            #   You might need to add explicit type casts.
            task_id = str(task_id)
        with sa_session_transaction() as session:
            query = session.query(KojiBuildTargetModel).filter_by(task_id=task_id)
            if target:
                query = query.filter_by(target=target)
            return query.first()

    @classmethod
    def create(
        cls,
        task_id: Optional[str],
        web_url: Optional[str],
        target: str,
        status: str,
        scratch: bool,
        koji_build_group: "KojiBuildGroupModel",
        sidetag: Optional[str] = None,
        nvr: Optional[str] = None,
    ) -> "KojiBuildTargetModel":
        with sa_session_transaction(commit=True) as session:
            build = cls()
            build.task_id = task_id
            build.status = status
            build.web_url = web_url
            build.target = target
            build.scratch = scratch
            build.sidetag = sidetag
            build.nvr = nvr
            session.add(build)

            koji_build_group.koji_build_targets.append(build)
            session.add(koji_build_group)

            return build

    @classmethod
    def get(
        cls,
        build_id: str,
        target: str,
    ) -> Optional["KojiBuildTargetModel"]:
        return cls.get_by_task_id(build_id, target)

    def __repr__(self):
        return (
            f"KojiBuildTargetModel(id={self.id}, build_submitted_time={self.build_submitted_time})"
        )

    @classmethod
    def get_last_successful_scratch_by_commit_target(
        cls,
        commit_sha: str,
        target: str,
    ) -> Optional["KojiBuildTargetModel"]:
        with sa_session_transaction() as session:
            return (
                session.query(KojiBuildTargetModel)
                .join(
                    KojiBuildTargetModel.group_of_targets,
                )
                .join(
                    PipelineModel,
                    PipelineModel.koji_build_group_id == KojiBuildGroupModel.id,
                )
                .join(
                    ProjectEventModel,
                    PipelineModel.project_event_id == ProjectEventModel.id,
                )
                .filter(
                    ProjectEventModel.commit_sha == commit_sha,
                    KojiBuildTargetModel.target == target,
                    KojiBuildTargetModel.scratch == True,  # noqa
                    KojiBuildTargetModel.status == "success",
                )
                .order_by(KojiBuildTargetModel.build_submitted_time.desc())
                .first()
            )

    @classmethod
    def get_all_successful_or_in_progress_by_nvr(
        cls,
        nvr: str,
    ) -> set["KojiBuildTargetModel"]:
        with sa_session_transaction() as session:
            return set(
                session.query(KojiBuildTargetModel)
                .filter(
                    KojiBuildTargetModel.nvr == nvr,
                    KojiBuildTargetModel.scratch == False,  # noqa
                    KojiBuildTargetModel.status.in_(
                        ("queued", "pending", "retry", "running", "success"),
                    ),
                )
                .all(),
            )

    @classmethod
    def get_all_projects(cls) -> set["GitProjectModel"]:
        """Get all git projects with a successful downstream koji build."""
        with sa_session_transaction() as session:
            query = (
                session.query(ProjectEventModel)
                .join(
                    PipelineModel,
                    PipelineModel.project_event_id == ProjectEventModel.id,
                )
                .join(
                    KojiBuildGroupModel,
                    PipelineModel.koji_build_group_id == KojiBuildGroupModel.id,
                )
                .join(
                    KojiBuildTargetModel,
                    KojiBuildGroupModel.id == KojiBuildTargetModel.koji_build_group_id,
                )
                .filter(
                    KojiBuildTargetModel.scratch == False,  # noqa
                    KojiBuildTargetModel.status == "success",
                )
            )
            project_event_branches = [
                project_event.get_project_event_object() for project_event in query
            ]
            projects = [branch.project for branch in project_event_branches]
            return set(projects)


class KojiTagRequestGroupModel(ProjectAndEventsConnector, GroupModel, Base):
    __tablename__ = "koji_tag_request_groups"
    id = Column(Integer, primary_key=True)
    submitted_time = Column(DateTime, default=datetime.utcnow)

    runs = relationship("PipelineModel", back_populates="koji_tag_request_group")
    koji_tag_request_targets = relationship(
        "KojiTagRequestTargetModel",
        back_populates="group_of_targets",
    )

    @property
    def grouped_targets(self):
        return self.koji_tag_request_targets

    def __repr__(self) -> str:
        return f"KojiTagRequestGroupModel(id={self.id}, submitted_time={self.submitted_time})"

    @classmethod
    def get_by_id(cls, id_: int) -> Optional["KojiTagRequestGroupModel"]:
        with sa_session_transaction() as session:
            return session.query(KojiTagRequestGroupModel).filter_by(id=id_).first()

    @classmethod
    def create(cls, run_model: "PipelineModel") -> "KojiTagRequestGroupModel":
        with sa_session_transaction(commit=True) as session:
            tag_request_group = cls()
            session.add(tag_request_group)
            if run_model.koji_tag_request_group:
                # Clone run model
                new_run_model = PipelineModel.create(
                    project_event=run_model.project_event,
                    package_name=run_model.package_name,
                )
                new_run_model.srpm_build = run_model.srpm_build
                new_run_model.koji_tag_request_group = tag_request_group
                session.add(new_run_model)
            else:
                run_model.koji_tag_request_group = tag_request_group
                session.add(run_model)
            return tag_request_group


class KojiTagRequestTargetModel(GroupAndTargetModelConnector, Base):
    """we create an entry for every target"""

    __tablename__ = "koji_tag_request_targets"
    id = Column(Integer, primary_key=True)
    task_id = Column(String, index=True)  # ID of the Koji tag task

    # chroot, but we use the word target in our docs
    target = Column(String)
    # URL to koji web ui for the particular build
    web_url = Column(String)
    # datetime.utcnow instead of datetime.utcnow() because its an argument to the function
    # so it will run when the koji build is initiated, not when the table is made
    tag_request_submitted_time = Column(DateTime, default=datetime.utcnow)

    sidetag = Column(String)
    nvr = Column(String)

    koji_tag_request_group_id = Column(Integer, ForeignKey("koji_tag_request_groups.id"))

    group_of_targets = relationship(
        "KojiTagRequestGroupModel",
        back_populates="koji_tag_request_targets",
    )

    def set_web_url(self, web_url: str):
        with sa_session_transaction(commit=True) as session:
            self.web_url = web_url
            session.add(self)

    def set_task_id(self, task_id: str):
        with sa_session_transaction(commit=True) as session:
            self.task_id = task_id
            session.add(self)

    def set_tag_request_submitted_time(self, tag_request_submitted_time: Optional[DateTime]):
        with sa_session_transaction(commit=True) as session:
            self.tag_request_submitted_time = tag_request_submitted_time
            session.add(self)

    @classmethod
    def get_by_id(cls, id_: int) -> Optional["KojiTagRequestTargetModel"]:
        with sa_session_transaction() as session:
            return session.query(KojiTagRequestTargetModel).filter_by(id=id_).first()

    @classmethod
    def get_all(cls) -> Iterable["KojiTagRequestTargetModel"]:
        with sa_session_transaction() as session:
            return session.query(KojiTagRequestTargetModel)

    @classmethod
    def get_range(
        cls,
        first: int,
        last: int,
    ) -> Iterable["KojiTagRequestTargetModel"]:
        with sa_session_transaction() as session:
            query = session.query(KojiTagRequestTargetModel).order_by(
                desc(KojiTagRequestTargetModel.id),
            )

            return query.slice(first, last)

    @classmethod
    def create(
        cls,
        task_id: Optional[str],
        web_url: Optional[str],
        target: str,
        koji_tag_request_group: "KojiTagRequestGroupModel",
        sidetag: Optional[str] = None,
        nvr: Optional[str] = None,
    ) -> "KojiTagRequestTargetModel":
        with sa_session_transaction(commit=True) as session:
            tag_request = cls()
            tag_request.task_id = task_id
            tag_request.web_url = web_url
            tag_request.target = target
            tag_request.sidetag = sidetag
            tag_request.nvr = nvr
            session.add(tag_request)

            koji_tag_request_group.koji_tag_request_targets.append(tag_request)
            session.add(koji_tag_request_group)

            return tag_request

    def __repr__(self):
        return (
            f"KojiTagRequestTargetModel(id={self.id}, "
            f"tag_submitted_time={self.tag_request_submitted_time})"
        )


class SRPMBuildModel(ProjectAndEventsConnector, Base):
    __tablename__ = "srpm_builds"
    id = Column(Integer, primary_key=True)
    status = Column(Enum(BuildStatus))
    # our logs we want to show to the user
    logs = Column(Text)
    build_submitted_time = Column(DateTime, default=datetime.utcnow)
    build_start_time = Column(DateTime)
    build_finished_time = Column(DateTime)
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
        project_event_model: ProjectEventModel,
        package_name: Optional[str] = None,
        copr_build_id: Optional[str] = None,
        copr_web_url: Optional[str] = None,
    ) -> tuple["SRPMBuildModel", "PipelineModel"]:
        """
        Create a new model for SRPM and connect it to the PipelineModel.

        * New SRPMBuildModel model will have connection to a new PipelineModel.
        * The newly created PipelineModel can reuse existing ProjectEventModel
          (e.g.: one pull-request can have multiple runs).

        More specifically:
        * On PR creation:
          -> SRPMBuildModel is created.
          -> New PipelineModel is created.
          -> ProjectEventModel is created.
        * On `/packit build` comment or new push:
          -> SRPMBuildModel is created.
          -> New PipelineModel is created.
          -> ProjectEventModel is reused.
        * On `/packit test` comment:
          -> SRPMBuildModel and CoprBuildTargetModel are reused.
          -> New TFTTestRunTargetModel is created.
          -> New PipelineModel is created and
             collects this new TFTTestRunTargetModel with old SRPMBuildModel and
             CoprBuildTargetModel.
        """
        with sa_session_transaction(commit=True) as session:
            srpm_build = cls()
            srpm_build.status = BuildStatus.pending
            srpm_build.copr_build_id = copr_build_id
            srpm_build.copr_web_url = copr_web_url
            session.add(srpm_build)

            # Create a new run model, reuse project_event_model if it exists:
            new_run_model = PipelineModel.create(
                project_event=project_event_model,
                package_name=package_name,
            )
            new_run_model.srpm_build = srpm_build
            session.add(new_run_model)

            return srpm_build, new_run_model

    @classmethod
    def get_by_id(
        cls,
        id_: int,
    ) -> Optional["SRPMBuildModel"]:
        with sa_session_transaction() as session:
            return session.query(SRPMBuildModel).filter_by(id=id_).first()

    @classmethod
    def get_range(cls, first: int, last: int) -> Iterable["SRPMBuildModel"]:
        with sa_session_transaction() as session:
            return (
                session.query(SRPMBuildModel).order_by(desc(SRPMBuildModel.id)).slice(first, last)
            )

    @classmethod
    def get_by_copr_build_id(
        cls,
        copr_build_id: Union[str, int],
    ) -> Optional["SRPMBuildModel"]:
        if isinstance(copr_build_id, int):
            copr_build_id = str(copr_build_id)
        with sa_session_transaction() as session:
            return session.query(SRPMBuildModel).filter_by(copr_build_id=copr_build_id).first()

    @classmethod
    def get_older_than(cls, delta: timedelta) -> Iterable["SRPMBuildModel"]:
        """Return builds older than delta, whose logs/artifacts haven't been discarded yet."""
        delta_ago = datetime.now(timezone.utc) - delta
        with sa_session_transaction() as session:
            return session.query(SRPMBuildModel).filter(
                SRPMBuildModel.build_submitted_time < delta_ago,
                SRPMBuildModel.logs.isnot(None),
            )

    def set_url(self, url: Optional[str]) -> None:
        with sa_session_transaction(commit=True) as session:
            self.url = null() if url is None else url
            session.add(self)

    def set_logs(self, logs: Optional[str]) -> None:
        with sa_session_transaction(commit=True) as session:
            self.logs = null() if logs is None else logs
            session.add(self)

    def set_copr_build_id(self, copr_build_id: str) -> None:
        with sa_session_transaction(commit=True) as session:
            self.copr_build_id = copr_build_id
            session.add(self)

    def set_copr_web_url(self, copr_web_url: str) -> None:
        with sa_session_transaction(commit=True) as session:
            self.copr_web_url = copr_web_url
            session.add(self)

    def set_start_time(self, start_time: datetime) -> None:
        with sa_session_transaction(commit=True) as session:
            self.build_start_time = start_time
            session.add(self)

    def set_end_time(self, end_time: datetime) -> None:
        with sa_session_transaction(commit=True) as session:
            self.build_finished_time = end_time
            session.add(self)

    def set_build_logs_url(self, logs_url: str) -> None:
        with sa_session_transaction(commit=True) as session:
            self.logs_url = logs_url
            session.add(self)

    def set_status(self, status: BuildStatus) -> None:
        with sa_session_transaction(commit=True) as session:
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
        cls,
        namespace: str,
        status: str,
        fas_account: Optional[str] = None,
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
        with sa_session_transaction(commit=True) as session:
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
        with sa_session_transaction() as session:
            return session.query(AllowlistModel).filter_by(namespace=namespace).first()

    @classmethod
    def get_by_status(cls, status: str) -> Iterable["AllowlistModel"]:
        """
        Get list of namespaces with specific status.

        Args:
            status (str): Status of the namespaces. AllowlistStatus enumeration as string.

        Returns:
            List of the namespaces with set status.
        """
        with sa_session_transaction() as session:
            return session.query(AllowlistModel).filter_by(status=status)

    @classmethod
    def remove_namespace(cls, namespace: str):
        with sa_session_transaction(commit=True) as session:
            namespace_entry = session.query(AllowlistModel).filter_by(
                namespace=namespace,
            )
            if namespace_entry.one_or_none():
                namespace_entry.delete()

    @classmethod
    def get_all(cls) -> Iterable["AllowlistModel"]:
        with sa_session_transaction() as session:
            return session.query(AllowlistModel)

    def to_dict(self) -> dict[str, str]:
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


tf_copr_association_table = Table(
    "tf_copr_build_association_table",
    # TODO: sqlalchemy-stubs should now support declarative_base but there are too many
    #       typing fixes necessary to do it now
    Base.metadata,  # type: ignore
    Column("copr_id", ForeignKey("copr_build_targets.id"), primary_key=True),
    Column("tft_id", ForeignKey("tft_test_run_targets.id"), primary_key=True),
)


tf_koji_association_table = Table(
    "tf_koji_build_association_table",
    # TODO: sqlalchemy-stubs should now support declarative_base but there are too many
    #       typing fixes necessary to do it now
    Base.metadata,  # type: ignore
    Column("koji_id", ForeignKey("koji_build_targets.id"), primary_key=True),
    Column("tft_id", ForeignKey("tft_test_run_targets.id"), primary_key=True),
)


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
    retry = "retry"
    complete = "complete"
    canceled = "canceled"
    cancel_requested = "cancel-requested"

    @classmethod
    def from_string(cls, value):
        try:
            return cls(value)
        except ValueError:
            return cls.unknown


class TFTTestRunGroupModel(ProjectAndEventsConnector, GroupModel, Base):
    __tablename__ = "tft_test_run_groups"
    id = Column(Integer, primary_key=True)
    submitted_time = Column(DateTime, default=datetime.utcnow)
    ranch = Column(String)

    runs = relationship("PipelineModel", back_populates="test_run_group")
    tft_test_run_targets = relationship(
        "TFTTestRunTargetModel",
        back_populates="group_of_targets",
    )

    def __repr__(self) -> str:
        return f"TFTTestRunGroupModel(id={self.id}, submitted_time={self.submitted_time})"

    @classmethod
    def create(cls, run_models: list["PipelineModel"], ranch: str) -> "TFTTestRunGroupModel":
        with sa_session_transaction(commit=True) as session:
            test_run_group = cls()
            test_run_group.ranch = ranch
            session.add(test_run_group)

            for run_model in run_models:
                if run_model.test_run_group:
                    # Clone run model
                    new_run_model = PipelineModel.create(
                        project_event=run_model.project_event,
                        package_name=run_model.package_name,
                    )
                    new_run_model.srpm_build = run_model.srpm_build
                    new_run_model.copr_build_group = run_model.copr_build_group
                    new_run_model.test_run_group = test_run_group
                    session.add(new_run_model)
                else:
                    run_model.test_run_group = test_run_group
                    session.add(run_model)

            return test_run_group

    @property
    def grouped_targets(self) -> list["TFTTestRunTargetModel"]:
        return self.tft_test_run_targets

    @classmethod
    def get_by_id(cls, group_id: int) -> Optional["TFTTestRunGroupModel"]:
        with sa_session_transaction() as session:
            return session.query(TFTTestRunGroupModel).filter_by(id=group_id).first()

    @classmethod
    def get_running(cls, commit_sha: str, ranch: str) -> Iterable[tuple["TFTTestRunTargetModel"]]:
        """Get list of currently running Testing Farm runs matching the passed
        arguments.

        Args:
            commit_sha: Commit hash that is used for filtering the running jobs.
            ranch: Testing Farm ranch where the tests are supposed to be run.

        Returns:
            An iterable over TFT target models that reprepresent matching TF
            runs that are _new_ (to be triggered), _queued_ (already submitted
            to the TF), or _running_.
        """
        q = (
            select(TFTTestRunTargetModel)
            .join(TFTTestRunGroupModel)
            .join(PipelineModel)
            .join(ProjectEventModel)
            .filter(
                ProjectEventModel.commit_sha == commit_sha,
                TFTTestRunGroupModel.ranch == ranch,
                TFTTestRunTargetModel.status.in_(
                    (
                        TestingFarmResult.queued,
                        TestingFarmResult.running,
                    )
                ),
            )
        )
        with sa_session_transaction() as session:
            return session.execute(q)


class TFTTestRunTargetModel(GroupAndTargetModelConnector, Base):
    __tablename__ = "tft_test_run_targets"
    id = Column(Integer, primary_key=True)
    pipeline_id = Column(String, index=True)
    identifier = Column(String)
    status = Column(Enum(TestingFarmResult))
    target = Column(String)
    web_url = Column(String)
    # datetime.utcnow instead of datetime.utcnow() because its an argument to the function
    # so it will run when the model is initiated, not when the table is made
    submitted_time = Column(DateTime, default=datetime.utcnow)
    data = Column(JSON)
    tft_test_run_group_id = Column(Integer, ForeignKey("tft_test_run_groups.id"), index=True)

    copr_builds = relationship(
        "CoprBuildTargetModel",
        secondary=tf_copr_association_table,
        backref="tft_test_run_targets",
    )
    koji_builds = relationship(
        "KojiBuildTargetModel",
        secondary=tf_koji_association_table,
        backref="tft_test_run_targets",
    )
    group_of_targets = relationship(
        "TFTTestRunGroupModel",
        back_populates="tft_test_run_targets",
    )

    def set_status(self, status: TestingFarmResult, created: Optional[DateTime] = None):
        """
        set status of the TF run and optionally set the created datetime as well
        """
        with sa_session_transaction(commit=True) as session:
            self.status = status
            if created and not self.submitted_time:
                self.submitted_time = created
            session.add(self)

    def set_web_url(self, web_url: str):
        with sa_session_transaction(commit=True) as session:
            self.web_url = web_url
            session.add(self)

    def set_pipeline_id(self, pipeline_id: str) -> None:
        with sa_session_transaction(commit=True) as session:
            self.pipeline_id = pipeline_id
            session.add(self)

    def add_copr_build(self, build: "CoprBuildTargetModel"):
        with sa_session_transaction(commit=True) as session:
            self.copr_builds.append(build)
            session.add(self)

    def add_koji_build(self, build: "KojiBuildTargetModel"):
        with sa_session_transaction(commit=True) as session:
            self.koji_builds.append(build)
            session.add(self)

    @classmethod
    def create(
        cls,
        pipeline_id: Optional[str],
        status: TestingFarmResult,
        target: str,
        test_run_group: "TFTTestRunGroupModel",
        web_url: Optional[str] = None,
        data: Optional[dict] = None,
        identifier: Optional[str] = None,
        copr_build_targets: Optional[list[CoprBuildTargetModel]] = None,
        koji_build_targets: Optional[list[KojiBuildTargetModel]] = None,
    ) -> "TFTTestRunTargetModel":
        with sa_session_transaction(commit=True) as session:
            test_run = cls()
            test_run.pipeline_id = pipeline_id
            test_run.identifier = identifier
            test_run.status = status
            test_run.target = target
            test_run.web_url = web_url
            test_run.data = data
            if copr_build_targets:
                test_run.copr_builds.extend(copr_build_targets)
            if koji_build_targets:
                test_run.koji_builds.extend(koji_build_targets)
            session.add(test_run)
            test_run_group.tft_test_run_targets.append(test_run)
            session.add(test_run_group)

            return test_run

    @classmethod
    def get_by_pipeline_id(cls, pipeline_id: str) -> Optional["TFTTestRunTargetModel"]:
        with sa_session_transaction() as session:
            return session.query(TFTTestRunTargetModel).filter_by(pipeline_id=pipeline_id).first()

    @classmethod
    def get_all_by_status(
        cls,
        *status: TestingFarmResult,
    ) -> Iterable["TFTTestRunTargetModel"]:
        """Returns all runs which currently have their status set to one
        of the requested statuses."""
        with sa_session_transaction() as session:
            return session.query(TFTTestRunTargetModel).filter(
                TFTTestRunTargetModel.status.in_(status),
            )

    @classmethod
    def get_by_id(cls, id: int) -> Optional["TFTTestRunTargetModel"]:
        with sa_session_transaction() as session:
            return session.query(TFTTestRunTargetModel).filter_by(id=id).first()

    @staticmethod
    def get_all_by_commit_target(
        commit_sha: str,
        target: Optional[str] = None,
    ) -> Iterable["TFTTestRunTargetModel"]:
        """
        All tests with the given commit_sha and optional target.
        """
        with sa_session_transaction() as session:
            query = (
                session.query(TFTTestRunTargetModel)
                .join(
                    TFTTestRunTargetModel.group_of_targets,
                )
                .join(
                    PipelineModel,
                    PipelineModel.test_run_group_id == TFTTestRunGroupModel.id,
                )
                .join(
                    ProjectEventModel,
                    PipelineModel.project_event_id == ProjectEventModel.id,
                )
                .filter(ProjectEventModel.commit_sha == commit_sha)
            )
            if target:
                query = query.filter(TFTTestRunTargetModel.target == target)

            return query

    @classmethod
    def get_range(cls, first: int, last: int) -> Iterable["TFTTestRunTargetModel"]:
        with sa_session_transaction() as session:
            return (
                session.query(TFTTestRunTargetModel)
                .order_by(desc(TFTTestRunTargetModel.id))
                .slice(first, last)
            )

    def __repr__(self):
        return f"TFTTestRunTargetModel(id={self.id}, pipeline_id={self.pipeline_id})"


class SyncReleaseTargetStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    error = "error"
    retry = "retry"
    submitted = "submitted"
    skipped = "skipped"


class SyncReleaseTargetModel(ProjectAndEventsConnector, Base):
    __tablename__ = "sync_release_run_targets"
    id = Column(Integer, primary_key=True)
    branch = Column(String, default="unknown")
    status = Column(Enum(SyncReleaseTargetStatus))
    submitted_time = Column(DateTime, default=datetime.utcnow)
    start_time = Column(DateTime)
    finished_time = Column(DateTime)
    logs = Column(Text)
    sync_release_id = Column(Integer, ForeignKey("sync_release_runs.id"), index=True)
    downstream_pr_url = Column(String)  # @TODO drop when the code uses downstream_pr

    sync_release = relationship(
        "SyncReleaseModel",
        back_populates="sync_release_targets",
    )
    pull_requests = relationship(
        "SyncReleasePullRequestModel",
        secondary=sync_release_pr_association_table,
    )

    def __repr__(self) -> str:
        return f"SyncReleaseTargetModel(id={self.id})"

    @classmethod
    def create(
        cls,
        status: SyncReleaseTargetStatus,
        branch: str,
    ) -> "SyncReleaseTargetModel":
        with sa_session_transaction(commit=True) as session:
            sync_release_target = cls()
            sync_release_target.status = status
            sync_release_target.branch = branch
            session.add(sync_release_target)
            return sync_release_target

    def set_status(self, status: SyncReleaseTargetStatus) -> None:
        with sa_session_transaction(commit=True) as session:
            self.status = status
            session.add(self)

    def set_downstream_pr_url(self, downstream_pr_url: str) -> None:
        with sa_session_transaction(commit=True) as session:
            self.downstream_pr_url = downstream_pr_url
            session.add(self)

    def set_downstream_prs(self, downstream_prs: list["SyncReleasePullRequestModel"]) -> None:
        with sa_session_transaction(commit=True) as session:
            self.pull_requests = downstream_prs
            session.add(self)

    def set_start_time(self, start_time: DateTime) -> None:
        with sa_session_transaction(commit=True) as session:
            self.start_time = start_time
            session.add(self)

    def set_finished_time(self, finished_time: DateTime) -> None:
        with sa_session_transaction(commit=True) as session:
            self.finished_time = finished_time
            session.add(self)

    def set_logs(self, logs: str) -> None:
        with sa_session_transaction(commit=True) as session:
            self.logs = logs
            session.add(self)

    @classmethod
    def get_by_id(cls, id_: int) -> Optional["SyncReleaseTargetModel"]:
        with sa_session_transaction() as session:
            return session.query(SyncReleaseTargetModel).filter_by(id=id_).first()

    @classmethod
    def get_all_downstream_projects(cls) -> set["GitProjectModel"]:
        """Get all downstream projects with a pr created by Packit."""
        with sa_session_transaction() as session:
            query = (
                session.query(GitProjectModel, SyncReleaseTargetModel.status)
                .join(
                    SyncReleasePullRequestModel,
                    SyncReleasePullRequestModel.project_id == GitProjectModel.id,
                )
                .join(
                    sync_release_pr_association_table,
                    SyncReleasePullRequestModel.id
                    == sync_release_pr_association_table.c.sync_release_pr_id,
                )
                .join(
                    SyncReleaseTargetModel,
                    SyncReleaseTargetModel.id
                    == sync_release_pr_association_table.c.sync_release_target_id,
                )
                .filter(
                    SyncReleaseTargetModel.status == SyncReleaseTargetStatus.submitted,
                )
            )
            return {row[0] for row in query}


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

    def __repr__(self) -> str:
        return (
            f"SyncReleaseModel(id={self.id}, submitted_time={self.submitted_time}, "
            f"job_type={self.job_type})"
        )

    @classmethod
    def create_with_new_run(
        cls,
        status: SyncReleaseStatus,
        project_event_model: ProjectEventModel,
        job_type: SyncReleaseJobType,
        package_name: Optional[str] = None,
    ) -> tuple["SyncReleaseModel", "PipelineModel"]:
        """
        Create a new model for SyncRelease and connect it to the PipelineModel.

        * New SyncReleaseModel model will have connection to a new PipelineModel.
        * The newly created PipelineModel can reuse existing ProjectEventModel
          (e.g.: one IssueModel can have multiple runs).

        More specifically:
        * On `/packit propose-downstream` issue comment:
          -> SyncReleaseModel is created.
          -> New PipelineModel is created.
          -> ProjectEventModel is created.
        * Something went wrong, after correction and another `/packit propose-downstream` comment:
          -> SyncReleaseModel is created.
          -> PipelineModel is created.
          -> ProjectEventModel is reused.
        * TODO: we will use propose-downstream in commit-checks - fill in once it's implemented
        """
        with sa_session_transaction(commit=True) as session:
            sync_release = cls()
            sync_release.status = status
            sync_release.job_type = job_type
            session.add(sync_release)

            # Create a pipeline, reuse project_event_model if it exists:
            pipeline = PipelineModel.create(
                project_event=project_event_model,
                package_name=package_name,
            )
            pipeline.sync_release_run = sync_release
            session.add(pipeline)

            return sync_release, pipeline

    def set_status(self, status: SyncReleaseStatus) -> None:
        with sa_session_transaction(commit=True) as session:
            self.status = status
            session.add(self)

    @classmethod
    def get_by_id(cls, id_: int) -> Optional["SyncReleaseModel"]:
        with sa_session_transaction() as session:
            return session.query(SyncReleaseModel).filter_by(id=id_).first()

    @classmethod
    def get_all_by_status(cls, status: str) -> Iterable["SyncReleaseModel"]:
        with sa_session_transaction() as session:
            return session.query(SyncReleaseModel).filter_by(status=status)

    @classmethod
    def get_range(
        cls,
        first: int,
        last: int,
        job_type: SyncReleaseJobType = SyncReleaseJobType.propose_downstream,
    ) -> Iterable["SyncReleaseModel"]:
        with sa_session_transaction() as session:
            return (
                session.query(SyncReleaseModel)
                .order_by(desc(SyncReleaseModel.id))
                .filter_by(job_type=job_type)
                .slice(first, last)
            )


AbstractBuildTestDbType = Union[
    CoprBuildTargetModel,
    KojiBuildTargetModel,
    SRPMBuildModel,
    TFTTestRunTargetModel,
    SyncReleaseModel,
]


class ProjectAuthenticationIssueModel(Base):
    __tablename__ = "project_authentication_issue"

    id = Column(Integer, primary_key=True)
    project = relationship(
        "GitProjectModel",
        back_populates="project_authentication_issue",
    )
    # Check to know if we created an issue for the repo.
    issue_created = Column(Boolean)
    project_id = Column(Integer, ForeignKey("git_projects.id"))

    @classmethod
    def get_project(
        cls,
        namespace: str,
        repo_name: str,
        project_url: str,
    ) -> Optional["ProjectAuthenticationIssueModel"]:
        with sa_session_transaction(commit=True) as session:
            project = GitProjectModel.get_or_create(
                namespace=namespace,
                repo_name=repo_name,
                project_url=project_url,
            )
            return (
                session.query(ProjectAuthenticationIssueModel)
                .filter_by(project_id=project.id)
                .first()
            )

    @classmethod
    def create(
        cls,
        namespace: str,
        repo_name: str,
        project_url: str,
        issue_created: bool,
    ) -> "ProjectAuthenticationIssueModel":
        with sa_session_transaction(commit=True) as session:
            project = GitProjectModel.get_or_create(
                namespace=namespace,
                repo_name=repo_name,
                project_url=project_url,
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
        with sa_session_transaction() as session:
            return session.query(GithubInstallationModel).filter_by(id=id).first()

    @classmethod
    def get_by_account_login(
        cls,
        account_login: str,
    ) -> Optional["GithubInstallationModel"]:
        with sa_session_transaction() as session:
            return (
                session.query(GithubInstallationModel)
                .filter_by(account_login=account_login)
                .first()
            )

    @classmethod
    def get_all(cls) -> Iterable["GithubInstallationModel"]:
        with sa_session_transaction() as session:
            return session.query(GithubInstallationModel)

    @classmethod
    def create_or_update(cls, event):
        with sa_session_transaction(commit=True) as session:
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
            installation.repositories = [cls.get_project(repo).id for repo in event.repositories]
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
        Integer,
        ForeignKey("pull_requests.id"),
        unique=True,
        index=True,
    )
    dist_git_pull_request_id = Column(
        Integer,
        ForeignKey("pull_requests.id"),
        unique=True,
        index=True,
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
        with sa_session_transaction(commit=True) as session:
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
        with sa_session_transaction() as session:
            return session.query(SourceGitPRDistGitPRModel).filter_by(id=id_).one_or_none()

    @classmethod
    def get_by_source_git_id(cls, id_: int) -> Optional["SourceGitPRDistGitPRModel"]:
        with sa_session_transaction() as session:
            return (
                session.query(SourceGitPRDistGitPRModel)
                .filter_by(source_git_pull_request_id=id_)
                .one_or_none()
            )

    @classmethod
    def get_by_dist_git_id(cls, id_: int) -> Optional["SourceGitPRDistGitPRModel"]:
        with sa_session_transaction() as session:
            return (
                session.query(SourceGitPRDistGitPRModel)
                .filter_by(dist_git_pull_request_id=id_)
                .one_or_none()
            )


class VMImageBuildStatus(str, enum.Enum):
    """An enum of all possible build statuses"""

    success = "success"
    pending = "pending"
    building = "building"
    uploading = "uploading"
    registering = "registering"
    failure = "failure"
    error = "error"


class VMImageBuildTargetModel(ProjectAndEventsConnector, Base):
    """
    Representation of VM Image build for one target.
    """

    __tablename__ = "vm_image_build_targets"
    id = Column(Integer, primary_key=True)
    build_id = Column(String, index=True)  # vm image build id

    # git forge project url
    project_url = Column(String)
    # project name as shown in copr
    project_name = Column(String)
    owner = Column(String)
    # what's the build status?
    status = Column(Enum(VMImageBuildStatus))
    # chroot, but we use the word target in our docs
    target = Column(String)
    # the PR id where the triggering build comment comes from
    pr_id = Column(String)
    # for monitoring: time when we set the status about accepted task
    task_accepted_time = Column(DateTime)
    # datetime.utcnow instead of datetime.utcnow() because its an argument to the function
    # so it will run when the copr build is initiated, not when the table is made
    build_submitted_time = Column(DateTime, default=datetime.utcnow)
    build_start_time = Column(DateTime)
    build_finished_time = Column(DateTime)

    # metadata for the build which didn't make it to schema yet
    data = Column(JSON)

    runs = relationship("PipelineModel", back_populates="vm_image_build")

    def set_start_time(self, start_time: datetime):
        with sa_session_transaction(commit=True) as session:
            self.build_start_time = start_time
            session.add(self)

    def set_end_time(self, end_time: datetime):
        with sa_session_transaction(commit=True) as session:
            self.build_finished_time = end_time
            session.add(self)

    def set_status(self, status: VMImageBuildStatus):
        with sa_session_transaction(commit=True) as session:
            self.status = status
            session.add(self)

    def set_build_logs_url(self, build_logs: str):
        with sa_session_transaction(commit=True) as session:
            self.build_logs_url = build_logs
            session.add(self)

    @classmethod
    def get_by_id(cls, id_: int) -> Optional["VMImageBuildTargetModel"]:
        with sa_session_transaction() as session:
            return session.query(VMImageBuildTargetModel).filter_by(id=id_).first()

    @classmethod
    def get_all(cls) -> Iterable["VMImageBuildTargetModel"]:
        with sa_session_transaction() as session:
            return session.query(VMImageBuildTargetModel).order_by(
                desc(VMImageBuildTargetModel.id),
            )

    @classmethod
    def get_all_by_build_id(
        cls,
        build_id: Union[str, int],
    ) -> Iterable["VMImageBuildTargetModel"]:
        """Returns all builds with that build_id, irrespective of target"""
        if isinstance(build_id, int):
            # See the comment in get_by_task_id()
            build_id = str(build_id)
        with sa_session_transaction() as session:
            return session.query(VMImageBuildTargetModel).filter_by(build_id=build_id)

    @classmethod
    def get_all_by_status(
        cls,
        status: VMImageBuildStatus,
    ) -> Iterable["VMImageBuildTargetModel"]:
        """Returns all builds which currently have the given status."""
        with sa_session_transaction() as session:
            return session.query(VMImageBuildTargetModel).filter_by(status=status)

    @classmethod
    def get_by_build_id(
        cls,
        build_id: Union[str, int],
        target: Optional[str] = None,
    ) -> Optional["VMImageBuildTargetModel"]:
        """Returns the build matching the build_id and the target"""

        if isinstance(build_id, int):
            # PG is pesky about this:
            #   LINE 3: WHERE copr_builds.build_id = 1245767 AND copr_builds.target ...
            #   HINT:  No operator matches the given name and argument type(s).
            #   You might need to add explicit type casts.
            build_id = str(build_id)
        with sa_session_transaction() as session:
            query = session.query(VMImageBuildTargetModel).filter_by(build_id=build_id)
            if target:
                query = query.filter_by(target=target)
            return query.first()

    @staticmethod
    def get_all_by(
        project_name: str,
        commit_sha: str,
        owner: Optional[str] = None,
        target: Optional[str] = None,
    ) -> Iterable["VMImageBuildTargetModel"]:
        """All owner/project_name builds sorted from latest to oldest
        with the given commit_sha and optional target.
        """
        with sa_session_transaction() as session:
            query = (
                session.query(VMImageBuildTargetModel)
                .join(
                    PipelineModel,
                    PipelineModel.vm_image_build_id == VMImageBuildTargetModel.id,
                )
                .join(
                    ProjectEventModel,
                    PipelineModel.project_event_id == ProjectEventModel.id,
                )
                .filter(VMImageBuildTargetModel.project_name == project_name)
                .filter(ProjectEventModel.commit_sha == commit_sha)
                .order_by(VMImageBuildTargetModel.build_id.desc())
            )

            if owner:
                query = query.filter(VMImageBuildTargetModel.owner == owner)
            if target:
                query = query.filter(VMImageBuildTargetModel.target == target)

            return query

    @classmethod
    def get_all_by_commit(cls, commit_sha: str) -> Iterable["VMImageBuildTargetModel"]:
        """Returns all builds that match a given commit sha"""
        with sa_session_transaction() as session:
            return (
                session.query(VMImageBuildTargetModel)
                .join(
                    PipelineModel,
                    PipelineModel.vm_image_build_id == VMImageBuildTargetModel.id,
                )
                .join(
                    ProjectEventModel,
                    PipelineModel.project_event_id == ProjectEventModel.id,
                )
                .filter(ProjectEventModel.commit_sha == commit_sha)
                .order_by(VMImageBuildTargetModel.build_id.desc())
            )

    @classmethod
    def create(
        cls,
        build_id: str,
        project_name: str,
        owner: str,
        project_url: str,
        target: str,
        status: VMImageBuildStatus,
        run_model: "PipelineModel",
        task_accepted_time: Optional[datetime] = None,
    ) -> "VMImageBuildTargetModel":
        with sa_session_transaction(commit=True) as session:
            build = cls()
            build.build_id = build_id
            build.status = status
            build.project_name = project_name
            build.owner = owner
            build.project_url = project_url
            build.target = target
            build.task_accepted_time = task_accepted_time
            session.add(build)

            if run_model.vm_image_build:
                # Clone run model
                new_run_model = PipelineModel.create(
                    project_event=run_model.project_event,
                    package_name=run_model.package_name,
                )
                new_run_model.srpm_build = run_model.srpm_build
                new_run_model.copr_build_group = run_model.copr_build_group
                new_run_model.vm_image_build = build
                session.add(new_run_model)
            else:
                run_model.vm_image_build = build
                session.add(run_model)

            return build

    @classmethod
    def get(
        cls,
        build_id: str,
        target: str,
    ) -> Optional["VMImageBuildTargetModel"]:
        return cls.get_by_build_id(build_id, target)

    def __repr__(self):
        return (
            f"VMImageBuildTargetModel(id={self.id}, "
            f"build_submitted_time={self.build_submitted_time})"
        )


class SidetagGroupModel(Base):
    __tablename__ = "sidetag_groups"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, index=True)

    sidetags = relationship("SidetagModel", back_populates="sidetag_group")

    @classmethod
    def get_or_create(cls, name: str) -> "SidetagGroupModel":
        with sa_session_transaction(commit=True) as session:
            group = cls.get_by_name(name)
            if not group:
                group = cls()
                group.name = name
                session.add(group)
            return group

    @classmethod
    def get_by_name(cls, name: str) -> Optional["SidetagGroupModel"]:
        with sa_session_transaction() as session:
            return session.query(cls).filter_by(name=name).first()

    def get_sidetag_by_target(self, target: str) -> Optional["SidetagModel"]:
        with sa_session_transaction() as session:
            return (
                session.query(SidetagModel)
                .filter_by(sidetag_group_id=self.id, target=target)
                .first()
            )


class SidetagModel(Base):
    __tablename__ = "sidetags"
    id = Column(Integer, primary_key=True)
    koji_name = Column(String, unique=True, index=True)
    target = Column(String)
    sidetag_group_id = Column(Integer, ForeignKey("sidetag_groups.id"))

    sidetag_group = relationship("SidetagGroupModel", back_populates="sidetags")

    @classmethod
    @contextmanager
    def get_or_create_for_updating(
        cls,
        group_name: str,
        target: str,
    ) -> Generator["SidetagModel", None, None]:
        """
        Context manager that gets or creates a sidetag upon entering the context,
        provides a corresponding instance of `SidetagModel` to be updated within the context
        and commits the changes upon exiting the context, all within a single transaction.
        """
        with sa_session_transaction(commit=True) as session:
            # lock the sidetag group in the DB by using SELECT ... FOR UPDATE
            # all other workers accessing this group will block here until the context is exited
            group = (
                session.query(SidetagGroupModel)
                .filter_by(name=group_name)
                .with_for_update()
                .first()
            )

            sidetag = session.query(cls).filter_by(sidetag_group_id=group.id, target=target).first()
            if not sidetag:
                sidetag = cls()
                sidetag.target = target
                session.add(sidetag)

                group.sidetags.append(sidetag)
                session.add(group)

            yield sidetag
            session.add(sidetag)

    @classmethod
    def get_by_koji_name(cls, koji_name: str) -> Optional["SidetagModel"]:
        with sa_session_transaction() as session:
            return session.query(SidetagModel).filter_by(koji_name=koji_name).first()


class OSHScanStatus(str, enum.Enum):
    """An enum of all possible build statuses"""

    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    canceled = "canceled"


class OSHScanModel(Base):
    __tablename__ = "scans"
    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, unique=True)  # open scan hub id
    status = Column(Enum(OSHScanStatus))
    url = Column(String)
    issues_added_count = Column(Integer)
    issues_added_url = Column(String)
    issues_fixed_url = Column(String)
    scan_results_url = Column(String)
    submitted_time = Column(DateTime, default=datetime.utcnow)
    copr_build_target_id = Column(
        Integer,
        ForeignKey("copr_build_targets.id"),
        unique=True,
    )
    copr_build_target = relationship(
        "CoprBuildTargetModel",
        back_populates="scan",
        uselist=False,
    )

    @classmethod
    def get_or_create(cls, task_id: int) -> "OSHScanModel":
        with sa_session_transaction(commit=True) as session:
            scan = cls.get_by_task_id(task_id)
            if not scan:
                scan = cls()
                scan.task_id = task_id
                scan.status = OSHScanStatus.pending
                session.add(scan)
            return scan

    def set_status(self, status: OSHScanStatus) -> None:
        with sa_session_transaction(commit=True) as session:
            self.status = status
            session.add(self)

    def set_url(self, url: str) -> None:
        with sa_session_transaction(commit=True) as session:
            self.url = url
            session.add(self)

    def set_issues_added_url(self, issues_added_url: str) -> None:
        with sa_session_transaction(commit=True) as session:
            self.issues_added_url = issues_added_url
            session.add(self)

    def set_issues_fixed_url(self, issues_fixed_url: str) -> None:
        with sa_session_transaction(commit=True) as session:
            self.issues_fixed_url = issues_fixed_url
            session.add(self)

    def set_scan_results_url(self, scan_results_url: str) -> None:
        with sa_session_transaction(commit=True) as session:
            self.scan_results_url = scan_results_url
            session.add(self)

    def set_issues_added_count(self, issues_added_count: int) -> None:
        with sa_session_transaction(commit=True) as session:
            self.issues_added_count = issues_added_count
            session.add(self)

    @classmethod
    def get_by_task_id(cls, task_id: int) -> Optional["OSHScanModel"]:
        with sa_session_transaction() as session:
            return session.query(cls).filter_by(task_id=task_id).first()

    @classmethod
    def get_by_id(cls, id_: int) -> Optional["OSHScanModel"]:
        with sa_session_transaction() as session:
            return session.query(OSHScanModel).filter_by(id=id_).first()

    @classmethod
    def get_range(cls, first: int, last: int) -> Iterable["OSHScanModel"]:
        with sa_session_transaction() as session:
            return session.query(OSHScanModel).order_by(desc(OSHScanModel.id)).slice(first, last)


@cached(cache=TTLCache(maxsize=2048, ttl=(60 * 60 * 24)))
def get_usage_data(datetime_from=None, datetime_to=None, top=10) -> dict:
    """
    Get usage data.

    Example:
    ```
    >>> safe_dump(get_usage_data(top=3))
    active_projects:
      instances:
        github.com: 279
        gitlab.com: 3
        gitlab.freedesktop.org: 3
        gitlab.gnome.org: 2
      project_count: 287
      top_projects_by_events_handled:
        https://github.com/avocado-framework/avocado: 1327
        https://github.com/cockpit-project/cockpit: 1829
        https://github.com/systemd/systemd: 4960
    all_projects:
      instances:
        git.centos.org: 25
        github.com: 7855
        gitlab.com: 8
        gitlab.freedesktop.org: 4
        gitlab.gnome.org: 2
        src.fedoraproject.org: 22175
      project_count: 30069
    events:
      branch_push:
        events_handled: 115
        top_projects:
          https://github.com/packit/ogr: 3
          https://github.com/packit/packit: 3
          https://github.com/rhinstaller/anaconda: 3
      issue:
        events_handled: 18
        top_projects:
          https://github.com/martinpitt/python-dbusmock: 2
          https://github.com/packit/packit: 3
          https://github.com/packit/specfile: 3
      pull_request:
        events_handled: 26605
        top_projects:
          https://github.com/avocado-framework/avocado: 1327
          https://github.com/cockpit-project/cockpit: 1808
          https://github.com/systemd/systemd: 4960
      release:
        events_handled: 425
        top_projects:
          https://github.com/facebook/folly: 40
          https://github.com/packit/ogr: 33
          https://github.com/packit/packit: 57
    jobs:
      copr_build_targets:
        job_runs: 530955
        top_projects_by_job_runs:
          https://github.com/osbuild/osbuild: 38186
          https://github.com/osbuild/osbuild-composer: 106786
          https://github.com/systemd/systemd: 60158
      koji_build_targets:
        job_runs: 1466
        top_projects_by_job_runs:
          https://github.com/containers/podman: 297
          https://github.com/packit/ogr: 509
          https://github.com/rear/rear: 267
      srpm_builds:
        job_runs: 103695
        top_projects_by_job_runs:
          https://github.com/cockpit-project/cockpit: 6937
          https://github.com/packit/hello-world: 10409
          https://github.com/systemd/systemd: 14489
      sync_release_runs:
        job_runs: 419
        top_projects_by_job_runs:
          https://github.com/martinpitt/python-dbusmock: 38
          https://github.com/packit/packit: 38
          https://github.com/rhinstaller/anaconda: 34
      tft_test_run_targets:
        job_runs: 150525
        top_projects_by_job_runs:
          https://github.com/cockpit-project/cockpit: 21157
          https://github.com/oamg/convert2rhel: 15506
          https://github.com/teemtee/tmt: 22136
      vm_image_build_targets:
        job_runs: 2
        top_projects_by_job_runs:
          https://github.com/packit/ogr: 2

    ```
    """
    jobs = {}
    for job_model in [
        SRPMBuildModel,
        CoprBuildGroupModel,
        KojiBuildGroupModel,
        VMImageBuildTargetModel,
        TFTTestRunGroupModel,
        SyncReleaseModel,
    ]:
        if not hasattr(job_model, "__tablename__"):
            # otherwise mypi complains:
            # "type[ProjectAndEventsConnector]" has no attribute "__tablename__"
            continue

        jobs[job_model.__tablename__] = {
            "job_runs": GitProjectModel.get_job_usage_numbers_count_all_project_events(
                datetime_from=datetime_from,
                datetime_to=datetime_to,
                job_result_model=job_model,
            ),
            "top_projects_by_job_runs": GitProjectModel.get_job_usage_numbers_all_project_events(
                datetime_from=datetime_from,
                datetime_to=datetime_to,
                top=top,
                job_result_model=job_model,
            ),
        }

    return {
        "all_projects": {
            "project_count": GitProjectModel.get_project_count(),
            "instances": GitProjectModel.get_instance_numbers(),
        },
        "active_projects": {
            "project_count": GitProjectModel.get_active_projects_count(
                datetime_from=datetime_from,
                datetime_to=datetime_to,
            ),
            "top_projects_by_events_handled": GitProjectModel.get_active_projects_usage_numbers(
                datetime_from=datetime_from,
                datetime_to=datetime_to,
                top=top,
            ),
            "instances": GitProjectModel.get_instance_numbers_for_active_projects(
                datetime_from=datetime_from,
                datetime_to=datetime_to,
            ),
        },
        "events": {
            project_event_type.value: {
                "events_handled": GitProjectModel.get_project_event_usage_count(
                    datetime_from=datetime_from,
                    datetime_to=datetime_to,
                    project_event_type=project_event_type,
                ),
                "top_projects": GitProjectModel.get_project_event_usage_numbers(
                    datetime_from=datetime_from,
                    datetime_to=datetime_to,
                    top=top,
                    project_event_type=project_event_type,
                ),
            }
            for project_event_type in ProjectEventModelType
        },
        "jobs": jobs,
    }


@cached(cache=TTLCache(maxsize=1, ttl=(60 * 60 * 24)))
def get_onboarded_projects() -> tuple[dict[int, str], dict[int, str]]:
    """Returns a tuple with two dictionaries of project IDs and URLs:
    onboarded projects: projects which have a
      merged downstream PR, a Koji build or a Bodhi update
    almost onboarded projects: projects with
      a downstream PR created but not yet merged
    """
    known_onboarded_projects = GitProjectModel.get_known_onboarded_downstream_projects()

    bodhi_updates = BodhiUpdateTargetModel.get_all_projects()
    koji_builds = KojiBuildTargetModel.get_all_projects()
    onboarded_projects = bodhi_updates.union(koji_builds).union(
        known_onboarded_projects,
    )

    # find **downstream git projects** with a PR created by Packit
    downstream_synced_projects = SyncReleaseTargetModel.get_all_downstream_projects()
    # if there exist a downstream Packit PR we are not sure it has been
    # merged, the project is *almost onboarded* until the PR is merged
    # (unless we already know it has a koji build or bodhi update, then
    # we don't need to check for a merged PR - it obviously has one)
    almost_onboarded_projects = downstream_synced_projects.difference(
        onboarded_projects,
    )
    # do not re-check projects we already checked and we know they
    # have a merged Packit PR
    recheck_if_onboarded = almost_onboarded_projects.difference(
        known_onboarded_projects,
    )

    onboarded = {
        project.id: project.project_url
        for project in onboarded_projects.union(known_onboarded_projects)
    }
    almost_onboarded = {
        project.id: project.project_url
        for project in recheck_if_onboarded.difference(onboarded_projects)
    }
    return (onboarded, almost_onboarded)
