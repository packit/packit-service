# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json
from typing import Optional

import pytest
from flexmock import flexmock

from packit_service.config import ServiceConfig
from packit_service.events.event import Event
from packit_service.events.forgejo.action_run import PullRequest as ActionRunPullRequest
from packit_service.events.forgejo.action_run import Push as ActionRunPush
from packit_service.events.forgejo.issue import Comment as IssueComment
from packit_service.events.forgejo.pr import Action as PullRequest
from packit_service.events.forgejo.pr import Comment as PullRequestComment
from packit_service.events.forgejo.push import Commit
from packit_service.models import (
    ProjectEventModel,
)
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


@pytest.fixture
def forgejo_event(request):
    return request.getfixturevalue(request.param)


@pytest.mark.parametrize(
    "forgejo_event, type",
    [
        ["forgejo_distgit_action_run_pr", ActionRunPullRequest],
        ["forgejo_distgit_action_run_push", ActionRunPush],
        ["forgejo_distgit_issue_comment", IssueComment],
        ["forgejo_distgit_pr_comment", PullRequestComment],
        ["forgejo_distgit_pr", PullRequest],
        ["forgejo_distgit_push", Commit],
    ],
    indirect=["forgejo_event"],
)
def test_forgejo_event_get_dict_is_json_serializable_after_db_cache(forgejo_event, type):
    """
    Test that all supported Forgejo event dictionaries are serializable after database
    caching of the event.db_project_object property. If there are any non-serializable
    properties inside the event dictionary, this test will fail for that given event type.

    It was previously discovered that when `_pull_request` and `_event` properties weren't
    popped from the dictionary of Forgejo pull request events, json.dumps(event_dict)
    would then fail on TypeError: Object of type <event type> is not JSON serializable.
    This test was created to make sure no event type fails on this error.
    """

    object_model = project_event_model = flexmock()
    mocked_method = None

    if type in (PullRequest, PullRequestComment, ActionRunPullRequest):
        mocked_method = "add_pull_request_event"

        project = flexmock(
            namespace="rpms",
            repo="packit",
        )

        service_config = ServiceConfig.get_service_config()
        flexmock(service_config).should_receive("get_project").and_return(project)

    if type == PullRequestComment:
        flexmock(PullRequestComment, repo_name="", repo_namespace="")

    elif type in (Commit, ActionRunPush):
        mocked_method = "add_branch_push_event"

    elif type == IssueComment:
        flexmock(IssueComment, tag_name="")
        mocked_method = "add_issue_event"

    flexmock(ProjectEventModel).should_receive(
        f"{mocked_method}",
    ).and_return((object_model, project_event_model)).once()

    event: Optional[Event] = Parser.parse_event(forgejo_event)

    assert event
    event_dict = event.get_dict()
    assert json.dumps(event_dict)

    _ = event.db_project_object
    event_dict = event.get_dict()
    assert json.dumps(event_dict)
