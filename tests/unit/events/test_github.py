# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json
from datetime import datetime, timezone

import pytest
from flexmock import flexmock
from ogr import GithubService
from ogr.services.github import GithubProject
from packit.config import JobConfigTriggerType

from packit_service.config import PackageConfigGetter
from packit_service.models import (
    AllowlistStatus,
    GitBranchModel,
    ProjectEventModel,
    ProjectEventModelType,
    ProjectReleaseModel,
    PullRequestModel,
)
from packit_service.worker.events.comment import CommitCommentEvent
from packit_service.worker.events.enums import (
    IssueCommentAction,
    PullRequestAction,
    PullRequestCommentAction,
)
from packit_service.worker.events.github import (
    CheckRerunCommitEvent,
    CheckRerunPullRequestEvent,
    CheckRerunReleaseEvent,
    InstallationEvent,
    IssueCommentEvent,
    PullRequestCommentGithubEvent,
    PullRequestGithubEvent,
    PushGitHubEvent,
    ReleaseEvent,
)
from packit_service.worker.parser import Parser
from tests.spellbook import DATA_DIR


@pytest.fixture()
def github_installation():
    file = "installation_created.json"
    with open(DATA_DIR / "webhooks" / "github" / file) as outfile:
        return json.load(outfile)


@pytest.fixture()
def github_issue_comment_propose_downstream():
    with open(
        DATA_DIR / "webhooks" / "github" / "issue_propose_downstream.json",
    ) as outfile:
        return json.load(outfile)


@pytest.fixture()
def github_issue_comment_no_handler():
    return json.loads(
        (DATA_DIR / "webhooks" / "github" / "issue_comment_no_handler.json").read_text(),
    )


@pytest.fixture()
def github_pr_comment_empty():
    with open(DATA_DIR / "webhooks" / "github" / "pr_comment_empty.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def github_push():
    with open(DATA_DIR / "webhooks" / "github" / "push.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def github_push_branch():
    with open(DATA_DIR / "webhooks" / "github" / "push_branch.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def check_rerun():
    with open(
        DATA_DIR / "webhooks" / "github" / "checkrun_rerequested.json",
    ) as outfile:
        return json.load(outfile)


@pytest.fixture()
def github_pr_comment_created():
    with open(
        DATA_DIR / "webhooks" / "github" / "pr_comment_copr_build.json",
    ) as outfile:
        return json.load(outfile)


@pytest.fixture()
def commit_comment():
    with open(DATA_DIR / "webhooks" / "github" / "commit_comment.json") as outfile:
        return json.load(outfile)


def test_parse_installation(github_installation):
    event_object = Parser.parse_event(github_installation)

    assert isinstance(event_object, InstallationEvent)
    assert event_object.installation_id == 1708454
    assert event_object.account_login == "packit-service"
    assert event_object.account_id == 46870917
    assert event_object.account_url == "https://api.github.com/users/packit-service"
    assert event_object.account_type == "Organization"
    assert event_object.created_at == datetime.fromtimestamp(1567090283, timezone.utc)
    assert event_object.sender_login == "jpopelka"
    assert event_object.sender_id == 288686
    assert event_object.status == AllowlistStatus.waiting
    assert event_object.repositories == ["jpopelka/brewutils"]


def test_parse_release(github_release_webhook):
    event_object = Parser.parse_event(github_release_webhook)

    assert isinstance(event_object, ReleaseEvent)
    assert event_object.repo_namespace == "packit-service"
    assert event_object.repo_name == "hello-world"
    assert event_object.tag_name == "0.3.0"
    assert event_object.project_url == "https://github.com/packit-service/hello-world"


def test_parse_pr(github_pr_webhook):
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
    assert not event_object.base_project  # With Github app, we cannot work with fork repo

    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=None,
        project=event_object.project,
        pr_id=342,
        reference="528b803be6f93e19ca4130bf4976f2800a3004c4",
        fail_when_missing=False,
    ).and_return(
        flexmock(get_package_config_views=lambda: {}),
    ).once()
    assert event_object.packages_config


def test_parse_github_push(github_push_branch):
    event_object = Parser.parse_event(github_push_branch)

    assert isinstance(event_object, PushGitHubEvent)
    assert event_object.repo_namespace == "packit-service"
    assert event_object.repo_name == "hello-world"
    assert event_object.commit_sha == "04885ff850b0fa0e206cd09db73565703d48f99b"
    assert event_object.project_url == "https://github.com/packit-service/hello-world"
    assert event_object.git_ref == "build-branch"

    assert isinstance(event_object.project, GithubProject)
    assert event_object.project.full_repo_name == "packit-service/hello-world"
    assert not event_object.base_project

    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=event_object.base_project,
        project=event_object.project,
        pr_id=None,
        reference="04885ff850b0fa0e206cd09db73565703d48f99b",
        fail_when_missing=False,
    ).and_return(
        flexmock(get_package_config_views=lambda: {}),
    ).once()
    assert event_object.packages_config


def test_parse_github_push_branch(github_push_branch):
    event_object = Parser.parse_event(github_push_branch)

    assert isinstance(event_object, PushGitHubEvent)
    assert event_object.repo_namespace == "packit-service"
    assert event_object.repo_name == "hello-world"
    assert event_object.commit_sha == "04885ff850b0fa0e206cd09db73565703d48f99b"
    assert event_object.project_url == "https://github.com/packit-service/hello-world"
    assert event_object.git_ref == "build-branch"

    assert isinstance(event_object.project, GithubProject)
    assert event_object.project.full_repo_name == "packit-service/hello-world"
    assert not event_object.base_project

    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=event_object.base_project,
        project=event_object.project,
        pr_id=None,
        reference="04885ff850b0fa0e206cd09db73565703d48f99b",
        fail_when_missing=False,
    ).and_return(
        flexmock(get_package_config_views=lambda: {}),
    ).once()

    assert event_object.packages_config


