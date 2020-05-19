"""
Let's test flask views.
"""

import pytest
from flexmock import flexmock

from packit_service.models import (
    CoprBuildModel,
    PullRequestModel,
    GitProjectModel,
    SRPMBuildModel,
)
from packit_service.service.app import application
from packit_service.service.urls import (
    get_copr_build_log_url_from_flask,
    get_srpm_log_url_from_flask,
)


@pytest.fixture
def client():
    application.config["TESTING"] = True
    # this affects all tests actually, heads up!
    application.config["SERVER_NAME"] = "localhost:5000"
    application.config["PREFERRED_URL_SCHEME"] = "https"

    with application.test_client() as client:
        yield client


@pytest.fixture(autouse=True)
def _setup_app_context_for_test():
    """
    Given app is session-wide, sets up a app context per test to ensure that
    app and request stack is not shared between tests.
    """
    ctx = application.app_context()
    ctx.push()
    yield  # tests will run here
    ctx.pop()


def test_get_logs(client):
    chroot = "foo-1-x86_64"
    state = "pending"
    build_id = 2

    project = GitProjectModel()
    project.namespace = "john-foo"
    project.repo_name = "bar"

    pr = PullRequestModel()
    pr.pr_id = 234
    pr.project = project

    srpm_build = SRPMBuildModel()
    srpm_build.logs = "asd<br>qwe"

    c = CoprBuildModel()
    c.target = chroot
    c.build_id = str(build_id)
    c.srpm_build = srpm_build
    c.status = state
    c.web_url = (
        "https://copr.fedorainfracloud.org/coprs/john-foo-bar/john-foo-bar/build/2/"
    )
    c.build_logs_url = "https://localhost:5000/build/2/foo-1-x86_64/logs"

    flexmock(CoprBuildModel).should_receive("get_by_id").and_return(c)
    flexmock(CoprBuildModel).should_receive("get_project").and_return(project)
    flexmock(CoprBuildModel).should_receive("job_trigger").and_return(
        flexmock(get_trigger_object=lambda: pr)
    )

    url = f"/copr-build/1/logs"
    logs_url = get_copr_build_log_url_from_flask(1)
    assert logs_url.endswith(url)

    resp = client.get(url)
    expected = (
        "<html><head>"
        f"<title>COPR build {project.namespace}/{project.repo_name}:"
        f" PR #{pr.pr_id}</title></head><body>"
        f"COPR build ID: {c.build_id}<br>"
        f"State: {c.status}<br><br>"
        f'Build web interface URL: <a href="{c.web_url}">{c.web_url}</a><br>'
        f'Build logs: <a href="{c.build_logs_url}">{c.build_logs_url}</a><br>'
        "SRPM creation logs:<br><br>"
        f"<pre>{c.srpm_build.logs}</pre>"
        "<br></body></html>"
    )
    assert resp.data == expected.encode()


def test_get_srpm_logs(client):
    srpm_build = SRPMBuildModel()
    srpm_build.id = 2
    srpm_build.logs = "asd\nqwe"

    flexmock(SRPMBuildModel).should_receive("get_by_id").and_return(srpm_build)

    url = f"/srpm-build/2/logs"
    logs_url = get_srpm_log_url_from_flask(2)
    assert logs_url.endswith(url)

    resp = client.get(url)
    expected = (
        "<html><head>"
        "<title>SRPM Build id=2</title></head><body>"
        "SRPM creation logs:<br><br>"
        "<pre>asd\nqwe</pre>"
        "<br></body></html>"
    )
    assert resp.data == expected.encode()
