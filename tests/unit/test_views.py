"""
Let's test flask views.
"""
from datetime import datetime

import pytest
from flexmock import flexmock

from packit_service.models import (
    CoprBuildModel,
    JobTriggerModelType,
    SRPMBuildModel,
)
from packit_service.service.app import packit_as_a_service as application
from packit_service.service.urls import (
    get_copr_build_info_url_from_flask,
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
    state = "success"
    build_id = 2

    project = flexmock()
    project.namespace = "john-foo"
    project.repo_name = "bar"

    pr = flexmock()
    pr.job_trigger_model_type = JobTriggerModelType.pull_request
    pr.pr_id = 234
    pr.project = project

    srpm = flexmock()
    srpm.url = "https://some.random.copr.subdomain.org/my_srpm.srpm"
    srpm.build_submitted_time = datetime(
        year=2020, month=1, day=1, hour=0, minute=0, second=0, microsecond=0
    )

    c = flexmock()
    c.target = chroot
    c.build_id = str(build_id)
    c.srpm_build_id = 11
    c.status = state
    c.srpm_build = srpm
    c.web_url = (
        "https://copr.fedorainfracloud.org/coprs/john-foo-bar/john-foo-bar/build/2/"
    )
    c.build_logs_url = "https://localhost:5000/build/2/foo-1-x86_64/logs"
    c.owner = "packit"
    c.build_submitted_time = datetime(
        year=2020, month=1, day=1, hour=0, minute=0, second=0, microsecond=0
    )
    c.project_name = "example_project"
    c.should_receive("get_trigger_object").and_return(pr)
    c.should_receive("get_project").and_return(project)
    c.should_receive("get_srpm_build").and_return(srpm)

    flexmock(CoprBuildModel).should_receive("get_by_id").and_return(c)

    url = "/copr-build/1"
    logs_url = get_copr_build_info_url_from_flask(1)
    assert logs_url.endswith(url)

    resp = client.get(url).data.decode()
    assert f"srpm-build/{c.srpm_build_id}/logs" in resp
    assert c.web_url in resp
    assert c.build_logs_url in resp
    assert c.target in resp
    assert "Status: success" in resp
    assert "You can install" in resp

    assert "Download SRPM" in resp
    assert srpm.url in resp


def test_get_srpm_logs(client):
    srpm_build = flexmock()
    srpm_build.id = 2
    srpm_build.logs = "asd\nqwe"

    flexmock(SRPMBuildModel).should_receive("get_by_id").and_return(srpm_build)

    url = "/srpm-build/2/logs"
    logs_url = get_srpm_log_url_from_flask(2)
    assert logs_url.endswith(url)

    resp = client.get(url).data.decode()
    assert srpm_build.logs in resp
    assert f"build {srpm_build.id}" in resp
