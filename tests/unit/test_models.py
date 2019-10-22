from packit_service.service.events import InstallationEvent, WhitelistStatus
from packit_service.service.models import CoprBuild, Installation


def test_serialize_installs():
    ev = InstallationEvent(
        1, "hubert", 2, "https://", "yes", 123, 234, "konrad", WhitelistStatus.waiting
    )
    i = Installation.create(1, ev, save=False)
    s = i.serialize()
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
