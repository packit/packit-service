# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
Data layer on top of PSQL using sqlalch
"""

import enum
import logging
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from os import getenv
from typing import (
    Dict,
    Iterable,
    List,
    Optional,
    TYPE_CHECKING,
    Tuple,
    Type,
    Union,
    Set,
    overload,
)
from urllib.parse import urlparse

from cachetools.func import ttl_cache
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
    Table,
)
from sqlalchemy.dialects.postgresql import array as psql_array
from sqlalchemy.exc import MultipleResultsFound
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import (
    Session as SQLASession,
    relationship,
    scoped_session,
    sessionmaker,
)
from sqlalchemy.sql.functions import count
from sqlalchemy.types import ARRAY

from packit.config import JobConfigTriggerType
from packit.exceptions import PackitException
from packit_service.constants import ALLOWLIST_CONSTANTS

logger = logging.getLogger(__name__)

_CACHE_MAXSIZE = 100
_CACHE_TTL = timedelta(hours=1).seconds


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
    return (
        getenv("POOL", "solo") in ("gevent", "eventlet")
        and int(getenv("CONCURRENCY", 1)) > 1
    )


if is_multi_threaded():
    # Multi-(green)threaded workers can't use scoped_session()
    # Downside of a single session is that if postgres is (oom)killed and a transaction
    # fails to rollback you have to restart the workers so that they pick another session.
    singleton_session = Session()
    logger.debug("Going to use a single SQLAlchemy session.")
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


def get_submitted_time_from_model(
    model: Union["CoprBuildTargetModel", "TFTTestRunTargetModel"]
) -> datetime:
    # TODO: unify `submitted_name` (or better -> create for both models `task_accepted_time`)
    # to delete this mess plz
    if isinstance(model, CoprBuildTargetModel):
        return model.build_submitted_time

    return model.submitted_time


@overload
def get_most_recent_targets(
    models: Iterable["CoprBuildTargetModel"],
) -> List["CoprBuildTargetModel"]:
    """Overload for type-checking"""


@overload
def get_most_recent_targets(
    models: Iterable["TFTTestRunTargetModel"],
) -> List["TFTTestRunTargetModel"]:
    """Overload for type-checking"""


def get_most_recent_targets(
    models: Union[
        Iterable["CoprBuildTargetModel"],
        Iterable["TFTTestRunTargetModel"],
    ],
) -> Union[List["CoprBuildTargetModel"], List["TFTTestRunTargetModel"]]:
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
            most_recent_models.get(model.target) is None
            or get_submitted_time_from_model(most_recent_models[model.target])
            < submitted_time_of_current_model
        ):
            most_recent_models[model.target] = model

    return list(most_recent_models.values())


@overload
def filter_most_recent_target_models_by_status(
    models: Iterable["CoprBuildTargetModel"],
    statuses_to_filter_with: List[str],
) -> Set["CoprBuildTargetModel"]:
    """Overload for type-checking"""


@overload
def filter_most_recent_target_models_by_status(
    models: Iterable["TFTTestRunTargetModel"],
    statuses_to_filter_with: List[str],
) -> Set["TFTTestRunTargetModel"]:
    """Overload for type-checking"""


def filter_most_recent_target_models_by_status(
    models: Union[
        Iterable["CoprBuildTargetModel"],
        Iterable["TFTTestRunTargetModel"],
    ],
    statuses_to_filter_with: List[str],
) -> Union[Set["CoprBuildTargetModel"], Set["TFTTestRunTargetModel"]]:
    logger.info(
        f"Trying to filter targets with possible status: {statuses_to_filter_with} in {models}"
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
    statuses_to_filter_with: List[str],
) -> Optional[Set[str]]:
    filtered_models = filter_most_recent_target_models_by_status(
        models, statuses_to_filter_with
    )
    return {model.target for model in filtered_models} if filtered_models else None


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
        try:
            trigger = (
                sa_session()
                .query(JobTriggerModel)
                .filter_by(type=self.job_trigger_model_type, trigger_id=self.id)
                .one_or_none()
            )
        except MultipleResultsFound as e:
            msg = f"Multiple run models for type {self.job_trigger_model_type} and id {self.id}."
            logger.error(msg)
            raise PackitException(msg) from e
        return trigger.runs if trigger else []

    def _get_run_item(
        self, model_type: Type["AbstractBuildTestDbType"]
    ) -> List["AbstractBuildTestDbType"]:
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


class ProjectAndTriggersConnector:
    """
    Abstract class that is inherited by build/test group models
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


