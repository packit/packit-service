from flask import url_for

from packit_service.models import CoprBuildModel
from packit_service.service.views import _get_build_info
from tests_requre.conftest import SampleValues


def test_get_build_logs_for_build_pr(clean_before_and_after, a_copr_build_for_pr):
    response = _get_build_info(a_copr_build_for_pr, build_description="COPR build")
    assert "We can't find any info" not in response
    assert "Builds for the-namespace/the-repo-name: PR #342" in response
    assert a_copr_build_for_pr.status in response
    assert a_copr_build_for_pr.target in response
    assert str(a_copr_build_for_pr.get_srpm_build().id) in response
    assert a_copr_build_for_pr.build_logs_url in response
    assert f"Status: {a_copr_build_for_pr.status}" in response
    assert "For more info see" in response
    assert "You can install the built RPMs by following these steps" not in response

    a_copr_build_for_pr.status = SampleValues.status_success
    response = _get_build_info(a_copr_build_for_pr, build_description="COPR build")
    assert "You can install the built RPMs by following these steps" in response
    assert (
        "Please note that the RPMs should be used only in a testing environment."
        in response
    )


def test_get_build_logs_for_build_branch_push(
    clean_before_and_after, a_copr_build_for_branch_push
):
    response = _get_build_info(
        a_copr_build_for_branch_push, build_description="COPR build"
    )
    assert "We can't find any info" not in response
    assert "Builds for the-namespace/the-repo-name: branch build-branch" in response
    assert a_copr_build_for_branch_push.status in response
    assert a_copr_build_for_branch_push.target in response
    assert str(a_copr_build_for_branch_push.get_srpm_build().id) in response
    assert a_copr_build_for_branch_push.build_logs_url in response
    assert f"Status: {a_copr_build_for_branch_push.status}" in response
    assert "For more info see" in response
    assert "You can install the built RPMs by following these steps" not in response

    a_copr_build_for_branch_push.status = SampleValues.status_success
    response = _get_build_info(
        a_copr_build_for_branch_push, build_description="COPR build"
    )
    assert "You can install the built RPMs by following these steps" in response
    assert (
        "Please note that the RPMs should be used only in a testing environment."
        not in response
    )


def test_get_build_logs_for_build_release(
    clean_before_and_after, a_copr_build_for_release
):
    response = _get_build_info(a_copr_build_for_release, build_description="COPR build")
    assert "We can't find any info" not in response
    assert "Builds for the-namespace/the-repo-name: release v1.0.2" in response
    assert a_copr_build_for_release.status in response
    assert a_copr_build_for_release.target in response
    assert str(a_copr_build_for_release.get_srpm_build().id) in response
    assert a_copr_build_for_release.build_logs_url in response
    assert f"Status: {a_copr_build_for_release.status}" in response
    assert "For more info see" in response
    assert "You can install the built RPMs by following these steps" not in response

    a_copr_build_for_release.status = SampleValues.status_success
    response = _get_build_info(a_copr_build_for_release, build_description="COPR build")
    assert "You can install the built RPMs by following these steps" in response
    assert (
        "Please note that the RPMs should be used only in a testing environment."
        not in response
    )


def test_srpm_logs_view(
    client, clean_before_and_after, srpm_build_model_with_new_run_for_pr
):
    # Logs view uses the id of the SRPMBuildModel not CoprBuildModel
    srpm_build_model, _ = srpm_build_model_with_new_run_for_pr
    response = client.get(
        url_for("builds.get_srpm_build_logs_by_id", id_=srpm_build_model.id)
    )
    response = response.data.decode()

    assert "SRPM build logs" in response
    assert str(srpm_build_model.id) in response
    assert "some\nboring\nlogs" in response


def test_copr_build_info_view(client, clean_before_and_after, multiple_copr_builds):
    build = CoprBuildModel.get_by_build_id(123456, SampleValues.chroots[0])
    build.set_build_logs_url(
        "https://copr.somewhere/results/owner/package/target/build.logs"
    )
    response = client.get(url_for("builds.copr_build_info", id_=str(build.id)))
    response = response.data.decode()

    assert "Builds for the-namespace/the-repo-name: PR #342" in response
    assert build.status in response
    assert build.target in response
    assert str(build.get_srpm_build().id) in response
    assert build.build_logs_url in response
    assert f"Status: {build.status}" in response
    assert "For more info see" in response
    assert "just now" in response


def test_koji_build_info_view(client, clean_before_and_after, a_koji_build_for_pr):
    response = client.get(
        url_for("builds.koji_build_info", id_=str(a_koji_build_for_pr.id))
    )
    response = response.data.decode()

    assert "Builds for the-namespace/the-repo-name: PR #342" in response
    assert a_koji_build_for_pr.status in response
    assert a_koji_build_for_pr.target in response
    assert str(a_koji_build_for_pr.get_srpm_build().id) in response
    assert a_koji_build_for_pr.build_logs_url in response
    assert f"Status: {a_koji_build_for_pr.status}" in response
    assert "For more info see" in response
    assert "You can install the built RPMs by following these steps" not in response

    a_koji_build_for_pr.status = SampleValues.status_success
    response = _get_build_info(a_koji_build_for_pr, build_description="COPR build")
    # no installation instructions for koji build
    assert "You can install the built RPMs by following these steps" not in response
