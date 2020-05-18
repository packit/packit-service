from flask import url_for

from packit_service.models import CoprBuildModel, KojiBuildModel
from packit_service.service.views import _get_build_logs_for_build
from tests_requre.conftest import SampleValues


def test_get_build_logs_for_build_pr(clean_before_and_after, a_copr_build_for_pr):
    response = _get_build_logs_for_build(
        a_copr_build_for_pr, build_description="COPR build"
    )
    assert "We can't find any info" not in response
    assert (
        response
        == "<html><head><title>COPR build the-namespace/the-repo-name: PR #342</title>"
        "</head><body>COPR build ID: 123456<br>State: pending<br><br>"
        "Build web interface URL: "
        '<a href="https://copr.something.somewhere/123456">'
        "https://copr.something.somewhere/123456</a><br>"
        "SRPM creation logs:<br><br><pre>some\nboring\nlogs</pre><br></body></html>"
    )


def test_get_build_logs_for_build_branch_push(
    clean_before_and_after, a_copr_build_for_branch_push
):
    response = _get_build_logs_for_build(
        a_copr_build_for_branch_push, build_description="COPR build"
    )
    assert "We can't find any info" not in response
    assert (
        response
        == "<html><head><title>COPR build the-namespace/the-repo-name: branch build-branch</title>"
        "</head><body>COPR build ID: 123456<br>State: pending<br><br>"
        "Build web interface URL: "
        '<a href="https://copr.something.somewhere/123456">'
        "https://copr.something.somewhere/123456</a><br>"
        "SRPM creation logs:<br><br><pre>some\nboring\nlogs</pre><br></body></html>"
    )


def test_get_build_logs_for_build_release(
    clean_before_and_after, a_copr_build_for_release
):
    response = _get_build_logs_for_build(
        a_copr_build_for_release, build_description="COPR build"
    )
    assert "We can't find any info" not in response
    assert (
        response
        == "<html><head><title>COPR build the-namespace/the-repo-name: release v1.0.2</title>"
        "</head><body>COPR build ID: 123456<br>State: pending<br><br>"
        "Build web interface URL: "
        '<a href="https://copr.something.somewhere/123456">'
        "https://copr.something.somewhere/123456</a><br>"
        "SRPM creation logs:<br><br><pre>some\nboring\nlogs</pre><br></body></html>"
    )


def test_srpm_logs_view(client, clean_before_and_after, multiple_copr_builds):
    build = CoprBuildModel.get_by_build_id(123456, SampleValues.chroots[0])
    # Logs view uses the id of the SRPMBuildModel not CoprBuildModel
    response = client.get(
        url_for("builds.get_srpm_build_logs_by_id", id_=str(build.srpm_build.id))
    )
    assert (
        response.data.decode() == "<html><head><title>SRPM Build id="
        f"{build.srpm_build.id}</title></head>"
        f"<body>SRPM creation logs:<br><br><pre>some\nboring\nlogs</pre><br></body></html>"
    )


def test_copr_build_logs_view(client, clean_before_and_after, multiple_copr_builds):
    build = CoprBuildModel.get_by_build_id(123456, SampleValues.chroots[0])
    # Logs view uses the id of the SRPMBuildModel not CoprBuildModel
    response = client.get(
        url_for("builds.get_copr_build_logs_by_id", id_=str(build.id))
    )
    assert response.data.decode() == (
        "<html><head><title>COPR build the-namespace/the-repo-name: "
        "PR #342</title></head><body>COPR build ID: 123456<br>"
        "State: pending<br><br>Build web interface URL: "
        '<a href="https://copr.something.somewhere/123456">'
        "https://copr.something.somewhere/123456</a>"
        "<br>SRPM creation logs:<br><br><pre>"
        "some\nboring\nlogs</pre><br></body></html>"
    )


def test_koji_build_logs_view(client, clean_before_and_after, multiple_koji_builds):
    build = KojiBuildModel.get_by_build_id(123456, SampleValues.chroots[0])
    # Logs view uses the id of the SRPMBuildModel not CoprBuildModel
    response = client.get(
        url_for("builds.get_koji_build_logs_by_id", id_=str(build.id))
    )
    assert (
        response.data.decode()
        == "<html><head><title>Koji build the-namespace/the-repo-name: PR "
        "#342</title></head><body>Koji build ID: 123456<br>State: "
        "pending<br><br>Build web interface URL: "
        '<a href="https://koji.something.somewhere/123456">'
        "https://koji.something.somewhere/123456</a><br>SRPM "
        "creation logs:<br><br><pre>some\nboring\nlogs</pre><br></body></html>"
    )
