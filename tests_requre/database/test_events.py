# MIT License
#
# Copyright (c) 2018-2020 Red Hat, Inc.

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

from flexmock import flexmock

from ogr.services.github import GithubProject
from packit_service.models import (
    ProjectReleaseModel,
    GitProjectModel,
    GitBranchModel,
    PullRequestModel,
)
from packit_service.service.events import (
    ReleaseEvent,
    PushGitHubEvent,
    PullRequestEvent,
    PullRequestCommentEvent,
    TestingFarmResultsEvent,
)
from packit_service.worker.parser import Parser


def test_release_event_existing_release(
    clean_before_and_after, release_model, release_event_dict
):
    flexmock(GithubProject).should_receive("get_sha_from_tag").and_return(
        "aksjdaksjdla"
    )

    event_object = Parser.parse_event(release_event_dict)
    assert isinstance(event_object, ReleaseEvent)

    assert event_object.identifier == "v1.0.2"
    assert event_object.git_ref == "v1.0.2"
    assert event_object.commit_sha == "aksjdaksjdla"
    assert event_object.tag_name == "v1.0.2"

    assert isinstance(event_object.db_trigger, ProjectReleaseModel)
    assert event_object.db_trigger == release_model
    assert event_object.db_trigger.tag_name == "v1.0.2"
    assert event_object.db_trigger.commit_hash == "aksjdaksjdla"

    assert isinstance(event_object.db_trigger.project, GitProjectModel)
    assert event_object.db_trigger.project.namespace == "the-namespace"
    assert event_object.db_trigger.project.repo_name == "the-repo-name"


def test_release_event_non_existing_release(clean_before_and_after, release_event_dict):
    flexmock(GithubProject).should_receive("get_sha_from_tag").and_return(
        "aksjdaksjdla"
    )

    event_object = Parser.parse_event(release_event_dict)
    assert isinstance(event_object, ReleaseEvent)

    assert event_object.identifier == "v1.0.2"
    assert event_object.git_ref == "v1.0.2"
    assert event_object.commit_sha == "aksjdaksjdla"
    assert event_object.tag_name == "v1.0.2"

    assert isinstance(event_object.db_trigger, ProjectReleaseModel)
    assert event_object.db_trigger.tag_name == "v1.0.2"
    assert event_object.db_trigger.commit_hash == "aksjdaksjdla"

    assert isinstance(event_object.db_trigger.project, GitProjectModel)
    assert event_object.db_trigger.project.namespace == "the-namespace"
    assert event_object.db_trigger.project.repo_name == "the-repo-name"


def test_push_branch_event_existing_branch(
    clean_before_and_after, branch_model, push_branch_event_dict
):
    event_object = Parser.parse_event(push_branch_event_dict)
    assert isinstance(event_object, PushGitHubEvent)

    assert event_object.identifier == "build-branch"
    assert event_object.git_ref == "build-branch"
    assert event_object.commit_sha == "04885ff850b0fa0e206cd09db73565703d48f99b"

    assert isinstance(event_object.db_trigger, GitBranchModel)
    assert event_object.db_trigger == branch_model
    assert event_object.db_trigger.name == "build-branch"

    assert isinstance(event_object.db_trigger.project, GitProjectModel)
    assert event_object.db_trigger.project.namespace == "the-namespace"
    assert event_object.db_trigger.project.repo_name == "the-repo-name"


def test_push_branch_event_non_existing_branch(
    clean_before_and_after, push_branch_event_dict
):
    event_object = Parser.parse_event(push_branch_event_dict)
    assert isinstance(event_object, PushGitHubEvent)

    assert event_object.identifier == "build-branch"
    assert event_object.git_ref == "build-branch"
    assert event_object.commit_sha == "04885ff850b0fa0e206cd09db73565703d48f99b"

    assert isinstance(event_object.db_trigger, GitBranchModel)
    assert event_object.db_trigger.name == "build-branch"

    assert isinstance(event_object.db_trigger.project, GitProjectModel)
    assert event_object.db_trigger.project.namespace == "the-namespace"
    assert event_object.db_trigger.project.repo_name == "the-repo-name"


def test_pr_event_existing_pr(clean_before_and_after, pr_model, pr_event_dict):
    event_object = Parser.parse_event(pr_event_dict)
    assert isinstance(event_object, PullRequestEvent)

    assert event_object.identifier == "342"
    assert event_object.git_ref is None
    assert event_object.commit_sha == "528b803be6f93e19ca4130bf4976f2800a3004c4"
    assert event_object.pr_id == 342

    assert isinstance(event_object.db_trigger, PullRequestModel)
    assert event_object.db_trigger == pr_model
    assert event_object.db_trigger.pr_id == 342

    assert isinstance(event_object.db_trigger.project, GitProjectModel)
    assert event_object.db_trigger.project.namespace == "the-namespace"
    assert event_object.db_trigger.project.repo_name == "the-repo-name"


