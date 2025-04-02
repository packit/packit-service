# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
import pytest
from flask import url_for
from packit.utils import nested_get

from packit_service.models import (
    PipelineModel,
    SyncReleaseStatus,
    SyncReleaseTargetStatus,
    TestingFarmResult,
)
from packit_service.service.api.runs import process_runs
from tests_openshift.conftest import SampleValues


# Check if the API is working
def test_api_health(client):
    response = client.get(url_for("api.healthz_health_check"))
    response_dict = response.json
    assert response.status_code == 200
    assert response_dict["status"] == "We are healthy!"


#  Test Copr Builds
def test_copr_builds_list(client, clean_before_and_after, multiple_copr_builds):
    response = client.get(url_for("api.copr-builds_copr_builds_list"))
    response_dict = response.json
    assert response_dict[0]["packit_id_per_chroot"]["fedora-42-x86_64"] in {
        build.id for build in multiple_copr_builds
    }
    assert response_dict[0]["project"] == SampleValues.different_project_name
    assert response_dict[1]["project"] == SampleValues.project

    assert response_dict[1]["web_url"] == SampleValues.copr_web_url
    assert response_dict[1]["repo_namespace"] == SampleValues.repo_namespace
    assert response_dict[1]["repo_name"] == SampleValues.repo_name
    assert response_dict[1]["pr_id"] == SampleValues.pr_id
    assert {len(response_build["status_per_chroot"]) for response_build in response_dict} == {1, 2}

    assert response_dict[1]["build_submitted_time"] is not None
    assert response_dict[1]["project_url"] == SampleValues.project_url

    # four builds, but three unique build ids
    assert len(response_dict) == 3
    assert {response_build["build_id"] for response_build in response_dict} == {
        build.build_id for build in multiple_copr_builds
    }


#  Test Copr Builds with status waiting_for_srpm
def test_copr_builds_list_waiting_for_srpm(
    client,
    clean_before_and_after,
    a_copr_build_waiting_for_srpm,
):
    response = client.get(url_for("api.copr-builds_copr_builds_list"))
    response_dict = response.json
    assert response_dict == []


#  Test Pagination
def test_pagination(client, clean_before_and_after, too_many_copr_builds):
    response_1 = client.get(
        url_for("api.copr-builds_copr_builds_list") + "?page=2&per_page=20",
    )
    response_dict_1 = response_1.json
    assert len(list(response_dict_1[1]["status_per_chroot"])) == 2
    assert response_dict_1[1]["build_submitted_time"] is not None
    assert len(response_dict_1) == 20  # three builds, but two unique build ids

    response_2 = client.get(
        url_for("api.copr-builds_copr_builds_list") + "?page=1&per_page=30",
    )
    response_dict_2 = response_2.json
    assert len(list(response_dict_2[1]["status_per_chroot"])) == 2
    assert response_dict_2[1]["build_submitted_time"] is not None
    assert len(response_dict_2) == 30  # three builds, but two unique build ids


# Test detailed build info
def test_detailed_copr_build_info(client, clean_before_and_after, a_copr_build_for_pr):
    response = client.get(
        url_for("api.copr-builds_copr_build_item", id=a_copr_build_for_pr.id),
    )
    response_dict = response.json
    assert response_dict["build_id"] == SampleValues.build_id
    assert response_dict["status"] == SampleValues.status_pending
    assert response_dict["chroot"] == SampleValues.target
    assert response_dict["build_submitted_time"] is not None
    assert "build_start_time" in response_dict
    assert "build_finished_time" in response_dict
    assert response_dict["commit_sha"] == SampleValues.commit_sha
    assert response_dict["web_url"] == SampleValues.copr_web_url
    assert "build_logs_url" in response_dict
    assert response_dict["copr_project"] == SampleValues.project
    assert response_dict["copr_owner"] == SampleValues.owner
    assert response_dict["srpm_build_id"] == a_copr_build_for_pr.get_srpm_build().id

    # Project info:
    assert response_dict["repo_namespace"] == SampleValues.repo_namespace
    assert response_dict["repo_name"] == SampleValues.repo_name
    assert response_dict["project_url"] == SampleValues.project_url
    assert response_dict["pr_id"] == SampleValues.pr_id
    assert response_dict["built_packages"] == SampleValues.built_packages
    assert "branch_name" in response_dict
    assert "release" in response_dict