def test_get_project_pr(github_pr_webhook, mock_config):
    event_object = Parser.parse_event(github_pr_webhook)

    assert isinstance(event_object, PullRequestGithubEvent)

    assert isinstance(event_object.project, GithubProject)
    assert isinstance(event_object.project.service, GithubService)
    assert event_object.project.namespace == "packit-service"
    assert event_object.project.repo == "packit"


def test_get_project_release(github_release_webhook, mock_config):
    event_object = Parser.parse_event(github_release_webhook)

    assert isinstance(event_object, ReleaseEvent)

    assert isinstance(event_object.project, GithubProject)
    assert isinstance(event_object.project.service, GithubService)
    assert event_object.project.namespace == "packit-service"
    assert event_object.project.repo == "hello-world"


def test_parse_check_rerun_commit(check_rerun):
    trigger = flexmock(ProjectEventModel, event_id=123)
    branch_model = GitBranchModel(name="main")
    flexmock(ProjectEventModel).should_receive("get_by_id").with_args(
        123456,
    ).and_return(trigger)
    flexmock(trigger).should_receive("get_project_event_object").and_return(
        branch_model,
    )
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
        "get_package_config_from_repo",
    ).with_args(
        base_project=event_object.base_project,
        project=event_object.project,
        pr_id=None,
        reference="0e5d8b51fd5dfa460605e1497d22a76d65c6d7fd",
        fail_when_missing=False,
    ).and_return(
        flexmock(get_package_config_views=lambda: {}),
    ).once()
    assert event_object.packages_config
    assert event_object.build_targets_override is None
    assert event_object.tests_targets_override == {"fedora-rawhide-x86_64"}
    assert event_object.actor == "lbarcziova"


def test_parse_check_rerun_pull_request(check_rerun):
    trigger = flexmock(ProjectEventModel, event_id=1234)
    pr_model = PullRequestModel(pr_id=12)
    flexmock(ProjectEventModel).should_receive("get_by_id").with_args(
        123456,
    ).and_return(trigger)
    flexmock(trigger).should_receive("get_project_event_object").and_return(pr_model)
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
    assert not event_object.base_project  # With Github app, we cannot work with fork repo
    assert event_object.check_name_job == "testing-farm"
    assert event_object.check_name_target == "fedora-rawhide-x86_64"
    assert event_object.actor == "lbarcziova"

    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=None,
        project=event_object.project,
        pr_id=12,
        reference="0e5d8b51fd5dfa460605e1497d22a76d65c6d7fd",
        fail_when_missing=False,
    ).and_return(
        flexmock(get_package_config_views=lambda: {}),
    ).once()
    assert event_object.packages_config
    assert event_object.build_targets_override is None
    assert event_object.tests_targets_override == {"fedora-rawhide-x86_64"}


