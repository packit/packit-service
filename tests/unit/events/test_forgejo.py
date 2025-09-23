# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json

import pytest
from flexmock import flexmock
from ogr.services.forgejo import ForgejoProject

from packit_service.config import ServiceConfig
from packit_service.events.enums import (
    IssueCommentAction,
    PullRequestAction,
    PullRequestCommentAction,
)
from packit_service.events.forgejo import issue, pr, push
from packit_service.package_config_getter import PackageConfigGetter
from packit_service.worker.parser import Parser
from tests.spellbook import DATA_DIR

PATH_TO_FORGEJO_WEBHOOKS = DATA_DIR / "webhooks" / "forgejo"


@pytest.fixture()
def pull_request_opened():
    with open(PATH_TO_FORGEJO_WEBHOOKS / "pr_opened.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def pull_request_comment():
    with open(PATH_TO_FORGEJO_WEBHOOKS / "pr_comment.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def issue_comment():
    with open(PATH_TO_FORGEJO_WEBHOOKS / "issue_comment.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def push_new_branch():
    with open(PATH_TO_FORGEJO_WEBHOOKS / "push_new_branch.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def tag_push():
    with open(PATH_TO_FORGEJO_WEBHOOKS / "tag_push.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def push_with_many_commit():
    with open(PATH_TO_FORGEJO_WEBHOOKS / "push_with_many_commits.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def push_with_one_commit():
    with open(PATH_TO_FORGEJO_WEBHOOKS / "push_with_one_commit.json") as outfile:
        return json.load(outfile)


def test_parse_forgejo_tag_push(tag_push):
    mock_project = flexmock(full_repo_name="packit/test-repo-to-generate-events")
    flexmock(ServiceConfig).should_receive("get_project").and_return(mock_project)

    event_object = Parser.parse_event(tag_push)

    assert isinstance(event_object, push.Commit)
    assert event_object.repo_namespace == "packit"
    assert event_object.repo_name == "test-repo-to-generate-events"
    assert event_object.commit_sha == "9abc2644c3b9828db4bbe30c795b140c6c55089f"
    assert (
        event_object.project_url
        == "https://v10.next.forgejo.org/packit/test-repo-to-generate-events"
    )
    assert event_object.git_ref == "fifth"

    assert event_object.project.full_repo_name == "packit/test-repo-to-generate-events"
    assert not event_object.base_project

    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=event_object.base_project,
        project=event_object.project,
        pr_id=None,
        reference="9abc2644c3b9828db4bbe30c795b140c6c55089f",
        fail_when_missing=False,
    ).and_return(
        flexmock(get_package_config_views=dict),
    ).once()
    assert event_object.packages_config


def test_parse_pull_request(pull_request_opened):
    mock_service = flexmock(get_project=lambda namespace, repo: flexmock())
    mock_project = flexmock(
        full_repo_name="packit/test-repo-to-generate-events", service=mock_service
    )
    flexmock(ServiceConfig).should_receive("get_project").and_return(mock_project)

    event_object = Parser.parse_event(pull_request_opened)

    assert isinstance(event_object, pr.Action)
    assert event_object.action == PullRequestAction.opened
    assert event_object.pr_id == 2
    assert event_object.base_repo_namespace == "mfocko"
    assert event_object.base_repo_name == "test-repo-to-generate-events"
    assert event_object.base_ref == "37182f59ccaaa21584e7b580442fd33b60fe3f80"
    assert event_object.target_repo_namespace == "packit"
    assert event_object.target_repo_name == "test-repo-to-generate-events"
    assert (
        event_object.project_url
        == "https://v10.next.forgejo.org/packit/test-repo-to-generate-events"
    )
    assert event_object.commit_sha == "37182f59ccaaa21584e7b580442fd33b60fe3f80"
    assert event_object.actor == "mfocko"

    assert event_object.project.full_repo_name == "packit/test-repo-to-generate-events"
    assert event_object.base_project

    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=event_object.base_project,
        project=event_object.project,
        pr_id=2,
        reference="37182f59ccaaa21584e7b580442fd33b60fe3f80",
        fail_when_missing=False,
    ).and_return(
        flexmock(get_package_config_views=dict),
    ).once()
    assert event_object.packages_config


def test_parse_pull_request_comment(pull_request_comment):
    mock_project = flexmock(
        full_repo_name="packit/test-repo-to-generate-events",
        get_pr=lambda pr_id: flexmock(head_commit="37182f59ccaaa21584e7b580442fd33b60fe3f80"),
        service=flexmock(get_project=lambda namespace, repo: flexmock()),
    )
    flexmock(ServiceConfig).should_receive("get_project").and_return(mock_project)

    event_object = Parser.parse_event(pull_request_comment)

    assert isinstance(event_object, pr.Comment)
    assert event_object.action == PullRequestCommentAction.created
    assert event_object.pr_id == 2
    assert event_object.base_repo_namespace == "mfocko"
    assert event_object.base_repo_name == "test-repo-to-generate-events"
    assert event_object.target_repo_namespace == "packit"
    assert event_object.target_repo_name == "test-repo-to-generate-events"
    assert (
        event_object.project_url
        == "https://v10.next.forgejo.org/packit/test-repo-to-generate-events"
    )
    assert event_object.actor == "mfocko"
    assert event_object.comment == "PR comment"

    assert event_object.project.full_repo_name == "packit/test-repo-to-generate-events"
    assert event_object.base_project

    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=event_object.base_project,
        project=event_object.project,
        pr_id=2,
        reference="37182f59ccaaa21584e7b580442fd33b60fe3f80",
        fail_when_missing=False,
    ).and_return(
        flexmock(get_package_config_views=dict),
    ).once()
    assert event_object.packages_config


def test_parse_issue_comment(issue_comment):
    mock_project = flexmock(full_repo_name="packit/test-repo-to-generate-events")
    flexmock(ServiceConfig).should_receive("get_project").and_return(mock_project)

    event_object = Parser.parse_event(issue_comment)

    assert isinstance(event_object, issue.Comment)
    assert event_object.action == IssueCommentAction.created
    assert event_object.issue_id == 1
    assert event_object.repo_namespace == "packit"
    assert event_object.repo_name == "test-repo-to-generate-events"
    assert event_object.target_repo == "packit/test-repo-to-generate-events"
    assert event_object.base_ref == "main"
    assert (
        event_object.project_url
        == "https://v10.next.forgejo.org/packit/test-repo-to-generate-events"
    )
    assert event_object.actor == "mfocko"
    assert event_object.comment == "issue comment"

    assert event_object.project.full_repo_name == "packit/test-repo-to-generate-events"
    assert not event_object.base_project

    flexmock(event_object.project).should_receive("get_releases").and_return([])
    flexmock(ForgejoProject).should_receive("get_sha_from_tag").never()
    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=event_object.base_project,
        project=event_object.project,
        pr_id=None,
        reference=None,
        fail_when_missing=False,
    ).and_return(
        flexmock(get_package_config_views=dict),
    ).once()
    assert event_object.packages_config
    assert event_object.commit_sha is None
    assert event_object.tag_name == ""


def test_parse_forgejo_push_many_commits(push_with_many_commit):
    mock_project = flexmock(full_repo_name="packit/test-repo-to-generate-events")
    flexmock(ServiceConfig).should_receive("get_project").and_return(mock_project)

    event_object = Parser.parse_event(push_with_many_commit)

    assert isinstance(event_object, push.Commit)
    assert event_object.repo_namespace == "packit"
    assert event_object.repo_name == "test-repo-to-generate-events"
    assert event_object.commit_sha == "fa9bbf46c6ae89b755716683814b03b6a2c82263"
    assert (
        event_object.project_url
        == "https://v10.next.forgejo.org/packit/test-repo-to-generate-events"
    )
    assert event_object.git_ref == "main"

    assert event_object.project.full_repo_name == "packit/test-repo-to-generate-events"
    assert not event_object.base_project

    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=event_object.base_project,
        project=event_object.project,
        pr_id=None,
        reference="fa9bbf46c6ae89b755716683814b03b6a2c82263",
        fail_when_missing=False,
    ).and_return(
        flexmock(get_package_config_views=dict),
    ).once()
    assert event_object.packages_config


def test_parse_forgejo_push_new_branch(push_new_branch):
    mock_project = flexmock(full_repo_name="packit/test-repo-to-generate-events")
    flexmock(ServiceConfig).should_receive("get_project").and_return(mock_project)

    event_object = Parser.parse_event(push_new_branch)

    assert isinstance(event_object, push.Commit)
    assert event_object.repo_namespace == "packit"
    assert event_object.repo_name == "test-repo-to-generate-events"
    assert event_object.commit_sha == "24f660b69e4608f63ddd55d5c5b459f348e5f272"
    assert (
        event_object.project_url
        == "https://v10.next.forgejo.org/packit/test-repo-to-generate-events"
    )
    assert event_object.git_ref == "new-branch"

    assert event_object.project.full_repo_name == "packit/test-repo-to-generate-events"
    assert not event_object.base_project

    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=event_object.base_project,
        project=event_object.project,
        pr_id=None,
        reference="24f660b69e4608f63ddd55d5c5b459f348e5f272",
        fail_when_missing=False,
    ).and_return(
        flexmock(get_package_config_views=dict),
    ).once()
    assert event_object.packages_config


