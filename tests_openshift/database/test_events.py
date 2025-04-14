# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
from operator import attrgetter

from flexmock import flexmock
from ogr.services.github import GithubProject

from packit_service.constants import KojiTaskState
from packit_service.events import (
    github,
    gitlab,
    koji,
    testing_farm,
)
from packit_service.models import (
    BuildStatus,
    CoprBuildTargetModel,
    GitBranchModel,
    GitProjectModel,
    ProjectReleaseModel,
    PullRequestModel,
    TestingFarmResult,
    TFTTestRunTargetModel,
    filter_most_recent_target_names_by_status,
)
from packit_service.worker.helpers.testing_farm import TestingFarmJobHelper
from packit_service.worker.parser import Parser
from tests_openshift.conftest import SampleValues


def test_release_event_existing_release(
    clean_before_and_after,
    release_model,
    release_event_dict,
):
    flexmock(GithubProject).should_receive("get_sha_from_tag").and_return(
        SampleValues.commit_sha,
    )

    event_object = Parser.parse_event(release_event_dict)
    assert isinstance(event_object, github.release.Release)

    assert event_object.identifier == "v1.0.2"
    assert event_object.git_ref == "v1.0.2"
    assert event_object.commit_sha == "80201a74d96c"
    assert event_object.tag_name == "v1.0.2"

    assert isinstance(event_object.db_project_object, ProjectReleaseModel)
    assert event_object.db_project_object == release_model
    assert event_object.db_project_object.tag_name == "v1.0.2"
    assert event_object.db_project_object.commit_hash == "80201a74d96c"

    assert isinstance(event_object.db_project_object.project, GitProjectModel)
    assert event_object.db_project_object.project.namespace == "the-namespace"
    assert event_object.db_project_object.project.repo_name == "the-repo-name"


def test_release_event_non_existing_release(clean_before_and_after, release_event_dict):
    flexmock(GithubProject).should_receive("get_sha_from_tag").and_return(
        SampleValues.commit_sha,
    )

    event_object = Parser.parse_event(release_event_dict)
    assert isinstance(event_object, github.release.Release)

    assert event_object.identifier == "v1.0.2"
    assert event_object.git_ref == "v1.0.2"
    assert event_object.commit_sha == "80201a74d96c"
    assert event_object.tag_name == "v1.0.2"

    assert isinstance(event_object.db_project_object, ProjectReleaseModel)
    assert event_object.db_project_object.tag_name == "v1.0.2"
    assert event_object.db_project_object.commit_hash == "80201a74d96c"

    assert isinstance(event_object.db_project_object.project, GitProjectModel)
    assert event_object.db_project_object.project.namespace == "the-namespace"
    assert event_object.db_project_object.project.repo_name == "the-repo-name"


def test_push_branch_event_existing_branch(
    clean_before_and_after,
    branch_model,
    push_branch_event_dict,
):
    event_object = Parser.parse_event(push_branch_event_dict)
    assert isinstance(event_object, github.push.Commit)

    assert event_object.identifier == "build-branch"
    assert event_object.git_ref == "build-branch"
    assert event_object.commit_sha == "04885ff850b0fa0e206cd09db73565703d48f99b"

    assert isinstance(event_object.db_project_object, GitBranchModel)
    assert event_object.db_project_object == branch_model
    assert event_object.db_project_object.name == "build-branch"

    assert isinstance(event_object.db_project_object.project, GitProjectModel)
    assert event_object.db_project_object.project.namespace == "the-namespace"
    assert event_object.db_project_object.project.repo_name == "the-repo-name"


def test_push_branch_event_non_existing_branch(
    clean_before_and_after,
    push_branch_event_dict,
):
    event_object = Parser.parse_event(push_branch_event_dict)
    assert isinstance(event_object, github.push.Commit)

    assert event_object.identifier == "build-branch"
    assert event_object.git_ref == "build-branch"
    assert event_object.commit_sha == "04885ff850b0fa0e206cd09db73565703d48f99b"

    assert isinstance(event_object.db_project_object, GitBranchModel)
    assert event_object.db_project_object.name == "build-branch"

    assert isinstance(event_object.db_project_object.project, GitProjectModel)
    assert event_object.db_project_object.project.namespace == "the-namespace"
    assert event_object.db_project_object.project.repo_name == "the-repo-name"