def test_koji_builds_list(client, clean_before_and_after, multiple_koji_builds):
    response = client.get(url_for("api.koji-builds_koji_builds_list"))
    response_dict = response.json
    assert len(response_dict) == 5
    assert response_dict[0]["packit_id"] in {build.id for build in multiple_koji_builds}
    assert response_dict[1]["status"] == SampleValues.status_pending
    assert response_dict[1]["web_url"] == SampleValues.koji_web_url
    assert response_dict[1]["repo_namespace"] == SampleValues.repo_namespace
    assert response_dict[1]["repo_name"] == SampleValues.repo_name
    assert response_dict[1]["project_url"] == SampleValues.project_url
    assert response_dict[1]["pr_id"] == SampleValues.pr_id

    assert response_dict[1]["build_submitted_time"] is not None

    assert {response_build["task_id"] for response_build in response_dict} == {
        build.task_id for build in multiple_koji_builds
    }


def test_koji_builds_list_non_scratch(
    client,
    clean_before_and_after,
    multiple_koji_builds,
):
    response = client.get(
        url_for("api.koji-builds_koji_builds_list") + "?scratch=false",
    )
    response_dict = response.json
    assert len(response_dict) == 1


def test_koji_builds_list_scratch(client, clean_before_and_after, multiple_koji_builds):
    response = client.get(url_for("api.koji-builds_koji_builds_list") + "?scratch=true")
    response_dict = response.json
    assert len(response_dict) == 4


def test_detailed_koji_build_info(client, clean_before_and_after, a_koji_build_for_pr):
    response = client.get(
        url_for("api.koji-builds_koji_build_item", id=a_koji_build_for_pr.id),
    )
    response_dict = response.json
    assert response_dict["task_id"] == SampleValues.build_id
    assert response_dict["status"] == SampleValues.status_pending
    assert response_dict["chroot"] == SampleValues.target
    assert response_dict["build_submitted_time"] is not None
    assert "build_start_time" in response_dict
    assert "build_finished_time" in response_dict
    assert response_dict["commit_sha"] == SampleValues.commit_sha
    assert response_dict["web_url"] == SampleValues.koji_web_url
    assert "build_logs_urls" in response_dict
    assert response_dict["srpm_build_id"] == a_koji_build_for_pr.get_srpm_build().id

    # Project info:
    assert response_dict["repo_namespace"] == SampleValues.repo_namespace
    assert response_dict["repo_name"] == SampleValues.repo_name
    assert response_dict["project_url"] == SampleValues.project_url
    assert response_dict["pr_id"] == SampleValues.pr_id
    assert "branch_name" in response_dict
    assert "release" in response_dict


def test_detailed_koji_build_info_non_scratch(
    client,
    clean_before_and_after,
    a_koji_build_for_pr_non_scratch,
):
    response = client.get(
        url_for(
            "api.koji-builds_koji_build_item",
            id=a_koji_build_for_pr_non_scratch.id,
        ),
    )
    response_dict = response.json
    assert response_dict["task_id"] == SampleValues.build_id
    assert response_dict["status"] == SampleValues.status_pending
    assert response_dict["chroot"] == SampleValues.target
    assert response_dict["build_submitted_time"] is not None
    assert "build_start_time" in response_dict
    assert "build_finished_time" in response_dict
    assert response_dict["commit_sha"] == SampleValues.commit_sha
    assert response_dict["web_url"] == SampleValues.koji_web_url
    assert "build_logs_urls" in response_dict
    assert response_dict["srpm_build_id"] is None


def test_detailed_koji_build_info_for_pr(
    client,
    clean_before_and_after,
    a_koji_build_for_pr,
):
    response = client.get(
        url_for("api.koji-builds_koji_build_item", id=a_koji_build_for_pr.id),
    )
    response_dict = response.json
    assert response_dict["pr_id"] == SampleValues.pr_id


def test_detailed_koji_build_info_for_branch_push(
    client,
    clean_before_and_after,
    a_koji_build_for_branch_push,
):
    response = client.get(
        url_for("api.koji-builds_koji_build_item", id=a_koji_build_for_branch_push.id),
    )
    response_dict = response.json
    assert response_dict["branch_name"] == SampleValues.branch


def test_detailed_koji_build_info_for_release(
    client,
    clean_before_and_after,
    a_koji_build_for_release,
):
    response = client.get(
        url_for("api.koji-builds_koji_build_item", id=a_koji_build_for_release.id),
    )
    response_dict = response.json
    assert response_dict["release"] == SampleValues.tag_name


