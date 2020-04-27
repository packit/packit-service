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
from github import Github
from github.GitRelease import GitRelease as PyGithubRelease

from ogr.abstract import GitTag
from ogr.services.github import GithubProject, GithubRelease
from packit.config import JobConfigTriggerType
from packit.local_project import LocalProject
from packit_service.config import ServiceConfig
from packit_service.constants import SANDCASTLE_WORK_DIR
from packit_service.models import JobTriggerModelType
from packit_service.service.db_triggers import AddPullRequestDbTrigger
from packit_service.service.events import (
    PullRequestEvent,
    PushGitHubEvent,
    ReleaseEvent,
)
from packit_service.worker.parser import Parser
from packit_service.worker.whitelist import Whitelist
from tests.spellbook import SAVED_HTTPD_REQS, DATA_DIR


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


@pytest.fixture(
    params=[
        [
            {
                "trigger": "pull_request",
                "job": "copr_build",
                "metadata": {"targets": "fedora-rawhide-x86_64"},
            }
        ],
        [
            {
                "trigger": "pull_request",
                "job": "tests",
                "metadata": {"targets": "fedora-rawhide-x86_64"},
            }
        ],
        [
            {
                "trigger": "pull_request",
                "job": "copr_build",
                "metadata": {"targets": "fedora-rawhide-x86_64"},
            },
            {
                "trigger": "pull_request",
                "job": "tests",
                "metadata": {"targets": "fedora-rawhide-x86_64"},
            },
        ],
    ]
)
def mock_pr_comment_functionality(request):
    packit_yaml = (
        "{'specfile_path': '', 'synced_files': [], 'jobs': " + str(request.param) + "}"
    )
    flexmock(
        GithubProject,
        full_repo_name="packit-service/hello-world",
        get_file_content=lambda path, ref: packit_yaml,
        get_web_url=lambda: "https://github.com/the-namespace/the-repo",
        get_pr=lambda pr_id: flexmock(head_commit="12345"),
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)

    config = ServiceConfig()
    config.command_handler_work_dir = SANDCASTLE_WORK_DIR
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(config)
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(
        flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request)
    )
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Whitelist, check_and_report=True)


@pytest.fixture()
def mock_issue_comment_functionality():
    packit_yaml = (
        "{'specfile_path': '', 'synced_files': [],"
        "'jobs': [{'trigger': 'release', 'job': 'propose_downstream',"
        "'metadata': {'dist-git-branch': 'master'}}],"
        "'downstream_package_name': 'packit'}"
    )
    flexmock(
        GithubProject,
        get_file_content=lambda path, ref: packit_yaml,
        full_repo_name="packit-service/packit",
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)
    flexmock(GithubProject).should_receive("who_can_merge_pr").and_return({"phracek"})
    flexmock(GithubProject).should_receive("issue_comment").and_return(None)
    flexmock(GithubProject).should_receive("issue_close").and_return(None)
    gr = GithubRelease(
        tag_name="0.5.1",
        url="packit-service/packit",
        created_at="",
        tarball_url="https://foo/bar",
        git_tag=flexmock(GitTag),
        project=flexmock(GithubProject),
        raw_release=flexmock(PyGithubRelease),
    )
    flexmock(GithubProject).should_receive("get_releases").and_return([gr])
    config = ServiceConfig()
    config.command_handler_work_dir = SANDCASTLE_WORK_DIR
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(config)
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Whitelist, check_and_report=True)


@pytest.fixture()
def copr_build_pr():
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
    copr_build_model = flexmock(
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

    return copr_build_model


@pytest.fixture()
def copr_build_centos_pr():
    project_model = flexmock(
        repo_name="packit-hello-world",
        namespace="source-git",
        project_url="https://git.stg.centos.org/source-git/packit-hello-world",
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
    copr_build_model = flexmock(
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

    return copr_build_model


@pytest.fixture()
def copr_build_branch_push():
    project_model = flexmock(
        repo_name="bar", namespace="foo", project_url="https://github.com/foo/bar"
    )
    branch_model = flexmock(
        id=1,
        name="build-branch",
        project=project_model,
        job_config_trigger_type=JobConfigTriggerType.commit,
    )
    trigger_model = flexmock(
        id=2,
        type=JobTriggerModelType.branch_push,
        trigger_id=1,
        get_trigger_object=lambda: branch_model,
    )
    copr_build_model = flexmock(
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

    return copr_build_model


@pytest.fixture()
def copr_build_release():
    project_model = flexmock(
        repo_name="bar", namespace="foo", project_url="https://github.com/foo/bar"
    )
    release_model = flexmock(
        id=1,
        tag_name="v1.0.1",
        project=project_model,
        commit_hash="0011223344",
        job_config_trigger_type=JobConfigTriggerType.release,
    )
    trigger_model = flexmock(
        id=2,
        type=JobTriggerModelType.release,
        trigger_id=1,
        get_trigger_object=lambda: release_model,
    )
    copr_build_model = flexmock(
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

    return copr_build_model


@pytest.fixture()
def pull_request_webhook() -> dict:
    with open(DATA_DIR / "webhooks" / "github_pr_event.json", "r") as outfile:
        return json.load(outfile)


@pytest.fixture()
def branch_push_webhook() -> dict:
    with open(DATA_DIR / "webhooks" / "github_push_branch.json", "r") as outfile:
        return json.load(outfile)


@pytest.fixture()
def release_webhook() -> dict:
    with open(DATA_DIR / "webhooks" / "github_release_event.json", "r") as outfile:
        return json.load(outfile)


@pytest.fixture()
def branch_push_event(branch_push_webhook) -> PushGitHubEvent:
    return Parser.parse_push_event(branch_push_webhook)


@pytest.fixture()
def release_event(release_webhook) -> ReleaseEvent:
    return Parser.parse_release_event(release_webhook)


@pytest.fixture()
def pull_request_event(pull_request_webhook) -> PullRequestEvent:
    return Parser.parse_pr_event(pull_request_webhook)