def test_parse_check_rerun_release(check_rerun):
    trigger = flexmock(ProjectEventModel, event_id=123)
    release_model = ProjectReleaseModel(tag_name="0.1.0")
    flexmock(ProjectEventModel).should_receive("get_by_id").with_args(
        123456,
    ).and_return(trigger)
    flexmock(trigger).should_receive("get_project_event_object").and_return(
        release_model,
    )

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


def test_parse_pr_comment_created(github_pr_comment_created):
    event_object = Parser.parse_event(github_pr_comment_created)

    assert isinstance(event_object, PullRequestCommentGithubEvent)
    assert event_object.action == PullRequestCommentAction.created
    assert event_object.pr_id == 9
    assert event_object.base_repo_namespace == "phracek"
    assert event_object.base_repo_name is None  # It's not present in the payload
    assert event_object.target_repo_namespace == "packit-service"
    assert event_object.target_repo_name == "hello-world"
    assert event_object.project_url == "https://github.com/packit-service/hello-world"
    assert event_object.actor == "phracek"
    assert event_object.comment == "/packit copr-build"

    assert isinstance(event_object.project, GithubProject)
    assert event_object.project.full_repo_name == "packit-service/hello-world"
    assert not event_object.base_project  # With Github app, we cannot work with fork repo

    flexmock(GithubProject).should_receive("get_pr").with_args(9).and_return(
        flexmock(head_commit="12345"),
    )

    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=None,
        project=event_object.project,
        pr_id=9,
        reference="12345",
        fail_when_missing=False,
    ).and_return(
        flexmock(get_package_config_views=lambda: {}),
    ).once()
    assert event_object.packages_config


def test_parse_pr_comment_empty(github_pr_comment_empty):
    event_object = Parser.parse_event(github_pr_comment_empty)

    assert isinstance(event_object, PullRequestCommentGithubEvent)
    assert event_object.action == PullRequestCommentAction.created
    assert event_object.pr_id == 9
    assert event_object.base_repo_namespace == "phracek"
    assert event_object.base_repo_name is None  # It's not present in the payload
    assert event_object.target_repo_namespace == "packit-service"
    assert event_object.target_repo_name == "hello-world"
    assert event_object.project_url == "https://github.com/packit-service/hello-world"
    assert event_object.actor == "phracek"
    assert event_object.comment == ""

    assert isinstance(event_object.project, GithubProject)
    assert event_object.project.full_repo_name == "packit-service/hello-world"
    assert not event_object.base_project  # With Github app, we cannot work with fork repo

    flexmock(GithubProject).should_receive("get_pr").with_args(9).and_return(
        flexmock(head_commit="12345"),
    )

    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=None,
        project=event_object.project,
        pr_id=9,
        reference="12345",
        fail_when_missing=False,
    ).and_return(
        flexmock(get_package_config_views=lambda: {}),
    ).once()

    assert event_object.packages_config


def test_parse_issue_comment(github_issue_comment_propose_downstream):
    event_object = Parser.parse_event(github_issue_comment_propose_downstream)

    assert isinstance(event_object, IssueCommentEvent)
    assert event_object.action == IssueCommentAction.created
    assert event_object.issue_id == 512
    assert event_object.repo_namespace == "packit-service"
    assert event_object.repo_name == "packit"
    assert event_object.target_repo == f"{event_object.repo_namespace}/{event_object.repo_name}"
    assert event_object.base_ref == "master"
    assert event_object.project_url == "https://github.com/packit-service/packit"
    assert event_object.actor == "phracek"
    assert event_object.comment == "/packit propose-downstream"

    assert isinstance(event_object.project, GithubProject)
    assert event_object.project.full_repo_name == "packit-service/packit"
    assert not event_object.base_project

    flexmock(event_object.project).should_receive("get_releases").and_return(
        [flexmock(tag_name="0.5.0")],
    )
    flexmock(GithubProject, get_sha_from_tag=lambda tag_name: "123456")
    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=event_object.base_project,
        project=event_object.project,
        pr_id=None,
        reference="123456",
        fail_when_missing=False,
    ).and_return(
        flexmock(get_package_config_views=lambda: {}),
    ).once()
    assert event_object.packages_config