#  Test SRPM Builds
def test_srpm_builds_list(client, clean_before_and_after, a_copr_build_for_pr):
    response = client.get(
        url_for("api.srpm-builds_srpm_builds_list", id=a_copr_build_for_pr.id),
    )
    response_dict = response.json
    assert response_dict[0]["status"] == "success"
    assert isinstance(response_dict[0]["srpm_build_id"], int)
    assert response_dict[0]["log_url"] is not None
    assert response_dict[0]["repo_namespace"] == SampleValues.repo_namespace
    assert response_dict[0]["repo_name"] == SampleValues.repo_name
    assert response_dict[0]["project_url"] == SampleValues.project_url
    assert response_dict[0]["pr_id"] == SampleValues.pr_id
    assert response_dict[0]["branch_name"] is None  # trigger was PR, not branch push
    assert response_dict[0]["build_submitted_time"] is not None


def test_srpm_build_info(
    client,
    clean_before_and_after,
    srpm_build_model_with_new_run_for_pr,
):
    srpm_build_model, _ = srpm_build_model_with_new_run_for_pr
    response = client.get(
        url_for("api.srpm-builds_srpm_build_item", id=srpm_build_model.id),
    )
    response_dict = response.json

    assert response_dict["status"] == "success"
    assert response_dict["build_submitted_time"] is not None
    assert "url" in response_dict
    assert response_dict["logs"] is not None

    # Project info:
    assert response_dict["repo_namespace"] == SampleValues.repo_namespace
    assert response_dict["repo_name"] == SampleValues.repo_name
    assert response_dict["project_url"] == SampleValues.project_url
    assert response_dict["pr_id"] == SampleValues.pr_id
    assert "branch_name" in response_dict
    assert "release" in response_dict


def test_srpm_build_in_copr_info(
    client,
    clean_before_and_after,
    srpm_build_in_copr_model,
):
    srpm_build_model, _ = srpm_build_in_copr_model
    response = client.get(
        url_for("api.srpm-builds_srpm_build_item", id=srpm_build_model.id),
    )
    response_dict = response.json

    assert response_dict["status"] == "success"
    assert response_dict["build_submitted_time"] is not None
    assert "url" in response_dict
    assert response_dict["logs"] is None
    assert response_dict["copr_build_id"] is not None
    assert response_dict["copr_web_url"] is not None


def test_allowlist_all(client, clean_before_and_after, new_allowlist_entry):
    """Test Allowlist API (all)"""
    response = client.get(url_for("api.allowlist_allowlist"))
    response_dict = response.json
    assert response_dict[0]["namespace"] == "github.com/Rayquaza"
    assert response_dict[0]["status"] == "approved_manually"
    assert len(list(response_dict)) == 1


def test_allowlist_specific(client, clean_before_and_after, new_allowlist_entry):
    """Test Allowlist API (specific user)"""
    user_1 = client.get(
        url_for("api.allowlist_allowlist_item", namespace="github.com/Rayquaza"),
    )
    assert user_1.json["namespace"] == "github.com/Rayquaza"
    assert user_1.json["status"] == "approved_manually"

    user_2 = client.get(
        url_for("api.allowlist_allowlist_item", namespace="github.com/Zacian"),
    )
    assert user_2.status_code == 204  # No content when not in allowlist


def test_get_testing_farm_results(
    client,
    clean_before_and_after,
    multiple_new_test_runs,
):
    """Test Get Testing Farm Results"""
    response = client.get(url_for("api.testing-farm_testing_farm_results"))
    response_dict = response.json
    assert len(response_dict) == 4
    assert response_dict[0]["packit_id"] in {test_run.id for test_run in multiple_new_test_runs}
    assert response_dict[0]["pipeline_id"] == SampleValues.another_different_pipeline_id
    assert response_dict[0]["target"] == SampleValues.chroots[0]
    assert response_dict[0]["ref"] == SampleValues.different_commit_sha
    assert response_dict[0]["pr_id"] == 4
    assert response_dict[0]["status"] == "running"
    assert response_dict[0]["repo_namespace"] == SampleValues.repo_namespace
    assert response_dict[0]["repo_name"] == SampleValues.repo_name
    assert response_dict[0]["web_url"] == SampleValues.testing_farm_url

    assert response_dict[1]["pipeline_id"] == SampleValues.different_pipeline_id
    assert response_dict[1]["target"] == SampleValues.chroots[0]
    assert response_dict[1]["ref"] == SampleValues.commit_sha
    assert response_dict[1]["pr_id"] == 342
    assert response_dict[1]["status"] == "new"

    assert response_dict[2]["target"] in SampleValues.chroots


