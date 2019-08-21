# MIT License
#
# Copyright (c) 2018-2019 Red Hat, Inc.

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

import json

import pytest
from flexmock import flexmock

from tests.spellbook import DATA_DIR

from packit_service.worker.jobs import SteveJobs
from packit_service.worker.copr_build import CoprBuildHandler
from packit_service.worker.handler import HandlerResults


@pytest.fixture()
def pr_copr_build_comment_event():
    return json.loads(
        (DATA_DIR / "webhooks" / "github_pr_comment_copr_build.json").read_text()
    )


@pytest.fixture()
def pr_build_comment_event():
    return json.loads(
        (DATA_DIR / "webhooks" / "github_pr_comment_build.json").read_text()
    )


@pytest.fixture()
def pr_empty_comment_event():
    return json.loads(
        (DATA_DIR / "webhooks" / "github_issue_comment_empty.json").read_text()
    )


@pytest.fixture()
def pr_packit_only_comment_event():
    return json.loads(
        (DATA_DIR / "webhooks" / "github_issue_comment_packit_only.json").read_text()
    )


@pytest.fixture()
def pr_wrong_packit_comment_event():
    return json.loads(
        (
            DATA_DIR / "webhooks" / "github_issue_comment_wrong_packit_command.json"
        ).read_text()
    )


def test_pr_comment_copr_build_handler(
    mock_pr_comment_functionality, pr_copr_build_comment_event
):
    flexmock(CoprBuildHandler).should_receive("run_copr_build").and_return(
        HandlerResults(success=True, details={})
    )
    steve = SteveJobs()
    results = steve.process_message(pr_copr_build_comment_event)
    assert results.get("jobs", {})
    assert "pull_request_action" in results.get("jobs", {})
    assert "created" in results.get("event", {}).get("action", None)
    assert results.get("event", {}).get("pr_id", None) == 9
    assert "comment" in results.get("trigger", None)
    assert results.get("event", {}).get("comment", None) == "/packit copr-build"
    assert results.get("jobs", {}).get("pull_request_action", {}).get("success")


# def test_pr_comment_build_handler(
#     mock_pr_comment_functionality, pr_build_comment_event
# ):
#     flexmock(PackitAPI).should_receive("build").with_args(
#         dist_git_branch="master"
#     ).once()
#     steve = SteveJobs()
#
#     results = steve.process_message(pr_build_comment_event)
#     assert results.get("jobs", {})
#     assert "pull_request_action" in results.get("jobs", {})
#     assert "created" in results.get("event", {}).get("action", None)
#     assert results.get("event", {}).get("pr_id", None) == 9
#     assert "comment" in results.get("trigger", None)
#     assert results.get("event", {}).get("comment", None) == "/packit build"
#     assert (
#         results.get("jobs", {}).get(PullRequestCommentAction.build, {}).get("success")
#     )
#


def test_pr_comment_empty_handler(
    mock_pr_comment_functionality, pr_empty_comment_event
):
    steve = SteveJobs()

    results = steve.process_message(pr_empty_comment_event)
    assert results.get("jobs", {})
    jobs = results.get("jobs", {})
    event = results.get("event", {})
    assert "pull_request_action" in jobs
    assert "created" in event.get("action", None)
    assert event.get("pr_id", None) == 9
    assert "comment" in results.get("trigger", None)
    assert event.get("comment", None) == ""
    assert not jobs.get("pull_request_action", {}).get("success")
    msg = "PR comment '' is empty."
    assert jobs.get("pull_request_action", {}).get("details", {}).get("msg") == msg


def test_pr_comment_packit_only_handler(
    mock_pr_comment_functionality, pr_packit_only_comment_event
):
    steve = SteveJobs()

    results = steve.process_message(pr_packit_only_comment_event)
    assert results.get("jobs", {})
    jobs = results.get("jobs", {})
    event = results.get("event", {})
    assert "pull_request_action" in jobs
    assert "created" in event.get("action", None)
    assert event.get("pr_id", None) == 9
    assert "comment" in results.get("trigger", None)
    assert event.get("comment", None) == "/packit"
    assert not jobs.get("pull_request_action", {}).get("success")
    msg = "PR comment '/packit' does not contain a packit-service command."
    assert jobs.get("pull_request_action", {}).get("details", {}).get("msg") == msg


def test_pr_comment_wrong_packit_command_handler(
    mock_pr_comment_functionality, pr_wrong_packit_comment_event
):
    steve = SteveJobs()

    results = steve.process_message(pr_wrong_packit_comment_event)
    assert results.get("jobs", {})
    jobs = results.get("jobs", {})
    event = results.get("event", {})
    assert "pull_request_action" in jobs
    assert "created" in event.get("action", None)
    assert event.get("pr_id", None) == 9
    assert "comment" in results.get("trigger", None)
    assert event.get("comment", None) == "/packit foobar"
    assert not jobs.get("pull_request_action", {}).get("success")
    msg = "PR comment '/packit foobar' does not contain a valid packit-service command."
    assert jobs.get("pull_request_action", {}).get("details", {}).get("msg") == msg
