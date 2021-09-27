# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json
import os
from datetime import datetime
from pathlib import Path

import pytest
from flexmock import flexmock

from ogr import GithubService, GitlabService
from packit.config import JobConfigTriggerType
from packit_service.config import ServiceConfig
from packit_service.models import JobTriggerModelType, JobTriggerModel
from packit_service.worker.events import (
    PullRequestGithubEvent,
    PushGitHubEvent,
    ReleaseEvent,
    MergeRequestGitlabEvent,
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
        """path points to a file where the http communication will be saved"""
        conf = ServiceConfig()
        # TODO: add pagure support
        # conf._pagure_user_token = os.environ.get("PAGURE_TOKEN", "test")
        # conf._pagure_fork_token = os.environ.get("PAGURE_FORK_TOKEN", "test")
        conf._github_token = os.getenv("GITHUB_TOKEN", None)
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
        job_trigger_model_type=JobTriggerModelType.pull_request,
        **trigger_model_kwargs,
    )
    trigger_model = flexmock(
        id=2,
        type=job_trigger_model_type,
        trigger_id=1,
        get_trigger_object=lambda: pr_model,
    )

    runs = []
    srpm_build = flexmock(logs="asdsdf", url=None, runs=runs)
    copr_build = flexmock(
        id=1,
        build_id="1",
        commit_sha="0011223344",
        project_name="some-project",
        owner="some-owner",
        web_url="https://some-url",
        target="some-target",
        status="some-status",
        runs=runs,
        set_status=lambda x: None,
        set_built_packages=lambda x: None,
        built_packages=None,
        task_accepted_time=datetime.now(),
    )

    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=pr_model.job_trigger_model_type, trigger_id=pr_model.id
    ).and_return(trigger_model)

    def mock_set_status(status):
        copr_build.status = status

    def mock_set_built_packages(built_packages):
        copr_build.built_packages = built_packages

    copr_build.set_status = mock_set_status
    copr_build._srpm_build_for_mocking = srpm_build
    copr_build.get_trigger_object = lambda: pr_model
    copr_build.get_srpm_build = lambda: srpm_build

    run_model = flexmock(
        id=3, job_trigger=trigger_model, srpm_build=srpm_build, copr_build=copr_build
    )
    runs.append(run_model)

    return copr_build


@pytest.fixture()
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
        job_trigger_model_type=JobTriggerModelType.pull_request,
    )
    trigger_model = flexmock(
        id=2,
        type=JobTriggerModelType.pull_request,
        trigger_id=1,
        get_trigger_object=lambda: pr_model,
    )
    runs = []
    srpm_build = flexmock(logs="asdsdf", url=None, runs=runs)
    koji_build_model = flexmock(
        id=1,
        build_id="1",
        commit_sha="0011223344",
        project_name="some-project",
        owner="some-owner",
        web_url="https://some-url",
        target="some-target",
        status="some-status",
        runs=runs,
    )
    koji_build_model._srpm_build_for_mocking = srpm_build
    koji_build_model.get_trigger_object = lambda: pr_model
    koji_build_model.get_srpm_build = lambda: srpm_build

    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=pr_model.job_trigger_model_type, trigger_id=pr_model.id
    ).and_return(trigger_model)

    run_model = flexmock(
        id=3,
        job_trigger=trigger_model,
        srpm_build=srpm_build,
        copr_build=koji_build_model,
    )
    runs.append(run_model)

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
def github_push_webhook():
    with open(DATA_DIR / "webhooks" / "github" / "push_branch.json") as outfile:
        return json.load(outfile)


@pytest.fixture(scope="module")
def github_pr_event(github_pr_webhook) -> PullRequestGithubEvent:
    return Parser.parse_pr_event(github_pr_webhook)


@pytest.fixture(scope="module")
def github_push_event(github_push_webhook) -> PushGitHubEvent:
    return Parser.parse_push_event(github_push_webhook)


@pytest.fixture(scope="module")
def gitlab_mr_webhook():
    with open(DATA_DIR / "webhooks" / "gitlab" / "mr_event.json") as outfile:
        return json.load(outfile)


@pytest.fixture(scope="module")
def gitlab_mr_event(gitlab_mr_webhook) -> MergeRequestGitlabEvent:
    return Parser.parse_mr_event(gitlab_mr_webhook)


@pytest.fixture
def cache_clear(request):
    """
    Fixture which cleans lru_cache of functions defined in module variable CACHE_CLEAR.
    This allows reliable test results.

    :return:
    """

    if getattr(request.module, "CACHE_CLEAR", None):
        [f.cache_clear() for f in getattr(request.module, "CACHE_CLEAR")]
