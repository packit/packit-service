"""
Data models for jobs, tasks, builds, etc. The models are mapped to redis.
"""
import copy
from typing import List, Dict, Union

from persistentdict.dict_in_redis import PersistentDict

from packit_service.service.events import InstallationEvent, WhitelistStatus

# if identifier is not set, we need to create primary key ourselves
# with this variable, we keep track on what's the last PK
LAST_PK = "last-pk"


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
