from flask import url_for

from tests_requre.conftest import SampleValues


# Check if the API is working
def test_api_health(client):
    response = client.get(url_for("api.healthz_health_check"))
    assert response.status_code == 200
    assert response.data.decode() == '"We are healthy!"\n'


#  Test Copr Builds
def test_copr_builds_list(client, clean_before_and_after, multiple_copr_builds):
    response = client.get(url_for("api.copr-builds_copr_builds_list"))
    response_dict = response.json
    assert response_dict[0]["project"] == SampleValues.different_project_name
    assert response_dict[1]["project"] == SampleValues.project
    assert response_dict[1]["build_id"] == SampleValues.build_id
    assert response_dict[1]["web_url"] == SampleValues.copr_web_url
    assert response_dict[1]["repo_namespace"] == SampleValues.repo_namespace
    assert response_dict[1]["repo_name"] == SampleValues.repo_name
    assert response_dict[1]["pr_id"] == SampleValues.pr_id
    assert len(list(response_dict[1]["status_per_chroot"])) == 2
    assert response_dict[1]["status_per_chroot"]["fedora-42-x86_64"] == "success"
    assert response_dict[1]["status_per_chroot"]["fedora-43-x86_64"] == "pending"
    assert response_dict[1]["build_submitted_time"] is not None
    assert len(response_dict) == 2  # three builds, but two unique build ids


#  Test Pagination
def test_pagination(client, clean_before_and_after, too_many_copr_builds):
    response_1 = client.get(
        url_for("api.copr-builds_copr_builds_list") + "?page=2&per_page=20"
    )
    response_dict_1 = response_1.json
    assert len(list(response_dict_1[1]["status_per_chroot"])) == 2
    assert response_dict_1[1]["build_submitted_time"] is not None
    assert len(response_dict_1) == 20  # three builds, but two unique build ids

    response_2 = client.get(
        url_for("api.copr-builds_copr_builds_list") + "?page=1&per_page=30"
    )
    response_dict_2 = response_2.json
    assert len(list(response_dict_2[1]["status_per_chroot"])) == 2
    assert response_dict_2[1]["build_submitted_time"] is not None
    assert len(response_dict_2) == 30  # three builds, but two unique build ids


# Test detailed build info
def test_detailed_copr_build_info(client, clean_before_and_after, multiple_copr_builds):
    response = client.get(
        url_for("api.copr-builds_installation_item", id=SampleValues.build_id)
    )
    response_dict = response.json
    assert response_dict["project"] == SampleValues.project
    assert response_dict["owner"] == SampleValues.owner
    assert response_dict["build_id"] == SampleValues.build_id
    assert response_dict["commit_sha"] == SampleValues.commit_sha
    assert response_dict["web_url"] == SampleValues.copr_web_url
    assert response_dict["srpm_logs"] == SampleValues.srpm_logs
    assert response_dict["ref"] == SampleValues.ref
    assert response_dict["repo_namespace"] == SampleValues.repo_namespace
    assert response_dict["repo_name"] == SampleValues.repo_name
    assert response_dict["git_repo"] == SampleValues.project_url
    assert response_dict["pr_id"] == SampleValues.pr_id
    assert len(response_dict["chroots"]) == 2
    assert len(list(response_dict["status_per_chroot"])) == 2
    assert response_dict["status_per_chroot"]["fedora-42-x86_64"] == "success"
    assert response_dict["status_per_chroot"]["fedora-43-x86_64"] == "pending"
    assert response_dict["build_submitted_time"] is not None


def test_koji_builds_list(client, clean_before_and_after, multiple_koji_builds):
    response = client.get(url_for("api.koji-builds_koji_builds_list"))
    response_dict = response.json
    assert len(response_dict) == 3
    assert response_dict[0]["build_id"] == SampleValues.build_id
    assert response_dict[1]["build_id"] == SampleValues.different_build_id
    assert response_dict[2]["build_id"] == SampleValues.another_different_build_id

    assert response_dict[1]["status"] == SampleValues.status_pending
    assert response_dict[1]["web_url"] == SampleValues.koji_web_url
    assert response_dict[1]["repo_namespace"] == SampleValues.repo_namespace
    assert response_dict[1]["repo_name"] == SampleValues.repo_name
    assert response_dict[1]["project_url"] == SampleValues.project_url
    assert response_dict[1]["pr_id"] == SampleValues.pr_id

    assert response_dict[1]["build_submitted_time"] is not None
    assert "build_start_time" in response_dict[1]
    assert "build_finished_time" in response_dict[1]


