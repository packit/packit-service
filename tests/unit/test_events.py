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
from datetime import datetime, timezone

import pytest
from flexmock import flexmock
from ogr.services.github import GithubProject, GithubService
from packit.config import JobTriggerType

from packit_service.config import ServiceConfig
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
    CoprBuildEvent,
    CoprBuildDB,
    FedmsgTopic,
    DistGitEvent,
)
from packit_service.worker.parser import Parser
from tests.spellbook import DATA_DIR


class TestEvents:
    @pytest.fixture()
    def installation(self, request):
        file = f"installation_{request.param}.json"
        with open(DATA_DIR / "webhooks" / file) as outfile:
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
        with open(DATA_DIR / "webhooks" / "github_pr_comment_empty.json") as outfile:
            return json.load(outfile)

    @pytest.fixture()
    def distgit_commit(self):
        with open(DATA_DIR / "webhooks" / "distgit_commit.json") as outfile:
            return json.load(outfile)

    @pytest.fixture()
    def copr_build_results_start(self):
        with open(DATA_DIR / "fedmsg" / "copr_build_start.json", "r") as outfile:
            return json.load(outfile)

    @pytest.fixture()
    def copr_build_results_end(self):
        with open(DATA_DIR / "fedmsg" / "copr_build_end.json", "r") as outfile:
            return json.load(outfile)

    @pytest.fixture()
    def mock_config(self):
        service_config = ServiceConfig()
        service_config.github_app_id = 123123
        service_config.github_app_cert_path = None
        service_config.github_token = "token"
        service_config.dry_run = False
        service_config.github_requests_log_path = "/path"
        ServiceConfig.service_config = service_config

    # https://stackoverflow.com/questions/35413134/what-does-indirect-true-false-in-pytest-mark-parametrize-do-mean
    @pytest.mark.parametrize("installation", ["added", "created"], indirect=True)
    def test_parse_installation(self, installation):
        event_object = Parser.parse_event(installation)

        assert isinstance(event_object, InstallationEvent)
        assert event_object.trigger == JobTriggerType.installation
        assert event_object.installation_id == 1708454
        assert event_object.account_login == "packit-service"
        assert event_object.account_id == 46870917
        assert event_object.account_url == "https://api.github.com/users/packit-service"
        assert event_object.account_type == "Organization"
        assert event_object.created_at == datetime.fromtimestamp(
            1567090283, timezone.utc
        )
        assert event_object.sender_login == "jpopelka"
        assert event_object.sender_id == 288686
        assert event_object.status == WhitelistStatus.waiting
        assert event_object.repositories == ["jpopelka/brewutils"]

    def test_parse_release(self, release):
        event_object = Parser.parse_event(release)

        assert isinstance(event_object, ReleaseEvent)
        assert event_object.trigger == JobTriggerType.release
        assert event_object.repo_namespace == "Codertocat"
        assert event_object.repo_name == "Hello-World"
        assert event_object.tag_name == "0.0.1"
        assert event_object.project_url == "https://github.com/Codertocat/Hello-World"

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
        assert event_object.project_url == "https://github.com/packit-service/packit"
        assert event_object.commit_sha == "528b803be6f93e19ca4130bf4976f2800a3004c4"

        def _get_f_c(*args, **kwargs):
            raise FileNotFoundError()

        flexmock(GithubProject, get_file_content=_get_f_c)
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
        assert (
            event_object.project_url == "https://github.com/packit-service/hello-world"
        )
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
        assert (
            event_object.project_url == "https://github.com/packit-service/hello-world"
        )
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
        assert event_object.base_ref == "master"
        assert event_object.project_url == "https://github.com/packit-service/packit"
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
        assert (
            event_object.project_url == "https://github.com/packit-service/hello-world"
        )
        assert event_object.commit_sha == "46597d9b66a1927b50376f73bdb1ec1a5757c330"
        assert event_object.message == "Error or info message to display"
        assert event_object.environment == "Fedora-Cloud-Base-29-1.2.x86_64.qcow2"
        assert event_object.copr_repo_name == "packit/packit-service-hello-world-10-stg"
        assert event_object.copr_chroot == "fedora-29-x86_64"

    def test_parse_copr_build_event_start(self, copr_build_results_start):
        flexmock(CoprBuildDB).should_receive("get_build").and_return(
            {"repo_name": "foo", "https_url": "url"}
        )

        event_object = Parser.parse_event(copr_build_results_start)

        assert isinstance(event_object, CoprBuildEvent)
        assert event_object.topic == FedmsgTopic.copr_build_started
        assert event_object.build_id == 1044215
        assert event_object.chroot == "fedora-rawhide-x86_64"
        assert event_object.status == 3
        assert event_object.owner == "packit"
        assert event_object.project_name == "packit-service-hello-world-24-stg"
        assert event_object.project_url == "url"
        assert event_object.base_repo_name == "foo"
        assert event_object.pkg == "hello"

    def test_parse_copr_build_event_end(self, copr_build_results_end):
        flexmock(CoprBuildDB).should_receive("get_build").and_return(
            {"repo_name": "foo", "https_url": "url"}
        )

        event_object = Parser.parse_event(copr_build_results_end)

        assert isinstance(event_object, CoprBuildEvent)
        assert event_object.topic == FedmsgTopic.copr_build_finished
        assert event_object.build_id == 1044215
        assert event_object.chroot == "fedora-rawhide-x86_64"
        assert event_object.status == 1
        assert event_object.owner == "packit"
        assert event_object.project_name == "packit-service-hello-world-24-stg"
        assert event_object.project_url == "url"
        assert event_object.base_repo_name == "foo"
        assert event_object.pkg == "hello"

    def test_get_project_pr(self, pull_request, mock_config):
        event_object = Parser.parse_event(pull_request)

        assert isinstance(event_object, PullRequestEvent)

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

    def test_distgit_commit(self, distgit_commit):
        event_object = Parser.parse_event(distgit_commit)

        assert isinstance(event_object, DistGitEvent)
        assert event_object.topic == FedmsgTopic.dist_git_push
        assert event_object.repo_namespace == "rpms"
        assert event_object.repo_name == "buildah"
        assert event_object.ref == "abcd"
        assert event_object.branch == "master"
        assert event_object.msg_id == "2019-49c02775-6d37-40a9-b108-879e3511c49a"
