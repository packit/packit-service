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

from packit_service.service.views import _get_build_logs_for_build


def test_get_build_logs_for_build_copr_build_pr(
    clean_before_and_after, a_copr_build_for_pr
):
    response = _get_build_logs_for_build(a_copr_build_for_pr)
    assert "We can't find any info" not in response
    assert (
        response
        == "<html><head><title>Build the-namespace/the-repo-name: PR #1</title>"
        "</head><body>COPR Build ID: 123456<br>State: pending<br><br>"
        "Build web interface URL: "
        '<a href="https://copr.something.somewhere/123456">'
        "https://copr.something.somewhere/123456</a><br>"
        "SRPM creation logs:<br><br><pre>asd\nqwe\n</pre><br></body></html>"
    )


def test_get_build_logs_for_build_copr_build_branch_push(
    clean_before_and_after, a_copr_build_for_branch_push
):
    response = _get_build_logs_for_build(a_copr_build_for_branch_push)
    assert "We can't find any info" not in response
    assert (
        response
        == "<html><head><title>Build the-namespace/the-repo-name: branch build-branch</title>"
        "</head><body>COPR Build ID: 123456<br>State: pending<br><br>"
        "Build web interface URL: "
        '<a href="https://copr.something.somewhere/123456">'
        "https://copr.something.somewhere/123456</a><br>"
        "SRPM creation logs:<br><br><pre>asd\nqwe\n</pre><br></body></html>"
    )


def test_get_build_logs_for_build_copr_build_release(
    clean_before_and_after, a_copr_build_for_release
):
    response = _get_build_logs_for_build(a_copr_build_for_release)
    assert "We can't find any info" not in response
    assert (
        response
        == "<html><head><title>Build the-namespace/the-repo-name: release v1.0.2</title>"
        "</head><body>COPR Build ID: 123456<br>State: pending<br><br>"
        "Build web interface URL: "
        '<a href="https://copr.something.somewhere/123456">'
        "https://copr.something.somewhere/123456</a><br>"
        "SRPM creation logs:<br><br><pre>asd\nqwe\n</pre><br></body></html>"
    )