def test_get_testing_farm_result(client, clean_before_and_after, a_new_test_run_pr):
    response = client.get(
        url_for("api.testing-farm_testing_farm_result", id=a_new_test_run_pr.id),
    )
    response_dict = response.json

    assert response_dict["pipeline_id"] == a_new_test_run_pr.pipeline_id
    assert response_dict["chroot"] == SampleValues.target
    assert response_dict["status"] == TestingFarmResult.new
    assert response_dict["commit_sha"] == SampleValues.commit_sha
    assert response_dict["web_url"] == SampleValues.testing_farm_url

    # Project info:
    assert response_dict["repo_namespace"] == SampleValues.repo_namespace
    assert response_dict["repo_name"] == SampleValues.repo_name
    assert response_dict["project_url"] == SampleValues.project_url
    assert response_dict["pr_id"] == SampleValues.pr_id
    assert "branch_name" in response_dict
    assert "release" in response_dict


def test_get_projects_list(client, clean_before_and_after, a_copr_build_for_pr):
    """Test Get Projects"""
    response = client.get(url_for("api.projects_projects_list"))
    response_dict = response.json
    assert len(response_dict) == 1
    assert response_dict[0]["namespace"] == SampleValues.repo_namespace
    assert response_dict[0]["repo_name"] == SampleValues.repo_name
    assert response_dict[0]["project_url"] == SampleValues.project_url
    assert response_dict[0]["prs_handled"] == 1


def test_get_projects_list_forge(
    client,
    clean_before_and_after,
    multiple_forge_projects,
):
    """Test Get Projects by Forge"""
    response = client.get(
        url_for(
            "api.projects_projects_forge",
            forge="github.com",
        ),
    )
    response_dict = response.json
    assert len(response_dict) == 2


def test_get_projects_list_namespace(
    client,
    clean_before_and_after,
    multiple_copr_builds,
):
    """Test Get Projects by Namespace"""
    response = client.get(
        url_for(
            "api.projects_projects_namespace",
            forge="github.com",
            namespace="the-namespace",
        ),
    )
    response_dict = response.json
    assert len(response_dict) == 1
    assert response_dict[0]["namespace"] == SampleValues.repo_namespace
    assert response_dict[0]["repo_name"] == SampleValues.repo_name


def test_get_project_info(client, clean_before_and_after, a_copr_build_for_pr):
    """Test Get a single project's info"""
    response = client.get(
        url_for(
            "api.projects_project_info",
            forge="github.com",
            namespace="the-namespace",
            repo_name="the-repo-name",
        ),
    )
    project = response.json
    assert project["namespace"] == SampleValues.repo_namespace
    assert project["repo_name"] == SampleValues.repo_name
    assert project["project_url"] == SampleValues.project_url
    assert project["prs_handled"] == 1


def test_get_projects_prs(client, clean_before_and_after, a_copr_build_for_pr):
    """Test Get Project's Pull Requests"""
    response = client.get(
        url_for(
            "api.projects_projects_p_rs",
            forge="github.com",
            namespace="the-namespace",
            repo_name="the-repo-name",
        ),
    )
    response_dict = response.json
    assert len(response_dict) == 1
    assert response_dict[0]["pr_id"] is not None
    assert response_dict[0]["builds"][0]["build_id"] == SampleValues.build_id
    assert response_dict[0]["builds"][0]["status"] == "pending"
    assert response_dict[0]["srpm_builds"][0]["status"] == "success"


def test_get_projects_prs_koji(client, clean_before_and_after, a_koji_build_for_pr):
    """Test Get Project's Pull Requests"""
    response = client.get(
        url_for(
            "api.projects_projects_p_rs",
            forge="github.com",
            namespace="the-namespace",
            repo_name="the-repo-name",
        ),
    )
    response_dict = response.json
    assert len(response_dict) == 1
    assert response_dict[0]["pr_id"] is not None
    assert response_dict[0]["srpm_builds"][0]["status"] == "success"
    assert response_dict[0]["koji_builds"][0]["status"] == "pending"


def test_get_projects_issues(client, clean_before_and_after, an_issue_model):
    """Test Get Project's Issues"""
    response = client.get(
        url_for(
            "api.projects_project_issues",
            forge="github.com",
            namespace="the-namespace",
            repo_name="the-repo-name",
        ),
    )
    response_dict = response.json
    assert response_dict[0] == SampleValues.issue_id


def test_get_projects_releases(client, clean_before_and_after, release_model):
    """Test Get Project's Releases"""
    response = client.get(
        url_for(
            "api.projects_project_releases",
            forge="github.com",
            namespace="the-namespace",
            repo_name="the-repo-name",
        ),
    )
    response_dict = response.json
    assert len(response_dict) == 1
    assert response_dict[0]["tag_name"] == SampleValues.tag_name
    assert response_dict[0]["commit_hash"] == SampleValues.commit_sha


