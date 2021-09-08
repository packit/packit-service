# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
from inspect import signature

import github
import gitlab
import pytest
from flexmock import flexmock

from ogr import PagureService
from ogr.abstract import CommitStatus
from ogr.services.github import GithubProject
from ogr.services.github.check_run import (
    create_github_check_run_output,
    GithubCheckRunStatus,
    GithubCheckRunResult,
)
from ogr.services.gitlab import GitlabProject
from ogr.services.pagure import PagureProject

from packit_service.worker.reporting import (
    StatusReporter,
    BaseCommitStatus,
    StatusReporterGithubStatuses,
    StatusReporterGitlab,
    StatusReporterGithubChecks,
)
from packit_service.constants import MSG_RERUN_NOT_SUPPORTED

create_table_content = StatusReporterGithubChecks._create_table


@pytest.mark.parametrize(
    (
        "project,commit_sha,"
        "pr_id,pr_object,"
        "state,description,check_name,url,state_to_set,"
        "uid"
    ),
    [
        pytest.param(
            flexmock(),
            "7654321",
            None,
            flexmock(head_commit="1234567", source_project=flexmock()),
            BaseCommitStatus.pending,
            "We made it!",
            "packit/pagure-rpm-build",
            "https://api.packit.dev/build/113/logs",
            CommitStatus.pending,
            None,
            id="Pagure PR, not head commit",
        ),
        pytest.param(
            flexmock(),
            "7654321",
            None,
            flexmock(head_commit="7654321", source_project=flexmock()),
            BaseCommitStatus.error,
            "We made it!",
            "packit/pagure-rpm-build",
            "https://api.packit.dev/build/114/logs",
            CommitStatus.error,
            "8d8d0d428ccee1112042f6d06f6b334a",
            id="Pagure PR, head commit",
        ),
    ],
)
def test_set_status_pagure(
    project,
    commit_sha,
    pr_id,
    pr_object,
    state,
    description,
    check_name,
    url,
    state_to_set,
    uid,
):
    project = PagureProject(None, None, PagureService())
    reporter = StatusReporter.get_instance(project, commit_sha, pr_id)
    act_upon = flexmock(pr_object.source_project) if pr_id else flexmock(PagureProject)

    act_upon.should_receive("set_commit_status").with_args(
        commit_sha, state_to_set, url, description, check_name, trim=True
    ).once()

    if pr_id is not None:
        flexmock(PagureProject).should_receive("get_pr").with_args(pr_id).and_return(
            pr_object
        )

    reporter.set_status(state, description, check_name, url)


@pytest.mark.parametrize(
    ("commit_sha,pr_id,pr_object," "state,description,check_name,url,state_to_set"),
    [
        pytest.param(
            "7654321",
            None,
            None,
            BaseCommitStatus.success,
            "We made it!",
            "packit/pr-rpm-build",
            "https://api.packit.dev/build/111/logs",
            CommitStatus.success,
            id="Gitlab branch",
        ),
        pytest.param(
            "7654321",
            1,
            flexmock(source_project=flexmock()),
            BaseCommitStatus.success,
            "We made it!",
            "packit/pr-rpm-build",
            "https://api.packit.dev/build/111/logs",
            CommitStatus.success,
            id="Gitlab PR",
        ),
    ],
)
def test_set_status_gitlab(
    commit_sha, pr_id, pr_object, state, description, check_name, url, state_to_set
):
    project = GitlabProject(None, None, None)
    reporter = StatusReporter.get_instance(project, commit_sha, pr_id)
    act_upon = flexmock(pr_object.source_project) if pr_id else flexmock(GitlabProject)

    act_upon.should_receive("set_commit_status").with_args(
        commit_sha, state_to_set, url, description, check_name, trim=True
    ).once()

    if pr_id is not None:
        flexmock(GitlabProject).should_receive("get_pr").with_args(pr_id).and_return(
            pr_object
        )

    reporter.set_status(state, description, check_name, url)