def test_parse_issue_comment_no_handler(github_issue_comment_no_handler):
    event_object = Parser.parse_event(github_issue_comment_no_handler)

    assert isinstance(event_object, IssueCommentEvent)
    assert isinstance(event_object.project, GithubProject)
    assert event_object.project.full_repo_name == "packit-service/packit"
    assert not event_object.base_project

    flexmock(event_object.project).should_receive("get_releases").and_return([])
    flexmock(GithubProject).should_receive("get_sha_from_tag").never()
    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=event_object.base_project,
        project=event_object.project,
        pr_id=None,
        reference=None,
        fail_when_missing=False,
    ).and_return(
        flexmock(get_package_config_views=lambda: {}),
    ).once()
    assert event_object.packages_config
    assert event_object.commit_sha is None
    assert event_object.tag_name == ""


@pytest.mark.parametrize(
    "check_name, db_project_object, result",
    [
        pytest.param(
            "propose-downstream:f35",
            flexmock(
                get_project_event_object=flexmock(
                    job_config_trigger_type=JobConfigTriggerType.release,
                ),
            ),
            ("propose-downstream", "f35", None),
            id="propose_downstream",
        ),
        pytest.param(
            "propose-downstream:f35:first",
            flexmock(
                get_project_event_object=flexmock(
                    job_config_trigger_type=JobConfigTriggerType.release,
                ),
            ),
            ("propose-downstream", "f35", "first"),
            id="propose_downstream_identifier",
        ),
        pytest.param(
            "rpm-build:fedora-35-x86_64",
            flexmock(
                get_project_event_object=flexmock(
                    job_config_trigger_type=JobConfigTriggerType.pull_request,
                ),
            ),
            ("rpm-build", "fedora-35-x86_64", None),
            id="rpm_build_pr",
        ),
        pytest.param(
            "rpm-build:1.0.1:fedora-35-x86_64",
            flexmock(
                get_project_event_object=flexmock(
                    job_config_trigger_type=JobConfigTriggerType.release,
                ),
            ),
            ("rpm-build", "fedora-35-x86_64", None),
            id="rpm_build_release",
        ),
        pytest.param(
            "rpm-build:main:fedora-35-x86_64",
            flexmock(
                get_project_event_object=flexmock(
                    job_config_trigger_type=JobConfigTriggerType.commit,
                ),
            ),
            ("rpm-build", "fedora-35-x86_64", None),
            id="rpm_build_commit",
        ),
        pytest.param(
            "rpm-build:fedora-35-x86_64:first",
            flexmock(
                get_project_event_object=flexmock(
                    job_config_trigger_type=JobConfigTriggerType.pull_request,
                ),
            ),
            ("rpm-build", "fedora-35-x86_64", "first"),
            id="rpm_build_pr_identifier",
        ),
        pytest.param(
            "rpm-build:1.0.1:fedora-35-x86_64:first",
            flexmock(
                get_project_event_object=flexmock(
                    job_config_trigger_type=JobConfigTriggerType.release,
                ),
            ),
            ("rpm-build", "fedora-35-x86_64", "first"),
            id="rpm_build_release_identifier",
        ),
        pytest.param(
            "rpm-build:main:fedora-35-x86_64:first",
            flexmock(
                get_project_event_object=flexmock(
                    job_config_trigger_type=JobConfigTriggerType.commit,
                ),
            ),
            ("rpm-build", "fedora-35-x86_64", "first"),
            id="rpm_build_commit_identifier",
        ),
        pytest.param(
            "testing-farm:fedora-35-x86_64",
            flexmock(
                get_project_event_object=flexmock(
                    job_config_trigger_type=JobConfigTriggerType.pull_request,
                ),
            ),
            ("testing-farm", "fedora-35-x86_64", None),
            id="testing_farm_pr",
        ),
        pytest.param(
            "testing-farm:1.0.1:fedora-35-x86_64",
            flexmock(
                get_project_event_object=flexmock(
                    job_config_trigger_type=JobConfigTriggerType.release,
                ),
            ),
            ("testing-farm", "fedora-35-x86_64", None),
            id="testing_farm_release",
        ),
        pytest.param(
            "testing-farm:main:fedora-35-x86_64",
            flexmock(
                get_project_event_object=flexmock(
                    job_config_trigger_type=JobConfigTriggerType.commit,
                ),
            ),
            ("testing-farm", "fedora-35-x86_64", None),
            id="testing_farm_commit",
        ),
        pytest.param(
            "testing-farm:fedora-35-x86_64:first",
            flexmock(
                get_project_event_object=flexmock(
                    job_config_trigger_type=JobConfigTriggerType.pull_request,
                ),
            ),
            ("testing-farm", "fedora-35-x86_64", "first"),
            id="testing_farm_pr_identifier",
        ),
        pytest.param(
            "testing-farm:1.0.1:fedora-35-x86_64:first",
            flexmock(
                get_project_event_object=flexmock(
                    job_config_trigger_type=JobConfigTriggerType.release,
                ),
            ),
            ("testing-farm", "fedora-35-x86_64", "first"),
            id="testing_farm_release_identifier",
        ),
        pytest.param(
            "testing-farm:main:fedora-35-x86_64:first",
            flexmock(
                get_project_event_object=flexmock(
                    job_config_trigger_type=JobConfigTriggerType.commit,
                ),
            ),
            ("testing-farm", "fedora-35-x86_64", "first"),
            id="testing_farm_commit_identifier",
        ),
        pytest.param(
            "koji-build:fedora-35-x86_64",
            flexmock(
                get_project_event_object=flexmock(
                    job_config_trigger_type=JobConfigTriggerType.pull_request,
                ),
            ),
            ("koji-build", "fedora-35-x86_64", None),
            id="koji_build_pr",
        ),
        pytest.param(
            "koji-build:1.0.1:fedora-35-x86_64",
            flexmock(
                get_project_event_object=flexmock(
                    job_config_trigger_type=JobConfigTriggerType.release,
                ),
            ),
            ("koji-build", "fedora-35-x86_64", None),
            id="koji_build_release",
        ),
        pytest.param(
            "koji-build:main:fedora-35-x86_64",
            flexmock(
                get_project_event_object=flexmock(
                    job_config_trigger_type=JobConfigTriggerType.commit,
                ),
            ),
            ("koji-build", "fedora-35-x86_64", None),
            id="koji_build_commit",
        ),
        pytest.param(
            "koji-build:fedora-35-x86_64:first",
            flexmock(
                get_project_event_object=flexmock(
                    job_config_trigger_type=JobConfigTriggerType.pull_request,
                ),
            ),
            ("koji-build", "fedora-35-x86_64", "first"),
            id="koji_build_pr_identifier",
        ),
        pytest.param(
            "koji-build:1.0.1:fedora-35-x86_64:first",
            flexmock(
                get_project_event_object=flexmock(
                    job_config_trigger_type=JobConfigTriggerType.release,
                ),
            ),
            ("koji-build", "fedora-35-x86_64", "first"),
            id="koji_build_release_identifier",
        ),
        pytest.param(
            "koji-build:main:fedora-35-x86_64:first",
            flexmock(
                get_project_event_object=flexmock(
                    job_config_trigger_type=JobConfigTriggerType.commit,
                ),
            ),
            ("koji-build", "fedora-35-x86_64", "first"),
            id="koji_build_commit_identifier",
        ),
    ],
)
def test_parse_check_name(check_name, db_project_object, result):
    assert Parser.parse_check_name(check_name, db_project_object) == result


