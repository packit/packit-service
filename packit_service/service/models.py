"""
Data models for jobs, tasks, builds, etc. The models are mapped to redis.
"""
import copy
import os
from typing import List, Dict, Union, TYPE_CHECKING

from persistentdict.dict_in_redis import PersistentDict
from sqlalchemy import Column, Integer, String, ForeignKey, Text, JSON, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker, Session

from packit_service.service.events import InstallationEvent, WhitelistStatus

# if identifier is not set, we need to create primary key ourselves
# with this variable, we keep track on what's the last PK
LAST_PK = "last-pk"

# SQLAlchemy session, get it with `get_sa_session`
session_instance = None


class Model:
    """ Abstract representation of a single object passing through p-s """

    # a "table" to store collection of objects
    table_name: str
    # unique identifier
    identifier: Union[int, str] = None

    @classmethod
    def db(cls) -> PersistentDict:
        if not cls.table_name:
            raise RuntimeError("table_name is not set")
        return PersistentDict(hash_name=cls.table_name)

    def save(self):
        """ store the current state of the object inside redis """
        db = self.db()
        if not self.identifier:
            try:
                last_pk = db[LAST_PK]
            except KeyError:
                # we are going to insert first item
                last_pk = 0
            self.identifier = last_pk + 1
            db[LAST_PK] = self.identifier
        db[self.identifier] = self.serialize()

    def serialize(self):
        """ convert from python data structure into a json serializable dict for PersistentDict """
        data = self.__dict__
        cp = copy.deepcopy(data)
        # we don't need to store table_name & identifier (key)
        cp.pop("table_name", None)
        cp.pop("identifier", None)
        return cp

    def deserialize(self, inp: Dict):
        """
        reverse operation as serialize,
        i.e. fill attributes of this object with what PersistentDict returns"""
        # this is pretty nasty: we could possibly replace this with a serialization
        # library or an ORM framework
        if "_service_config" in inp:
            del inp["_service_config"]
        self.__dict__ = inp

    @classmethod
    def from_dict(cls, inp: Dict):
        """ create instance from dictionary"""
        instance = cls()
        instance.deserialize(inp)
        return instance

    @classmethod
    def all(cls):
        """ get a dict of keys:instances """
        return {k: cls.from_dict(v) for k, v in cls.db().items() if k != LAST_PK}

    def __str__(self):
        return f"{self.table_name} - {self.__dict__}"

    def __repr__(self):
        return f"Model({self.identifier}, {self.table_name}, {self.__dict__})"


class Installation(Model):
    """ GitHub app installation event """

    table_name = "github_installation"
    event_data: InstallationEvent

    @classmethod
    def create(cls, installation_id: int, event: InstallationEvent, save: bool = True):
        i = cls()
        i.identifier = installation_id
        i.event_data = event
        if save:
            i.save()
        return i

    def serialize(self):
        cp = super().serialize()
        cp["event_data"] = self.event_data.get_dict()
        if "_service_config" in cp["event_data"]:
            del cp["event_data"]["_service_config"]
        return cp

    def deserialize(self, inp: Dict):
        """ reverse operation as serialize """
        event_data = inp["event_data"]
        del event_data["trigger"]
        event_data["status"] = WhitelistStatus(event_data["status"])
        inp["event_data"] = InstallationEvent(**event_data)
        inp["identifier"] = event_data["installation_id"]
        super().deserialize(inp)


class Build(Model):
    """ An abstract build model """

    status: str
    build_id: int


class CoprBuild(Build):
    """ A build in COPR """

    table_name = "copr-builds"
    project: str
    owner: str
    chroots: List[str]

    def __str__(self):
        return f"[#{self.build_id}] {self.owner}/{self.project}"

    @classmethod
    def create(
        cls,
        project: str,
        owner: str,
        chroots: List[str],
        identifier: int = None,
        save: bool = True,
    ):
        b = cls()
        b.identifier = identifier  # generate new ID if None
        b.project = project
        b.owner = owner
        b.chroots = chroots
        if save:
            b.save()
        return b


def get_sa_session() -> Session:
    """ get SQLAlchemy session """
    global session_instance
    if session_instance is None:
        url = (
            f"postgres+psycopg2://{os.getenv('POSTGRESQL_USER')}"
            f":{os.getenv('POSTGRESQL_PASSWORD')}@postgres:5432/{os.getenv('POSTGRESQL_DATABASE')}"
        )
        engine = create_engine(url)
        Session = sessionmaker(bind=engine)
        session_instance = Session()
    return session_instance


# https://github.com/python/mypy/issues/2477#issuecomment-313984522 ^_^
if TYPE_CHECKING:
    Base = object
else:
    Base = declarative_base()


class GitHubProject(Base):
    __tablename__ = "github_projects"
    id = Column(Integer, primary_key=True)
    namespace = Column(String)
    repo_name = Column(String)


class PullRequest(Base):
    __tablename__ = "pull_requests"
    id = Column(Integer, primary_key=True)  # our database PK
    pr_id = Column(
        String, index=True
    )  # GitHub PR ID - let's not make this PK since we can't control it
    project_id = Column(Integer, ForeignKey("github_projects.id"))
    project = relationship("GitHubProject")


# Franta suggests to consider enums here: whoever takes this, research how
# enums are handled in postgres and sqlalch
JOB_TYPE_SRPM = "SRPM"
JOB_TYPE_COPR_RPM = "COPR-RPM"
JOB_TYPE_TFT = "TFT"


class JobRun(Base):
    """ a line in the commit status check """

    __tablename__ = "job_runs"
    id = Column(Integer, primary_key=True)
    pr_id = Column(Integer, ForeignKey("pull_requests.id"))
    pr = relationship("PullRequest")
    logs = Column(Text)
    commit_sha = Column(String)
    status = Column(String)
    job_type = Column(String)  # SRPM, COPR-RPM, TFT
    # metadata is reserved to sqlalch
    data = Column(JSON)
