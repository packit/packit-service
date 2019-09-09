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

"""
Tests for events parsing
"""

import json

import pytest
from flexmock import flexmock
from ogr.services.github import GithubProject, GithubService
from packit.config import JobTriggerType

from packit_service.config import Config
from packit_service.service.events import (
    WhitelistStatus,
    InstallationEvent,
    ReleaseEvent,
    PullRequestEvent,
    PullRequestAction,
    TestingFarmResultsEvent,
    TestingFarmResult,
    PullRequestCommentEvent,
    PullRequestCommentAction,
    IssueCommentEvent,
    IssueCommentAction,
)
from packit_service.worker.parser import Parser
from tests.spellbook import DATA_DIR


class TestEvents:
    @pytest.fixture()
    def installation(self):
        with open(DATA_DIR / "webhooks" / "installation.json", "r") as outfile:
            return json.load(outfile)

    @pytest.fixture()
    def release(self):
        with open(DATA_DIR / "webhooks" / "release_event.json", "r") as outfile:
            return json.load(outfile)

    @pytest.fixture()
    def pull_request(self):
        with open(DATA_DIR / "webhooks" / "github_pr_event.json", "r") as outfile:
            return json.load(outfile)

    @pytest.fixture()
    def issue_comment_request(self):
        with open(
            DATA_DIR / "webhooks" / "github_issue_propose_update.json", "r"
        ) as outfile:
            return json.load(outfile)

    @pytest.fixture()
    def testing_farm_results(self):
        with open(DATA_DIR / "webhooks" / "testing_farm_results.json", "r") as outfile:
            return json.load(outfile)

    @pytest.fixture()
    def pr_comment_created_request(self):
        with open(
            DATA_DIR / "webhooks" / "github_pr_comment_copr_build.json", "r"
        ) as outfile:
            return json.load(outfile)

    @pytest.fixture()
    def pr_comment_empty_request(self):
        with open(
            DATA_DIR / "webhooks" / "github_pr_comment_empty.json", "r"
        ) as outfile:
            return json.load(outfile)

    @pytest.fixture()
    def mock_config(self):
        config = flexmock(Config)
        config.github_app_id = 123123
        config.github_app_cert_path = None
        config.github_token = "token"
        config.dry_run = False
        config.github_requests_log_path = "/path"
        config.should_receive("get_service_config").and_return(flexmock(Config))

    def test_parse_installation(self, installation):
        event_object = Parser.parse_event(installation)

        assert isinstance(event_object, InstallationEvent)
        assert event_object.trigger == JobTriggerType.installation
        assert event_object.installation_id == 1173510
        assert event_object.account_login == "user-cont"
        assert event_object.account_id == 26160778
        assert event_object.account_url == "https://api.github.com/users/rpitonak"
        assert event_object.account_type == "User"
        assert event_object.created_at == 1560941425
        assert event_object.sender_login == "rpitonak"
        assert event_object.sender_id == 26160778
        assert event_object.status == WhitelistStatus.waiting

    def test_parse_release(self, release):
        event_object = Parser.parse_event(release)

        assert isinstance(event_object, ReleaseEvent)
        assert event_object.trigger == JobTriggerType.release
        assert event_object.repo_namespace == "Codertocat"
        assert event_object.repo_name == "Hello-World"
        assert event_object.tag_name == "0.0.1"
        assert event_object.https_url == "https://github.com/Codertocat/Hello-World"

    def test_parse_pr(self, pull_request):
        event_object = Parser.parse_event(pull_request)

        assert isinstance(event_object, PullRequestEvent)
        assert event_object.trigger == JobTriggerType.pull_request
        assert event_object.action == PullRequestAction.opened
        assert event_object.pr_id == 342
        assert event_object.base_repo_namespace == "packit-service"
        assert event_object.base_repo_name == "packit"
        assert event_object.base_ref == "528b803be6f93e19ca4130bf4976f2800a3004c4"
        assert event_object.target_repo == "packit-service/packit"
        assert event_object.https_url == "https://github.com/packit-service/packit"
        assert event_object.commit_sha == "528b803be6f93e19ca4130bf4976f2800a3004c4"

        def _get_f_c(*args, **kwargs):
            raise FileNotFoundError()

        flexmock(GithubProject, get_file_content=_get_f_c)
        flexmock(Config, get_service_config=Config())
        assert event_object.get_package_config() is None

    def test_parse_pr_comment_created(self, pr_comment_created_request):
        event_object = Parser.parse_event(pr_comment_created_request)

        assert isinstance(event_object, PullRequestCommentEvent)
        assert event_object.trigger == JobTriggerType.comment
        assert event_object.action == PullRequestCommentAction.created
        assert event_object.pr_id == 9
        assert event_object.base_repo_namespace == "packit-service"
        assert event_object.base_repo_name == "hello-world"
        assert (
            event_object.target_repo
            == f"{event_object.base_repo_namespace}/{event_object.base_repo_name}"
        )
        assert event_object.https_url == "https://github.com/packit-service/hello-world"
        assert event_object.github_login == "phracek"
        assert event_object.comment == "/packit copr-build"

    def test_parse_pr_comment_empty(self, pr_comment_empty_request):
        event_object = Parser.parse_event(pr_comment_empty_request)

        assert isinstance(event_object, PullRequestCommentEvent)
        assert event_object.trigger == JobTriggerType.comment
        assert event_object.action == PullRequestCommentAction.created
        assert event_object.pr_id == 9
        assert event_object.base_repo_namespace == "packit-service"
        assert event_object.base_repo_name == "hello-world"
        assert (
            event_object.target_repo
            == f"{event_object.base_repo_namespace}/{event_object.base_repo_name}"
        )
        assert event_object.https_url == "https://github.com/packit-service/hello-world"
        assert event_object.github_login == "phracek"
        assert event_object.comment == ""

    def test_parse_issue_comment(self, issue_comment_request):
        event_object = Parser.parse_event(issue_comment_request)

        assert isinstance(event_object, IssueCommentEvent)
        assert event_object.trigger == JobTriggerType.comment
        assert event_object.action == IssueCommentAction.created
        assert event_object.issue_id == 512
        assert event_object.base_repo_namespace == "packit-service"
        assert event_object.base_repo_name == "packit"
        assert (
            event_object.target_repo
            == f"{event_object.base_repo_namespace}/{event_object.base_repo_name}"
        )
        assert event_object.https_url == "https://github.com/packit-service/packit"
        assert event_object.github_login == "phracek"
        assert event_object.comment == "/packit propose-update"

    def test_parse_testing_farm_results(self, testing_farm_results):
        event_object = Parser.parse_event(testing_farm_results)

        assert isinstance(event_object, TestingFarmResultsEvent)
        assert event_object.trigger == JobTriggerType.testing_farm_results
        assert event_object.pipeline_id == "43e310b6-c1f1-4d3e-a95c-6c1eca235296"
        assert event_object.result == TestingFarmResult.passed
        assert event_object.repo_namespace == "packit-service"
        assert event_object.repo_name == "hello-world"
        assert event_object.ref == "pull/10/head"
        assert event_object.https_url == "https://github.com/packit-service/hello-world"
        assert event_object.commit_sha == "46597d9b66a1927b50376f73bdb1ec1a5757c330"
        assert event_object.message == "Error or info message to display"
        assert event_object.environment == "Fedora-Cloud-Base-29-1.2.x86_64.qcow2"
        assert event_object.copr_repo_name == "packit/packit-service-hello-world-10-stg"
        assert event_object.copr_chroot == "fedora-29-x86_64"

    def test_get_project_pr(self, pull_request, mock_config):
        event_object = Parser.parse_event(pull_request)

        assert isinstance(event_object, PullRequestEvent)

        flexmock(Config).should_receive("get_service_config").and_return(
            flexmock(Config)
        )
        project = event_object.get_project()

        assert isinstance(project, GithubProject)
        assert isinstance(project.service, GithubService)
        assert project.namespace == "packit-service"
        assert project.repo == "packit"

    def test_get_project_release(self, release, mock_config):
        event_object = Parser.parse_event(release)

        assert isinstance(event_object, ReleaseEvent)

        project = event_object.get_project()

        assert isinstance(project, GithubProject)
        assert isinstance(project.service, GithubService)
        assert project.namespace == "Codertocat"
        assert project.repo == "Hello-World"

    def test_get_project_testing_farm_results(self, testing_farm_results, mock_config):
        event_object = Parser.parse_event(testing_farm_results)

        assert isinstance(event_object, TestingFarmResultsEvent)

        project = event_object.get_project()

        assert isinstance(project, GithubProject)
        assert isinstance(project.service, GithubService)
        assert project.namespace == "packit-service"
        assert project.repo == "hello-world"
