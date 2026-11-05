# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json

import pytest
from flexmock import flexmock

from packit_service.config import ServiceConfig
from packit_service.events import forgejo
from packit_service.package_config_getter import PackageConfigGetter
from packit_service.worker.parser import Parser
from tests.spellbook import DATA_DIR


@pytest.fixture()
def forgejo_distgit_push():
    with open(DATA_DIR / "fedmsg" / "forgejo_dg_push.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def forgejo_distgit_pr():
    with open(DATA_DIR / "fedmsg" / "forgejo_dg_pr.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def forgejo_distgit_pr_comment():
    with open(DATA_DIR / "fedmsg" / "forgejo_dg_pr_comment.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def forgejo_distgit_issue_comment():
    with open(DATA_DIR / "fedmsg" / "forgejo_dg_issue_comment.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def forgejo_distgit_action_run_pr():
    with open(DATA_DIR / "fedmsg" / "forgejo_dg_action_run_pr.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def forgejo_distgit_action_run_push():
    with open(DATA_DIR / "fedmsg" / "forgejo_dg_action_run_push.json") as outfile:
        return json.load(outfile)


def test_parse_forgejo_distgit_push(forgejo_distgit_push):
    event_object = Parser.parse_event(forgejo_distgit_push)

    assert isinstance(event_object, forgejo.push.Commit)

    assert event_object.repo_namespace == "betulependule"
    assert event_object.repo_name == "packit"
    assert event_object.git_ref == "forgejo-testing-branch"
    assert event_object.commit_sha == "7ba5d80f7678c32f9baba883c12a9d3eebb3b208"
    assert event_object.commit_sha_before == "34fcb6811c3d027becf3f26c9b6872ace599afaf"
    assert event_object.committer == "betulependule"
    assert (
        event_object.project_url == "https://forgejo-src.stg.fedoraproject.org/betulependule/packit"
    )


def test_parse_forgejo_distgit_pr(forgejo_distgit_pr):
    event_object = Parser.parse_event(forgejo_distgit_pr)

    assert isinstance(event_object, forgejo.pr.Action)

    assert event_object.action.value == "opened"
    assert event_object.pr_id == 1
    assert event_object.base_repo_namespace == "betulependule"
    assert event_object.base_repo_name == "packit"
    assert event_object.base_ref == "forgejo-testing-branch"
    assert event_object.target_repo_namespace == "rpms"
    assert event_object.target_repo_name == "packit"
    assert event_object.target_branch == "rawhide"
    assert event_object.project_url == "https://forgejo-src.stg.fedoraproject.org/rpms/packit"
    assert event_object.commit_sha == "34fcb6811c3d027becf3f26c9b6872ace599afaf"
    assert event_object.actor == "betulependule"
    assert event_object.body.startswith("Hello world, this is a new test.")

    project = flexmock(default_branch="rawhide")
    service = flexmock()
    service.should_receive("get_project").and_return(project)
    flexmock(ServiceConfig).should_receive("get_project").and_return(
        flexmock(default_branch="rawhide", service=service)
    )

    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=project,
        project=event_object.project,
        reference="34fcb6811c3d027becf3f26c9b6872ace599afaf",
        pr_id=1,
        fail_when_missing=False,
    ).and_return(
        flexmock(get_package_config_views=dict),
    ).once()
    assert event_object.packages_config


def test_parse_forgejo_distgit_pr_comment(forgejo_distgit_pr_comment):
    event_object = Parser.parse_event(forgejo_distgit_pr_comment)

    assert isinstance(event_object, forgejo.pr.Comment)

    assert event_object.action.value == "created"
    assert event_object.pr_id == 1
    assert event_object.base_ref == "forgejo-testing-branch"
    assert event_object.base_repo_namespace == "betulependule"
    assert event_object.base_repo_name == "packit"
    assert event_object.target_repo_namespace == "rpms"
    assert event_object.target_repo_name == "packit"
    assert event_object.project_url == "https://forgejo-src.stg.fedoraproject.org/rpms/packit"
    assert (
        event_object.source_project_url
        == "https://forgejo-src.stg.fedoraproject.org/betulependule/packit"
    )
    assert event_object.actor == "betulependule"
    assert event_object.comment.startswith("We are the lucky ones")
    assert event_object.comment_id == 24896
    assert event_object.commit_sha == "7ba5d80f7678c32f9baba883c12a9d3eebb3b208"

    flexmock(ServiceConfig).should_receive("get_project").and_return(
        flexmock(default_branch="rawhide")
    )
    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=None,
        project=event_object.project,
        reference="rawhide",
        pr_id=None,
        fail_when_missing=False,
    ).and_return(
        flexmock(get_package_config_views=dict),
    ).once()
    assert event_object.packages_config


def test_parse_forgejo_distgit_issue_comment(forgejo_distgit_issue_comment):
    event_object = Parser.parse_event(forgejo_distgit_issue_comment)

    assert isinstance(event_object, forgejo.issue.Comment)

    assert event_object.action.value == "created"
    assert event_object.issue_id == 63
    assert event_object.base_ref == "rawhide"
    assert event_object.repo_namespace == "rpms"
    assert event_object.repo_name == "packit"
    assert event_object.target_repo == "rpms/packit"
    assert event_object.project_url == "https://forgejo-src.stg.fedoraproject.org/rpms/packit"
    assert event_object.actor == "betulependule"
    assert event_object.comment == "Testing comment: It's almost midnight"
    assert event_object.comment_id == 2347986


def test_parse_forgejo_distgit_action_run_pr(forgejo_distgit_action_run_pr):
    event_object = Parser.parse_event(forgejo_distgit_action_run_pr)

    assert isinstance(event_object, forgejo.action_run.PullRequest)

    assert event_object.actor == "betulependule"
    assert event_object.title == "Update .forgejo/workflows/demo.yaml"
    assert event_object.comment is None
    assert event_object.status == "success"
    assert event_object.date_updated == "2026-10-12T14:41:18Z"
    assert (
        event_object.url
        == "https://forgejo-src.stg.fedoraproject.org/betulependule/packit/actions/runs/4"
    )
    assert event_object.commit_sha == "4fe4a244e9e53b4e8d3dd57e03dfeed92a6ee147"
    assert event_object.pr_id == 2
    assert (
        event_object.pr_url
        == "https://forgejo-src.stg.fedoraproject.org/betulependule/packit/pulls/2"
    )
    assert event_object.pr_source_branch == "betulependule-patch-1"
    assert (
        event_object.project_url == "https://forgejo-src.stg.fedoraproject.org/betulependule/packit"
    )
    assert event_object.project_name == "packit"
    assert event_object.project_namespace == "betulependule"


def test_parse_forgejo_distgit_action_run_push(forgejo_distgit_action_run_push):
    event_object = Parser.parse_event(forgejo_distgit_action_run_push)

    assert isinstance(event_object, forgejo.action_run.Push)

    assert event_object.actor == "betulependule"
    assert event_object.title == "Update .forgejo/workflows/demo.yaml"
    assert event_object.comment is None
    assert event_object.status == "success"
    assert event_object.date_updated == "2026-10-12T14:41:18Z"
    assert (
        event_object.url
        == "https://forgejo-src.stg.fedoraproject.org/betulependule/packit/actions/runs/3"
    )
    assert event_object.commit_sha == "4fe4a244e9e53b4e8d3dd57e03dfeed92a6ee147"
    assert event_object.git_ref == "betulependule-patch-1"
    assert event_object.pr_id is None
    assert (
        event_object.project_url == "https://forgejo-src.stg.fedoraproject.org/betulependule/packit"
    )
    assert event_object.project_name == "packit"
    assert event_object.project_namespace == "betulependule"
