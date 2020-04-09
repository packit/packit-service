"""

Revision ID: c6250555a36c
Revises: d7c2f99cd14d
Create Date: 2020-04-06 09:34:50.929724

"""
import enum
from datetime import datetime, timezone
from json import loads
from os import getenv
from copr.v3 import CoprNoResultException, Client
from celery.backends.database import Task
from redis import Redis
from typing import TYPE_CHECKING, Union, List
from persistentdict.dict_in_redis import PersistentDict

from alembic import op
from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    ForeignKey,
    Enum,
    orm,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, relationship
from sqlalchemy.types import PickleType

from packit_service.service.events import InstallationEvent
from packit_service.constants import WHITELIST_CONSTANTS

# revision identifiers, used by Alembic.
revision = "c6250555a36c"
down_revision = "d7c2f99cd14d"
branch_labels = None
depends_on = None

if TYPE_CHECKING:
    Base = object
else:
    Base = declarative_base()


# Redis models
class RedisModel:
    table_name: str
    identifier: Union[int, str] = None

    @classmethod
    def db(cls) -> PersistentDict:
        if not cls.table_name:
            raise RuntimeError("table_name is not set")
        return PersistentDict(hash_name=cls.table_name)


class RedisInstallation(RedisModel):
    table_name = "github_installation"
    event_data: InstallationEvent


class RedisBuild(RedisModel):
    status: str
    build_id: int
    build_submitted_time: str = None
    build_start_time: str = None
    build_finished_time: str = None


class RedisCoprBuild(RedisBuild):
    table_name = "copr-builds"
    project: str
    owner: str
    chroots: List[str]


# Postgres models
class TaskResultUpgradeModel(Base):
    __tablename__ = "task_results"
    task_id = Column(String, primary_key=True)
    jobs = Column(PickleType)
    event = Column(PickleType)

    @classmethod
    def get_by_id(cls, session: Session, task_id: str):
        return session.query(TaskResultUpgradeModel).filter_by(task_id=task_id).first()

    @classmethod
    def add_task_result(cls, session: Session, task_id, task_result_dict):
        task_result = cls.get_by_id(session, task_id)
        if task_result is None:
            task_result = cls()
            task_result.task_id = task_id
        task_result.jobs = task_result_dict.get("jobs")
        task_result.event = task_result_dict.get("event")
        session.add(task_result)
        return task_result


class WhitelistStatus(str, enum.Enum):
    approved_automatically = WHITELIST_CONSTANTS["approved_automatically"]
    waiting = WHITELIST_CONSTANTS["waiting"]
    approved_manually = WHITELIST_CONSTANTS["approved_manually"]


class WhitelistUpgradeModel(Base):
    __tablename__ = "whitelist"
    id = Column(Integer, primary_key=True)
    account_name = Column(String, index=True)
    status = Column(Enum(WhitelistStatus))

    @classmethod
    def add_account(cls, session: Session, account_name: str, status: str):
        account = cls.get_account(session, account_name)
        if account:
            account.status = status
            session.add(account)
            return account
        account = cls()
        account.account_name = account_name
        account.status = status
        session.add(account)
        return account

    @classmethod
    def get_account(cls, session: Session, account_name: str):
        return (
            session.query(WhitelistUpgradeModel)
            .filter_by(account_name=account_name)
            .first()
        )


class InstallationUpgradeModel(Base):
    __tablename__ = "github_installations"
    id = Column(Integer, primary_key=True, autoincrement=True)
    account_login = Column(String)
    account_id = Column(Integer)
    account_url = Column(String)
    account_type = Column(String)
    sender_id = Column(Integer)
    sender_login = Column(String)
    created_at = Column(DateTime)

    @classmethod
    def get_by_account_login(cls, session: Session, account_login: str):
        return (
            session.query(InstallationUpgradeModel)
            .filter_by(account_login=account_login)
            .first()
        )

    @classmethod
    def create(
        cls,
        session: Session,
        account_login,
        account_id,
        account_type,
        account_url,
        sender_login,
        sender_id,
        created_at,
    ):
        installation = cls.get_by_account_login(session, account_login)
        if not installation:
            installation = cls()
            installation.account_login = account_login
            installation.account_id = account_id
            installation.account_url = account_url
            installation.account_type = account_type
            installation.sender_login = sender_login
            installation.sender_id = sender_id
            installation.created_at = created_at
            session.add(installation)
        return installation


class CoprBuildUpgradeModel(Base):
    __tablename__ = "copr_builds"
    id = Column(Integer, primary_key=True)
    build_id = Column(String, index=True)
    job_trigger_id = Column(Integer, ForeignKey("build_triggers.id"))
    job_trigger = relationship("JobTriggerUpgradeModel", back_populates="copr_builds")
    status = Column(String)
    target = Column(String)
    web_url = Column(String)

    build_submitted_time = Column(DateTime, default=datetime.utcnow)
    build_start_time = Column(DateTime)
    build_finished_time = Column(DateTime)

    project_name = Column(String)
    owner = Column(String)

    @classmethod
    def get_by_build_id(cls, session: Session, build_id: str, target: str):
        return (
            session.query(CoprBuildUpgradeModel)
            .filter_by(build_id=build_id, target=target)
            .first()
        )

    @classmethod
    def get_or_create(
        cls,
        session: Session,
        build_id: str,
        project_name: str,
        owner: str,
        web_url: str,
        target: str,
        status: str,
        job_trigger,
        build_submitted_time,
        build_start_time,
        build_finished_time,
    ):
        build = cls.get_by_build_id(session, build_id, target)
        if not build:
            build = cls()
            build.build_id = build_id
            build.job_trigger = job_trigger
            build.status = status
            build.project_name = project_name
            build.owner = owner
            build.web_url = web_url
            build.target = target
            build.build_submitted_time = build_submitted_time
            build.build_start_time = build_start_time
            build.build_finished_time = build_finished_time
            session.add(build)
        return build