@pytest.mark.parametrize(
    (
        "project,commit_sha,pr_id,pr_object,state,title,summary,"
        "check_name,url,check_status,check_conclusion"
    ),
    [
        pytest.param(
            flexmock(),
            "7654321",
            "11",
            flexmock(),
            BaseCommitStatus.success,
            "We made it!",
            create_table_content(
                url="https://api.packit.dev/build/111/logs",
                links_to_external_services=None,
            ),
            "packit/pr-rpm-build",
            "https://api.packit.dev/build/111/logs",
            GithubCheckRunStatus.completed,
            GithubCheckRunResult.success,
            id="GitHub PR",
        ),
        pytest.param(
            flexmock(),
            "7654321",
            "11",
            flexmock(),
            BaseCommitStatus.running,
            "In progress",
            create_table_content(
                url="https://api.packit.dev/build/111/logs",
                links_to_external_services=None,
            ),
            "packit/pr-rpm-build",
            "https://api.packit.dev/build/111/logs",
            GithubCheckRunStatus.in_progress,
            None,
            id="GitHub PR",
        ),
        pytest.param(
            flexmock(),
            "7654321",
            None,
            flexmock(),
            BaseCommitStatus.failure,
            "We made it!",
            create_table_content(
                url="https://api.packit.dev/build/112/logs",
                links_to_external_services=None,
            )
            + MSG_RERUN_NOT_SUPPORTED,
            "packit/branch-rpm-build",
            "https://api.packit.dev/build/112/logs",
            GithubCheckRunStatus.completed,
            GithubCheckRunResult.failure,
            id="branch push",
        ),
    ],
)
def test_set_status_github_check(
    project,
    commit_sha,
    pr_id,
    pr_object,
    state,
    title,
    summary,
    check_name,
    url,
    check_status,
    check_conclusion,
):
    project = GithubProject(None, None, None)
    reporter = StatusReporter.get_instance(project, commit_sha, pr_id)
    act_upon = flexmock(GithubProject)

    act_upon.should_receive("create_check_run").with_args(
        name=check_name,
        commit_sha=commit_sha,
        url=url,
        status=check_status,
        conclusion=check_conclusion,
        output=create_github_check_run_output(title, summary),
    ).once()

    reporter.set_status(state, title, check_name, url)


