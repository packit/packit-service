from datetime import datetime

from packit_service.service.events import InstallationEvent, WhitelistStatus
from packit_service.service.models import Task, CoprBuild, Installation


def test_serialize_task():
    t = Task.create("123", {1: 2, "a": "b"}, save=False)
    s = t.serialize()
    assert s["metadata"] == {1: 2, "a": "b"}
    assert s["identifier"] == "123"
    assert isinstance(s["date_created"], str)
    t.date_created = None
    nt = Task()
    nt.deserialize(s)
    assert nt.identifier == "123"
    assert nt.metadata == {1: 2, "a": "b"}
    assert isinstance(nt.date_created, datetime)


def test_serialize_installs():
    ev = InstallationEvent(
        1, "hubert", 2, "https://", "yes", 123, 234, "konrad", WhitelistStatus.waiting
    )
    i = Installation.create(1, ev, save=False)
    s = i.serialize()
    assert s["identifier"] == 1
    assert s["event_data"]

    i2 = Installation()
    i2.deserialize(s)

    assert i2.event_data
    assert i2.identifier == 1


def test_serialize_copr_build():
    b = CoprBuild.create("foo", "bar", ["a", "b"], save=False)
    s = b.serialize()
    assert s["project"] == "foo"
    assert s["owner"] == "bar"
    assert s["chroots"] == ["a", "b"]
    nb = CoprBuild()
    nb.deserialize(s)
    assert nb.project == "foo"
    assert nb.owner == "bar"
    assert nb.chroots == ["a", "b"]
