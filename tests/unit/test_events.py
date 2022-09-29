# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
Tests for events parsing
"""
import json
from datetime import datetime, timezone, timedelta

import pytest
from flexmock import flexmock
from ogr.services.github import GithubProject, GithubService
from ogr.services.gitlab import GitlabProject, GitlabService
from ogr.services.pagure import PagureProject

from ogr import PagureService
from packit_service.config import ServiceConfig, PackageConfigGetter
from packit_service.constants import KojiBuildState, KojiTaskState
from packit_service.models import (
    CoprBuildTargetModel,
    KojiBuildTargetModel,
    TestingFarmResult,
    TFTTestRunTargetModel,
    AllowlistStatus,
    JobTriggerModel,
    GitBranchModel,
    ProjectReleaseModel,
    PullRequestModel,
    get_submitted_time_from_model,
    get_most_recent_targets,
)
from packit_service.worker.events import (
    KojiTaskEvent,
    PushPagureEvent,
    TestingFarmResultsEvent,
    AbstractCoprBuildEvent,
    IssueCommentGitlabEvent,
    MergeRequestCommentGitlabEvent,
    MergeRequestGitlabEvent,
    PushGitlabEvent,
    InstallationEvent,
    IssueCommentEvent,
    PullRequestCommentGithubEvent,
    PullRequestGithubEvent,
    PushGitHubEvent,
    ReleaseEvent,
    PullRequestCommentPagureEvent,
    PipelineGitlabEvent,
    CheckRerunCommitEvent,
    CheckRerunPullRequestEvent,
    CheckRerunReleaseEvent,
)
from packit_service.worker.events.enums import (
    PullRequestAction,
    PullRequestCommentAction,
    IssueCommentAction,
    FedmsgTopic,
    GitlabEventAction,
)
from packit_service.worker.events.koji import KojiBuildEvent
from packit_service.worker.events.pagure import PullRequestFlagPagureEvent
from packit_service.worker.helpers.testing_farm import TestingFarmJobHelper
from packit_service.worker.parser import Parser
from tests.spellbook import DATA_DIR


@pytest.fixture(scope="module")
def copr_build_results_start():
    with open(DATA_DIR / "fedmsg" / "copr_build_start.json") as outfile:
        return json.load(outfile)


@pytest.fixture(scope="module")
def copr_build_results_end():
    with open(DATA_DIR / "fedmsg" / "copr_build_end.json") as outfile:
        return json.load(outfile)


class TestEvents:
    @pytest.fixture()
    def github_installation(self):
        file = "installation_created.json"
        with open(DATA_DIR / "webhooks" / "github" / file) as outfile:
            return json.load(outfile)

    @pytest.fixture()
    def github_issue_comment_propose_downstream(self):
        with open(
            DATA_DIR / "webhooks" / "github" / "issue_propose_downstream.json"
        ) as outfile:
            return json.load(outfile)

    @pytest.fixture()
    def github_issue_comment_no_handler(self):
        return json.loads(
            (
                DATA_DIR / "webhooks" / "github" / "issue_comment_no_handler.json"
            ).read_text()
        )

    @pytest.fixture()
    def testing_farm_notification(self):
        with open(
            DATA_DIR / "webhooks" / "testing_farm" / "notification.json"
        ) as outfile:
            return json.load(outfile)

    @pytest.fixture()
    def testing_farm_results(self):
        with open(DATA_DIR / "webhooks" / "testing_farm" / "results.json") as outfile:
            return json.load(outfile)

    @pytest.fixture()
    def testing_farm_results_error(self):
        with open(
            DATA_DIR / "webhooks" / "testing_farm" / "results_error.json"
        ) as outfile:
            return json.load(outfile)

    @pytest.fixture()
    def github_pr_comment_created(self):
        with open(
            DATA_DIR / "webhooks" / "github" / "pr_comment_copr_build.json"
        ) as outfile:
            return json.load(outfile)

    @pytest.fixture()
    def merge_request(self):
        with open(DATA_DIR / "webhooks" / "gitlab" / "mr_event.json") as outfile:
            return json.load(outfile)

    @pytest.fixture()
    def merge_request_update(self):
        with open(DATA_DIR / "webhooks" / "gitlab" / "mr_update_event.json") as outfile:
            return json.load(outfile)

    @pytest.fixture()
    def merge_request_closed(self):
        with open(DATA_DIR / "webhooks" / "gitlab" / "mr_closed.json") as outfile:
            return json.load(outfile)

    @pytest.fixture()
    def github_pr_comment_empty(self):
        with open(
            DATA_DIR / "webhooks" / "github" / "pr_comment_empty.json"
        ) as outfile:
            return json.load(outfile)

    @pytest.fixture()
    def github_push(self):
        with open(DATA_DIR / "webhooks" / "github" / "push.json") as outfile:
            return json.load(outfile)

    @pytest.fixture()
    def github_push_branch(self):
        with open(DATA_DIR / "webhooks" / "github" / "push_branch.json") as outfile:
            return json.load(outfile)

    @pytest.fixture()
    def gitlab_push(self):
        with open(
            DATA_DIR / "webhooks" / "gitlab" / "push_with_one_commit.json"
        ) as outfile:
            return json.load(outfile)

    @pytest.fixture()
    def gitlab_push_many_commits(self):
        with open(
            DATA_DIR / "webhooks" / "gitlab" / "push_with_many_commits.json"
        ) as outfile:
            return json.load(outfile)

    @pytest.fixture()
    def gitlab_issue_comment(self):
        with open(DATA_DIR / "webhooks" / "gitlab" / "issue_comment.json") as outfile:
            return json.load(outfile)

    @pytest.fixture()
    def gitlab_mr_comment(self):
        with open(DATA_DIR / "webhooks" / "gitlab" / "mr_comment.json") as outfile:
            return json.load(outfile)

    @pytest.fixture()
    def gitlab_mr_pipeline(self):
        with open(DATA_DIR / "webhooks" / "gitlab" / "mr_pipeline.json") as outfile:
            return json.load(outfile)

    @pytest.fixture()
    def pagure_pr_flag_updated(self):
        with open(DATA_DIR / "fedmsg" / "pagure_pr_flag_updated.json") as outfile:
            return json.load(outfile)

    @pytest.fixture()
    def pagure_pr_comment_added(self):
        with open(DATA_DIR / "fedmsg" / "pagure_pr_comment.json") as outfile:
            return json.load(outfile)

    @pytest.fixture()
    def distgit_commit(self):
        with open(DATA_DIR / "fedmsg" / "distgit_commit.json") as outfile:
            return json.load(outfile)

    @pytest.fixture()
    def check_rerun(self):
        with open(
            DATA_DIR / "webhooks" / "github" / "checkrun_rerequested.json"
        ) as outfile:
            return json.load(outfile)

    @pytest.fixture()
    def copr_models(self):
        time = datetime(2000, 4, 28, 14, 9, 33, 860293)
        latest_time = datetime.utcnow()
        fake_copr = flexmock(build_id="1", build_submitted_time=time, target="target")
        flexmock(CoprBuildTargetModel).new_instances(fake_copr)
        copr = CoprBuildTargetModel()
        copr.__class__ = CoprBuildTargetModel

        another_fake_copr = flexmock(
            build_id="2", build_submitted_time=latest_time, target="target"
        )
        flexmock(CoprBuildTargetModel).new_instances(another_fake_copr)
        another_copr = CoprBuildTargetModel()
        another_copr.__class__ = CoprBuildTargetModel

        yield [copr, another_copr]

    @pytest.fixture()
    def tf_models(self):
        time = datetime(2000, 4, 28, 14, 9, 33, 860293)
        latest_time = datetime.utcnow()
        fake_tf = flexmock(pipeline_id="1", submitted_time=time, target="target")
        flexmock(TFTTestRunTargetModel).new_instances(fake_tf)
        tf = TFTTestRunTargetModel()
        tf.__class__ = TFTTestRunTargetModel

        another_fake_tf = flexmock(
            pipeline_id="2", submitted_time=latest_time, target="target"
        )
        flexmock(TFTTestRunTargetModel).new_instances(another_fake_tf)
        another_tf = TFTTestRunTargetModel()
        another_tf.__class__ = TFTTestRunTargetModel
        yield [tf, another_tf]

    @pytest.fixture()
    def mock_config(self):
        service_config = ServiceConfig()
        service_config.services = {
            GithubService(token="token"),
            GitlabService(token="token"),
            PagureService(instance_url="https://src.fedoraproject.org", token="1234"),
        }
        service_config.github_requests_log_path = "/path"
        ServiceConfig.service_config = service_config

    def test_parse_installation(self, github_installation):
        event_object = Parser.parse_event(github_installation)

        assert isinstance(event_object, InstallationEvent)
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
        assert event_object.status == AllowlistStatus.waiting
        assert event_object.repositories == ["jpopelka/brewutils"]

    def test_parse_release(self, github_release_webhook):
        event_object = Parser.parse_event(github_release_webhook)

        assert isinstance(event_object, ReleaseEvent)
        assert event_object.repo_namespace == "packit-service"
        assert event_object.repo_name == "hello-world"
        assert event_object.tag_name == "0.3.0"
        assert (
            event_object.project_url == "https://github.com/packit-service/hello-world"
        )

    def test_parse_mr(self, merge_request):
        event_object = Parser.parse_event(merge_request)

        assert isinstance(event_object, MergeRequestGitlabEvent)
        assert event_object.action == GitlabEventAction.opened
        assert event_object.object_id == 58759529
        assert event_object.identifier == "1"
        assert event_object.source_repo_namespace == "testing/packit"
        assert event_object.source_repo_name == "hello-there"
        assert event_object.source_repo_branch == "test"
        assert event_object.commit_sha == "1f6a716aa7a618a9ffe56970d77177d99d100022"
        assert event_object.target_repo_namespace == "testing/packit"
        assert event_object.target_repo_name == "hello-there"
        assert event_object.target_repo_branch == "master"
        assert (
            event_object.project_url == "https://gitlab.com/testing/packit/hello-there"
        )

        assert isinstance(event_object.project, GitlabProject)
        assert event_object.project.full_repo_name == "testing/packit/hello-there"
        assert event_object.base_project.full_repo_name == "testing/packit/hello-there"

        flexmock(PackageConfigGetter).should_receive(
            "get_package_config_from_repo"
        ).with_args(
            base_project=event_object.base_project,
            project=event_object.project,
            pr_id=1,
            reference="1f6a716aa7a618a9ffe56970d77177d99d100022",
            fail_when_missing=False,
        ).and_return(
            flexmock()
        ).once()
        assert event_object.package_config

    def test_parse_mr_action(self, merge_request_update):
        event_object = Parser.parse_event(merge_request_update)
        assert isinstance(event_object, MergeRequestGitlabEvent)
        assert event_object.action == GitlabEventAction.update
        assert event_object.commit_sha == "45e272a57335e4e308f3176df6e9226a9e7805a9"
        assert event_object.oldrev == "94ccba9f986629e24b432c11d9c7fd20bb2ea51d"
        assert event_object.identifier == "2"

        assert isinstance(event_object.project, GitlabProject)
        assert event_object.project.full_repo_name == "testing/packit/hello-there"
        assert event_object.base_project.full_repo_name == "testing/packit/hello-there"

        flexmock(PackageConfigGetter).should_receive(
            "get_package_config_from_repo"
        ).with_args(
            base_project=event_object.base_project,
            project=event_object.project,
            pr_id=2,
            reference="45e272a57335e4e308f3176df6e9226a9e7805a9",
            fail_when_missing=False,
        ).and_return(
            flexmock()
        ).once()
        assert event_object.package_config

    def test_parse_mr_closed(self, merge_request_closed):
        event_object = Parser.parse_event(merge_request_closed)
        assert isinstance(event_object, MergeRequestGitlabEvent)
        assert event_object.action == GitlabEventAction.closed

    def test_parse_pr(self, github_pr_webhook):
        event_object = Parser.parse_event(github_pr_webhook)

        assert isinstance(event_object, PullRequestGithubEvent)
        assert event_object.action == PullRequestAction.opened
        assert event_object.pr_id == 342
        assert event_object.base_repo_namespace == "lbarcziova"
        assert event_object.base_repo_name == "packit"
        assert event_object.base_ref == "528b803be6f93e19ca4130bf4976f2800a3004c4"
        assert event_object.target_repo_namespace == "packit-service"
        assert event_object.target_repo_name == "packit"
        assert event_object.project_url == "https://github.com/packit-service/packit"
        assert event_object.commit_sha == "528b803be6f93e19ca4130bf4976f2800a3004c4"

        assert isinstance(event_object.project, GithubProject)
        assert event_object.project.full_repo_name == "packit-service/packit"
        assert (
            not event_object.base_project  # With Github app, we cannot work with fork repo
        )

        flexmock(PackageConfigGetter).should_receive(
            "get_package_config_from_repo"
        ).with_args(
            base_project=None,
            project=event_object.project,
            pr_id=342,
            reference="528b803be6f93e19ca4130bf4976f2800a3004c4",
            fail_when_missing=False,
        ).and_return(
            flexmock()
        ).once()
        assert event_object.package_config

    def test_parse_pr_comment_created(self, github_pr_comment_created):
        event_object = Parser.parse_event(github_pr_comment_created)

        assert isinstance(event_object, PullRequestCommentGithubEvent)
        assert event_object.action == PullRequestCommentAction.created
        assert event_object.pr_id == 9
        assert event_object.base_repo_namespace == "phracek"
        assert event_object.base_repo_name is None  # It's not present in the payload
        assert event_object.target_repo_namespace == "packit-service"
        assert event_object.target_repo_name == "hello-world"
        assert (
            event_object.project_url == "https://github.com/packit-service/hello-world"
        )
        assert event_object.actor == "phracek"
        assert event_object.comment == "/packit copr-build"

        assert isinstance(event_object.project, GithubProject)
        assert event_object.project.full_repo_name == "packit-service/hello-world"
        assert (
            not event_object.base_project  # With Github app, we cannot work with fork repo
        )

        flexmock(GithubProject).should_receive("get_pr").with_args(pr_id=9).and_return(
            flexmock(head_commit="12345")
        )

        flexmock(PackageConfigGetter).should_receive(
            "get_package_config_from_repo"
        ).with_args(
            base_project=None,
            project=event_object.project,
            pr_id=9,
            reference="12345",
            fail_when_missing=False,
        ).and_return(
            flexmock()
        ).once()
        assert event_object.package_config

    def test_parse_mr_comment(self, gitlab_mr_comment):
        event_object = Parser.parse_event(gitlab_mr_comment)

        assert isinstance(event_object, MergeRequestCommentGitlabEvent)
        assert event_object.action == GitlabEventAction.opened
        assert event_object.pr_id == 2
        assert event_object.source_repo_namespace == "testing/packit"
        assert event_object.source_repo_name == "hello-there"
        assert event_object.target_repo_namespace == "testing/packit"
        assert event_object.target_repo_name == "hello-there"
        assert (
            event_object.project_url == "https://gitlab.com/testing/packit/hello-there"
        )
        assert event_object.user_login == "shreyaspapi"
        assert event_object.comment == "must be reopened"
        assert event_object.commit_sha == "45e272a57335e4e308f3176df6e9226a9e7805a9"

        assert isinstance(event_object.project, GitlabProject)
        assert event_object.project.full_repo_name == "testing/packit/hello-there"

        assert isinstance(event_object.project, GitlabProject)
        assert event_object.base_project.full_repo_name == "testing/packit/hello-there"

        flexmock(PackageConfigGetter).should_receive(
            "get_package_config_from_repo"
        ).with_args(
            base_project=event_object.base_project,
            project=event_object.project,
            pr_id=2,
            reference="45e272a57335e4e308f3176df6e9226a9e7805a9",
            fail_when_missing=False,
        ).and_return(
            flexmock()
        ).once()
        assert event_object.package_config

    def test_parse_pr_comment_empty(self, github_pr_comment_empty):
        event_object = Parser.parse_event(github_pr_comment_empty)

        assert isinstance(event_object, PullRequestCommentGithubEvent)
        assert event_object.action == PullRequestCommentAction.created
        assert event_object.pr_id == 9
        assert event_object.base_repo_namespace == "phracek"
        assert event_object.base_repo_name is None  # It's not present in the payload
        assert event_object.target_repo_namespace == "packit-service"
        assert event_object.target_repo_name == "hello-world"
        assert (
            event_object.project_url == "https://github.com/packit-service/hello-world"
        )
        assert event_object.actor == "phracek"
        assert event_object.comment == ""

        assert isinstance(event_object.project, GithubProject)
        assert event_object.project.full_repo_name == "packit-service/hello-world"
        assert (
            not event_object.base_project  # With Github app, we cannot work with fork repo
        )

        flexmock(GithubProject).should_receive("get_pr").with_args(pr_id=9).and_return(
            flexmock(head_commit="12345")
        )

        flexmock(PackageConfigGetter).should_receive(
            "get_package_config_from_repo"
        ).with_args(
            base_project=None,
            project=event_object.project,
            pr_id=9,
            reference="12345",
            fail_when_missing=False,
        ).and_return(
            flexmock()
        ).once()

        assert event_object.package_config

    def test_parse_issue_comment(self, github_issue_comment_propose_downstream):
        event_object = Parser.parse_event(github_issue_comment_propose_downstream)

        assert isinstance(event_object, IssueCommentEvent)
        assert event_object.action == IssueCommentAction.created
        assert event_object.issue_id == 512
        assert event_object.repo_namespace == "packit-service"
        assert event_object.repo_name == "packit"
        assert (
            event_object.target_repo
            == f"{event_object.repo_namespace}/{event_object.repo_name}"
        )
        assert event_object.base_ref == "master"
        assert event_object.project_url == "https://github.com/packit-service/packit"
        assert event_object.actor == "phracek"
        assert event_object.comment == "/packit propose-downstream"

        assert isinstance(event_object.project, GithubProject)
        assert event_object.project.full_repo_name == "packit-service/packit"
        assert not event_object.base_project

        flexmock(event_object.project).should_receive("get_latest_release").and_return(
            flexmock(tag_name="0.5.0")
        )
        flexmock(GithubProject, get_sha_from_tag=lambda tag_name: "123456")
        flexmock(PackageConfigGetter).should_receive(
            "get_package_config_from_repo"
        ).with_args(
            base_project=event_object.base_project,
            project=event_object.project,
            pr_id=None,
            reference="123456",
            fail_when_missing=False,
        ).and_return(
            flexmock()
        ).once()
        assert event_object.package_config

    def test_parse_issue_comment_no_handler(self, github_issue_comment_no_handler):
        event_object = Parser.parse_event(github_issue_comment_no_handler)

        assert isinstance(event_object, IssueCommentEvent)
        assert isinstance(event_object.project, GithubProject)
        assert event_object.project.full_repo_name == "packit-service/packit"
        assert not event_object.base_project

        flexmock(event_object.project).should_receive("get_latest_release").and_return(
            None
        )
        flexmock(GithubProject).should_receive("get_sha_from_tag").never()
        flexmock(PackageConfigGetter).should_receive(
            "get_package_config_from_repo"
        ).with_args(
            base_project=event_object.base_project,
            project=event_object.project,
            pr_id=None,
            reference=None,
            fail_when_missing=False,
        ).and_return(
            flexmock()
        ).once()
        assert event_object.package_config
        assert event_object.commit_sha is None
        assert event_object.tag_name == ""

    def test_parse_gitlab_issue_comment(self, gitlab_issue_comment):
        event_object = Parser.parse_event(gitlab_issue_comment)

        assert isinstance(event_object, IssueCommentGitlabEvent)
        assert event_object.action == GitlabEventAction.opened
        assert event_object.issue_id == 1
        assert event_object.repo_namespace == "testing/packit"
        assert event_object.repo_name == "hello-there"

        assert (
            event_object.project_url == "https://gitlab.com/testing/packit/hello-there"
        )
        assert event_object.user_login == "shreyaspapi"
        assert event_object.comment == "testing comment"

        assert isinstance(event_object.project, GitlabProject)
        assert event_object.project.full_repo_name == "testing/packit/hello-there"

        flexmock(event_object.project).should_receive("get_latest_release").and_return(
            flexmock(tag_name="0.5.0")
        )
        flexmock(event_object.project, get_sha_from_tag=lambda tag_name: "123456")
        flexmock(PackageConfigGetter).should_receive(
            "get_package_config_from_repo"
        ).with_args(
            base_project=event_object.base_project,
            project=event_object.project,
            pr_id=None,
            reference="123456",
            fail_when_missing=False,
        ).and_return(
            flexmock()
        ).once()
        assert event_object.package_config

    def test_parse_github_push(self, github_push_branch):
        event_object = Parser.parse_event(github_push_branch)

        assert isinstance(event_object, PushGitHubEvent)
        assert event_object.repo_namespace == "packit-service"
        assert event_object.repo_name == "hello-world"
        assert event_object.commit_sha == "04885ff850b0fa0e206cd09db73565703d48f99b"
        assert (
            event_object.project_url == "https://github.com/packit-service/hello-world"
        )
        assert event_object.git_ref == "build-branch"

        assert isinstance(event_object.project, GithubProject)
        assert event_object.project.full_repo_name == "packit-service/hello-world"
        assert not event_object.base_project

        flexmock(PackageConfigGetter).should_receive(
            "get_package_config_from_repo"
        ).with_args(
            base_project=event_object.base_project,
            project=event_object.project,
            pr_id=None,
            reference="04885ff850b0fa0e206cd09db73565703d48f99b",
            fail_when_missing=False,
        ).and_return(
            flexmock()
        ).once()
        assert event_object.package_config

    def test_parse_gitlab_push(self, gitlab_push):
        event_object = Parser.parse_event(gitlab_push)

        assert isinstance(event_object, PushGitlabEvent)
        assert event_object.repo_namespace == "testing/packit"
        assert event_object.repo_name == "hello-there"
        assert event_object.commit_sha == "cb2859505e101785097e082529dced35bbee0c8f"
        assert (
            event_object.project_url == "https://gitlab.com/testing/packit/hello-there"
        )
        assert event_object.git_ref == "test2"

        assert isinstance(event_object.project, GitlabProject)
        assert event_object.project.full_repo_name == "testing/packit/hello-there"
        assert not event_object.base_project

        flexmock(PackageConfigGetter).should_receive(
            "get_package_config_from_repo"
        ).with_args(
            base_project=event_object.base_project,
            project=event_object.project,
            pr_id=None,
            reference="cb2859505e101785097e082529dced35bbee0c8f",
            fail_when_missing=False,
        ).and_return(
            flexmock()
        ).once()
        assert event_object.package_config

    def test_parse_gitlab_push_many_commits(self, gitlab_push_many_commits):
        event_object = Parser.parse_event(gitlab_push_many_commits)

        assert isinstance(event_object, PushGitlabEvent)
        assert event_object.repo_namespace == "mike"
        assert event_object.repo_name == "diaspora"
        assert event_object.commit_sha == "da1560886d4f094c3e6c9ef40349f7d38b5d27d7"
        assert event_object.project_url == "http://gitlab.com/mike/diaspora"
        assert event_object.git_ref == "master"

        assert isinstance(event_object.project, GitlabProject)
        assert event_object.project.full_repo_name == "mike/diaspora"
        assert not event_object.base_project

        flexmock(PackageConfigGetter).should_receive(
            "get_package_config_from_repo"
        ).with_args(
            base_project=event_object.base_project,
            project=event_object.project,
            pr_id=None,
            reference="da1560886d4f094c3e6c9ef40349f7d38b5d27d7",
            fail_when_missing=False,
        ).and_return(
            flexmock()
        ).once()
        assert event_object.package_config

    def test_parse_github_push_branch(self, github_push_branch):
        event_object = Parser.parse_event(github_push_branch)

        assert isinstance(event_object, PushGitHubEvent)
        assert event_object.repo_namespace == "packit-service"
        assert event_object.repo_name == "hello-world"
        assert event_object.commit_sha == "04885ff850b0fa0e206cd09db73565703d48f99b"
        assert (
            event_object.project_url == "https://github.com/packit-service/hello-world"
        )
        assert event_object.git_ref == "build-branch"

        assert isinstance(event_object.project, GithubProject)
        assert event_object.project.full_repo_name == "packit-service/hello-world"
        assert not event_object.base_project

        flexmock(PackageConfigGetter).should_receive(
            "get_package_config_from_repo"
        ).with_args(
            base_project=event_object.base_project,
            project=event_object.project,
            pr_id=None,
            reference="04885ff850b0fa0e206cd09db73565703d48f99b",
            fail_when_missing=False,
        ).and_return(
            flexmock()
        ).once()

        assert event_object.package_config

    def test_parse_gitlab_pipeline(self, gitlab_mr_pipeline):
        event_object = Parser.parse_event(gitlab_mr_pipeline)

        assert isinstance(event_object, PipelineGitlabEvent)
        assert (
            event_object.project_url
            == "https://gitlab.com/redhat/centos-stream/rpms/luksmeta"
        )
        assert event_object.project_name == "luksmeta"
        assert event_object.pipeline_id == 384095584
        assert event_object.git_ref == "9-c9s-src-5"
        assert event_object.status == "failed"
        assert event_object.detailed_status == "failed"
        assert event_object.source == "merge_request_event"
        assert event_object.commit_sha == "ee58e259da263ecb4c1f0129be7aef8cfd4dedd6"
        assert (
            event_object.merge_request_url
            == "https://gitlab.com/redhat/centos-stream/rpms/luksmeta/-/merge_requests/4"
        )

        flexmock(PullRequestModel).should_receive("get_or_create").and_return(
            flexmock()
        )
        # assert event_object.db_trigger
        assert isinstance(event_object.project, GitlabProject)
        assert (
            event_object.project.full_repo_name == "redhat/centos-stream/rpms/luksmeta"
        )
        assert not event_object.base_project

        flexmock(PackageConfigGetter).should_receive(
            "get_package_config_from_repo"
        ).with_args(
            base_project=event_object.base_project,
            project=event_object.project,
            pr_id=None,
            reference="ee58e259da263ecb4c1f0129be7aef8cfd4dedd6",
            fail_when_missing=False,
        ).and_return(
            flexmock()
        ).once()
        assert event_object.package_config

    def test_parse_pagure_flag(self, pagure_pr_flag_updated):
        event_object = Parser.parse_event(pagure_pr_flag_updated)

        assert isinstance(event_object, PullRequestFlagPagureEvent)
        assert event_object.project_url == "https://src.fedoraproject.org/rpms/packit"
        assert event_object.pr_id == 268

        assert event_object.username == "Zuul"
        assert event_object.comment == "Jobs result is success"
        assert event_object.status == "success"
        assert event_object.date_updated == 1646754511
        assert (
            event_object.url
            == "https://fedora.softwarefactory-project.io/zuul/buildset/66ec2c23c78446afa2fd993"
        )
        assert event_object.commit_sha == "c69960e6f562c90905435fec824fcae952abfad6"
        assert (
            event_object.pr_url
            == "https://src.fedoraproject.org/rpms/packit/pull-request/268"
        )
        assert event_object.pr_source_branch == "0.47.0-f36-update"
        assert event_object.project_name == "packit"
        assert event_object.project_namespace == "rpms"

    def test_parse_pagure_pull_request_comment(self, pagure_pr_comment_added):
        event_object = Parser.parse_event(pagure_pr_comment_added)

        assert isinstance(event_object, PullRequestCommentPagureEvent)
        assert event_object.pr_id == 36
        assert event_object.base_repo_namespace == "rpms"
        assert event_object.base_repo_name == "python-teamcity-messages"
        assert event_object.base_repo_owner == "mmassari"
        assert event_object.base_ref is None
        assert event_object.target_repo == "python-teamcity-messages"
        assert event_object.commit_sha == "beaf90bcecc51968a46663f8d6f092bfdc92e682"
        assert event_object.user_login == "mmassari"
        assert event_object.comment == "/packit koji-build"
        assert (
            event_object.project_url
            == "https://src.fedoraproject.org/rpms/python-teamcity-messages"
        )

        assert isinstance(event_object.project, PagureProject)
        assert event_object.project.full_repo_name == "rpms/python-teamcity-messages"
        assert isinstance(event_object.base_project, PagureProject)
        assert (
            event_object.base_project.full_repo_name == "rpms/python-teamcity-messages"
        )

        flexmock(PackageConfigGetter).should_receive(
            "get_package_config_from_repo"
        ).with_args(
            base_project=event_object.base_project,
            project=event_object.project,
            pr_id=36,
            reference="beaf90bcecc51968a46663f8d6f092bfdc92e682",
            fail_when_missing=False,
        ).and_return(
            flexmock()
        ).once()
        flexmock(PagureProject).should_receive("get_web_url").and_return(
            "https://src.fedoraproject.org/rpms/python-teamcity-messages"
        )
        assert event_object.package_config

    @pytest.mark.parametrize("identifier", [None, "foo"])
    def test_parse_testing_farm_notification(
        self, testing_farm_notification, testing_farm_results, identifier
    ):
        request_id = "129bd474-e4d3-49e0-9dec-d994a99feebc"
        flexmock(TestingFarmJobHelper).should_receive("get_request_details").with_args(
            request_id
        ).and_return(testing_farm_results)
        flexmock(TFTTestRunTargetModel).should_receive("get_by_pipeline_id").and_return(
            flexmock(
                job_trigger=flexmock(),
                data={"base_project_url": "https://github.com/packit/packit"},
                commit_sha="12345",
                identifier=identifier,
            )
            .should_receive("get_trigger_object")
            .and_return(flexmock(pr_id=10))
            .once()
            .mock()
        )
        event_object = Parser.parse_event(testing_farm_notification)

        assert isinstance(event_object, TestingFarmResultsEvent)
        assert event_object.pipeline_id == request_id
        assert event_object.result == TestingFarmResult.passed
        assert event_object.project_url == "https://github.com/packit/packit"
        assert event_object.commit_sha == "12345"
        assert not event_object.summary
        assert event_object.compose == "Fedora-32"
        assert event_object.copr_build_id == "1810530"
        assert event_object.copr_chroot == "fedora-32-x86_64"
        assert event_object.db_trigger
        assert isinstance(event_object.project, GithubProject)
        assert event_object.project.full_repo_name == "packit/packit"
        assert event_object.identifier == identifier

    def test_parse_testing_farm_notification_error(
        self, testing_farm_notification, testing_farm_results_error
    ):
        request_id = "129bd474-e4d3-49e0-9dec-d994a99feebc"
        flexmock(TestingFarmJobHelper).should_receive("get_request_details").with_args(
            request_id
        ).and_return(testing_farm_results_error)
        flexmock(TFTTestRunTargetModel).should_receive("get_by_pipeline_id").and_return(
            flexmock(
                job_trigger=flexmock(),
                data={"base_project_url": "https://github.com/packit/packit"},
                commit_sha="12345",
                identifier=None,
            )
            .should_receive("get_trigger_object")
            .and_return(flexmock(pr_id=10))
            .once()
            .mock()
        )
        event_object = Parser.parse_event(testing_farm_notification)

        assert isinstance(event_object, TestingFarmResultsEvent)
        assert event_object.pipeline_id == request_id
        assert event_object.result == TestingFarmResult.error
        assert event_object.project_url == "https://github.com/packit/packit"
        assert event_object.commit_sha == "12345"
        assert event_object.summary == "something went wrong"
        assert event_object.compose == "Fedora-32"
        assert event_object.copr_build_id == "1810530"
        assert event_object.copr_chroot == "fedora-32-x86_64"
        assert event_object.db_trigger
        assert isinstance(event_object.project, GithubProject)
        assert event_object.project.full_repo_name == "packit/packit"
        assert not event_object.identifier

    def test_parse_copr_build_event_start(
        self, copr_build_results_start, copr_build_pr
    ):
        flexmock(CoprBuildTargetModel).should_receive("get_by_build_id").and_return(
            copr_build_pr
        )

        event_object = Parser.parse_event(copr_build_results_start)

        assert isinstance(event_object, AbstractCoprBuildEvent)
        assert event_object.topic == FedmsgTopic.copr_build_started
        assert event_object.build_id == 1044215
        assert event_object.chroot == "fedora-rawhide-x86_64"
        assert event_object.status == 3
        assert event_object.owner == "packit"
        assert event_object.project_name == "packit-service-hello-world-24"
        assert (
            event_object.project_url == "https://github.com/packit-service/hello-world"
        )
        assert event_object.base_repo_name == "hello-world"
        assert event_object.base_repo_namespace == "packit-service"
        assert event_object.pkg == "hello"

        assert isinstance(event_object.project, GithubProject)
        assert event_object.project.full_repo_name == "packit-service/hello-world"

        assert (
            not event_object.base_project  # With Github app, we cannot work with fork repo
        )

        assert event_object.get_copr_build_logs_url() == (
            "https://download.copr.fedorainfracloud.org/results/packit/"
            "packit-service-hello-world-24/fedora-rawhide-x86_64/01044215-hello/builder-live.log.gz"
        )

        flexmock(PackageConfigGetter).should_receive(
            "get_package_config_from_repo"
        ).with_args(
            base_project=None,
            project=event_object.project,
            pr_id=24,
            reference="0011223344",
            fail_when_missing=False,
        ).and_return(
            flexmock()
        ).once()

        assert event_object.package_config

    def test_parse_copr_build_event_end(self, copr_build_results_end, copr_build_pr):
        flexmock(CoprBuildTargetModel).should_receive("get_by_build_id").and_return(
            copr_build_pr
        )

        event_object = Parser.parse_event(copr_build_results_end)

        assert isinstance(event_object, AbstractCoprBuildEvent)
        assert event_object.topic == FedmsgTopic.copr_build_finished
        assert event_object.build_id == 1044215
        assert event_object.chroot == "fedora-rawhide-x86_64"
        assert event_object.status == 1
        assert event_object.owner == "packit"
        assert event_object.project_name == "packit-service-hello-world-24"
        assert event_object.base_repo_name == "hello-world"
        assert event_object.base_repo_namespace == "packit-service"
        assert event_object.pkg == "hello"
        assert event_object.git_ref == "0011223344"

        flexmock(GithubProject).should_receive("get_pr").with_args(
            pr_id=123
        ).and_return(flexmock(author="the-fork"))
        assert isinstance(event_object.project, GithubProject)
        assert event_object.project.full_repo_name == "packit-service/hello-world"

        assert (
            not event_object.base_project  # With Github app, we cannot work with fork repo
        )

        flexmock(PackageConfigGetter).should_receive(
            "get_package_config_from_repo"
        ).with_args(
            base_project=None,
            project=event_object.project,
            pr_id=24,
            reference="0011223344",
            fail_when_missing=False,
        ).and_return(
            flexmock()
        ).once()

        assert event_object.package_config

    def test_parse_koji_build_scratch_event_start(
        self, koji_build_scratch_start, koji_build_pr
    ):
        flexmock(KojiBuildTargetModel).should_receive("get_by_build_id").and_return(
            koji_build_pr
        )

        event_object = Parser.parse_event(koji_build_scratch_start)

        assert isinstance(event_object, KojiTaskEvent)
        assert event_object.build_id == 45270170
        assert event_object.state == KojiTaskState.open
        assert not event_object.rpm_build_task_id

        assert isinstance(event_object.project, GithubProject)
        assert event_object.project.full_repo_name == "foo/bar"

    def test_parse_koji_build_scratch_event_end(
        self, koji_build_scratch_end, koji_build_pr
    ):
        flexmock(KojiBuildTargetModel).should_receive("get_by_build_id").and_return(
            koji_build_pr
        )

        event_object = Parser.parse_event(koji_build_scratch_end)

        assert isinstance(event_object, KojiTaskEvent)
        assert event_object.build_id == 45270170
        assert event_object.state == KojiTaskState.closed
        assert event_object.rpm_build_task_id == 45270227

        flexmock(GithubProject).should_receive("get_pr").with_args(
            pr_id=123
        ).and_return(flexmock(author="the-fork"))
        assert isinstance(event_object.project, GithubProject)
        assert event_object.project.full_repo_name == "foo/bar"

    def test_parse_koji_build_event_start_old_format(
        self, koji_build_start_old_format, mock_config
    ):
        event_object = Parser.parse_event(koji_build_start_old_format)

        assert isinstance(event_object, KojiBuildEvent)
        assert event_object.build_id == 1864700
        assert event_object.state == KojiBuildState.building
        assert not event_object.old_state
        assert event_object.rpm_build_task_id == 79721403
        assert event_object.package_name == "packit"
        assert event_object.commit_sha == "0eb3e12005cb18f15d3054020f7ac934c01eae08"
        assert event_object.branch_name == "rawhide"
        assert event_object.git_ref == "rawhide"
        assert event_object.epoch is None
        assert event_object.version == "0.43.0"
        assert event_object.release == "1.fc36"
        assert event_object.nvr == "packit-0.43.0-1.fc36"
        assert event_object.project_url == "https://src.fedoraproject.org/rpms/packit"

        assert isinstance(event_object.project, PagureProject)
        assert event_object.project.full_repo_name == "rpms/packit"

        packit_yaml = (
            "{'specfile_path': 'packit.spec', 'synced_files': [],"
            "'jobs': [{'trigger': 'commit', 'job': 'sync_from_downstream'}],"
            "'downstream_package_name': 'packit'}"
        )
        flexmock(PagureProject).should_receive("get_file_content").with_args(
            path=".distro/source-git.yaml",
            ref="0eb3e12005cb18f15d3054020f7ac934c01eae08",
        ).and_raise(FileNotFoundError, "Not found.")
        flexmock(PagureProject).should_receive("get_file_content").with_args(
            path=".packit.yaml", ref="0eb3e12005cb18f15d3054020f7ac934c01eae08"
        ).and_return(packit_yaml)

        assert event_object.package_config

    def test_parse_koji_build_event_start_rawhide(
        self, koji_build_start_rawhide, mock_config
    ):
        event_object = Parser.parse_event(koji_build_start_rawhide)

        assert isinstance(event_object, KojiBuildEvent)
        assert event_object.build_id == 1874074
        assert event_object.state == KojiBuildState.building
        assert not event_object.old_state
        assert event_object.rpm_build_task_id == 80860894
        assert event_object.package_name == "python-ogr"
        assert event_object.commit_sha == "e029dd5250dde9a37a2cdddb6d822d973b09e5da"
        assert event_object.branch_name == "rawhide"
        assert event_object.git_ref == "rawhide"
        assert event_object.epoch is None
        assert event_object.version == "0.34.0"
        assert event_object.release == "1.fc36"
        assert event_object.nvr == "python-ogr-0.34.0-1.fc36"
        assert (
            event_object.project_url == "https://src.fedoraproject.org/rpms/python-ogr"
        )

        assert isinstance(event_object.project, PagureProject)
        assert event_object.project.full_repo_name == "rpms/python-ogr"

        packit_yaml = (
            "{'specfile_path': 'python-ogr.spec', 'synced_files': [],"
            "'jobs': [{'trigger': 'commit', 'job': 'sync_from_downstream'}],"
            "'downstream_package_name': 'python-ogr'}"
        )
        flexmock(PagureProject).should_receive("get_file_content").with_args(
            path=".distro/source-git.yaml",
            ref="e029dd5250dde9a37a2cdddb6d822d973b09e5da",
        ).and_raise(FileNotFoundError, "Not found.")
        flexmock(PagureProject).should_receive("get_file_content").with_args(
            path=".packit.yaml", ref="e029dd5250dde9a37a2cdddb6d822d973b09e5da"
        ).and_return(packit_yaml)

        assert event_object.package_config

    def test_parse_koji_build_event_start_f35(self, koji_build_start_f35, mock_config):
        event_object = Parser.parse_event(koji_build_start_f35)

        assert isinstance(event_object, KojiBuildEvent)
        assert event_object.build_id == 1874070
        assert event_object.state == KojiBuildState.building
        assert not event_object.old_state
        assert event_object.rpm_build_task_id == 80860789
        assert event_object.package_name == "python-ogr"
        assert event_object.commit_sha == "51b57ec04f5e6e9066ac859a1408cfbf1ead307e"
        assert event_object.branch_name == "f35"
        assert event_object.git_ref == "f35"
        assert event_object.epoch is None
        assert event_object.version == "0.34.0"
        assert event_object.release == "1.fc35"
        assert event_object.nvr == "python-ogr-0.34.0-1.fc35"
        assert (
            event_object.project_url == "https://src.fedoraproject.org/rpms/python-ogr"
        )

        assert isinstance(event_object.project, PagureProject)
        assert event_object.project.full_repo_name == "rpms/python-ogr"

        packit_yaml = (
            "{'specfile_path': 'python-ogr.spec', 'synced_files': [],"
            "'jobs': [{'trigger': 'commit', 'job': 'sync_from_downstream'}],"
            "'downstream_package_name': 'python-ogr'}"
        )
        flexmock(PagureProject).should_receive("get_file_content").with_args(
            path=".distro/source-git.yaml",
            ref="51b57ec04f5e6e9066ac859a1408cfbf1ead307e",
        ).and_raise(FileNotFoundError, "Not found.")
        flexmock(PagureProject).should_receive("get_file_content").with_args(
            path=".packit.yaml", ref="51b57ec04f5e6e9066ac859a1408cfbf1ead307e"
        ).and_return(packit_yaml)

        assert event_object.package_config

    def test_parse_koji_build_event_start_epel8(
        self, koji_build_start_epel8, mock_config
    ):
        event_object = Parser.parse_event(koji_build_start_epel8)

        assert isinstance(event_object, KojiBuildEvent)
        assert event_object.build_id == 1874072
        assert event_object.state == KojiBuildState.building
        assert not event_object.old_state
        assert event_object.rpm_build_task_id == 80860791
        assert event_object.package_name == "python-ogr"
        assert event_object.commit_sha == "23806a208e32cc937f3a6eb151c62cbbc10d8f96"
        assert event_object.branch_name == "epel8"
        assert event_object.git_ref == "epel8"
        assert event_object.epoch is None
        assert event_object.version == "0.34.0"
        assert event_object.release == "1.el8"
        assert (
            event_object.project_url == "https://src.fedoraproject.org/rpms/python-ogr"
        )

        assert isinstance(event_object.project, PagureProject)
        assert event_object.project.full_repo_name == "rpms/python-ogr"

        packit_yaml = (
            "{'specfile_path': 'python-ogr.spec', 'synced_files': [],"
            "'jobs': [{'trigger': 'commit', 'job': 'sync_from_downstream'}],"
            "'downstream_package_name': 'python-ogr'}"
        )
        flexmock(PagureProject).should_receive("get_file_content").with_args(
            path=".distro/source-git.yaml",
            ref="23806a208e32cc937f3a6eb151c62cbbc10d8f96",
        ).and_raise(FileNotFoundError, "Not found.")
        flexmock(PagureProject).should_receive("get_file_content").with_args(
            path=".packit.yaml", ref="23806a208e32cc937f3a6eb151c62cbbc10d8f96"
        ).and_return(packit_yaml)

        assert event_object.package_config

    def test_parse_koji_build_event_completed_old_format(
        self, koji_build_completed_old_format, mock_config
    ):
        event_object = Parser.parse_event(koji_build_completed_old_format)

        assert isinstance(event_object, KojiBuildEvent)
        assert event_object.build_id == 1864700
        assert event_object.state == KojiBuildState.complete
        assert event_object.old_state == KojiBuildState.building
        assert event_object.rpm_build_task_id == 79721403
        assert event_object.package_name == "packit"
        assert event_object.commit_sha == "0eb3e12005cb18f15d3054020f7ac934c01eae08"
        assert event_object.branch_name == "rawhide"
        assert event_object.git_ref == "rawhide"
        assert event_object.epoch is None
        assert event_object.version == "0.43.0"
        assert event_object.release == "1.fc36"
        assert event_object.nvr == "packit-0.43.0-1.fc36"
        assert event_object.project_url == "https://src.fedoraproject.org/rpms/packit"

        assert isinstance(event_object.project, PagureProject)
        assert event_object.project.full_repo_name == "rpms/packit"

        packit_yaml = (
            "{'specfile_path': 'packit.spec', 'synced_files': [],"
            "'jobs': [{'trigger': 'commit', 'job': 'sync_from_downstream'}],"
            "'downstream_package_name': 'packit'}"
        )
        flexmock(PagureProject).should_receive("get_file_content").with_args(
            path=".distro/source-git.yaml",
            ref="0eb3e12005cb18f15d3054020f7ac934c01eae08",
        ).and_raise(FileNotFoundError, "Not found.")
        flexmock(PagureProject).should_receive("get_file_content").with_args(
            path=".packit.yaml", ref="0eb3e12005cb18f15d3054020f7ac934c01eae08"
        ).and_return(packit_yaml)

        assert event_object.package_config

    def test_parse_koji_build_event_completed_rawhide(
        self, koji_build_completed_rawhide, mock_config
    ):
        event_object = Parser.parse_event(koji_build_completed_rawhide)

        assert isinstance(event_object, KojiBuildEvent)
        assert event_object.build_id == 1874074
        assert event_object.state == KojiBuildState.complete
        assert event_object.old_state == KojiBuildState.building
        assert event_object.rpm_build_task_id == 80860894
        assert event_object.package_name == "python-ogr"
        assert event_object.commit_sha == "e029dd5250dde9a37a2cdddb6d822d973b09e5da"
        assert event_object.branch_name == "rawhide"
        assert event_object.git_ref == "rawhide"
        assert event_object.epoch is None
        assert event_object.version == "0.34.0"
        assert event_object.release == "1.fc36"
        assert event_object.nvr == "python-ogr-0.34.0-1.fc36"
        assert (
            event_object.project_url == "https://src.fedoraproject.org/rpms/python-ogr"
        )

        assert isinstance(event_object.project, PagureProject)
        assert event_object.project.full_repo_name == "rpms/python-ogr"

        packit_yaml = (
            "{'specfile_path': 'python-ogr.spec', 'synced_files': [],"
            "'jobs': [{'trigger': 'commit', 'job': 'sync_from_downstream'}],"
            "'downstream_package_name': 'python-ogr'}"
        )
        flexmock(PagureProject).should_receive("get_file_content").with_args(
            path=".distro/source-git.yaml",
            ref="e029dd5250dde9a37a2cdddb6d822d973b09e5da",
        ).and_raise(FileNotFoundError, "Not found.")
        flexmock(PagureProject).should_receive("get_file_content").with_args(
            path=".packit.yaml", ref="e029dd5250dde9a37a2cdddb6d822d973b09e5da"
        ).and_return(packit_yaml)

        assert event_object.package_config

    def test_parse_koji_build_event_completed_f35(
        self, koji_build_completed_f35, mock_config
    ):
        event_object = Parser.parse_event(koji_build_completed_f35)

        assert isinstance(event_object, KojiBuildEvent)
        assert event_object.build_id == 1874070
        assert event_object.state == KojiBuildState.complete
        assert event_object.old_state == KojiBuildState.building
        assert event_object.rpm_build_task_id == 80860789
        assert event_object.package_name == "python-ogr"
        assert event_object.commit_sha == "51b57ec04f5e6e9066ac859a1408cfbf1ead307e"
        assert event_object.branch_name == "f35"
        assert event_object.git_ref == "f35"
        assert event_object.epoch is None
        assert event_object.version == "0.34.0"
        assert event_object.release == "1.fc35"
        assert event_object.nvr == "python-ogr-0.34.0-1.fc35"
        assert (
            event_object.project_url == "https://src.fedoraproject.org/rpms/python-ogr"
        )

        assert isinstance(event_object.project, PagureProject)
        assert event_object.project.full_repo_name == "rpms/python-ogr"

        packit_yaml = (
            "{'specfile_path': 'python-ogr.spec', 'synced_files': [],"
            "'jobs': [{'trigger': 'commit', 'job': 'sync_from_downstream'}],"
            "'downstream_package_name': 'python-ogr'}"
        )
        flexmock(PagureProject).should_receive("get_file_content").with_args(
            path=".distro/source-git.yaml",
            ref="51b57ec04f5e6e9066ac859a1408cfbf1ead307e",
        ).and_raise(FileNotFoundError, "Not found.")
        flexmock(PagureProject).should_receive("get_file_content").with_args(
            path=".packit.yaml", ref="51b57ec04f5e6e9066ac859a1408cfbf1ead307e"
        ).and_return(packit_yaml)

        assert event_object.package_config

    def test_parse_koji_build_event_completed_epel8(
        self, koji_build_completed_epel8, mock_config
    ):
        event_object = Parser.parse_event(koji_build_completed_epel8)

        assert isinstance(event_object, KojiBuildEvent)
        assert event_object.build_id == 1874072
        assert event_object.state == KojiBuildState.complete
        assert event_object.old_state == KojiBuildState.building
        assert event_object.rpm_build_task_id == 80860791
        assert event_object.package_name == "python-ogr"
        assert event_object.commit_sha == "23806a208e32cc937f3a6eb151c62cbbc10d8f96"
        assert event_object.branch_name == "epel8"
        assert event_object.git_ref == "epel8"
        assert event_object.epoch is None
        assert event_object.version == "0.34.0"
        assert event_object.release == "1.el8"
        assert event_object.nvr == "python-ogr-0.34.0-1.el8"
        assert (
            event_object.project_url == "https://src.fedoraproject.org/rpms/python-ogr"
        )

        assert isinstance(event_object.project, PagureProject)
        assert event_object.project.full_repo_name == "rpms/python-ogr"

        packit_yaml = (
            "{'specfile_path': 'python-ogr.spec', 'synced_files': [],"
            "'jobs': [{'trigger': 'commit', 'job': 'sync_from_downstream'}],"
            "'downstream_package_name': 'python-ogr'}"
        )
        flexmock(PagureProject).should_receive("get_file_content").with_args(
            path=".distro/source-git.yaml",
            ref="23806a208e32cc937f3a6eb151c62cbbc10d8f96",
        ).and_raise(FileNotFoundError, "Not found.")
        flexmock(PagureProject).should_receive("get_file_content").with_args(
            path=".packit.yaml", ref="23806a208e32cc937f3a6eb151c62cbbc10d8f96"
        ).and_return(packit_yaml)

        assert event_object.package_config

    def test_get_project_pr(self, github_pr_webhook, mock_config):
        event_object = Parser.parse_event(github_pr_webhook)

        assert isinstance(event_object, PullRequestGithubEvent)

        assert isinstance(event_object.project, GithubProject)
        assert isinstance(event_object.project.service, GithubService)
        assert event_object.project.namespace == "packit-service"
        assert event_object.project.repo == "packit"

    def test_get_project_release(self, github_release_webhook, mock_config):
        event_object = Parser.parse_event(github_release_webhook)

        assert isinstance(event_object, ReleaseEvent)

        assert isinstance(event_object.project, GithubProject)
        assert isinstance(event_object.project.service, GithubService)
        assert event_object.project.namespace == "packit-service"
        assert event_object.project.repo == "hello-world"

    def test_get_project_testing_farm_notification(
        self, testing_farm_notification, testing_farm_results, mock_config
    ):
        request_id = "129bd474-e4d3-49e0-9dec-d994a99feebc"
        flexmock(TestingFarmJobHelper).should_receive("get_request_details").with_args(
            request_id
        ).and_return(testing_farm_results)
        flexmock(TFTTestRunTargetModel).should_receive("get_by_pipeline_id").with_args(
            request_id
        ).and_return(
            flexmock(
                data={"base_project_url": "abc"}, commit_sha="12345", identifier=None
            )
        )
        event_object = Parser.parse_event(testing_farm_notification)

        assert isinstance(event_object, TestingFarmResultsEvent)
        assert isinstance(event_object.pipeline_id, str)
        assert event_object.pipeline_id == request_id
        assert event_object.project_url == "abc"
        assert event_object.commit_sha == "12345"

    def test_distgit_pagure_push(self, distgit_commit):

        event_object = Parser.parse_event(distgit_commit)

        assert isinstance(event_object, PushPagureEvent)
        assert event_object.repo_namespace == "rpms"
        assert event_object.repo_name == "buildah"
        assert event_object.commit_sha == "abcd"
        assert event_object.git_ref == "main"
        assert event_object.project_url == "https://src.fedoraproject.org/rpms/buildah"

    def test_distgit_pagure_push_packit(self, distgit_push_packit):
        event_object = Parser.parse_event(distgit_push_packit)
        assert isinstance(event_object, PushPagureEvent)
        assert event_object.committer == "pagure"

    def test_json_testing_farm_notification(
        self, testing_farm_notification, testing_farm_results
    ):
        flexmock(TestingFarmJobHelper).should_receive("get_request_details").and_return(
            testing_farm_results
        )
        flexmock(TFTTestRunTargetModel).should_receive("get_by_pipeline_id").and_return(
            flexmock(
                data={"base_project_url": "abc"}, commit_sha="12345", identifier=None
            )
        )
        event_object = Parser.parse_event(testing_farm_notification)
        assert json.dumps(event_object.pipeline_id)

    def test_parse_check_rerun_commit(self, check_rerun):
        trigger = flexmock(JobTriggerModel, trigger_id=123)
        branch_model = GitBranchModel(name="main")
        flexmock(JobTriggerModel).should_receive("get_by_id").with_args(
            123456
        ).and_return(trigger)
        flexmock(trigger).should_receive("get_trigger_object").and_return(branch_model)
        event_object = Parser.parse_event(check_rerun)

        assert isinstance(event_object, CheckRerunCommitEvent)
        assert event_object.repo_namespace == "packit"
        assert event_object.repo_name == "hello-world"
        assert event_object.commit_sha == "0e5d8b51fd5dfa460605e1497d22a76d65c6d7fd"
        assert event_object.project_url == "https://github.com/packit/hello-world"
        assert event_object.git_ref == "main"
        assert event_object.identifier == "main"
        assert event_object.check_name_job == "testing-farm"
        assert event_object.check_name_target == "fedora-rawhide-x86_64"

        assert isinstance(event_object.project, GithubProject)
        assert event_object.project.full_repo_name == "packit/hello-world"
        assert not event_object.base_project

        flexmock(PackageConfigGetter).should_receive(
            "get_package_config_from_repo"
        ).with_args(
            base_project=event_object.base_project,
            project=event_object.project,
            pr_id=None,
            reference="0e5d8b51fd5dfa460605e1497d22a76d65c6d7fd",
            fail_when_missing=False,
        ).and_return(
            flexmock()
        ).once()
        assert event_object.package_config
        assert event_object.build_targets_override is None
        assert event_object.tests_targets_override == {"fedora-rawhide-x86_64"}
        assert event_object.actor == "lbarcziova"

    def test_parse_check_rerun_pull_request(self, check_rerun):
        trigger = flexmock(JobTriggerModel, trigger_id=1234)
        pr_model = PullRequestModel(pr_id=12)
        flexmock(JobTriggerModel).should_receive("get_by_id").with_args(
            123456
        ).and_return(trigger)
        flexmock(trigger).should_receive("get_trigger_object").and_return(pr_model)
        event_object = Parser.parse_event(check_rerun)

        assert isinstance(event_object, CheckRerunPullRequestEvent)
        assert event_object.repo_namespace == "packit"
        assert event_object.repo_name == "hello-world"
        assert event_object.commit_sha == "0e5d8b51fd5dfa460605e1497d22a76d65c6d7fd"
        assert event_object.project_url == "https://github.com/packit/hello-world"
        assert event_object.pr_id == 12
        assert event_object.identifier == "12"
        assert isinstance(event_object.project, GithubProject)
        assert event_object.project.full_repo_name == "packit/hello-world"
        assert (
            not event_object.base_project  # With Github app, we cannot work with fork repo
        )
        assert event_object.check_name_job == "testing-farm"
        assert event_object.check_name_target == "fedora-rawhide-x86_64"
        assert event_object.actor == "lbarcziova"

        flexmock(PackageConfigGetter).should_receive(
            "get_package_config_from_repo"
        ).with_args(
            base_project=None,
            project=event_object.project,
            pr_id=12,
            reference="0e5d8b51fd5dfa460605e1497d22a76d65c6d7fd",
            fail_when_missing=False,
        ).and_return(
            flexmock()
        ).once()
        assert event_object.package_config
        assert event_object.build_targets_override is None
        assert event_object.tests_targets_override == {"fedora-rawhide-x86_64"}

    def test_parse_check_rerun_release(self, check_rerun):
        trigger = flexmock(JobTriggerModel, trigger_id=123)
        release_model = ProjectReleaseModel(tag_name="0.1.0")
        flexmock(JobTriggerModel).should_receive("get_by_id").with_args(
            123456
        ).and_return(trigger)
        flexmock(trigger).should_receive("get_trigger_object").and_return(release_model)

        event_object = Parser.parse_event(check_rerun)

        assert isinstance(event_object, CheckRerunReleaseEvent)
        assert event_object.repo_namespace == "packit"
        assert event_object.repo_name == "hello-world"
        assert event_object.commit_sha == "0e5d8b51fd5dfa460605e1497d22a76d65c6d7fd"
        assert event_object.project_url == "https://github.com/packit/hello-world"
        assert event_object.tag_name == "0.1.0"
        assert event_object.git_ref == "0.1.0"
        assert event_object.identifier == "0.1.0"
        assert event_object.check_name_job == "testing-farm"
        assert event_object.check_name_target == "fedora-rawhide-x86_64"
        assert event_object.build_targets_override is None
        assert event_object.tests_targets_override == {"fedora-rawhide-x86_64"}
        assert event_object.actor == "lbarcziova"

    def test_get_submitted_time_from_model(self):
        date = datetime.utcnow()

        fake_tf = flexmock(submitted_time=date)
        flexmock(TFTTestRunTargetModel).new_instances(fake_tf)
        tf = TFTTestRunTargetModel()
        tf.__class__ = TFTTestRunTargetModel
        assert date == get_submitted_time_from_model(tf)

        fake_copr = flexmock(build_submitted_time=date)
        flexmock(CoprBuildTargetModel).new_instances(fake_copr)
        copr = CoprBuildTargetModel()
        copr.__class__ = (
            CoprBuildTargetModel  # to pass in isinstance(model, CoprBuildTargetModel)
        )
        assert date == get_submitted_time_from_model(copr)

    def test_get_most_recent_targets(self, copr_models, tf_models):
        latest_copr_models = get_most_recent_targets(copr_models)
        assert len(latest_copr_models) == 1
        assert datetime.utcnow() - latest_copr_models[
            0
        ].build_submitted_time < timedelta(seconds=2)

        latest_tf_models = get_most_recent_targets(tf_models)
        assert len(latest_tf_models) == 1
        assert datetime.utcnow() - latest_tf_models[0].submitted_time < timedelta(
            seconds=2
        )