def test_get_projects_branches(
    client,
    clean_before_and_after,
    a_copr_build_for_branch_push,
):
    """Test Get Project's Releases"""
    response = client.get(
        url_for(
            "api.projects_project_branches",
            forge="github.com",
            namespace="the-namespace",
            repo_name="the-repo-name",
        ),
    )
    response_dict = response.json
    assert len(response_dict) == 1
    assert response_dict[0]["branch"] == SampleValues.branch


def test_meta(client, clean_before_and_after, a_copr_build_for_pr):
    """Test meta info like headers, status etc"""
    response = client.get(url_for("api.copr-builds_copr_builds_list"))
    assert response.status_code == 206
    assert response.status == "206 PARTIAL CONTENT"
    assert response.is_json
    assert response.headers["Access-Control-Allow-Origin"] == "*"


def test_process_runs_without_build(clean_before_and_after, runs_without_build):
    merged_runs = PipelineModel.get_merged_chroots(0, 10)
    result = process_runs(merged_runs)
    for item in result:
        assert not item["srpm"]
        assert item["time_submitted"]
        assert len(item["test_run"]) == 1
        assert item["trigger"]


def test_propose_downstream_list_releases(
    client,
    clean_before_and_after,
    multiple_propose_downstream_runs_with_propose_downstream_targets_release_trigger,
    multiple_pull_from_upstream_runs_with_targets_release_trigger,
):
    response = client.get(url_for("api.propose-downstream_propose_downstream_list"))
    response_dict = response.json

    # the order is reversed
    response_dict.reverse()
    assert response_dict[0]["status"] == SyncReleaseStatus.running
    assert response_dict[1]["status"] == SyncReleaseStatus.error
    assert response_dict[0]["submitted_time"] is not None
    assert response_dict[0]["release"] == SampleValues.tag_name
    assert response_dict[2]["release"] == SampleValues.different_tag_name

    assert (
        response_dict[0]["status_per_downstream_pr"][SampleValues.different_branch]
        == SyncReleaseTargetStatus.queued
    )
    assert (
        response_dict[0]["status_per_downstream_pr"][SampleValues.branch]
        == SyncReleaseTargetStatus.running
    )

    assert response_dict[0]["repo_namespace"] == SampleValues.repo_namespace
    assert response_dict[0]["repo_name"] == SampleValues.repo_name
    assert response_dict[0]["project_url"] == SampleValues.project_url

    assert len(response_dict) == 4


def test_pull_from_upstream_list(
    client,
    clean_before_and_after,
    multiple_pull_from_upstream_runs_with_targets_release_trigger,
    multiple_propose_downstream_runs_with_propose_downstream_targets_release_trigger,
):
    response = client.get(url_for("api.pull-from-upstream_pull_from_upstream_list"))
    response_dict = response.json

    # the order is reversed
    response_dict.reverse()
    assert response_dict[0]["status"] == SyncReleaseStatus.running
    assert response_dict[1]["status"] == SyncReleaseStatus.error
    assert response_dict[0]["submitted_time"] is not None
    assert response_dict[0]["release"] == SampleValues.tag_name
    assert response_dict[2]["release"] == SampleValues.different_tag_name

    assert (
        response_dict[0]["status_per_downstream_pr"][SampleValues.different_branch]
        == SyncReleaseTargetStatus.queued
    )
    assert (
        response_dict[0]["status_per_downstream_pr"][SampleValues.branch]
        == SyncReleaseTargetStatus.running
    )

    assert response_dict[0]["repo_namespace"] == SampleValues.repo_namespace
    assert response_dict[0]["repo_name"] == SampleValues.repo_name
    assert response_dict[0]["project_url"] == SampleValues.project_url

    assert len(response_dict) == 4


def test_propose_downstream_list_issues(
    client,
    clean_before_and_after,
    multiple_propose_downstream_runs_with_propose_downstream_targets_issue_trigger,
):
    response = client.get(url_for("api.propose-downstream_propose_downstream_list"))
    response_dict = response.json

    # the order is reversed
    response_dict.reverse()
    assert response_dict[0]["status"] == SyncReleaseStatus.running
    assert response_dict[3]["status"] == SyncReleaseStatus.finished
    assert response_dict[0]["submitted_time"] is not None
    assert response_dict[0]["issue_id"] == SampleValues.issue_id
    assert response_dict[3]["issue_id"] == SampleValues.different_issue_id

    assert (
        response_dict[0]["status_per_downstream_pr"][SampleValues.branch]
        == SyncReleaseTargetStatus.retry
    )
    assert (
        response_dict[0]["status_per_downstream_pr"][SampleValues.different_branch]
        == SyncReleaseTargetStatus.error
    )

    assert response_dict[0]["repo_namespace"] == SampleValues.repo_namespace
    assert response_dict[0]["repo_name"] == SampleValues.repo_name
    assert response_dict[0]["project_url"] == SampleValues.project_url

    assert len(response_dict) == 4