def test_parse_forgejo_push_one_commit(push_with_one_commit):
    mock_project = flexmock(full_repo_name="packit/test-repo-to-generate-events")
    flexmock(ServiceConfig).should_receive("get_project").and_return(mock_project)

    event_object = Parser.parse_event(push_with_one_commit)

    assert isinstance(event_object, push.Commit)
    assert event_object.repo_namespace == "packit"
    assert event_object.repo_name == "test-repo-to-generate-events"
    assert event_object.commit_sha == "c85008a0b44a60370e48a45a9f9d39da5b472e11"
    assert (
        event_object.project_url
        == "https://v10.next.forgejo.org/packit/test-repo-to-generate-events"
    )
    assert event_object.git_ref == "main"

    assert event_object.project.full_repo_name == "packit/test-repo-to-generate-events"
    assert not event_object.base_project

    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=event_object.base_project,
        project=event_object.project,
        pr_id=None,
        reference="c85008a0b44a60370e48a45a9f9d39da5b472e11",
        fail_when_missing=False,
    ).and_return(
        flexmock(get_package_config_views=dict),
    ).once()
    assert event_object.packages_config


def test_parse_forgejo_pr_vs_issue_comment_discrimination(pull_request_comment, issue_comment):
    """
    Test that the parser correctly discriminates between PR comments and issue comments.
    In Forgejo, PRs are treated as special issues, so the parser needs to distinguish
    between them based on the webhook payload structure.
    """
    mock_project = flexmock(
        full_repo_name="packit/test-repo-to-generate-events",
        get_pr=lambda pr_id: flexmock(head_commit="37182f59ccaaa21584e7b580442fd33b60fe3f80"),
        get_releases=list,
        service=flexmock(get_project=lambda namespace, repo: flexmock()),
    )
    flexmock(ServiceConfig).should_receive("get_project").and_return(mock_project)

    # Test PR comment parsing
    pr_comment_event = Parser.parse_event(pull_request_comment)

    assert isinstance(pr_comment_event, pr.Comment)
    assert pr_comment_event.action == PullRequestCommentAction.created
    assert pr_comment_event.pr_id == 2
    assert pr_comment_event.comment == "PR comment"
    assert pr_comment_event.actor == "mfocko"

    # Test issue comment parsing
    issue_comment_event = Parser.parse_event(issue_comment)

    assert isinstance(issue_comment_event, issue.Comment)
    assert issue_comment_event.action == IssueCommentAction.created
    assert issue_comment_event.issue_id == 1
    assert issue_comment_event.comment == "issue comment"
    assert issue_comment_event.actor == "mfocko"

    assert pr_comment_event.event_type() == "forgejo.pr.Comment"
    assert issue_comment_event.event_type() == "forgejo.issue.Comment"

    assert pr_comment_event.project_url == issue_comment_event.project_url
    assert (
        pr_comment_event.project_url
        == "https://v10.next.forgejo.org/packit/test-repo-to-generate-events"
    )

    flexmock(ForgejoProject).should_receive("get_sha_from_tag").never()

    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=pr_comment_event.base_project,
        project=pr_comment_event.project,
        pr_id=2,
        reference="37182f59ccaaa21584e7b580442fd33b60fe3f80",
        fail_when_missing=False,
    ).and_return(
        flexmock(get_package_config_views=dict),
    ).once()

    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=issue_comment_event.base_project,
        project=issue_comment_event.project,
        pr_id=None,
        reference=None,
        fail_when_missing=False,
    ).and_return(
        flexmock(get_package_config_views=dict),
    ).once()

    assert pr_comment_event.packages_config
    assert issue_comment_event.packages_config