@pytest.mark.parametrize(
    (
        "project,commit_sha,"
        "pr_id,has_pr_id,pr_object,"
        "state,description,check_name,url,state_to_set,"
        "exception_mock, status_reporter_type"
    ),
    [
        pytest.param(
            flexmock(),
            "7654321",
            "11",
            True,
            flexmock(),
            BaseCommitStatus.success,
            "We made it!",
            "packit/pr-rpm-build",
            "https://api.packit.dev/build/111/logs",
            CommitStatus.success,
            (
                github.GithubException,
                # https://docs.python.org/3/library/inspect.html#inspect.signature
                # to account for changes in positional arguments: pygithub 1.55 added headers
                # as additional positional argument; this creates an iterable and sets None
                # for every argument of GithubException.__init__ except for 'self'
                [
                    None
                    for param_name, param in signature(
                        github.GithubException.__init__
                    ).parameters.items()
                    if param_name != "self"
                ],
                dict(),
            ),
            StatusReporterGithubStatuses,
            id="GitHub PR",
        ),
        pytest.param(
            flexmock(),
            "7654321",
            None,
            False,
            flexmock(source_project=flexmock()),
            BaseCommitStatus.failure,
            "We made it!",
            "packit/branch-rpm-build",
            "https://api.packit.dev/build/112/logs",
            CommitStatus.failure,
            (gitlab.exceptions.GitlabCreateError, (), {"response_code": 403}),
            StatusReporterGitlab,
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
    state_to_set,
    exception_mock,
    status_reporter_type,
):
    reporter = status_reporter_type(project, commit_sha, pr_id)

    exception, exception_args, exception_kwargs = exception_mock

    project.should_receive("set_commit_status").with_args(
        commit_sha, state_to_set, url, description, check_name, trim=True
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
        project.should_receive("get_pr").with_args(pr_id).and_return(pr_object)

    reporter.set_status(state, description, check_name, url)


@pytest.mark.parametrize(
    "commit_sha,pr_id,state,check_names,url,result",
    [
        (
            "7654321",
            "11",
            BaseCommitStatus.success,
            "packit/pr-rpm-build",
            "https://api.packit.dev/build/111/logs",
            "SUCCESS",
        ),
        (
            "deadbeef",
            None,
            BaseCommitStatus.failure,
            "packit/branch-build",
            "https://api.packit.dev/build/111/logs",
            "FAILURE",
        ),
    ],
)
def test_report_status_by_comment(
    commit_sha,
    pr_id,
    state,
    check_names,
    url,
    result,
):
    project = GitlabProject(None, None, None)
    reporter = StatusReporter.get_instance(project, commit_sha, pr_id)
    act_upon = flexmock(GitlabProject)

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
        act_upon.should_receive("get_pr").with_args(pr_id=pr_id).and_return(
            flexmock().should_receive("comment").with_args(body=comment_body).mock()
        ).once()
    else:
        act_upon.should_receive("commit_comment").with_args(
            commit=commit_sha,
            body=comment_body,
        ).once()

    reporter.report_status_by_comment(state, url, check_names, "should include this")


@pytest.mark.parametrize(
    (
        "project,commit_sha,"
        "pr_id,pr_object,"
        "state,title,summary,"
        "check_name,url,check_status,"
        "check_conclusion,commit_state_to_set,exception_mock"
    ),
    [
        pytest.param(
            flexmock(),
            "7654321",
            "11",
            flexmock(source_project=flexmock()),
            BaseCommitStatus.success,
            "We made it!",
            create_table_content(
                url="https://api.packit.dev/build/111/logs",
                links_to_external_services=None,
            ),
            "packit/pr-rpm-build",
            "https://api.packit.dev/build/111/logs",
            GithubCheckRunStatus.completed,
            GithubCheckRunResult.success,
            CommitStatus.success,
            (
                github.GithubException,
                # https://docs.python.org/3/library/inspect.html#inspect.signature
                # to account for changes in positional arguments: pygithub 1.55 added headers
                # as additional positional argument; this creates an iterable and sets None
                # for every argument of GithubException.__init__ except for 'self'
                [
                    None
                    for param_name, param in signature(
                        github.GithubException.__init__
                    ).parameters.items()
                    if param_name != "self"
                ],
                dict(),
            ),
            id="GitHub PR",
        ),
    ],
)
def test_status_instead_check(
    project,
    commit_sha,
    pr_id,
    pr_object,
    state,
    title,
    summary,
    check_name,
    url,
    check_status,
    check_conclusion,
    commit_state_to_set,
    exception_mock,
):
    project = GithubProject(None, None, None)
    reporter = StatusReporter.get_instance(project, commit_sha, pr_id)
    act_upon = flexmock(GithubProject)

    exception, exception_args, exception_kwargs = exception_mock
    act_upon.should_receive("create_check_run").with_args(
        name=check_name,
        commit_sha=commit_sha,
        url=url,
        status=check_status,
        conclusion=check_conclusion,
        output=create_github_check_run_output(title, summary),
    ).and_raise(exception, *exception_args, **exception_kwargs).once()

    act_upon.should_receive("set_commit_status").with_args(
        commit_sha, commit_state_to_set, url, title, check_name, trim=True
    ).once()

    reporter.set_status(state, title, check_name, url)


def test_create_table():
    assert create_table_content(
        "dashboard-url",
        {"Testing Farm": "tf-url", "COPR build": "copr-build-url"},
    ) == (
        "| Name/Job | URL |\n"
        "| --- | --- |\n"
        "| Dashboard | dashboard-url |\n"
        "| Testing Farm | tf-url |\n"
        "| COPR build | copr-build-url |\n"
    )