def test_detailed_koji_build_info(client, clean_before_and_after, a_koji_build_for_pr):
    response = client.get(
        url_for("api.koji-builds_koji_build_item", id=SampleValues.build_id)
    )
    response_dict = response.json
    assert response_dict["build_id"] == SampleValues.build_id
    assert response_dict["status"] == SampleValues.status_pending
    assert response_dict["web_url"] == SampleValues.koji_web_url
    assert response_dict["repo_namespace"] == SampleValues.repo_namespace
    assert response_dict["repo_name"] == SampleValues.repo_name
    assert response_dict["project_url"] == SampleValues.project_url
    assert response_dict["pr_id"] == SampleValues.pr_id

    assert response_dict["build_submitted_time"] is not None
    assert "build_start_time" in response_dict
    assert "build_finished_time" in response_dict


def test_detailed_koji_build_info_for_pr(
    client, clean_before_and_after, a_koji_build_for_pr
):
    response = client.get(
        url_for("api.koji-builds_koji_build_item", id=SampleValues.build_id)
    )
    response_dict = response.json
    assert response_dict["pr_id"] == SampleValues.pr_id


def test_detailed_koji_build_info_for_branch_push(
    client, clean_before_and_after, a_koji_build_for_branch_push
):
    response = client.get(
        url_for("api.koji-builds_koji_build_item", id=SampleValues.build_id)
    )
    response_dict = response.json
    assert response_dict["build_branch"] == SampleValues.branch


def test_detailed_koji_build_info_for_release(
    client, clean_before_and_after, a_koji_build_for_release
):
    response = client.get(
        url_for("api.koji-builds_koji_build_item", id=SampleValues.build_id)
    )
    response_dict = response.json
    assert response_dict["release"] == SampleValues.tag_name


def test_whitelist_all(client, clean_before_and_after, new_whitelist_entry):
    """Test Whitelist API (all)"""
    response = client.get(url_for("api.whitelist_white_list"))
    response_dict = response.json
    assert response_dict[0]["account"] == "Rayquaza"
    assert response_dict[0]["status"] == "approved_manually"
    assert len(list(response_dict)) == 1


def test_whitelist_specific(client, clean_before_and_after, new_whitelist_entry):
    """Test Whitelist API (specific user)"""
    user_1 = client.get(url_for("api.whitelist_white_list_item", login="Rayquaza"))
    assert user_1.json["account"] == "Rayquaza"
    assert user_1.json["status"] == "approved_manually"

    user_2 = client.get(url_for("api.whitelist_white_list_item", login="Zacian"))
    assert user_2.status_code == 204  # No content when not in whitelist


def test_get_testing_farm_results(
    client, clean_before_and_after, multiple_new_test_runs
):
    """Test Get Testing Farm Results"""
    response = client.get(url_for("api.testing-farm_testing_farm_results"))
    response_dict = response.json
    assert len(response_dict) == 3
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

    assert response_dict[2]["target"] == SampleValues.chroots[1]


def test_get_projects_list(client, clean_before_and_after, a_copr_build_for_pr):
    """Test Get Projects"""
    response = client.get(url_for("api.projects_projects_list"))
    response_dict = response.json
    assert len(response_dict) == 1
    assert response_dict[0]["namespace"] == SampleValues.repo_namespace
    assert response_dict[0]["repo_name"] == SampleValues.repo_name
    assert response_dict[0]["project_url"] == SampleValues.project_url
    assert response_dict[0]["prs_handled"] == 1


def test_get_projects_prs(client, clean_before_and_after, a_copr_build_for_pr):
    """Test Get Project's Pull Requests"""
    response = client.get(
        url_for(
            "api.projects_projects_p_rs",
            forge="github.com",
            namespace="the-namespace",
            repo_name="the-repo-name",
        )
    )
    response_dict = response.json
    assert len(response_dict) == 1
    assert response_dict[0]["pr_id"] is not None
    assert response_dict[0]["builds"][0]["build_id"] == SampleValues.build_id
    assert response_dict[0]["builds"][0]["status"] == "pending"


def test_get_projects_issues(client, clean_before_and_after, an_issue_model):
    """Test Get Project's Issues"""
    response = client.get(
        url_for(
            "api.projects_project_issues",
            forge="github.com",
            namespace="the-namespace",
            repo_name="the-repo-name",
        )
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
        )
    )
    response_dict = response.json
    assert len(response_dict) == 1
    assert response_dict[0]["tag_name"] == SampleValues.tag_name
    assert response_dict[0]["commit_hash"] == SampleValues.commit_sha


def test_get_projects_branches(
    client, clean_before_and_after, a_copr_build_for_branch_push
):
    """Test Get Project's Releases"""
    response = client.get(
        url_for(
            "api.projects_project_branches",
            forge="github.com",
            namespace="the-namespace",
            repo_name="the-repo-name",
        )
    )
    response_dict = response.json
    assert len(response_dict) == 1
    assert response_dict[0]["branch"] == SampleValues.branch