def test_pr_event_existing_pr(clean_before_and_after, pr_model, pr_event_dict):
    event_object = Parser.parse_event(pr_event_dict)
    assert isinstance(event_object, github.pr.Action)

    assert event_object.identifier == "342"
    assert event_object.git_ref is None
    assert event_object.commit_sha == "528b803be6f93e19ca4130bf4976f2800a3004c4"
    assert event_object.pr_id == 342

    assert isinstance(event_object.db_project_object, PullRequestModel)
    assert event_object.db_project_object == pr_model
    assert event_object.db_project_object.pr_id == 342

    assert isinstance(event_object.db_project_object.project, GitProjectModel)
    assert event_object.db_project_object.project.namespace == "the-namespace"
    assert event_object.db_project_object.project.repo_name == "the-repo-name"


def test_mr_event_existing_mr(clean_before_and_after, mr_model, mr_event_dict):
    event_object = Parser.parse_event(mr_event_dict)
    assert isinstance(event_object, gitlab.mr.Action)

    assert event_object.git_ref is None
    assert event_object.commit_sha == "45e272a57335e4e308f3176df6e9226a9e7805a9"
    assert event_object.pr_id == 2

    assert isinstance(event_object.db_project_object, PullRequestModel)
    assert event_object.db_project_object == mr_model
    assert event_object.db_project_object.pr_id == 2

    assert isinstance(event_object.db_project_object.project, GitProjectModel)
    assert event_object.db_project_object.project.namespace == "the-namespace"
    assert event_object.db_project_object.project.repo_name == "repo-name"


def test_merge_request_comment_event(clean_before_and_after, mr_comment_event_dict):
    event_object = Parser.parse_event(mr_comment_event_dict)
    assert isinstance(event_object, gitlab.mr.Comment)

    assert event_object.pr_id == 2
    assert event_object.identifier == "2"
    assert event_object.git_ref is None

    assert event_object.commit_sha == "45e272a57335e4e308f3176df6e9226a9e7805a9"

    assert isinstance(event_object.db_project_object, PullRequestModel)
    assert event_object.db_project_object.pr_id == 2

    assert isinstance(event_object.db_project_object.project, GitProjectModel)
    assert event_object.db_project_object.project.namespace == "testing-packit"
    assert event_object.db_project_object.project.repo_name == "hello-there"


def test_push_gitlab_event(
    clean_before_and_after,
    branch_model_gitlab,
    push_gitlab_event_dict,
):
    event_object = Parser.parse_event(push_gitlab_event_dict)
    assert isinstance(event_object, gitlab.push.Commit)

    assert event_object.identifier == "build-branch"
    assert event_object.git_ref == "build-branch"
    assert event_object.commit_sha == "cb2859505e101785097e082529dced35bbee0c8f"

    assert isinstance(event_object.db_project_object, GitBranchModel)
    assert event_object.db_project_object == branch_model_gitlab
    assert event_object.db_project_object.name == "build-branch"

    assert isinstance(event_object.db_project_object.project, GitProjectModel)
    assert event_object.db_project_object.project.namespace == "the-namespace"
    assert event_object.db_project_object.project.repo_name == "repo-name"


def test_pr_event_non_existing_pr(clean_before_and_after, pr_event_dict):
    event_object = Parser.parse_event(pr_event_dict)
    assert isinstance(event_object, github.pr.Action)

    assert event_object.identifier == "342"
    assert event_object.git_ref is None
    assert event_object.commit_sha == "528b803be6f93e19ca4130bf4976f2800a3004c4"
    assert event_object.pr_id == 342

    assert isinstance(event_object.db_project_object, PullRequestModel)
    assert event_object.db_project_object.pr_id == 342

    assert isinstance(event_object.db_project_object.project, GitProjectModel)
    assert event_object.db_project_object.project.namespace == "the-namespace"
    assert event_object.db_project_object.project.repo_name == "the-repo-name"


