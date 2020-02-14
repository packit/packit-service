"""
Let's test flask views.
"""

import pytest
from flexmock import flexmock

from packit_service.models import CoprBuild, PullRequest, GitProject, SRPMBuild
from packit_service.service.app import application
from packit_service.service.urls import get_log_url
from packit_service.worker.utils import get_copr_build_url_for_values


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

    project = GitProject()
    project.namespace = "john-foo"
    project.repo_name = "bar"

    pr = PullRequest()
    pr.pr_id = 234
    pr.project = project

    srpm_build = SRPMBuild()
    srpm_build.logs = "asd<br>qwe"

    web_url = get_copr_build_url_for_values(
        project.namespace, project.repo_name, build_id
    )
    c = CoprBuild()
    c.target = chroot
    c.build_id = str(build_id)
    c.srpm_build = srpm_build
    c.status = state
    c.web_url = web_url
    c.build_logs_url = "https://localhost:5000/build/2/foo-1-x86_64/logs"
    c.pr = pr

    flexmock(CoprBuild).should_receive("get_by_id").and_return(c)

    url = f"/copr-build/1/logs"
    logs_url = get_log_url(1)
    assert logs_url.endswith(url)

    resp = client.get(url)
    expected = (
        "<html><head>"
        f"<title>Build {c.pr.project.namespace}/{c.pr.project.repo_name}"
        f" #{c.pr.pr_id}</title></head><body>"
        f"COPR Build ID: {c.build_id}<br>"
        f"State: {c.status}<br><br>"
        f'Build web interface URL: <a href="{c.web_url}">{c.web_url}</a><br>'
        f'Build logs: <a href="{c.build_logs_url}">{c.build_logs_url}</a><br>'
        "SRPM creation logs:<br><br>"
        f"<pre>{c.srpm_build.logs}</pre>"
        "<br></body></html>"
    )
    assert resp.data == expected.encode()