def test_detailed_propose_info_release(
    client,
    clean_before_and_after,
    propose_model_submitted_release,
):
    response = client.get(
        url_for(
            "api.propose-downstream_propose_result",
            id=propose_model_submitted_release.id,
        ),
    )
    response_dict = response.json

    assert response_dict["status"] == SyncReleaseTargetStatus.submitted
    assert response_dict["branch"] == SampleValues.branch
    assert response_dict["downstream_prs"] == [
        {
            "pr_id": SampleValues.downstream_pr_id,
            "branch": SampleValues.branch,
            "is_fast_forward": False,
            "url": SampleValues.downstream_pr_url,
        }
    ]
    assert response_dict["downstream_pr_project"] == SampleValues.downstream_project_url
    assert response_dict["submitted_time"] is not None
    assert response_dict["finished_time"] is not None
    assert response_dict["logs"] == "random logs"

    # Project info:
    assert response_dict["repo_namespace"] == SampleValues.repo_namespace
    assert response_dict["repo_name"] == SampleValues.repo_name
    assert response_dict["project_url"] == SampleValues.project_url
    assert response_dict["release"] == SampleValues.tag_name


def test_detailed_pull_from_upstream_info(
    client,
    clean_before_and_after,
    pull_from_upstream_target_model,
):
    response = client.get(
        url_for(
            "api.pull-from-upstream_pull_result",
            id=pull_from_upstream_target_model.id,
        ),
    )
    response_dict = response.json

    assert response_dict["status"] == SyncReleaseTargetStatus.submitted
    assert response_dict["branch"] == SampleValues.branch
    assert response_dict["downstream_prs"] == [
        {
            "pr_id": SampleValues.downstream_pr_id,
            "branch": SampleValues.branch,
            "is_fast_forward": False,
            "url": SampleValues.downstream_pr_url,
        }
    ]
    assert response_dict["downstream_pr_project"] == SampleValues.downstream_project_url
    assert response_dict["submitted_time"] is not None
    assert response_dict["finished_time"] is not None
    assert response_dict["logs"] == "random logs"

    # Project info:
    assert response_dict["repo_namespace"] == SampleValues.repo_namespace
    assert response_dict["repo_name"] == SampleValues.repo_name
    assert response_dict["project_url"] == SampleValues.project_url
    assert response_dict["release"] == SampleValues.tag_name


def test_detailed_pull_from_upstream_info_non_git(
    client,
    clean_before_and_after,
    pull_from_upstream_target_model_non_git,
):
    response = client.get(
        url_for(
            "api.pull-from-upstream_pull_result",
            id=pull_from_upstream_target_model_non_git.id,
        ),
    )
    response_dict = response.json

    assert response_dict["status"] == SyncReleaseTargetStatus.submitted
    assert response_dict["branch"] == SampleValues.branch
    assert response_dict["downstream_prs"] == [
        {
            "pr_id": SampleValues.downstream_pr_id,
            "branch": SampleValues.branch,
            "is_fast_forward": False,
            "url": SampleValues.downstream_pr_url,
        }
    ]
    assert response_dict["downstream_pr_project"] == SampleValues.downstream_project_url
    assert response_dict["submitted_time"] is not None
    assert response_dict["finished_time"] is not None
    assert response_dict["logs"] == "random logs"

    # Project info:
    assert response_dict["repo_namespace"] is None
    assert response_dict["repo_name"] is None
    assert (
        response_dict["project_url"]
        == f"https://release-monitoring.org/project/{SampleValues.anitya_project_id}"
    )
    assert response_dict["release"] is None
    assert response_dict["anitya_version"] == SampleValues.tag_name
    assert response_dict["anitya_project_id"] == SampleValues.anitya_project_id
    assert response_dict["anitya_project_name"] == SampleValues.anitya_project_name


def test_detailed_propose_info_issue(
    client,
    clean_before_and_after,
    propose_model_submitted_issue,
):
    response = client.get(
        url_for(
            "api.propose-downstream_propose_result",
            id=propose_model_submitted_issue.id,
        ),
    )
    response_dict = response.json

    assert response_dict["issue_id"] == SampleValues.issue_id


