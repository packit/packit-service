# MIT License
#
# Copyright (c) 2018-2020 Red Hat, Inc.

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.


# Create multiple allowlist entries
import pytest

from packit_service.models import get_sa_session, AllowlistModel


@pytest.fixture()
def multiple_allowlist_entries():
    with get_sa_session() as session:
        session.query(AllowlistModel).delete()
        yield [
            AllowlistModel.add_namespace(
                namespace="Rayquaza", status="approved_manually"
            ),
            AllowlistModel.add_namespace(
                namespace="Deoxys", status="approved_manually"
            ),
            # Not a typo, account_name repeated intentionally to check behaviour
            AllowlistModel.add_namespace(namespace="Deoxys", status="waiting"),
            AllowlistModel.add_namespace(namespace="Solgaleo", status="waiting"),
            AllowlistModel.add_namespace(
                namespace="Zacian", status="approved_manually"
            ),
        ]


# Create new allowlist entry
@pytest.fixture()
def new_allowlist_entry():
    with get_sa_session() as session:
        session.query(AllowlistModel).delete()
        yield AllowlistModel.add_namespace(
            namespace="Rayquaza", status="approved_manually"
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
    a = AllowlistModel.get_namespaces_by_status("waiting")
    assert len(list(a)) == 2
    b = AllowlistModel.get_namespaces_by_status("approved_manually")
    assert len(list(b)) == 2


def test_remove_namespace(clean_before_and_after, multiple_allowlist_entries):
    assert AllowlistModel.get_namespace("Rayquaza").namespace == "Rayquaza"
    AllowlistModel.remove_namespace("Rayquaza")
    assert AllowlistModel.get_namespace("Rayquaza") is None
