# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import contextlib

import pytest
from flexmock import flexmock
from gitlab.exceptions import GitlabError
from ogr import PagureService
from ogr.abstract import CommitStatus
from ogr.exceptions import GithubAPIException, GitlabAPIException
from ogr.services.github import GithubProject
from ogr.services.github.check_run import (
    GithubCheckRunResult,
    GithubCheckRunStatus,
    create_github_check_run_output,
)
from ogr.services.gitlab import GitlabProject
from ogr.services.pagure import PagureProject
from packit.config.notifications import (
    FailureCommentNotificationsConfig,
    NotificationsConfig,
)

from packit_service.worker.reporting import (
    BaseCommitStatus,
    DuplicateCheckMode,
    StatusReporter,
    StatusReporterGithubChecks,
    StatusReporterGithubStatuses,
    StatusReporterGitlab,
    update_message_with_configured_failure_comment_message,
)
from packit_service.worker.reporting.news import News

create_table_content = StatusReporterGithubChecks._create_table


@pytest.mark.parametrize(
    ("project,commit_sha,pr_id,pr_object,state,description,check_name,url,state_to_set,uid"),
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
        pytest.param(
            flexmock(),
            None,
            1,
            flexmock(head_commit="7654321", source_project=flexmock()),
            BaseCommitStatus.error,
            "We made it!",
            "packit/pagure-rpm-build",
            "https://api.packit.dev/build/114/logs",
            CommitStatus.error,
            "9c7deecdc3ab908cb5a555edd192135e",
            id="No commit_sha, pr_id exists",
        ),
        pytest.param(
            flexmock(),
            None,
            None,
            flexmock(head_commit="7654321", source_project=flexmock()),
            BaseCommitStatus.error,
            "We made it!",
            "packit/pagure-rpm-build",
            "https://api.packit.dev/build/114/logs",
            CommitStatus.error,
            "9c7deecdc3ab908cb5a555edd192135e",
            id="No commit_sha and no pr_id",
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
    reporter = StatusReporter.get_instance(
        project=project,
        commit_sha=commit_sha,
        pr_id=pr_id,
        packit_user="packit",
    )
    act_upon = flexmock(pr_object.source_project) if pr_id else flexmock(PagureProject)

    if commit_sha:
        act_upon.should_receive("set_commit_status").with_args(
            commit_sha,
            state_to_set,
            url,
            description,
            check_name,
            trim=True,
        ).once()
    elif pr_id:
        act_upon.should_receive("set_commit_status").never()
        pr_object.should_receive("set_flag").with_args(
            username=check_name, comment=description, url=url, status=state_to_set, uid=uid
        ).once()
    else:
        # Without pr_id of commit_sha the status cannot be set
        pass

    if pr_id is not None:
        flexmock(PagureProject).should_receive("get_pr").with_args(pr_id).and_return(
            pr_object,
        )

    reporter.set_status(state, description, check_name, url)


@pytest.mark.parametrize(
    ("commit_sha,pr_id,pr_object,state,description,check_name,url,state_to_set"),
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
    commit_sha,
    pr_id,
    pr_object,
    state,
    description,
    check_name,
    url,
    state_to_set,
):
    project = GitlabProject(None, None, None)
    reporter = StatusReporter.get_instance(
        project=project,
        commit_sha=commit_sha,
        pr_id=pr_id,
        packit_user="packit",
    )
    act_upon = flexmock(pr_object.source_project) if pr_id else flexmock(GitlabProject)

    act_upon.should_receive("set_commit_status").with_args(
        commit_sha,
        state_to_set,
        url,
        description,
        check_name,
        trim=True,
    ).once()

    if pr_id is not None:
        flexmock(GitlabProject).should_receive("get_pr").with_args(pr_id).and_return(
            pr_object,
        )

    reporter.set_status(state, description, check_name, url)


@pytest.mark.parametrize(
    (
        "project,commit_sha,pr_id,pr_object,state,title,summary,"
        "check_name,url,check_status,check_conclusion,project_object_id"
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
            1,
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
            1,
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
            ),
            "packit/branch-rpm-build",
            "https://api.packit.dev/build/112/logs",
            GithubCheckRunStatus.completed,
            GithubCheckRunResult.failure,
            1,
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
    project_object_id,
):
    flexmock(News).should_receive("get_sentence").and_return("Interesting news.")

    project = GithubProject(None, None, None)
    reporter = StatusReporter.get_instance(
        project=project,
        commit_sha=commit_sha,
        pr_id=pr_id,
        project_event_id=project_object_id,
        packit_user="packit",
    )
    act_upon = flexmock(GithubProject)

    act_upon.should_receive("create_check_run").with_args(
        name=check_name,
        commit_sha=commit_sha,
        url=url,
        external_id=str(project_object_id),
        status=check_status,
        conclusion=check_conclusion,
        output=create_github_check_run_output(
            title,
            summary + "\n\n---\n*Interesting news.*",
        ),
    ).once()

    reporter.set_status(state, title, check_name, url)


@pytest.mark.parametrize(
    (
        "project,commit_sha,"
        "pr_id,has_pr_id,pr_object,"
        "state,description,check_name,url,state_to_set,"
        "exception_type, exception_dict, status_reporter_type"
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
            GithubAPIException,
            {},
            StatusReporterGithubStatuses,
            id="GitHub PR",
        ),
        pytest.param(
            flexmock(),
            None,
            "11",
            True,
            flexmock(),
            BaseCommitStatus.success,
            "We made it!",
            "packit/pr-rpm-build",
            "https://api.packit.dev/build/111/logs",
            CommitStatus.success,
            GithubAPIException,
            {},
            StatusReporterGithubStatuses,
            id="GitHub commit_sha None, pr_id present",
        ),
        pytest.param(
            flexmock(),
            None,
            None,
            False,
            flexmock(),
            BaseCommitStatus.success,
            "We made it!",
            "packit/pr-rpm-build",
            "https://api.packit.dev/build/111/logs",
            CommitStatus.success,
            GithubAPIException,
            {},
            StatusReporterGithubStatuses,
            id="GitHub commit_sha None, pr_id None",
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
            GitlabAPIException,
            {"__cause__": GitlabError(response_code=403)},
            StatusReporterGitlab,
            id="branch push",
        ),
        pytest.param(
            flexmock(),
            None,
            11,
            True,
            flexmock(source_project=flexmock()),
            BaseCommitStatus.failure,
            "We made it!",
            "packit/branch-rpm-build",
            "https://api.packit.dev/build/112/logs",
            CommitStatus.failure,
            GitlabAPIException,
            {"__cause__": GitlabError(response_code=404)},
            StatusReporterGitlab,
            id="Gitlab commit_sha None, PR present",
        ),
        pytest.param(
            flexmock(),
            None,
            None,
            False,
            flexmock(source_project=flexmock()),
            BaseCommitStatus.failure,
            "We made it!",
            "packit/branch-rpm-build",
            "https://api.packit.dev/build/112/logs",
            CommitStatus.failure,
            GitlabAPIException,
            {"__cause__": GitlabError(response_code=404)},
            StatusReporterGitlab,
            id="Gitlab commit_sha None, PR None",
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
    exception_type,
    exception_dict,
    status_reporter_type,
):
    reporter = status_reporter_type(
        project=project, commit_sha=commit_sha, pr_id=pr_id, packit_user="packit"
    )

    exception = exception_type()
    for key, value in exception_dict.items():
        setattr(exception, key, value)

    project.should_receive("set_commit_status").with_args(
        commit_sha,
        state_to_set,
        url,
        description,
        check_name,
        trim=True,
    ).and_raise(exception).once()

    if commit_sha:
        project.should_receive("commit_comment").with_args(
            commit=commit_sha,
            body="\n".join(
                [
                    f"- name: {check_name}",
                    f"- state: {state.name}",
                    f"- url: {url if url else 'not provided'}",
                ],
            )
            + f"\n\n{description}",
        ).once()
        pr_object.should_receive("comment").never()
    elif has_pr_id:
        project.should_receive("commit_comment").never()
        expected_comment = (
            "| Job | Result |\n"
            "| ------------- | ------------ |\n"
            f"| [{check_name}]({url}) | {state.name.upper()} |\n"
            f"### Description\n\n{description}"
        )
        pr_object.should_receive("comment").with_args(body=expected_comment).once()
    else:
        project.should_receive("commit_comment").never()
        pr_object.should_receive("comment").never()

    project.should_receive("get_commit_comments").and_return([])
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
    reporter = StatusReporter.get_instance(
        project=project,
        commit_sha=commit_sha,
        pr_id=pr_id,
        packit_user="packit",
    )
    act_upon = flexmock(GitlabProject)

    comment_body = "\n".join(
        (
            "| Job | Result |",
            "| ------------- | ------------ |",
            f"| [{check_names}]({url}) | {result} |",
            "### Description\n",
            "should include this",
        ),
    )

    if pr_id:
        act_upon.should_receive("get_pr").with_args(pr_id).and_return(
            flexmock().should_receive("comment").with_args(body=comment_body).mock(),
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
        "check_conclusion,commit_state_to_set,exception_type,event_id"
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
            GithubAPIException,
            1,
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
    exception_type,
    event_id,
):
    flexmock(News).should_receive("get_sentence").and_return("Interesting news.")

    project = GithubProject(None, None, None)
    reporter = StatusReporter.get_instance(
        project=project,
        commit_sha=commit_sha,
        project_event_id=event_id,
        pr_id=pr_id,
        packit_user="packit",
    )
    act_upon = flexmock(GithubProject)

    act_upon.should_receive("create_check_run").with_args(
        name=check_name,
        commit_sha=commit_sha,
        url=url,
        external_id=str(event_id),
        status=check_status,
        conclusion=check_conclusion,
        output=create_github_check_run_output(
            title,
            summary + "\n\n---\n*Interesting news.*",
        ),
    ).and_raise(exception_type).once()

    act_upon.should_receive("set_commit_status").with_args(
        commit_sha,
        commit_state_to_set,
        url,
        title,
        check_name,
        trim=True,
    ).once()

    reporter.set_status(state, title, check_name, url)


def test_create_table():
    assert create_table_content(
        "dashboard.packit.dev-url",
        {"Testing Farm": "tf-url", "COPR build": "copr-build-url"},
    ) == (
        "| Name/Job | URL |\n"
        "| --- | --- |\n"
        "| Dashboard | dashboard.packit.dev-url |\n"
        "| Testing Farm | tf-url |\n"
        "| COPR build | copr-build-url |\n\n"
    )


@pytest.mark.parametrize(
    "pr_id,commit_sha,duplicate_check,existing_comments,should_comment",
    [
        # Basic cases, no duplicate check
        (1, None, DuplicateCheckMode.do_not_check, [], True),
        (None, "1234abd", DuplicateCheckMode.do_not_check, [], True),
        # PR comment with duplicate check
        (1, None, DuplicateCheckMode.check_last_comment, [], True),
        (
            1,
            None,
            DuplicateCheckMode.check_last_comment,
            [flexmock(author="Foo")],
            True,
        ),
        (
            1,
            None,
            DuplicateCheckMode.check_last_comment,
            [flexmock(author="packit-as-a-service", body="bar")],
            True,
        ),
        (
            1,
            None,
            DuplicateCheckMode.check_last_comment,
            [flexmock(author="packit-as-a-service", body="foo")],
            False,
        ),
        # Ogr project reverses the order for us
        (
            1,
            None,
            DuplicateCheckMode.check_last_comment,
            [
                flexmock(author="packit-as-a-service", body="bar"),
                flexmock(author="packit-as-a-service", body="foo"),
            ],
            True,
        ),
        (
            1,
            None,
            DuplicateCheckMode.check_last_comment,
            [
                flexmock(author="packit-as-a-service", body="foo"),
                flexmock(author="packit-as-a-service", body="bar"),
            ],
            False,
        ),
        # Commit comment with duplicate check
        (None, "1234abd", DuplicateCheckMode.check_last_comment, [], True),
        (
            None,
            "1234abd",
            DuplicateCheckMode.check_last_comment,
            [flexmock(author="Foo")],
            True,
        ),
        (
            None,
            "1234abd",
            DuplicateCheckMode.check_last_comment,
            [flexmock(author="packit-as-a-service", body="bar")],
            True,
        ),
        (
            None,
            "1234abd",
            DuplicateCheckMode.check_last_comment,
            [flexmock(author="packit-as-a-service", body="foo")],
            False,
        ),
        # Github returns this from oldest to newest and we reverse it on our end
        (
            None,
            "1234abd",
            DuplicateCheckMode.check_last_comment,
            [
                flexmock(author="packit-as-a-service", body="foo"),
                flexmock(author="packit-as-a-service", body="bar"),
            ],
            True,
        ),
        (
            None,
            "1234abd",
            DuplicateCheckMode.check_last_comment,
            [
                flexmock(author="packit-as-a-service", body="bar"),
                flexmock(author="packit-as-a-service", body="foo"),
            ],
            False,
        ),
        # Check all comments
        (
            1,
            None,
            DuplicateCheckMode.check_all_comments,
            [
                flexmock(author="packit-as-a-service", body="bar"),
                flexmock(author="packit-as-a-service", body="foo"),
            ],
            False,
        ),
        (
            None,
            "1234abd",
            DuplicateCheckMode.check_all_comments,
            [
                flexmock(author="packit-as-a-service", body="foo"),
                flexmock(author="packit-as-a-service", body="bar"),
            ],
            False,
        ),
    ],
)
def test_comment(pr_id, commit_sha, duplicate_check, existing_comments, should_comment):
    project = GithubProject(None, None, None)
    reporter = StatusReporter.get_instance(
        project=project,
        commit_sha=commit_sha,
        pr_id=pr_id,
        packit_user="packit-as-a-service",
    )

    act_upon = flexmock(project)

    if pr_id:
        pr = flexmock()
        act_upon.should_receive("get_pr").with_args(pr_id).and_return(pr)
        if duplicate_check != DuplicateCheckMode.do_not_check:
            flexmock(pr).should_receive("get_comments").with_args(
                reverse=True,
            ).and_return(existing_comments)

        if should_comment:
            pr.should_receive("comment").once()
        else:
            pr.should_receive("comment").never()
    else:
        if duplicate_check != DuplicateCheckMode.do_not_check:
            act_upon.should_receive("get_commit_comments").with_args(
                commit_sha,
            ).and_return(existing_comments)

        if should_comment:
            act_upon.should_receive("commit_comment").once()
        else:
            act_upon.should_receive("commit_comment").never()

    reporter.comment(body="foo", duplicate_check=duplicate_check)


@pytest.mark.parametrize(
    "comment,configured_message,result",
    [
        ("Some comment", None, "Some comment"),
        ("Some comment", "hello @admin", "Some comment\n\n---\nhello @admin"),
    ],
)
def test_update_message_with_configured_failure_comment_message(
    comment,
    configured_message,
    result,
):
    job_config = flexmock(
        notifications=NotificationsConfig(
            failure_comment=FailureCommentNotificationsConfig(configured_message),
        ),
    )
    assert update_message_with_configured_failure_comment_message(comment, job_config) == result


@pytest.mark.parametrize(
    "response_code,is_transient",
    [
        (None, True),  # Network error, response_code not set
        (429, True),  # Rate limiting
        (500, True),  # Server error
        (502, True),  # Bad gateway
        (400, False),  # Bad request
        (403, False),  # Forbidden
        (404, False),  # Not found
    ],
)
def test_is_transient_error(response_code, is_transient):
    """Test classification of API errors as transient across all platforms."""
    exception = flexmock(response_code=response_code)

    assert StatusReporter.is_transient_error(exception) == is_transient


@pytest.mark.parametrize(
    "reraise_transient_errors,response_code",
    [
        (True, 500),  # Transient error, reraise enabled -> should reraise
        (False, 500),  # Transient error, reraise disabled -> should fallback
        (True, 403),  # Non-transient error, reraise enabled -> should fallback
        (False, 403),  # Non-transient error, reraise disabled -> should fallback
    ],
)
def test_github_checks_error_handling(reraise_transient_errors, response_code):
    """Test error handling in StatusReporterGithubChecks."""
    project = GithubProject(None, None, None)
    reporter = StatusReporter.get_instance(
        project=project,
        commit_sha="abc123",
        pr_id=1,
        project_event_id=1,
        packit_user="packit",
        reraise_transient_errors=reraise_transient_errors,
    )

    exception = flexmock(GithubAPIException(), response_code=response_code)

    flexmock(GithubProject).should_receive("create_check_run").and_raise(
        exception,
    ).once()

    is_transient = reporter.is_transient_error(exception)

    if reraise_transient_errors and is_transient:
        # Should NOT fall back to commit status
        flexmock(GithubProject).should_receive("set_commit_status").never()
        # Should re-raise the exception
        expectation = pytest.raises(GithubAPIException)
    else:
        # Should fall back to commit status
        flexmock(GithubProject).should_receive("set_commit_status").with_args(
            "abc123",
            CommitStatus.success,
            "https://example.com",
            "Test completed",
            "packit/test",
            trim=True,
        ).once()
        # Should NOT raise
        expectation = contextlib.nullcontext()

    with expectation:
        reporter.set_status(
            BaseCommitStatus.success,
            "Test completed",
            "packit/test",
            "https://example.com",
        )


@pytest.mark.parametrize(
    "reporter_class,exception_class",
    [
        (StatusReporterGithubStatuses, GithubAPIException),
        (StatusReporterGitlab, GitlabAPIException),
    ],
)
@pytest.mark.parametrize(
    "reraise_transient_errors,response_code",
    [
        (True, 502),  # Transient error, reraise enabled -> should reraise
        (False, 500),  # Transient error, reraise disabled -> should fallback
        (True, 404),  # Non-transient error, reraise enabled -> should fallback
        (False, 404),  # Non-transient error, reraise disabled -> should fallback
    ],
)
def test_commit_status_error_handling(
    reporter_class, exception_class, reraise_transient_errors, response_code
):
    """Test error handling in commit status reporters (GitHub and GitLab)."""
    project = flexmock()
    reporter = flexmock(
        reporter_class(
            project=project,
            commit_sha="abc123",
            pr_id=1,
            packit_user="packit",
            reraise_transient_errors=reraise_transient_errors,
        )
    )

    exception = flexmock(exception_class(), response_code=response_code)

    project.should_receive("set_commit_status").and_raise(exception).once()

    is_transient = reporter.is_transient_error(exception)

    if reraise_transient_errors and is_transient:
        # Should NOT fall back to comment
        reporter.should_receive("_comment_as_set_status_fallback").never()
        # Should re-raise the exception
        expectation = pytest.raises(exception_class)
    else:
        # When commit_sha is present, it uses commit_comment
        reporter.should_receive("_comment_as_set_status_fallback").with_args(
            exception,
            BaseCommitStatus.success,
            "Build completed",
            "packit/build",
            "https://example.com",
        ).once()
        # Should NOT raise
        expectation = contextlib.nullcontext()

    with expectation:
        reporter.set_status(
            BaseCommitStatus.success,
            "Build completed",
            "packit/build",
            "https://example.com",
        )