def test_detailed_pull_from_upstream_without_pr_model(
    client,
    clean_before_and_after,
    pull_from_upstream_target_model_without_pr_model,
):
    response = client.get(
        url_for(
            "api.pull-from-upstream_pull_result",
            id=pull_from_upstream_target_model_without_pr_model.id,
        ),
    )
    response_dict = response.json

    assert response_dict["downstream_pr_project"] is None
    assert response_dict["downstream_prs"] == []


@pytest.mark.parametrize(
    "key_to_check",
    [
        "active_projects",
        "active_projects/project_count",
        "active_projects/top_projects_by_events_handled",
        "active_projects/instances",
        "active_projects/instances/github.com",
        "all_projects/project_count",
        "all_projects/instances/github.com",
        "events/pull_request/events_handled",
        "events/pull_request/top_projects",
        "events/release/events_handled",
        "jobs/srpm_builds/job_runs",
        "jobs/srpm_builds/top_projects_by_job_runs",
        "jobs/copr_build_groups/job_runs",
        "jobs/copr_build_groups/top_projects_by_job_runs",
        "jobs/copr_build_groups/per_event/pull_request/job_runs",
        "jobs/copr_build_groups/per_event/pull_request/top_projects_by_job_runs",
    ],
)
def test_usage_info_structure(
    client,
    clean_before_and_after,
    full_database,
    key_to_check,
):
    response = client.get(url_for("api.usage_usage"))
    response_dict = response.json

    assert nested_get(response_dict, *key_to_check.split("/")) is not None


def test_usage_info_datetime(client, clean_before_and_after, full_database):
    response = client.get(url_for("api.usage_usage") + "?to=2022-12-12")
    response_dict = response.json

    assert response_dict["active_projects"]["project_count"] == 0


def test_usage_info_top(client, clean_before_and_after, full_database):
    response = client.get(url_for("api.usage_usage") + "?top=0")
    response_dict = response.json

    assert len(response_dict["active_projects"]["top_projects_by_events_handled"]) == 0


@pytest.mark.parametrize(
    "key_to_check, expected_value",
    [
        ("active_projects/project_count", 1),
        (
            "active_projects/top_projects_by_events_handled",
            {"https://github.com/the-namespace/the-repo-name": 7},
        ),
        ("active_projects/instances", {"github.com": 1}),
        ("all_projects/project_count", 6),
        (
            "all_projects/instances",
            {"git.stg.centos.org": 1, "github.com": 4, "gitlab.com": 1},
        ),
        ("events/pull_request/events_handled", 2),
        (
            "events/pull_request/top_projects",
            {"https://github.com/the-namespace/the-repo-name": 2},
        ),
        ("events/release/events_handled", 2),
        ("jobs/srpm_builds/job_runs", 13),
        (
            "jobs/srpm_builds/top_projects_by_job_runs",
            {"https://github.com/the-namespace/the-repo-name": 13},
        ),
        ("jobs/copr_build_groups/job_runs", 13),
        (
            "jobs/copr_build_groups/top_projects_by_job_runs",
            {"https://github.com/the-namespace/the-repo-name": 13},
        ),
        (
            "jobs/copr_build_groups/per_event/pull_request/job_runs",
            9,
        ),
        (
            "jobs/copr_build_groups/per_event/pull_request/top_projects_by_job_runs",
            {"https://github.com/the-namespace/the-repo-name": 9},
        ),
    ],
)
def test_usage_info_values(
    client,
    clean_before_and_after,
    full_database,
    key_to_check,
    expected_value,
):
    response = client.get(url_for("api.usage_usage"))
    response_dict = response.json

    assert nested_get(response_dict, *key_to_check.split("/")) == expected_value


def test_project_usage_info(
    client,
    clean_before_and_after,
    full_database,
):
    response = client.get(
        url_for(
            "api.usage_project_usage",
            forge="github.com",
            namespace="the-namespace",
            repo_name="the-repo-name",
        ),
    )
    response_dict = response.json

    assert nested_get(response_dict, "events_handled", "events_handled") == 7
    assert nested_get(response_dict, "events_handled", "position") == 1
    assert (
        nested_get(
            response_dict,
            "events_handled",
            "per_event",
            "branch_push",
            "events_handled",
        )
        == 1
    )
    assert nested_get(response_dict, "jobs", "srpm_builds", "job_runs") == 13
    assert (
        nested_get(
            response_dict,
            "jobs",
            "srpm_builds",
            "per_event",
            "release",
            "job_runs",
        )
        == 1
    )
    assert nested_get(response_dict, "jobs", "tft_test_run_groups", "job_runs") == 5
    assert nested_get(response_dict, "jobs", "tft_test_run_groups", "position") == 1
    assert (
        nested_get(
            response_dict,
            "jobs",
            "tft_test_run_groups",
            "per_event",
            "pull_request",
            "job_runs",
        )
        == 4
    )
    assert (
        nested_get(
            response_dict,
            "jobs",
            "tft_test_run_groups",
            "per_event",
            "pull_request",
            "position",
        )
        == 1
    )


