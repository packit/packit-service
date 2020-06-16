# MIT License
#
# Copyright (c) 2020 Red Hat, Inc.
#
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
