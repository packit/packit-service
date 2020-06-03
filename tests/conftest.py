# MIT License
#
# Copyright (c) 2018-2019 Red Hat, Inc.

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
import json
import os
from pathlib import Path

import pytest
from flexmock import flexmock

from ogr import GithubService, GitlabService
from packit.config import JobConfigTriggerType

from packit_service.config import ServiceConfig
from packit_service.models import JobTriggerModelType
from packit_service.service.events import (
    PullRequestGithubEvent,
    ReleaseEvent,
)
from packit_service.worker.parser import Parser
from tests.spellbook import SAVED_HTTPD_REQS, DATA_DIR


@pytest.fixture(scope="session", autouse=True)
def global_service_config():
    """
    This config will be used instead of the one loaded from the local config file.

    You can still mock/overwrite the service config content in your tests
    but this one will be used by default.
    """
    service_config = ServiceConfig()
    service_config.services = {
        GithubService(token="token"),
        GitlabService(token="token"),
    }
    service_config.dry_run = False
    service_config.server_name = "localhost"
    service_config.github_requests_log_path = "/path"
    ServiceConfig.service_config = service_config


@pytest.fixture()
def dump_http_com():
    """
    This fixture is able to dump whole http traffic of a single test case
    so that no http comm is happening while testing

    Usage:
    1. add it to your test case and pass the test path
      def test_something(dump_http_com):
        service_config = dump_http_com(f"{Path(__file__).name}/pr_handle.yaml")
    2. Run your test
      GITHUB_TOKEN=asdqwe pytest-3 -k test_something
    3. Your http communication should now be stored in tests/data/http-requests/{path}
    4. Once you rerun the tests WITHOUT the token, the offline communication should be picked up
    """

    def f(path: str):
        """ path points to a file where the http communication will be saved """
        conf = ServiceConfig()
        # TODO: add pagure support
        # conf._pagure_user_token = os.environ.get("PAGURE_TOKEN", "test")
        # conf._pagure_fork_token = os.environ.get("PAGURE_FORK_TOKEN", "test")
        conf._github_token = os.getenv("GITHUB_TOKEN", None)
        conf.dry_run = True
        target_path: Path = SAVED_HTTPD_REQS / path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        conf.github_requests_log_path = str(target_path)
        return conf

    return f


def copr_build_model(
    repo_name="bar",
    repo_namespace="foo",
    forge_instance="github.com",
    job_config_trigger_type=JobConfigTriggerType.pull_request,
    job_trigger_model_type=JobTriggerModelType.pull_request,
    **trigger_model_kwargs,
):
    project_model = flexmock(
        repo_name=repo_name,
        namespace=repo_namespace,
        project_url=f"https://{forge_instance}/{repo_namespace}/{repo_name}",
    )
    pr_model = flexmock(
        id=1,
        pr_id=123,
        project=project_model,
        job_config_trigger_type=job_config_trigger_type,
        **trigger_model_kwargs,
    )
    trigger_model = flexmock(
        id=2,
        type=job_trigger_model_type,
        trigger_id=1,
        get_trigger_object=lambda: pr_model,
    )
    return flexmock(
        id=1,
        build_id="1",
        commit_sha="0011223344",
        project_name="some-project",
        owner="some-owner",
        web_url="https://some-url",
        target="some-target",
        status="some-status",
        srpm_build=flexmock(logs="asdsdf"),
        job_trigger=trigger_model,
    )


@pytest.fixture(scope="module")
def copr_build_pr():
    return copr_build_model()


@pytest.fixture()
def koji_build_pr():
    project_model = flexmock(
        repo_name="bar", namespace="foo", project_url="https://github.com/foo/bar"
    )
    pr_model = flexmock(
        id=1,
        pr_id=123,
        project=project_model,
        job_config_trigger_type=JobConfigTriggerType.pull_request,
    )
    trigger_model = flexmock(
        id=2,
        type=JobTriggerModelType.pull_request,
        trigger_id=1,
        get_trigger_object=lambda: pr_model,
    )
    koji_build_model = flexmock(
        id=1,
        build_id="1",
        commit_sha="0011223344",
        project_name="some-project",
        owner="some-owner",
        web_url="https://some-url",
        target="some-target",
        status="some-status",
        srpm_build=flexmock(logs="asdsdf"),
        job_trigger=trigger_model,
    )

    return koji_build_model


@pytest.fixture(scope="module")
def github_release_webhook() -> dict:
    with open(DATA_DIR / "webhooks" / "github" / "release.json") as outfile:
        return json.load(outfile)


@pytest.fixture(scope="module")
def release_event(github_release_webhook) -> ReleaseEvent:
    return Parser.parse_release_event(github_release_webhook)


@pytest.fixture(scope="module")
def github_pr_webhook():
    with open(DATA_DIR / "webhooks" / "github" / "pr.json") as outfile:
        return json.load(outfile)


@pytest.fixture(scope="module")
def github_pr_event(github_pr_webhook) -> PullRequestGithubEvent:
    return Parser.parse_pr_event(github_pr_webhook)