def test_parse_commit_comment(commit_comment):
    event_object = Parser.parse_event(commit_comment)

    assert isinstance(event_object, CommitCommentEvent)
    assert event_object.commit_sha == "eea05dd6fab70d8c4afc10b58ef14ecb25e4f9d8"
    assert event_object.repo_namespace == "packit"
    assert event_object.repo_namespace == "packit"
    assert event_object.project_url == "https://github.com/packit/packit"
    assert event_object.actor == "lbarcziova"
    assert event_object.comment == "/packit build"

    assert isinstance(event_object.project, GithubProject)
    assert event_object.project.full_repo_name == "packit/packit"

    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=None,
        project=event_object.project,
        reference="eea05dd6fab70d8c4afc10b58ef14ecb25e4f9d8",
        pr_id=None,
        fail_when_missing=False,
    ).and_return(
        flexmock(get_package_config_views=lambda: {}),
    ).once()

    assert event_object.packages_config


def test_parse_commit_comment_commit(commit_comment):
    event_object = Parser.parse_event(commit_comment)
    event_object.comment = "/packit build --commit stable"
    commit_sha = "eea05dd6fab70d8c4afc10b58ef14ecb25e4f9d8"

    assert isinstance(event_object, CommitCommentEvent)
    assert event_object.commit_sha == commit_sha
    assert event_object.repo_namespace == "packit"
    assert event_object.repo_namespace == "packit"
    assert event_object.project_url == "https://github.com/packit/packit"
    assert event_object.actor == "lbarcziova"
    assert event_object.git_ref == "stable"
    assert event_object.identifier == "stable"

    assert isinstance(event_object.project, GithubProject)
    assert event_object.project.full_repo_name == "packit/packit"

    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=None,
        project=event_object.project,
        reference=commit_sha,
        pr_id=None,
        fail_when_missing=False,
    ).and_return(
        flexmock(get_package_config_views=lambda: {}),
    ).once()

    flexmock(GithubProject).should_receive("get_commits").with_args(
        "stable",
    ).and_return([commit_sha])

    flexmock(ProjectEventModel).should_receive("get_or_create").with_args(
        type=ProjectEventModelType.branch_push,
        event_id=123,
        commit_sha=commit_sha,
    ).and_return(flexmock())
    flexmock(GitBranchModel).should_receive("get_or_create").with_args(
        branch_name="stable",
        namespace="packit",
        project_url="https://github.com/packit/packit",
        repo_name="packit",
    ).and_return(
        flexmock(project_event_model_type=ProjectEventModelType.branch_push, id=123),
    )

    assert event_object.packages_config
    assert event_object.db_project_event