def test_pr_comment_event_existing_pr(
    clean_before_and_after,
    pr_model,
    pr_comment_event_dict_packit_build,
):
    event_object = Parser.parse_event(pr_comment_event_dict_packit_build)
    assert isinstance(event_object, github.pr.Comment)

    assert event_object.identifier == "342"
    assert event_object.git_ref is None
    assert event_object.pr_id == 342
    assert event_object.project_url == "https://github.com/the-namespace/the-repo-name"

    flexmock(GithubProject).should_receive("get_pr").with_args(342).and_return(
        flexmock(head_commit="12345"),
    )
    assert event_object.commit_sha == "12345"

    assert isinstance(event_object.db_project_object, PullRequestModel)
    assert event_object.db_project_object == pr_model
    assert event_object.db_project_object.pr_id == 342

    assert isinstance(event_object.db_project_object.project, GitProjectModel)
    assert event_object.db_project_object.project.namespace == "the-namespace"
    assert event_object.db_project_object.project.repo_name == "the-repo-name"


def test_pr_comment_event_non_existing_pr(
    clean_before_and_after,
    pr_comment_event_dict_packit_build,
):
    event_object = Parser.parse_event(pr_comment_event_dict_packit_build)
    assert isinstance(event_object, github.pr.Comment)

    assert event_object.identifier == "342"
    assert event_object.git_ref is None
    assert event_object.pr_id == 342

    flexmock(GithubProject).should_receive("get_pr").with_args(342).and_return(
        flexmock(head_commit="12345"),
    )
    assert event_object.commit_sha == "12345"

    assert isinstance(event_object.db_project_object, PullRequestModel)
    assert event_object.db_project_object.pr_id == 342

    assert isinstance(event_object.db_project_object.project, GitProjectModel)
    assert event_object.db_project_object.project.namespace == "the-namespace"
    assert event_object.db_project_object.project.repo_name == "the-repo-name"


def test_testing_farm_response_existing_pr(
    clean_before_and_after,
    pr_model,
    a_new_test_run_pr,
    tf_notification,
    tf_result,
):
    flexmock(TestingFarmJobHelper).should_receive("get_request_details").with_args(
        SampleValues.pipeline_id,
    ).and_return(tf_result)
    event_object = Parser.parse_event(tf_notification)
    assert isinstance(event_object, testing_farm.Result)

    assert event_object.commit_sha == SampleValues.commit_sha

    assert isinstance(event_object.db_project_object, PullRequestModel)
    assert event_object.db_project_object == pr_model
    assert event_object.db_project_object.pr_id == 342

    assert isinstance(event_object.db_project_object.project, GitProjectModel)
    assert event_object.db_project_object.project.namespace == "the-namespace"
    assert event_object.db_project_object.project.repo_name == "the-repo-name"


def test_testing_farm_response_non_existing_pr(
    clean_before_and_after,
    tf_notification,
    tf_result,
):
    flexmock(TestingFarmJobHelper).should_receive("get_request_details").with_args(
        SampleValues.pipeline_id,
    ).and_return(tf_result)
    event_object = Parser.parse_event(tf_notification)
    assert isinstance(event_object, testing_farm.Result)

    assert event_object.commit_sha == SampleValues.different_commit_sha

    assert not event_object.db_project_object


def test_testing_farm_response_existing_branch_push(
    clean_before_and_after,
    branch_project_event_model,
    a_new_test_run_branch_push,
    tf_notification,
    tf_result,
):
    flexmock(TestingFarmJobHelper).should_receive("get_request_details").with_args(
        SampleValues.pipeline_id,
    ).and_return(tf_result)
    branch_model = branch_project_event_model.get_project_event_object()
    event_object = Parser.parse_event(tf_notification)
    assert isinstance(event_object, testing_farm.Result)

    assert event_object.commit_sha == SampleValues.commit_sha

    assert isinstance(event_object.db_project_object, GitBranchModel)
    assert event_object.db_project_object == branch_model
    assert event_object.db_project_object.name == "build-branch"

    assert isinstance(event_object.db_project_object.project, GitProjectModel)
    assert event_object.db_project_object.project.namespace == "the-namespace"
    assert event_object.db_project_object.project.repo_name == "the-repo-name"


