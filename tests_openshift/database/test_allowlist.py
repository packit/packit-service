# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

# Create multiple allowlist entries
import pytest

from packit_service.models import AllowlistModel, sa_session_transaction


@pytest.fixture()
def multiple_allowlist_entries():
    with sa_session_transaction() as session:
        session.query(AllowlistModel).delete()
        yield [
            AllowlistModel.add_namespace(
                namespace="Rayquaza",
                status="approved_manually",
            ),
            AllowlistModel.add_namespace(
                namespace="Deoxys",
                status="approved_manually",
            ),
            # Not a typo, account_name repeated intentionally to check behaviour
            AllowlistModel.add_namespace(namespace="Deoxys", status="waiting"),
            AllowlistModel.add_namespace(namespace="Solgaleo", status="waiting"),
            AllowlistModel.add_namespace(
                namespace="Zacian",
                status="approved_manually",
            ),
        ]


# Create new allowlist entry
@pytest.fixture()
def new_allowlist_entry():
    with sa_session_transaction() as session:
        session.query(AllowlistModel).delete()
        yield AllowlistModel.add_namespace(
            namespace="Rayquaza",
            status="approved_manually",
        )


def test_add_namespace(clean_before_and_after, new_allowlist_entry):
    assert new_allowlist_entry.status == "approved_manually"
    assert new_allowlist_entry.namespace == "Rayquaza"


def test_get_namespace(clean_before_and_after, multiple_allowlist_entries):
    assert AllowlistModel.get_namespace("Rayquaza").status == "approved_manually"
    assert AllowlistModel.get_namespace("Rayquaza").namespace == "Rayquaza"
    assert AllowlistModel.get_namespace("Deoxys").status == "waiting"
    assert AllowlistModel.get_namespace("Deoxys").namespace == "Deoxys"
    assert AllowlistModel.get_namespace("Solgaleo").status == "waiting"
    assert AllowlistModel.get_namespace("Solgaleo").namespace == "Solgaleo"


def test_get_namespaces_by_status(clean_before_and_after, multiple_allowlist_entries):
    a = AllowlistModel.get_by_status("waiting")
    assert len(list(a)) == 2
    b = AllowlistModel.get_by_status("approved_manually")
    assert len(list(b)) == 2


def test_remove_namespace(clean_before_and_after, multiple_allowlist_entries):
    assert AllowlistModel.get_namespace("Rayquaza").namespace == "Rayquaza"
    AllowlistModel.remove_namespace("Rayquaza")
    assert AllowlistModel.get_namespace("Rayquaza") is None
