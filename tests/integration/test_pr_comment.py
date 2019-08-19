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

import pytest
from flexmock import flexmock
from github import Github
from tests.spellbook import DATA_DIR

from ogr.services.github import GithubProject

from copr.v3.client import Client as CoprClient

from packit.local_project import LocalProject
from packit.api import PackitAPI
from packit_service.config import Config
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.whitelist import Whitelist
from packit_service.constants import SANDCASTLE_WORK_DIR
from packit_service.worker.copr_build import CoprBuildHandler
from packit_service.worker.handler import HandlerResults


@pytest.fixture()
def pr_copr_build_comment_event():
    return json.loads(
        (DATA_DIR / "webhooks" / "github_pr_comment_copr_build.json").read_text()
    )


@pytest.fixture()
def pr_build_comment_event():
    return json.loads(
        (DATA_DIR / "webhooks" / "github_pr_comment_build.json").read_text()
    )


def test_pr_comment_copr_build_handler(pr_copr_build_comment_event):
    packit_yaml = (
        "{'specfile_path': '', 'synced_files': [],"
        "'jobs': [{'trigger': 'pull_request', 'job': 'copr_build',"
        "'metadata': {'targets': 'fedora-rawhide-x86_64'}}]}"
    )
    copr_dict = {
        "login": "stevejobs",
        "username": "stevejobs",
        "token": "apple",
        "copr_url": "https://copr.fedorainfracloud.org",
    }
    flexmock(Github, get_repo=lambda full_name_or_id: None)
    flexmock(
        GithubProject,
        get_file_content=lambda path, ref: packit_yaml,
        full_repo_name="packit-service/hello-world",
    )
    flexmock(CoprClient).should_receive("create_from_config_file").and_return(
        CoprClient(copr_dict)
    )
    flexmock(GithubProject).should_receive("who_can_merge_pr").and_return({"phracek"})
    flexmock(GithubProject).should_receive("get_all_pr_commits").with_args(
        9
    ).and_return(["528b803be6f93e19ca4130bf4976f2800a3004c4"])
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Whitelist, check_and_report=True)
    flexmock(CoprBuildHandler).should_receive("run_copr_build").and_return(
        HandlerResults(success=True, details={})
    )
    config = Config()
    config.command_handler_work_dir = SANDCASTLE_WORK_DIR
    flexmock(Config).should_receive("get_service_config").and_return(config)
    steve = SteveJobs()

    results = steve.process_message(pr_copr_build_comment_event)
    assert "pull_request_action" in results.get("jobs", {})
    assert "created" in results.get("event", {}).get("action", None)
    assert "comment" in results.get("trigger", None)
    assert results.get("jobs", {}).get("pull_request_action", {}).get("success")


def test_pr_comment_build_handler(pr_build_comment_event):
    packit_yaml = (
        "{'specfile_path': '', 'synced_files': [],"
        "'downstream_package_name': 'hello-world',"
        "'jobs': [{'trigger': 'pull_request', 'job': 'build',"
        "'metadata': {'targets': 'fedora-rawhide-x86_64'}}]}"
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)
    flexmock(
        GithubProject,
        get_file_content=lambda path, ref: packit_yaml,
        full_repo_name="packit-service/hello-world",
    )
    flexmock(GithubProject).should_receive("who_can_merge_pr").and_return({"phracek"})
    flexmock(GithubProject).should_receive("get_all_pr_commits").with_args(
        9
    ).and_return(["528b803be6f93e19ca4130bf4976f2800a3004c4"])
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Whitelist, check_and_report=True)
    flexmock(PackitAPI).should_receive("build").with_args(
        dist_git_branch="master"
    ).once()
    config = Config()
    config.command_handler_work_dir = SANDCASTLE_WORK_DIR
    flexmock(Config).should_receive("get_service_config").and_return(config)
    steve = SteveJobs()

    results = steve.process_message(pr_build_comment_event)
    assert "pull_request_action" in results.get("jobs", {})
    assert "created" in results.get("event", {}).get("action", None)
    assert "comment" in results.get("trigger", None)
    assert results.get("jobs", {}).get("pull_request_action", {}).get("success")