class GroupAndTargetModelConnector:
    """
    Abstract class that is inherited by build/test models
    to share methods for accessing project and trigger models.
    """

    group_of_targets: ProjectAndTriggersConnector

    def get_job_trigger_model(self) -> Optional["JobTriggerModel"]:
        return self.group_of_targets.get_job_trigger_model()

    def get_trigger_object(self) -> Optional["AbstractTriggerDbType"]:
        return self.group_of_targets.get_trigger_object()

    def get_project(self) -> Optional["GitProjectModel"]:
        return self.group_of_targets.get_project()

    def get_pr_id(self) -> Optional[int]:
        return self.group_of_targets.get_pr_id()

    def get_issue_id(self) -> Optional[int]:
        return self.group_of_targets.get_issue_id()

    def get_branch_name(self) -> Optional[str]:
        return self.group_of_targets.get_branch_name()

    def get_release_tag(self) -> Optional[str]:
        return self.group_of_targets.get_release_tag()


class GroupModel:
    """An abstract class that all models grouping targets should inherit from."""

    @property
    def grouped_targets(self):
        """Returns the list of grouped targets."""
        raise NotImplementedError


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
    project_authentication_issue = relationship(
        "ProjectAuthenticationIssueModel", back_populates="project"
    )

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
    def get_by_id(cls, id_: int) -> Optional["GitProjectModel"]:
        return sa_session().query(GitProjectModel).filter_by(id=id_).first()

    @classmethod
    def get_range(cls, first: int, last: int) -> Iterable["GitProjectModel"]:
        return (
            sa_session()
            .query(GitProjectModel)
            .order_by(GitProjectModel.namespace)
            .slice(first, last)
        )

    @classmethod
    def get_by_forge(
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
    def get_by_forge_namespace(
        cls, forge: str, namespace: str
    ) -> Iterable["GitProjectModel"]:
        """Return projects of given forge and namespace"""
        return (
            sa_session()
            .query(GitProjectModel)
            .filter_by(instance_url=forge, namespace=namespace)
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
        cls, forge: str, namespace: str, repo_name: str
    ) -> Iterable["IssueModel"]:
        return (
            sa_session()
            .query(IssueModel)
            .join(IssueModel.project)
            .filter(
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
            .join(GitBranchModel.project)
            .filter(
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
            .join(ProjectReleaseModel.project)
            .filter(
                GitProjectModel.instance_url == forge,
                GitProjectModel.namespace == namespace,
                GitProjectModel.repo_name == repo_name,
            )
        )

    # ACTIVE PROJECTS

    @classmethod
    @ttl_cache(maxsize=_CACHE_MAXSIZE, ttl=_CACHE_TTL)
    def get_active_projects(
        cls, top: Optional[int] = None, datetime_from=None, datetime_to=None
    ) -> list[str]:
        """
        Active project is the one with at least one activity (=one pipeline)
        during the given period.
        """
        return list(
            cls.get_active_projects_usage_numbers(
                top=top, datetime_from=datetime_from, datetime_to=datetime_to
            ).keys()
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
                top=None, datetime_from=datetime_from, datetime_to=datetime_to
            )
        )

    @classmethod
    @ttl_cache(maxsize=_CACHE_MAXSIZE, ttl=_CACHE_TTL)
    def get_active_projects_usage_numbers(
        cls, top: Optional[int] = 10, datetime_from=None, datetime_to=None
    ) -> dict[str, int]:
        """
        Get the most active projects sorted by the number of related pipelines.
        """
        all_usage_numbers: dict[str, int] = Counter()
        for trigger_type in JobTriggerModelType:
            all_usage_numbers.update(
                cls.get_trigger_usage_numbers(
                    datetime_from=datetime_from,
                    datetime_to=datetime_to,
                    top=None,
                    trigger_type=trigger_type,
                )
            )
        return dict(
            sorted(all_usage_numbers.items(), key=lambda x: x[1], reverse=True)[:top]
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
        return sa_session().query(GitProjectModel).count()

    @classmethod
    @ttl_cache(maxsize=_CACHE_MAXSIZE, ttl=_CACHE_TTL)
    def get_instance_numbers(cls) -> Dict[str, int]:
        """
        Get the number of projects per each GIT instances.
        """
        return dict(
            sa_session()
            .query(
                GitProjectModel.instance_url,
                func.count(GitProjectModel.instance_url),
            )
            .group_by(GitProjectModel.instance_url)
            .all()
        )

    @classmethod
    @ttl_cache(maxsize=_CACHE_MAXSIZE, ttl=_CACHE_TTL)
    def get_instance_numbers_for_active_projects(
        cls, datetime_from=None, datetime_to=None
    ) -> Dict[str, int]:
        """
        Get the number of projects (at least one pipeline during the time period)
        per each GIT instances.
        """
        projects_per_instance: dict[str, set[str]] = {}

        for trigger_type in JobTriggerModelType:
            trigger_model = MODEL_FOR_TRIGGER[trigger_type]
            query = (
                sa_session()
                .query(
                    GitProjectModel.instance_url,
                    GitProjectModel.project_url,
                )
                .join(trigger_model, GitProjectModel.id == trigger_model.project_id)
                .join(JobTriggerModel, JobTriggerModel.trigger_id == trigger_model.id)
                .join(PipelineModel, PipelineModel.job_trigger_id == JobTriggerModel.id)
                .filter(JobTriggerModel.type == trigger_type)
            )
            if datetime_from:
                query = query.filter(PipelineModel.datetime >= datetime_from)
            if datetime_to:
                query = query.filter(PipelineModel.datetime <= datetime_to)

            query = query.group_by(
                GitProjectModel.project_url, GitProjectModel.instance_url
            )
            for instance, project in query.all():
                projects_per_instance.setdefault(instance, set())
                projects_per_instance[instance].add(project)

        return {
            instance: len(projects)
            for instance, projects in projects_per_instance.items()
        }

    @classmethod
    @ttl_cache(maxsize=_CACHE_MAXSIZE, ttl=_CACHE_TTL)
    def get_trigger_usage_count(
        cls, trigger_type: JobTriggerModelType, datetime_from=None, datetime_to=None
    ):
        """
        Get the number of triggers of a given type with at least one pipeline from the given period.
        """
        # TODO: share the computation with _get_trigger_usage_numbers
        #       (one query with top and one without)
        return sum(
            cls.get_trigger_usage_numbers(
                datetime_from=datetime_from,
                datetime_to=datetime_to,
                trigger_type=trigger_type,
                top=None,
            ).values()
        )

    @classmethod
    @ttl_cache(maxsize=_CACHE_MAXSIZE, ttl=_CACHE_TTL)
    def get_trigger_usage_numbers(
        cls, trigger_type, datetime_from=None, datetime_to=None, top=None
    ) -> dict[str, int]:
        """
        For each project, get the number of triggers of a given type with at least one pipeline
        from the given period.

        Order from the highest numbers.
        All if `top` not set, the first `top` projects returned otherwise.
        """
        trigger_model = MODEL_FOR_TRIGGER[trigger_type]
        query = (
            sa_session()
            .query(
                GitProjectModel.project_url,
                count(trigger_model.id).over(partition_by=GitProjectModel.project_url),
            )
            .join(trigger_model, GitProjectModel.id == trigger_model.project_id)
            .join(JobTriggerModel, JobTriggerModel.trigger_id == trigger_model.id)
            .join(PipelineModel, PipelineModel.job_trigger_id == JobTriggerModel.id)
            .filter(JobTriggerModel.type == trigger_type)
            .filter(GitProjectModel.instance_url != "src.fedoraproject.org")
        )
        if datetime_from:
            query = query.filter(PipelineModel.datetime >= datetime_from)
        if datetime_to:
            query = query.filter(PipelineModel.datetime <= datetime_to)

        query = (
            query.group_by(GitProjectModel.project_url, trigger_model.id)
            .distinct()
            .order_by(
                desc(
                    count(trigger_model.id).over(
                        partition_by=GitProjectModel.project_url
                    )
                )
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
        trigger_type,
        datetime_from=None,
        datetime_to=None,
    ) -> int:
        """
        Get the number of jobs of a given type with at least one pipeline
        from the given period and given trigger.
        """
        return sum(
            cls.get_job_usage_numbers(
                datetime_from=datetime_from,
                datetime_to=datetime_to,
                job_result_model=job_result_model,
                top=None,
                trigger_type=trigger_type,
            ).values()
        )

    @classmethod
    @ttl_cache(maxsize=_CACHE_MAXSIZE, ttl=_CACHE_TTL)
    def get_job_usage_numbers_count_all_triggers(
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
            cls.get_job_usage_numbers_all_triggers(
                datetime_from=datetime_from,
                datetime_to=datetime_to,
                job_result_model=job_result_model,
                top=None,
            ).values()
        )

    @classmethod
    @ttl_cache(maxsize=_CACHE_MAXSIZE, ttl=_CACHE_TTL)
    def get_job_usage_numbers(
        cls,
        job_result_model,
        trigger_type,
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
        trigger_model = MODEL_FOR_TRIGGER[trigger_type]
        pipeline_attribute = {
            SRPMBuildModel: PipelineModel.srpm_build_id,
            CoprBuildGroupModel: PipelineModel.copr_build_group_id,
            KojiBuildGroupModel: PipelineModel.koji_build_group_id,
            VMImageBuildTargetModel: PipelineModel.vm_image_build_id,
            TFTTestRunGroupModel: PipelineModel.test_run_group_id,
            SyncReleaseModel: PipelineModel.sync_release_run_id,
        }[job_result_model]

        query = (
            sa_session()
            .query(
                GitProjectModel.project_url,
                count(job_result_model.id).over(
                    partition_by=GitProjectModel.project_url
                ),
            )
            .join(trigger_model, GitProjectModel.id == trigger_model.project_id)
            .join(JobTriggerModel, JobTriggerModel.trigger_id == trigger_model.id)
            .join(PipelineModel, PipelineModel.job_trigger_id == JobTriggerModel.id)
            .join(job_result_model, job_result_model.id == pipeline_attribute)
            .filter(JobTriggerModel.type == trigger_type)
            # We have all the dist git projects in because of how we parse the events.
            .filter(GitProjectModel.instance_url != "src.fedoraproject.org")
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
                        partition_by=GitProjectModel.project_url
                    )
                )
            )
            .limit(top)
            .all()
        )

    @classmethod
    @ttl_cache(maxsize=_CACHE_MAXSIZE, ttl=_CACHE_TTL)
    def get_job_usage_numbers_all_triggers(
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
        for trigger_type in JobTriggerModelType:
            all_usage_numbers.update(
                cls.get_job_usage_numbers(
                    datetime_from=datetime_from,
                    datetime_to=datetime_to,
                    top=None,
                    job_result_model=job_result_model,
                    trigger_type=trigger_type,
                )
            )
        return dict(
            sorted(all_usage_numbers.items(), key=lambda x: x[1], reverse=True)[:top]
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
    project_id = Column(Integer, ForeignKey("git_projects.id"), index=True)
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
    def get(
        cls, pr_id: int, namespace: str, repo_name: str, project_url: str
    ) -> Optional["PullRequestModel"]:
        with sa_session_transaction() as session:
            project = GitProjectModel.get_or_create(
                namespace=namespace, repo_name=repo_name, project_url=project_url
            )
            return (
                session.query(PullRequestModel)
                .filter_by(pr_id=pr_id, project_id=project.id)
                .first()
            )

    @classmethod
    def get_by_id(cls, id_: int) -> Optional["PullRequestModel"]:
        return sa_session().query(PullRequestModel).filter_by(id=id_).first()

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
    project_id = Column(Integer, ForeignKey("git_projects.id"), index=True)
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
    project_id = Column(Integer, ForeignKey("git_projects.id"), index=True)
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
    trigger_id = Column(Integer, index=True)

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
    and TFTTestRunGroupModel.

    * One model of each build/test target/group model can be connected.
    * Each build/test model can be connected to multiple PipelineModels (e.g. on retrigger).
    * Each PipelineModel has to be connected to exactly one JobTriggerModel.
    * There can be multiple PipelineModels for one JobTriggerModel.
      (e.g. For each push to PR, there will be new PipelineModel, but same JobTriggerModel.)
    """

    __tablename__ = "pipelines"
    id = Column(Integer, primary_key=True)  # our database PK
    # datetime.utcnow instead of datetime.utcnow() because it's an argument to the function,
    # so it will run when the model is initiated, not when the table is made
    datetime = Column(DateTime, default=datetime.utcnow)

    job_trigger_id = Column(Integer, ForeignKey("job_triggers.id"))
    job_trigger = relationship("JobTriggerModel", back_populates="runs")

    srpm_build_id = Column(Integer, ForeignKey("srpm_builds.id"), index=True)
    srpm_build = relationship("SRPMBuildModel", back_populates="runs")
    copr_build_group_id = Column(
        Integer, ForeignKey("copr_build_groups.id"), index=True
    )
    copr_build_group = relationship("CoprBuildGroupModel", back_populates="runs")
    koji_build_group_id = Column(
        Integer, ForeignKey("koji_build_groups.id"), index=True
    )
    koji_build_group = relationship("KojiBuildGroupModel", back_populates="runs")
    vm_image_build_id = Column(
        Integer, ForeignKey("vm_image_build_targets.id"), index=True
    )
    vm_image_build = relationship("VMImageBuildTargetModel", back_populates="runs")
    test_run_group_id = Column(
        Integer, ForeignKey("tft_test_run_groups.id"), index=True
    )
    test_run_group = relationship("TFTTestRunGroupModel", back_populates="runs")
    sync_release_run_id = Column(
        Integer, ForeignKey("sync_release_runs.id"), index=True
    )
    sync_release_run = relationship("SyncReleaseModel", back_populates="runs")

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
            func.array_agg(psql_array([PipelineModel.copr_build_group_id])).label(
                "copr_build_group_id"
            ),
            func.array_agg(psql_array([PipelineModel.koji_build_group_id])).label(
                "koji_build_group_id"
            ),
            func.array_agg(psql_array([PipelineModel.test_run_group_id])).label(
                "test_run_group_id"
            ),
            func.array_agg(psql_array([PipelineModel.sync_release_run_id])).label(
                "sync_release_run_id",
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


class CoprBuildGroupModel(ProjectAndTriggersConnector, GroupModel, Base):
    __tablename__ = "copr_build_groups"
    id = Column(Integer, primary_key=True)
    submitted_time = Column(DateTime, default=datetime.utcnow)

    runs = relationship("PipelineModel", back_populates="copr_build_group")
    copr_build_targets = relationship(
        "CoprBuildTargetModel", back_populates="group_of_targets"
    )

    def __repr__(self) -> str:
        return (
            f"CoprBuildGroupModel(id={self.id}, submitted_time={self.submitted_time})"
        )

    @property
    def grouped_targets(self) -> List["CoprBuildTargetModel"]:
        return self.copr_build_targets

    @classmethod
    def create(cls, run_model: "PipelineModel") -> "CoprBuildGroupModel":
        with sa_session_transaction() as session:
            build_group = cls()
            session.add(build_group)
            if run_model.copr_build_group:
                # Clone run model
                new_run_model = PipelineModel.create(
                    type=run_model.job_trigger.type,
                    trigger_id=run_model.job_trigger.trigger_id,
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
        return sa_session().query(CoprBuildGroupModel).filter_by(id=group_id).first()


class BuildStatus(str, enum.Enum):
    """An enum of all possible build statuses"""

    success = "success"
    pending = "pending"
    failure = "failure"
    error = "error"
    waiting_for_srpm = "waiting_for_srpm"
    retry = "retry"


class CoprBuildTargetModel(GroupAndTargetModelConnector, Base):
    """
    Representation of Copr build for one target.
    """

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
        "CoprBuildGroupModel", back_populates="copr_build_targets"
    )

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

    def set_status(self, status: BuildStatus):
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

    def set_build_id(self, build_id: str):
        with sa_session_transaction() as session:
            self.build_id = build_id
            session.add(self)

    def get_srpm_build(self) -> Optional["SRPMBuildModel"]:
        # All SRPMBuild models for all the runs have to be same.
        return (
            self.group_of_targets.runs[0].srpm_build
            if self.group_of_targets.runs
            else None
        )

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
                func.json_agg(psql_array([CoprBuildTargetModel.status])).label(
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
    def get_all_by_status(cls, status: BuildStatus) -> Iterable["CoprBuildTargetModel"]:
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
        build_id: Optional[str],
        commit_sha: str,
        project_name: str,
        owner: str,
        web_url: Optional[str],
        target: str,
        status: BuildStatus,
        copr_build_group: "CoprBuildGroupModel",
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
            f"CoprBuildTargetModel(id={self.id}, "
            f"build_submitted_time={self.build_submitted_time})"
        )


class KojiBuildGroupModel(ProjectAndTriggersConnector, GroupModel, Base):
    __tablename__ = "koji_build_groups"
    id = Column(Integer, primary_key=True)
    submitted_time = Column(DateTime, default=datetime.utcnow)

    runs = relationship("PipelineModel", back_populates="koji_build_group")
    koji_build_targets = relationship(
        "KojiBuildTargetModel", back_populates="group_of_targets"
    )

    @property
    def grouped_targets(self):
        return self.koji_build_targets

    def __repr__(self) -> str:
        return (
            f"KojiBuildGroupModel(id={self.id}, submitted_time={self.submitted_time})"
        )

    @classmethod
    def get_by_id(cls, id_: int) -> Optional["KojiBuildGroupModel"]:
        return sa_session().query(KojiBuildGroupModel).filter_by(id=id_).first()

    @classmethod
    def create(cls, run_model: "PipelineModel") -> "KojiBuildGroupModel":
        with sa_session_transaction() as session:
            build_group = cls()
            session.add(build_group)
            if run_model.koji_build_group:
                # Clone run model
                new_run_model = PipelineModel.create(
                    type=run_model.job_trigger.type,
                    trigger_id=run_model.job_trigger.trigger_id,
                )
                new_run_model.srpm_build = run_model.srpm_build
                new_run_model.koji_build_group = build_group
                session.add(new_run_model)
            else:
                run_model.koji_build_group = build_group
                session.add(run_model)
            return build_group


class KojiBuildTargetModel(GroupAndTargetModelConnector, Base):
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
    koji_build_group_id = Column(Integer, ForeignKey("koji_build_groups.id"))

    group_of_targets = relationship(
        "KojiBuildGroupModel", back_populates="koji_build_targets"
    )

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

    def set_build_id(self, build_id: str):
        with sa_session_transaction() as session:
            self.build_id = build_id
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
        return (
            self.group_of_targets.runs[0].srpm_build
            if self.group_of_targets.runs
            else None
        )

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
        build_id: Optional[str],
        commit_sha: str,
        web_url: Optional[str],
        target: str,
        status: str,
        scratch: bool,
        koji_build_group: "KojiBuildGroupModel",
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

            koji_build_group.koji_build_targets.append(build)
            session.add(koji_build_group)

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
            srpm_build.status = BuildStatus.pending
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
    def get_range(cls, first: int, last: int) -> Iterable["SRPMBuildModel"]:
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

    def set_copr_build_id(self, copr_build_id: str) -> None:
        with sa_session_transaction() as session:
            self.copr_build_id = copr_build_id
            session.add(self)

    def set_copr_web_url(self, copr_web_url: str) -> None:
        with sa_session_transaction() as session:
            self.copr_web_url = copr_web_url
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

    def set_status(self, status: BuildStatus) -> None:
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


tf_copr_association_table = Table(
    "tf_copr_build_association_table",
    # TODO: sqlalchemy-stubs should now support declarative_base but there are too many
    #       typing fixes necessary to do it now
    Base.metadata,  # type: ignore
    Column("copr_id", ForeignKey("copr_build_targets.id"), primary_key=True),
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


class TFTTestRunGroupModel(ProjectAndTriggersConnector, GroupModel, Base):
    __tablename__ = "tft_test_run_groups"
    id = Column(Integer, primary_key=True)
    submitted_time = Column(DateTime, default=datetime.utcnow)

    runs = relationship("PipelineModel", back_populates="test_run_group")
    tft_test_run_targets = relationship(
        "TFTTestRunTargetModel", back_populates="group_of_targets"
    )

    def __repr__(self) -> str:
        return (
            f"TFTTestRunGroupModel(id={self.id}, submitted_time={self.submitted_time})"
        )

    @classmethod
    def create(cls, run_models: List["PipelineModel"]) -> "TFTTestRunGroupModel":
        with sa_session_transaction() as session:
            test_run_group = cls()
            session.add(test_run_group)

            for run_model in run_models:
                if run_model.test_run_group:
                    # Clone run model
                    new_run_model = PipelineModel.create(
                        type=run_model.job_trigger.type,
                        trigger_id=run_model.job_trigger.trigger_id,
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
    def grouped_targets(self) -> List["TFTTestRunTargetModel"]:
        return self.tft_test_run_targets

    @classmethod
    def get_by_id(cls, group_id: int) -> Optional["TFTTestRunGroupModel"]:
        return sa_session().query(TFTTestRunGroupModel).filter_by(id=group_id).first()


class TFTTestRunTargetModel(GroupAndTargetModelConnector, Base):
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
        "TFTTestRunGroupModel", back_populates="tft_test_run_targets"
    )

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

    def set_pipeline_id(self, pipeline_id: str) -> None:
        with sa_session_transaction() as session:
            self.pipeline_id = pipeline_id
            session.add(self)

    def add_copr_build(self, build: "CoprBuildTargetModel"):
        with sa_session_transaction() as session:
            self.copr_builds.append(build)
            session.add(self)

    @classmethod
    def create(
        cls,
        pipeline_id: Optional[str],
        commit_sha: str,
        status: TestingFarmResult,
        target: str,
        test_run_group: "TFTTestRunGroupModel",
        web_url: Optional[str] = None,
        data: dict = None,
        identifier: Optional[str] = None,
        copr_build_targets: Optional[List[CoprBuildTargetModel]] = None,
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
            if copr_build_targets:
                test_run.copr_builds.extend(copr_build_targets)
            session.add(test_run)
            test_run_group.tft_test_run_targets.append(test_run)
            session.add(test_run_group)

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


class SyncReleaseTargetStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    error = "error"
    retry = "retry"
    submitted = "submitted"


class SyncReleaseTargetModel(ProjectAndTriggersConnector, Base):
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
        "SyncReleaseModel", back_populates="sync_release_targets"
    )

    def __repr__(self) -> str:
        return f"SyncReleaseTargetModel(id={self.id})"

    @classmethod
    def create(
        cls, status: SyncReleaseTargetStatus, branch: str
    ) -> "SyncReleaseTargetModel":
        with sa_session_transaction() as session:
            sync_release_target = cls()
            sync_release_target.status = status
            sync_release_target.branch = branch
            session.add(sync_release_target)
            return sync_release_target

    def set_status(self, status: SyncReleaseTargetStatus) -> None:
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
    def get_by_id(cls, id_: int) -> Optional["SyncReleaseTargetModel"]:
        return sa_session().query(SyncReleaseTargetModel).filter_by(id=id_).first()


class SyncReleaseStatus(str, enum.Enum):
    running = "running"
    finished = "finished"
    error = "error"


class SyncReleaseJobType(str, enum.Enum):
    pull_from_upstream = "pull_from_upstream"
    propose_downstream = "propose_downstream"


class SyncReleaseModel(ProjectAndTriggersConnector, Base):
    __tablename__ = "sync_release_runs"
    id = Column(Integer, primary_key=True)
    status = Column(Enum(SyncReleaseStatus))
    submitted_time = Column(DateTime, default=datetime.utcnow)
    job_type = Column(
        Enum(SyncReleaseJobType), default=SyncReleaseJobType.propose_downstream
    )

    runs = relationship("PipelineModel", back_populates="sync_release_run")
    sync_release_targets = relationship(
        "SyncReleaseTargetModel", back_populates="sync_release"
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
        trigger_model: AbstractTriggerDbType,
        job_type: SyncReleaseJobType,
    ) -> Tuple["SyncReleaseModel", "PipelineModel"]:
        """
        Create a new model for SyncRelease and connect it to the PipelineModel.

        * New SyncReleaseModel model will have connection to a new PipelineModel.
        * The newly created PipelineModel can reuse existing JobTriggerModel
          (e.g.: one IssueModel can have multiple runs).

        More specifically:
        * On `/packit propose-downstream` issue comment:
          -> SyncReleaseModel is created.
          -> New PipelineModel is created.
          -> JobTriggerModel is created.
        * Something went wrong, after correction and another `/packit propose-downstream` comment:
          -> SyncReleaseModel is created.
          -> PipelineModel is created.
          -> JobTriggerModel is reused.
        * TODO: we will use propose-downstream in commit-checks - fill in once it's implemented
        """
        with sa_session_transaction() as session:
            sync_release = cls()
            sync_release.status = status
            sync_release.job_type = job_type
            session.add(sync_release)

            # Create a pipeline, reuse trigger_model if it exists:
            pipeline = PipelineModel.create(
                type=trigger_model.job_trigger_model_type, trigger_id=trigger_model.id
            )
            pipeline.sync_release_run = sync_release
            session.add(pipeline)

            return sync_release, pipeline

    def set_status(self, status: SyncReleaseStatus) -> None:
        with sa_session_transaction() as session:
            self.status = status
            session.add(self)

    @classmethod
    def get_by_id(cls, id_: int) -> Optional["SyncReleaseModel"]:
        return sa_session().query(SyncReleaseModel).filter_by(id=id_).first()

    @classmethod
    def get_all_by_status(cls, status: str) -> Iterable["SyncReleaseModel"]:
        return sa_session().query(SyncReleaseModel).filter_by(status=status)

    @classmethod
    def get_range_propose_downstream(
        cls, first: int, last: int
    ) -> Iterable["SyncReleaseModel"]:
        return (
            sa_session()
            .query(SyncReleaseModel)
            .order_by(desc(SyncReleaseModel.id))
            .filter_by(job_type=SyncReleaseJobType.propose_downstream)
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
        "GitProjectModel", back_populates="project_authentication_issue"
    )
    # Check to know if we created an issue for the repo.
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


class VMImageBuildStatus(str, enum.Enum):
    """An enum of all possible build statuses"""

    success = "success"
    pending = "pending"
    building = "building"
    uploading = "uploading"
    registering = "registering"
    failure = "failure"
    error = "error"


class VMImageBuildTargetModel(ProjectAndTriggersConnector, Base):
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
    # commit sha of the PR (or a branch, release) we used for a build
    commit_sha = Column(String, index=True)
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
        with sa_session_transaction() as session:
            self.build_start_time = start_time
            session.add(self)

    def set_end_time(self, end_time: datetime):
        with sa_session_transaction() as session:
            self.build_finished_time = end_time
            session.add(self)

    def set_status(self, status: VMImageBuildStatus):
        with sa_session_transaction() as session:
            self.status = status
            session.add(self)

    def set_build_logs_url(self, build_logs: str):
        with sa_session_transaction() as session:
            self.build_logs_url = build_logs
            session.add(self)

    @classmethod
    def get_by_id(cls, id_: int) -> Optional["VMImageBuildTargetModel"]:
        return sa_session().query(VMImageBuildTargetModel).filter_by(id=id_).first()

    @classmethod
    def get_all(cls) -> Iterable["VMImageBuildTargetModel"]:
        return (
            sa_session()
            .query(VMImageBuildTargetModel)
            .order_by(desc(VMImageBuildTargetModel.id))
        )

    @classmethod
    def get_all_by_build_id(
        cls, build_id: Union[str, int]
    ) -> Iterable["VMImageBuildTargetModel"]:
        """Returns all builds with that build_id, irrespective of target"""
        if isinstance(build_id, int):
            # See the comment in get_by_build_id()
            build_id = str(build_id)
        return sa_session().query(VMImageBuildTargetModel).filter_by(build_id=build_id)

    @classmethod
    def get_all_by_status(
        cls, status: VMImageBuildStatus
    ) -> Iterable["VMImageBuildTargetModel"]:
        """Returns all builds which currently have the given status."""
        return sa_session().query(VMImageBuildTargetModel).filter_by(status=status)

    @classmethod
    def get_by_build_id(
        cls, build_id: Union[str, int], target: str = None
    ) -> Optional["VMImageBuildTargetModel"]:
        """Returns the build matching the build_id and the target"""

        if isinstance(build_id, int):
            # PG is pesky about this:
            #   LINE 3: WHERE copr_builds.build_id = 1245767 AND copr_builds.target ...
            #   HINT:  No operator matches the given name and argument type(s).
            #   You might need to add explicit type casts.
            build_id = str(build_id)
        query = sa_session().query(VMImageBuildTargetModel).filter_by(build_id=build_id)
        if target:
            query = query.filter_by(target=target)
        return query.first()

    @staticmethod
    def get_all_by(
        project_name: str,
        commit_sha: str,
        owner: str = None,
        target: str = None,
    ) -> Iterable["VMImageBuildTargetModel"]:
        """All owner/project_name builds sorted from latest to oldest
        with the given commit_sha and optional target.
        """
        non_none_args = {
            arg: value for arg, value in locals().items() if value is not None
        }

        return (
            sa_session()
            .query(VMImageBuildTargetModel)
            .filter_by(**non_none_args)
            .order_by(VMImageBuildTargetModel.build_id.desc())
        )

    @classmethod
    def get_all_by_commit(cls, commit_sha: str) -> Iterable["VMImageBuildTargetModel"]:
        """Returns all builds that match a given commit sha"""
        return (
            sa_session().query(VMImageBuildTargetModel).filter_by(commit_sha=commit_sha)
        )

    @classmethod
    def create(
        cls,
        build_id: str,
        commit_sha: str,
        project_name: str,
        owner: str,
        project_url: str,
        target: str,
        status: VMImageBuildStatus,
        run_model: "PipelineModel",
        task_accepted_time: Optional[datetime] = None,
    ) -> "VMImageBuildTargetModel":
        with sa_session_transaction() as session:
            build = cls()
            build.build_id = build_id
            build.status = status
            build.project_name = project_name
            build.owner = owner
            build.commit_sha = commit_sha
            build.project_url = project_url
            build.target = target
            build.task_accepted_time = task_accepted_time
            session.add(build)

            if run_model.vm_image_build:
                # Clone run model
                new_run_model = PipelineModel.create(
                    type=run_model.job_trigger.type,
                    trigger_id=run_model.job_trigger.trigger_id,
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
