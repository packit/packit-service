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
import os
from pathlib import Path

import pytest
from copr.v3.client import Client as CoprClient
from flexmock import flexmock
from github import Github
from github.GitRelease import GitRelease as PyGithubRelease
from ogr.services.github import GithubProject, GithubRelease, GitTag
from packit.local_project import LocalProject

from packit_service.config import ServiceConfig
from packit_service.constants import SANDCASTLE_WORK_DIR
from packit_service.worker.whitelist import Whitelist
from tests.spellbook import SAVED_HTTPD_REQS


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


@pytest.fixture()
def mock_pr_comment_functionality():
    packit_yaml = (
        "{'specfile_path': '', 'synced_files': [],"
        "'jobs': [{'trigger': 'pull_request', 'job': 'copr_build',"
        "'metadata': {'targets': 'fedora-rawhide-x86_64'}}]}"
    )
    flexmock(
        GithubProject,
        get_file_content=lambda path, ref: packit_yaml,
        full_repo_name="packit-service/hello-world",
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)
    flexmock(GithubProject).should_receive("who_can_merge_pr").and_return({"phracek"})
    flexmock(GithubProject).should_receive("get_all_pr_commits").with_args(
        9
    ).and_return(["528b803be6f93e19ca4130bf4976f2800a3004c4"])
    config = ServiceConfig()
    config.command_handler_work_dir = SANDCASTLE_WORK_DIR
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(config)
    copr_dict = {
        "login": "stevejobs",
        "username": "stevejobs",
        "token": "apple",
        "copr_url": "https://copr.fedorainfracloud.org",
    }

    flexmock(CoprClient).should_receive("create_from_config_file").and_return(
        CoprClient(copr_dict)
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
    flexmock(GithubProject).should_receive("get_latest_release").and_return(gr)
    config = ServiceConfig()
    config.command_handler_work_dir = SANDCASTLE_WORK_DIR
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(config)
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Whitelist, check_and_report=True)