def test_bodhi_update_list(
    client,
    clean_before_and_after,
    multiple_bodhi_update_runs,
):
    response = client.get(url_for("api.bodhi-updates_bodhi_updates_list"))
    response_dict = response.json

    assert len(response_dict) == 2

    response_dict.reverse()
    assert response_dict[0]["status"] == "queued"
    assert response_dict[0]["koji_nvrs"] == SampleValues.nvr
    assert response_dict[0]["branch"] == SampleValues.dist_git_branch
    assert response_dict[0]["branch_name"] == SampleValues.branch

    assert response_dict[1]["koji_nvrs"] == SampleValues.different_nvr
    assert response_dict[1]["branch"] == SampleValues.different_dist_git_branch

    assert response_dict[0]["repo_namespace"] == SampleValues.repo_namespace
    assert response_dict[0]["repo_name"] == SampleValues.repo_name
    assert response_dict[0]["project_url"] == SampleValues.project_url


def test_bodhi_update_info(
    client,
    clean_before_and_after,
    bodhi_update_model,
):
    response = client.get(
        url_for("api.bodhi-updates_bodhi_update_item", id=bodhi_update_model.id),
    )
    response_dict = response.json
    assert response_dict["alias"] == SampleValues.alias
    assert response_dict["branch"] == SampleValues.dist_git_branch
    assert response_dict["web_url"] == SampleValues.bodhi_url
    assert response_dict["koji_nvrs"] == SampleValues.nvr
    assert response_dict["branch_name"] == SampleValues.branch
    assert response_dict["repo_name"] == SampleValues.repo_name
    assert response_dict["repo_namespace"] == SampleValues.repo_namespace
    assert response_dict["status"] == "error"
    assert response_dict["submitted_time"] is not None


def test_scan_info(
    client,
    clean_before_and_after,
    a_scan,
):
    response = client.get(
        url_for("api.openscanhub-scans_scan_item", id=a_scan.id),
    )
    response_dict = response.json
    assert response_dict["task_id"] == SampleValues.task_id
    assert response_dict["url"] == SampleValues.scan_url
    assert response_dict["status"] == SampleValues.scan_status_success
    assert response_dict["issues_added_url"] == SampleValues.issues_added_url
    assert response_dict["issues_fixed_url"] == SampleValues.issues_fixed_url
    assert response_dict["scan_results_url"] == SampleValues.scan_results_url
    assert response_dict["repo_namespace"] == SampleValues.repo_namespace
    assert response_dict["repo_name"] == SampleValues.repo_name
    assert response_dict["project_url"] == SampleValues.project_url
    assert response_dict["issues_added_count"] == SampleValues.issues_added_count


def test_scans_list(
    client,
    clean_before_and_after,
    a_scan,
):
    response = client.get(url_for("api.openscanhub-scans_scans_list"))
    response_dict = response.json

    assert len(response_dict) == 1


def test_koji_tag_request_info(
    client,
    clean_before_and_after,
    a_koji_tag_request,
):
    response = client.get(
        url_for("api.koji-tag-requests_koji_tag_request_item", id=a_koji_tag_request.id),
    )
    response_dict = response.json
    assert response_dict["task_id"] == SampleValues.build_id
    assert response_dict["web_url"] == SampleValues.koji_web_url
    assert response_dict["chroot"] == SampleValues.target
    assert response_dict["sidetag"] == SampleValues.sidetag
    assert response_dict["nvr"] == SampleValues.nvr
    assert response_dict["repo_namespace"] == SampleValues.repo_namespace
    assert response_dict["repo_name"] == SampleValues.repo_name
    assert response_dict["project_url"] == SampleValues.project_url


def test_koji_tag_requests_list(
    client,
    clean_before_and_after,
    a_koji_tag_request,
):
    response = client.get(url_for("api.koji-tag-requests_koji_tag_requests_list"))
    response_dict = response.json

    assert len(response_dict) == 1
