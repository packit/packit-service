# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from flexmock import flexmock
from bugzilla import Bugzilla

from packit_service.worker.psbugzilla import Bugzilla as PSBugzilla


def test_bugzilla_create_bug():
    flexmock(Bugzilla).should_receive("logged_in").and_return(True)
    flexmock(Bugzilla).should_receive("build_createbug").and_return({})
    flexmock(Bugzilla).should_receive("createbug").and_return(
        flexmock(id=1, weburl="url")
    )
    bz_id, bz_url = PSBugzilla("", "").create_bug(
        "product", "version", "component", "summary"
    )
    assert bz_id == 1
    assert bz_url == "url"


def test_bugzilla_add_patch():
    flexmock(Bugzilla).should_receive("logged_in").and_return(True)
    flexmock(Bugzilla).should_receive("attachfile").and_return(123)
    assert PSBugzilla("", "").add_patch(666, b"") == 123