def test_testing_farm_response_non_existing_branch_push(
    clean_before_and_after,
    tf_notification,
    tf_result,
):
    flexmock(TestingFarmJobHelper).should_receive("get_request_details").with_args(
        SampleValues.pipeline_id,
    ).and_return(tf_result)
    event_object = Parser.parse_event(tf_notification)

    assert isinstance(event_object, testing_farm.Result)

    # For backwards compatibility, unknown results are treated as pull-requests
    assert event_object.commit_sha == SampleValues.different_commit_sha

    assert not event_object.db_project_object


def test_koji_build_scratch_start(
    clean_before_and_after,
    pr_model,
    a_koji_build_for_pr,
    koji_build_scratch_start_dict,
):
    event_object = Parser.parse_event(koji_build_scratch_start_dict)
    assert isinstance(event_object, koji.result.Task)

    assert event_object.task_id == SampleValues.build_id
    assert event_object.state == KojiTaskState.open

    assert isinstance(event_object.db_project_object, PullRequestModel)
    assert event_object.db_project_object == pr_model
    assert event_object.db_project_object.pr_id == 342

    assert isinstance(event_object.db_project_object.project, GitProjectModel)
    assert event_object.db_project_object.project.namespace == "the-namespace"
    assert event_object.db_project_object.project.repo_name == "the-repo-name"


def test_koji_build_scratch_end(
    clean_before_and_after,
    pr_model,
    a_koji_build_for_pr,
    koji_build_scratch_end_dict,
):
    event_object = Parser.parse_event(koji_build_scratch_end_dict)
    assert isinstance(event_object, koji.result.Task)

    assert event_object.task_id == SampleValues.build_id
    assert event_object.state == KojiTaskState.closed

    assert isinstance(event_object.db_project_object, PullRequestModel)
    assert event_object.db_project_object == pr_model
    assert event_object.db_project_object.pr_id == 342

    assert isinstance(event_object.db_project_object.project, GitProjectModel)
    assert event_object.db_project_object.project.namespace == "the-namespace"
    assert event_object.db_project_object.project.repo_name == "the-repo-name"


def test_parse_check_rerun_commit(
    clean_before_and_after,
    branch_model,
    branch_project_event_model,
    check_rerun_event_dict_commit,
):
    check_rerun_event_dict_commit["check_run"]["external_id"] = str(
        branch_project_event_model.id,
    )
    event_object = Parser.parse_event(check_rerun_event_dict_commit)

    assert isinstance(event_object, github.check.Commit)
    assert event_object.repo_namespace == "packit"
    assert event_object.repo_name == "hello-world"
    assert event_object.commit_sha == "0e5d8b51fd5dfa460605e1497d22a76d65c6d7fd"
    assert event_object.project_url == "https://github.com/packit/hello-world"
    assert event_object.git_ref == branch_model.name
    assert event_object.identifier == branch_model.name
    assert event_object.check_name_job == "testing-farm"
    assert event_object.check_name_target == "fedora-rawhide-x86_64"

    assert isinstance(event_object.project, GithubProject)
    assert event_object.project.full_repo_name == "packit/hello-world"
    assert not event_object.base_project
    assert event_object.tests_targets_override == {("fedora-rawhide-x86_64", None)}


