"""
Let's test flask views.
"""

import pytest
from flexmock import flexmock

from packit_service.service.app import application
from packit_service.worker.copr_db import CoprBuildDB
from packit_service.worker.utils import get_copr_build_url_for_values


@pytest.fixture
def client():
    application.config["TESTING"] = True

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
    owner = "john-foo"
    chroot = "foo-1-x86_64"
    project_name = f"bar-{chroot}"
    build_id = 2
    web_url = get_copr_build_url_for_values(owner, project_name, build_id)
    flexmock(CoprBuildDB).should_receive("get_build").and_return(
        {
            "commit_sha": "abcde123456",
            "pr_id": 1,
            "repo_name": "",
            "repo_namespace": "",
            "ref": "branch",
            "https_url": f"https://github.com/{owner}/foo",
            "logs": "asd<br>qwe",
            "targets": {chroot: {"state": "pending", "build_logs": "https://logs"}},
            "web_url": web_url,
        }
    )

    resp = client.get(f"/build/{build_id}/{chroot}/logs")

    assert resp.data == (
        b"Build 2 is in state pending<br><br>"
        b"Build web interface URL: "
        b'<a href="https://copr.fedorainfracloud.org/coprs/john-foo/bar-foo-1-x86_64/build/2/">'
        b"https://copr.fedorainfracloud.org/coprs/john-foo/bar-foo-1-x86_64/build/2/</a><br>"
        b'Build logs: <a href="https://logs">https://logs</a><br>'
        b"SRPM creation logs:<br><br>asd<br>qwe<br>"
    )
