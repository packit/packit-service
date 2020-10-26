# MIT License
#
# Copyright (c) 2018-2020 Red Hat, Inc.
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

import github
import gitlab
import pytest
from flexmock import flexmock

from ogr.abstract import CommitStatus
from packit_service.worker.reporting import StatusReporter


@pytest.mark.parametrize(
    (
        "project,commit_sha,"
        "pr_id,has_pr_id,pr_object,"
        "state,description,check_name,url,"
        "needs_pr_flags,uid"
    ),
    [
        pytest.param(
            flexmock(),
            "7654321",
            "11",
            True,
            flexmock(),
            CommitStatus.success,
            "We made it!",
            "packit/pr-rpm-build",
            "https://api.packit.dev/build/111/logs",
            False,
            None,
            id="GitHub PR",
        ),
        pytest.param(
            flexmock(),
            "7654321",
            None,
            False,
            flexmock(),
            CommitStatus.failure,
            "We made it!",
            "packit/branch-rpm-build",
            "https://api.packit.dev/build/112/logs",
            False,
            None,
            id="branch push",
        ),
        pytest.param(
            flexmock(),
            "7654321",
            None,
            False,
            flexmock(head_commit="1234567"),
            CommitStatus.pending,
            "We made it!",
            "packit/pagure-rpm-build",
            "https://api.packit.dev/build/113/logs",
            False,
            None,
            id="Pagure PR, not head commit",
        ),
        pytest.param(
            flexmock(),
            "7654321",
            None,
            False,
            flexmock(head_commit="7654321"),
            CommitStatus.error,
            "We made it!",
            "packit/pagure-rpm-build",
            "https://api.packit.dev/build/114/logs",
            True,
            "8d8d0d428ccee1112042f6d06f6b334a",
            id="Pagure PR, head commit",
        ),
    ],
)
def test_set_status(
    project,
    commit_sha,
    pr_id,
    has_pr_id,
    pr_object,
    state,
    description,
    check_name,
    url,
    needs_pr_flags,
    uid,
):
    reporter = StatusReporter(project, commit_sha, pr_id)

    project.should_receive("set_commit_status").with_args(
        commit_sha, state, url, description, check_name, trim=True
    ).once()

    if has_pr_id:
        project.should_receive("get_pr").with_args(pr_id).once().and_return(pr_object)

    if needs_pr_flags:
        pr_object.should_receive("set_flag").with_args(
            check_name, description, url, state, uid
        )

    reporter.set_status(state, description, check_name, url)


@pytest.mark.parametrize(
    (
        "project,commit_sha,"
        "pr_id,has_pr_id,pr_object,"
        "state,description,check_name,url,"
        "exception_mock"
    ),
    [
        pytest.param(
            flexmock(),
            "7654321",
            "11",
            True,
            flexmock(),
            CommitStatus.success,
            "We made it!",
            "packit/pr-rpm-build",
            "https://api.packit.dev/build/111/logs",
            (github.GithubException, (None, None), dict()),
            id="GitHub PR",
        ),
        pytest.param(
            flexmock(),
            "7654321",
            None,
            False,
            flexmock(),
            CommitStatus.failure,
            "We made it!",
            "packit/branch-rpm-build",
            "https://api.packit.dev/build/112/logs",
            (gitlab.exceptions.GitlabCreateError, (), {"response_code": 403}),
            id="branch push",
        ),
    ],
)
def test_commit_comment_instead_of_status(
    project,
    commit_sha,
    pr_id,
    has_pr_id,
    pr_object,
    state,
    description,
    check_name,
    url,
    exception_mock,
):
    reporter = StatusReporter(project, commit_sha, pr_id)

    exception, exception_args, exception_kwargs = exception_mock
    project.should_receive("set_commit_status").with_args(
        commit_sha, state, url, description, check_name, trim=True
    ).and_raise(exception, *exception_args, **exception_kwargs).once()
    project.should_receive("commit_comment").with_args(
        commit=commit_sha,
        body="\n".join(
            [
                f"- name: {check_name}",
                f"- state: {state.name}",
                f"- url: {url if url else 'not provided'}",
            ]
        )
        + f"\n\n{description}",
    )

    if has_pr_id:
        project.should_receive("get_pr").with_args(pr_id).once().and_return(pr_object)

    reporter.set_status(state, description, check_name, url)


@pytest.mark.parametrize(
    ("project,commit_sha," "pr_id,pr_object," "state,description,check_names,url,"),
    [
        pytest.param(
            flexmock(),
            "7654321",
            "11",
            flexmock(),
            CommitStatus.success,
            "We made it!",
            "packit/pr-rpm-build",
            "https://api.packit.dev/build/111/logs",
        ),
    ],
)
def test_report_status_by_comment(
    project,
    commit_sha,
    pr_id,
    pr_object,
    state,
    description,
    check_names,
    url,
):
    reporter = StatusReporter(project, commit_sha, pr_id)

    project.should_receive("pr_comment").with_args(
        pr_id,
        f"| Job | Result |\n| ------------- | ------------ |\n| [{check_names}]({url}) | SUCCESS |",
    ).once()

    reporter.report_status_by_comment(state, url, check_names)