class JobTriggerModelType(str, enum.Enum):
    pull_request = "pull_request"
    branch_push = "branch_push"
    release = "release"
    issue = "issue"


class JobTriggerUpgradeModel(Base):
    __tablename__ = "build_triggers"
    id = Column(Integer, primary_key=True)
    type = Column(Enum(JobTriggerModelType))
    trigger_id = Column(Integer)
    copr_builds = relationship("CoprBuildUpgradeModel", back_populates="job_trigger")

    @classmethod
    def get_or_create(
        cls, session: Session, type: JobTriggerModelType, trigger_id: int
    ) -> "JobTriggerUpgradeModel":
        trigger = (
            session.query(JobTriggerUpgradeModel)
            .filter_by(type=type, trigger_id=trigger_id)
            .first()
        )
        if not trigger:
            trigger = JobTriggerUpgradeModel()
            trigger.type = type
            trigger.trigger_id = trigger_id
            session.add(trigger)
        return trigger


def add_task_to_celery_table(session, task_id, status, result, traceback, date_done):
    task_result = session.query(Task).filter_by(task_id=task_id).first()
    if task_result is None:
        task_result = Task(task_id)
    task_result.status = status
    task_result.result = result
    task_result.traceback = traceback
    task_result.date_done = date_done
    session.add(task_result)


def upgrade():
    bind = op.get_bind()
    session = orm.Session(bind=bind)

    db = Redis(
        host=getenv("REDIS_SERVICE_HOST", "localhost"),
        port=int(getenv("REDIS_SERVICE_PORT", "6379")),
        db=0,
        decode_responses=True,
    )

    # tasks
    keys = db.keys("celery-task-meta-*")
    for key in keys:
        data = loads(db.get(key))
        task_id = data.get("task_id")
        status = data.get("status")
        result = data.get("result")
        traceback = data.get("traceback")
        date_done = data.get("data_done")
        if isinstance(date_done, str):
            date_done = datetime.fromisoformat(date_done)

        # our table
        TaskResultUpgradeModel.add_task_result(
            session=session, task_id=task_id, task_result_dict=result
        )
        # celery table
        add_task_to_celery_table(
            session=session,
            task_id=task_id,
            status=status,
            result=result,
            traceback=traceback,
            date_done=date_done,
        )

    # whitelist
    db = PersistentDict(hash_name="whitelist")
    for account, data in db.get_all().items():
        if not isinstance(data, dict):
            continue

        status = data.get("status")
        WhitelistUpgradeModel.add_account(
            session=session, account_name=account, status=status
        )

    # installations
    for event in RedisInstallation.db().get_all().values():
        if not isinstance(event, dict):
            continue

        event = event["event_data"]
        account_login = event.get("account_login")
        account_id = event.get("account_id")
        account_url = event.get("account_url")
        account_type = event.get("account_type")
        sender_id = event.get("sender_id")
        sender_login = event.get("sender_login")

        created_at = event.get("created_at")
        if isinstance(created_at, (int, float)):
            created_at = datetime.fromtimestamp(created_at, timezone.utc)
        elif isinstance(created_at, str):
            created_at = created_at.replace("Z", "+00:00")
            created_at = datetime.fromisoformat(created_at)

        InstallationUpgradeModel.create(
            session=session,
            account_login=account_login,
            account_id=account_id,
            account_type=account_type,
            account_url=account_url,
            sender_login=sender_login,
            sender_id=sender_id,
            created_at=created_at,
        )

    #  copr-builds
    for copr_build in RedisCoprBuild.db().get_all().values():
        if not isinstance(copr_build, dict):
            continue

        project_name = copr_build.get("project")
        owner = copr_build.get("owner")
        chroots = copr_build.get("chroots")
        build_submitted_time = (
            datetime.fromisoformat(copr_build.get("build_submitted_time"))
            if copr_build.get("build_submitted_time")
            else None
        )
        build_id = copr_build.get("build_id")

        if not build_id:
            continue

        status = copr_build.get("status")
        web_url = (
            f"https://copr.fedorainfracloud.org/coprs/{owner}/{project_name}/"
            f"build/{build_id}/"
        )

        try:
            project_name_list = project_name.split("-")
            if project_name_list[-1] == "stg":
                pr_id = int(project_name_list[-2])
            else:
                pr_id = int(project_name_list[-1])

            job_trigger = JobTriggerUpgradeModel.get_or_create(
                type=JobTriggerModelType.pull_request,
                trigger_id=pr_id,
                session=session,
            )
        except Exception:
            continue

        try:
            copr = Client.create_from_config_file()
            build = copr.build_proxy.get(build_id)
            build_submitted_time = datetime.fromtimestamp(build.submitted_on)
            build_start_time = datetime.fromtimestamp(build.started_on)
            build_finished_time = datetime.fromtimestamp(build.ended_on)

        except CoprNoResultException:
            build_submitted_time = build_submitted_time or datetime(2020, 1, 1, 0, 0, 0)
            build_start_time = datetime(2020, 1, 1, 0, 10, 0)
            build_finished_time = datetime(2020, 1, 1, 0, 20, 0)

        for chroot in chroots:
            CoprBuildUpgradeModel.get_or_create(
                session=session,
                build_id=str(build_id),
                project_name=project_name,
                owner=owner,
                target=chroot,
                status=status,
                job_trigger=job_trigger,
                web_url=web_url,
                build_submitted_time=build_submitted_time,
                build_start_time=build_start_time,
                build_finished_time=build_finished_time,
            )

    session.commit()


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    pass
    # ### end Alembic commands ###