def test_parse_check_rerun_pull_request(
    clean_before_and_after,
    pr_model,
    pr_project_event_model,
    check_rerun_event_dict_commit,
):
    check_rerun_event_dict_commit["check_run"]["external_id"] = str(
        pr_project_event_model.id,
    )
    event_object = Parser.parse_event(check_rerun_event_dict_commit)

    assert isinstance(event_object, github.check.PullRequest)
    assert event_object.repo_namespace == "packit"
    assert event_object.repo_name == "hello-world"
    assert event_object.commit_sha == "0e5d8b51fd5dfa460605e1497d22a76d65c6d7fd"
    assert event_object.project_url == "https://github.com/packit/hello-world"
    assert event_object.pr_id == pr_model.pr_id
    assert event_object.identifier == str(pr_model.pr_id)
    assert isinstance(event_object.project, GithubProject)
    assert event_object.project.full_repo_name == "packit/hello-world"
    assert (
        not event_object.base_project  # With Github app, we cannot work with fork repo
    )
    assert event_object.check_name_job == "testing-farm"
    assert event_object.check_name_target == "fedora-rawhide-x86_64"
    assert event_object.tests_targets_override == {("fedora-rawhide-x86_64", None)}


def test_parse_check_rerun_release(
    clean_before_and_after,
    release_model,
    release_project_event_model,
    check_rerun_event_dict_commit,
):
    check_rerun_event_dict_commit["check_run"]["external_id"] = str(
        release_project_event_model.id,
    )
    event_object = Parser.parse_event(check_rerun_event_dict_commit)

    assert isinstance(event_object, github.check.Release)
    assert event_object.repo_namespace == "packit"
    assert event_object.repo_name == "hello-world"
    assert event_object.commit_sha == "0e5d8b51fd5dfa460605e1497d22a76d65c6d7fd"
    assert event_object.project_url == "https://github.com/packit/hello-world"
    assert event_object.tag_name == release_model.tag_name
    assert event_object.git_ref == release_model.tag_name
    assert event_object.identifier == release_model.tag_name
    assert isinstance(event_object.project, GithubProject)
    assert event_object.project.full_repo_name == "packit/hello-world"
    assert (
        not event_object.base_project  # With Github app, we cannot work with fork repo
    )
    assert event_object.check_name_job == "testing-farm"
    assert event_object.check_name_target == "fedora-rawhide-x86_64"
    assert event_object.tests_targets_override == {("fedora-rawhide-x86_64", None)}


def test_filter_failed_models_targets_copr(
    clean_before_and_after,
    multiple_copr_builds,
):
    builds_list = list(
        CoprBuildTargetModel.get_all_by(
            project_name=SampleValues.project,
            commit_sha=SampleValues.ref,
        ),
    )
    assert len(builds_list) == 3

    # these targets should be different
    assert builds_list[0].target != builds_list[2].target
    # 2 builds with failed status and one with success
    builds_list[0].set_status(BuildStatus.failure)
    builds_list[1].set_status(BuildStatus.failure)
    builds_list[2].set_status(BuildStatus.failure)

    filtered_models = filter_most_recent_target_names_by_status(
        models=builds_list,
        statuses_to_filter_with=[BuildStatus.failure],
    )

    assert len(filtered_models) == 2  # we don't do duplicate models here

    most_recent_duplicate = max(builds_list[:2], key=attrgetter("build_submitted_time"))
    assert (most_recent_duplicate.target, most_recent_duplicate.identifier) in filtered_models


def test_filter_failed_models_targets_tf(
    clean_before_and_after,
    multiple_new_test_runs,
):
    test_list = list(
        TFTTestRunTargetModel.get_all_by_commit_target(
            commit_sha=SampleValues.commit_sha,
        ),
    )
    assert len(test_list) == 3

    # 2 builds with failed status and one with success
    test_list[0].set_status(TestingFarmResult.failed)
    test_list[1].set_status(TestingFarmResult.error)
    test_list[2].set_status(TestingFarmResult.failed)

    filtered_models = filter_most_recent_target_names_by_status(
        models=test_list,
        statuses_to_filter_with=[
            TestingFarmResult.failed,
            TestingFarmResult.error,
        ],
    )

    assert len(filtered_models) == 2  # we don't do duplicates here

    most_recent_duplicate = max(test_list[1:3], key=attrgetter("submitted_time"))
    assert (most_recent_duplicate.target, most_recent_duplicate.identifier) in filtered_models
