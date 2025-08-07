# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json

import pytest

from packit_service.events import forgejo
from packit_service.events.enums import PullRequestAction
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
def forgejo_issue_comment():
    with open(DATA_DIR / "fedmsg" / "forgejo_issue_comment.json") as outfile:
        return json.load(outfile)


def test_parse_forgejo_push(forgejo_push):
    event_object = Parser.parse_event(forgejo_push)

    assert isinstance(event_object, forgejo.push.Commit)
    # the repo in the datagrepper payload is probably infra/docs or similar
    payload = forgejo_push.get("body", {})
    assert event_object.repo_namespace == payload["repository"]["owner"]["login"]
    assert event_object.repo_name == payload["repository"]["name"]
    assert event_object.commit_sha == payload["after"]
    assert event_object.commit_sha_before == payload["before"]
    assert event_object.project_url == payload["repository"]["html_url"]


def test_parse_forgejo_pr(forgejo_pr):
    event_object = Parser.parse_event(forgejo_pr)

    assert isinstance(event_object, forgejo.pr.Action)
    payload = forgejo_pr.get("body", {})
    action = payload["action"]
    action = "synchronize" if action == "synchronized" else action

    assert event_object.action == PullRequestAction[action]
    assert event_object.pr_id == payload["pull_request"]["number"]
    assert event_object.actor == payload["pull_request"]["user"]["login"]


def test_parse_forgejo_issue_comment(forgejo_issue_comment):
    event_object = Parser.parse_event(forgejo_issue_comment)

    payload = forgejo_issue_comment.get("body", {})
    if payload["issue"].get("pull_request") is not None:
        assert isinstance(event_object, forgejo.pr.Comment)
        assert event_object.pr_id == payload["issue"]["number"]
    else:
        assert isinstance(event_object, forgejo.issue.Comment)
        assert event_object.issue_id == payload["issue"]["number"]
    assert event_object.actor == payload["comment"]["user"]["login"]
    assert event_object.comment == payload["comment"]["body"]