def test_pr_event_non_existing_pr(clean_before_and_after, pr_event_dict):
    event_object = Parser.parse_event(pr_event_dict)
    assert isinstance(event_object, PullRequestEvent)

    assert event_object.identifier == "342"
    assert event_object.git_ref is None
    assert event_object.commit_sha == "528b803be6f93e19ca4130bf4976f2800a3004c4"
    assert event_object.pr_id == 342

    assert isinstance(event_object.db_trigger, PullRequestModel)
    assert event_object.db_trigger.pr_id == 342

    assert isinstance(event_object.db_trigger.project, GitProjectModel)
    assert event_object.db_trigger.project.namespace == "the-namespace"
    assert event_object.db_trigger.project.repo_name == "the-repo-name"


def test_pr_comment_event_existing_pr(
    clean_before_and_after, pr_model, pr_comment_event_dict_packit_build
):
    event_object = Parser.parse_event(pr_comment_event_dict_packit_build)
    assert isinstance(event_object, PullRequestCommentEvent)

    assert event_object.identifier == "342"
    assert event_object.commit_sha == ""  # ? Do we want it?
    assert event_object.git_ref is None
    assert event_object.pr_id == 342

    assert isinstance(event_object.db_trigger, PullRequestModel)
    assert event_object.db_trigger == pr_model
    assert event_object.db_trigger.pr_id == 342

    assert isinstance(event_object.db_trigger.project, GitProjectModel)
    assert event_object.db_trigger.project.namespace == "the-namespace"
    assert event_object.db_trigger.project.repo_name == "the-repo-name"


def test_pr_comment_event_non_existing_pr(
    clean_before_and_after, pr_comment_event_dict_packit_build
):
    event_object = Parser.parse_event(pr_comment_event_dict_packit_build)
    assert isinstance(event_object, PullRequestCommentEvent)

    assert event_object.identifier == "342"
    assert event_object.git_ref is None
    assert event_object.commit_sha == ""  # ? Do we want it?
    assert event_object.pr_id == 342

    assert isinstance(event_object.db_trigger, PullRequestModel)
    assert event_object.db_trigger.pr_id == 342

    assert isinstance(event_object.db_trigger.project, GitProjectModel)
    assert event_object.db_trigger.project.namespace == "the-namespace"
    assert event_object.db_trigger.project.repo_name == "the-repo-name"


def test_testing_farm_response_existing_pr(
    clean_before_and_after, pr_model, a_new_test_run_pr, tf_result_dict_pr
):
    event_object = Parser.parse_event(tf_result_dict_pr)
    assert isinstance(event_object, TestingFarmResultsEvent)

    assert event_object.identifier == "687abc76d67d"
    assert event_object.commit_sha == "687abc76d67d"
    assert event_object.git_ref == "687abc76d67d"

    assert isinstance(event_object.db_trigger, PullRequestModel)
    assert event_object.db_trigger == pr_model
    assert event_object.db_trigger.pr_id == 342

    assert isinstance(event_object.db_trigger.project, GitProjectModel)
    assert event_object.db_trigger.project.namespace == "the-namespace"
    assert event_object.db_trigger.project.repo_name == "the-repo-name"


def test_testing_farm_response_non_existing_pr(
    clean_before_and_after, tf_result_dict_pr
):
    event_object = Parser.parse_event(tf_result_dict_pr)
    assert isinstance(event_object, TestingFarmResultsEvent)

    assert event_object.identifier == "687abc76d67d"
    assert event_object.commit_sha == "687abc76d67d"
    assert event_object.git_ref == "687abc76d67d"

    assert not event_object.db_trigger


def test_testing_farm_response_existing_branch_push(
    clean_before_and_after,
    branch_model,
    a_new_test_run_branch_push,
    tf_result_dict_branch_push,
):
    event_object = Parser.parse_event(tf_result_dict_branch_push)
    assert isinstance(event_object, TestingFarmResultsEvent)

    assert event_object.identifier == "687abc76d67d"
    assert event_object.commit_sha == "687abc76d67d"
    assert event_object.git_ref == "687abc76d67d"

    assert isinstance(event_object.db_trigger, GitBranchModel)
    assert event_object.db_trigger == branch_model
    assert event_object.db_trigger.name == "build-branch"

    assert isinstance(event_object.db_trigger.project, GitProjectModel)
    assert event_object.db_trigger.project.namespace == "the-namespace"
    assert event_object.db_trigger.project.repo_name == "the-repo-name"


def test_testing_farm_response_non_existing_branch_push(
    clean_before_and_after, tf_result_dict_branch_push
):
    event_object = Parser.parse_event(tf_result_dict_branch_push)

    assert isinstance(event_object, TestingFarmResultsEvent)

    # For backwards compatibility, unknown results are treated as pull-requests
    assert event_object.identifier == "687abc76d67d"
    assert event_object.commit_sha == "687abc76d67d"
    assert event_object.git_ref == "687abc76d67d"

    assert not event_object.db_trigger