def test_parse_commit_comment_release(commit_comment):
    event_object = Parser.parse_event(commit_comment)
    event_object.comment = "/packit build --release 1.0.0"
    commit_sha = "eea05dd6fab70d8c4afc10b58ef14ecb25e4f9d8"

    assert isinstance(event_object, CommitCommentEvent)
    assert event_object.commit_sha == commit_sha
    assert event_object.repo_namespace == "packit"
    assert event_object.repo_namespace == "packit"
    assert event_object.project_url == "https://github.com/packit/packit"
    assert event_object.actor == "lbarcziova"
    assert event_object.git_ref == "1.0.0"
    assert event_object.identifier == "1.0.0"

    assert isinstance(event_object.project, GithubProject)
    assert event_object.project.full_repo_name == "packit/packit"

    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=None,
        project=event_object.project,
        reference=commit_sha,
        pr_id=None,
        fail_when_missing=False,
    ).and_return(
        flexmock(get_package_config_views=lambda: {}),
    ).once()

    flexmock(GithubProject).should_receive("get_release").and_return(
        flexmock(git_tag=flexmock(commit_sha=commit_sha)),
    )

    flexmock(ProjectEventModel).should_receive("get_or_create").with_args(
        type=ProjectEventModelType.release,
        event_id=123,
        commit_sha=commit_sha,
    ).and_return(flexmock())
    flexmock(ProjectReleaseModel).should_receive("get_or_create").with_args(
        tag_name="1.0.0",
        namespace="packit",
        repo_name="packit",
        project_url="https://github.com/packit/packit",
        commit_hash=commit_sha,
    ).and_return(
        flexmock(project_event_model_type=ProjectEventModelType.release, id=123),
    )

    assert event_object.packages_config
    assert event_object.db_project_event
