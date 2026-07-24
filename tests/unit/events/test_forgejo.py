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
def forgejo_push():
    with open(DATA_DIR / "fedmsg" / "forgejo_push.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def forgejo_pr():
    with open(DATA_DIR / "fedmsg" / "forgejo_pr.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def forgejo_pr_comment():
    with open(DATA_DIR / "fedmsg" / "forgejo_pr_comment.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def forgejo_issue_comment():
    with open(DATA_DIR / "fedmsg" / "forgejo_issue_comment.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def forgejo_action_run_pr():
    with open(DATA_DIR / "fedmsg" / "forgejo_action_run_pr.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def forgejo_action_run_push():
    with open(DATA_DIR / "fedmsg" / "forgejo_action_run_push.json") as outfile:
        return json.load(outfile)


def test_parse_forgejo_push(forgejo_push):
    event_object = Parser.parse_event(forgejo_push)

    assert isinstance(event_object, forgejo.push.Commit)

    assert event_object.repo_namespace == "infra"
    assert event_object.repo_name == "docs"
    assert event_object.git_ref == "main"
    assert event_object.commit_sha == "139828ad0b8fd3c5d172ccc0fc695a6d7a98f4c5"
    assert event_object.commit_sha_before == "8848429b902c7747e1b36af8b5225117b6254fb5"
    assert event_object.committer == "zlopez"
    assert event_object.project_url == "https://forge.fedoraproject.org/infra/docs"


def test_parse_forgejo_pr(forgejo_pr):
    event_object = Parser.parse_event(forgejo_pr)

    assert isinstance(event_object, forgejo.pr.Action)

    assert event_object.action.value == "synchronize"
    assert event_object.pr_id == 3501
    assert event_object.base_repo_namespace == "jpodivin"
    assert event_object.base_repo_name == "ansible"
    assert event_object.base_ref == "ld_credentials"
    assert event_object.target_repo_namespace == "infra"
    assert event_object.target_repo_name == "ansible"
    assert event_object.target_branch == "main"
    assert event_object.project_url == "https://forge.fedoraproject.org/infra/ansible"
    assert event_object.commit_sha == "1a69eb79bbfa26ec63e95863cf15dcd77f985ede"
    assert event_object.actor == "jpodivin"
    assert event_object.body.startswith("Tokens, database password and other values will")

    project = flexmock(default_branch="main")
    service = flexmock()
    service.should_receive("get_project").and_return(project)
    flexmock(ServiceConfig).should_receive("get_project").and_return(
        flexmock(default_branch="main", service=service)
    )

    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=project,
        project=event_object.project,
        reference="1a69eb79bbfa26ec63e95863cf15dcd77f985ede",
        pr_id=3501,
        fail_when_missing=False,
    ).and_return(
        flexmock(get_package_config_views=dict),
    ).once()
    assert event_object.packages_config


def test_parse_forgejo_pr_comment(forgejo_pr_comment):
    event_object = Parser.parse_event(forgejo_pr_comment)

    assert isinstance(event_object, forgejo.pr.Comment)

    assert event_object.action.value == "created"
    assert event_object.pr_id == 3366
    assert event_object.base_ref == "main"
    assert event_object.base_repo_namespace == "jgroman"
    assert event_object.base_repo_name == "ansible"
    assert event_object.target_repo_namespace == "infra"
    assert event_object.target_repo_name == "ansible"
    assert event_object.project_url == "https://forge.fedoraproject.org/infra/ansible"
    assert event_object.source_project_url == "https://forge.fedoraproject.org/jgroman/ansible"
    assert event_object.actor == "kparal"
    assert event_object.comment.startswith("Even though I haven't studied")
    assert event_object.comment_id == 759973
    assert event_object.commit_sha == "d0bb319af8dfa9944649231d09573ca02650ff9d"

    flexmock(ServiceConfig).should_receive("get_project").and_return(
        flexmock(default_branch="main")
    )
    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=None,
        project=event_object.project,
        reference="main",
        pr_id=None,
        fail_when_missing=False,
    ).and_return(
        flexmock(get_package_config_views=dict),
    ).once()
    assert event_object.packages_config


def test_parse_forgejo_issue_comment(forgejo_issue_comment):
    event_object = Parser.parse_event(forgejo_issue_comment)

    assert isinstance(event_object, forgejo.issue.Comment)

    assert event_object.action.value == "created"
    assert event_object.issue_id == 13131
    assert event_object.base_ref == "main"
    assert event_object.repo_namespace == "infra"
    assert event_object.repo_name == "tickets"
    assert event_object.target_repo == "infra/tickets"
    assert event_object.project_url == "https://forge.fedoraproject.org/infra/tickets"
    assert event_object.actor == "zlopez"
    assert event_object.comment == "User disabled in FAS."
    assert event_object.comment_id == 1062170


def test_parse_forgejo_action_run_pr(forgejo_action_run_pr):
    event_object = Parser.parse_event(forgejo_action_run_pr)

    assert isinstance(event_object, forgejo.action_run.PullRequest)

    assert event_object.actor == "smoliicek"
    assert event_object.title == "add app-actions to elnbuildsync"
    assert event_object.comment is None
    assert event_object.status == "success"
    assert event_object.date_updated == "2026-07-09T19:31:09Z"
    assert event_object.url == "https://forge.fedoraproject.org/infra/ansible/actions/runs/873"
    assert event_object.commit_sha == "05b2c16808c712d2cf2300ea6dd15732b1b76eae"
    assert event_object.pr_id == 3506
    assert event_object.pr_url == "https://forge.fedoraproject.org/infra/ansible/pulls/3506"
    assert event_object.pr_source_branch == "ocp-migrate/eln"
    assert event_object.project_url == "https://forge.fedoraproject.org/infra/ansible"
    assert event_object.project_name == "ansible"
    assert event_object.project_namespace == "infra"


def test_parse_forgejo_action_run_push(forgejo_action_run_push):
    event_object = Parser.parse_event(forgejo_action_run_push)

    assert isinstance(event_object, forgejo.action_run.Push)

    assert event_object.actor == "ryanlerch"
    assert event_object.title == "forgefiler: fix config not applying"
    assert event_object.comment is None
    assert event_object.status == "success"
    assert event_object.date_updated == "2026-07-14T00:21:01Z"
    assert event_object.url == "https://forge.fedoraproject.org/infra/ansible/actions/runs/961"
    assert event_object.commit_sha == "b2e70a1df15430d1118799bfd00360e47ec8e545"
    assert event_object.git_ref == "main"
    assert event_object.pr_id is None
    assert event_object.project_url == "https://forge.fedoraproject.org/infra/ansible"
    assert event_object.project_name == "ansible"
    assert event_object.project_namespace == "infra"
