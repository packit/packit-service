# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import github
import gitlab
import pytest
from flexmock import flexmock

from ogr.abstract import CommitStatus
from ogr.services.gitlab import GitlabProject
from packit_service.worker.reporting import StatusReporter


@pytest.mark.parametrize(
    (
        "project,commit_sha,"
        "pr_id,pr_object,"
        "state,description,check_name,url,"
        "needs_pr_flags,uid"
    ),
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
            False,
            None,
            id="GitHub PR",
        ),
        pytest.param(
            flexmock(),
            "7654321",
            None,
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

    if pr_id is not None:
        project.should_receive("get_pr").with_args(pr_id).once().and_return(pr_object)

    if needs_pr_flags:
        pr_object.should_receive("set_flag").with_args(
            check_name, description, url, state, uid
        )

    reporter.set_status(state, description, check_name, url)


@pytest.mark.parametrize(
    ("commit_sha,pr_id,pr_object," "state,description,check_name,url,"),
    [
        pytest.param(
            "7654321",
            None,
            None,
            CommitStatus.success,
            "We made it!",
            "packit/pr-rpm-build",
            "https://api.packit.dev/build/111/logs",
            id="Gitlab branch",
        ),
        pytest.param(
            "7654321",
            1,
            flexmock(source_project=flexmock()),
            CommitStatus.success,
            "We made it!",
            "packit/pr-rpm-build",
            "https://api.packit.dev/build/111/logs",
            id="Gitlab PR",
        ),
    ],
)
def test_set_status_gitlab(
    commit_sha,
    pr_id,
    pr_object,
    state,
    description,
    check_name,
    url,
):
    project = GitlabProject(None, None, None)
    reporter = StatusReporter(project, commit_sha, pr_id)
    act_upon = flexmock(pr_object.source_project) if pr_id else flexmock(GitlabProject)

    act_upon.should_receive("set_commit_status").with_args(
        commit_sha, state, url, description, check_name, trim=True
    ).once()

    if pr_id is not None:
        flexmock(GitlabProject).should_receive("get_pr").with_args(pr_id).and_return(
            pr_object
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
    "project,commit_sha,pr_id,state,check_names,url,result",
    [
        (
            flexmock(),
            "7654321",
            "11",
            CommitStatus.success,
            "packit/pr-rpm-build",
            "https://api.packit.dev/build/111/logs",
            "SUCCESS",
        ),
        (
            flexmock(),
            "deadbeef",
            None,
            CommitStatus.failure,
            "packit/branch-build",
            "https://api.packit.dev/build/111/logs",
            "FAILURE",
        ),
    ],
)
def test_report_status_by_comment(
    project,
    commit_sha,
    pr_id,
    state,
    check_names,
    url,
    result,
):
    reporter = StatusReporter(project, commit_sha, pr_id)

    comment_body = "\n".join(
        (
            "| Job | Result |",
            "| ------------- | ------------ |",
            f"| [{check_names}]({url}) | {result} |",
            "### Description\n",
            "should include this",
        )
    )

    if pr_id:
        project.should_receive("get_pr").with_args(pr_id=pr_id).and_return(
            flexmock().should_receive("comment").with_args(body=comment_body).mock()
        ).once()
    else:
        project.should_receive("commit_comment").with_args(
            commit=commit_sha,
            body=comment_body,
        ).once()

    reporter.report_status_by_comment(state, url, check_names, "should include this")
