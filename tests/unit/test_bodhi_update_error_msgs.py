import pytest

from flexmock import flexmock
from fedora.client import AuthError

from packit.exceptions import PackitException
from packit.config import (
    JobConfig,
    JobConfigTriggerType,
    JobType,
    PackageConfig,
    CommonPackageConfig,
)
from packit_service.config import ServiceConfig
from packit_service.worker.handlers import bodhi
from packit_service.worker.celery_task import CeleryTask
from packit_service.worker.events.enums import PullRequestAction
from packit_service.worker.events import (
    PullRequestCommentPagureEvent,
    IssueCommentEvent,
)
from packit_service.worker.handlers.bodhi import (
    RetriggerBodhiUpdateHandler,
    IssueCommentRetriggerBodhiUpdateHandler,
)
from packit_service.worker.handlers.mixin import KojiBuildData


@pytest.fixture(scope="module")
def package_config__job_config():
    package_config = PackageConfig(
        packages={
            "package": CommonPackageConfig(
                identifier="first",
            )
        },
        jobs=[
            JobConfig(
                type=JobType.bodhi_update,
                trigger=JobConfigTriggerType.pull_request,
                packages={
                    "package": CommonPackageConfig(
                        identifier="first",
                    )
                },
            ),
        ],
    )
    job_config = JobConfig(
        type=JobType.bodhi_update,
        trigger=JobConfigTriggerType.pull_request,
        packages={
            "package": CommonPackageConfig(
                identifier="first",
            )
        },
    )
    return package_config, job_config


@pytest.fixture(scope="module")
def package_config__job_config__pull_request_event(package_config__job_config):
    package_config, job_config = package_config__job_config
    flexmock(PullRequestCommentPagureEvent).should_receive("commit_sha").and_return(
        "abcdef"
    )
    data = PullRequestCommentPagureEvent(
        pr_id=123,
        action=PullRequestAction.opened,
        base_repo_namespace="a_namespace",
        base_repo_name="a_repo_name",
        base_repo_owner="a_owner",
        target_repo="a_target",
        project_url="projec_url",
        user_login="usr_login",
        comment="/packit creat-update",
        comment_id=321,
        base_ref="abcdef",
    ).get_dict()
    return package_config, job_config, data


def test_pull_request_retrigger_bodhi_update_no_koji_data(
    package_config__job_config__pull_request_event,
):
    package_config, job_config, data = package_config__job_config__pull_request_event

    msg = (
        "Packit failed on creating Bodhi update "
        "in dist-git (an url):\n\n"
        "| dist-git branch | error |\n"
        "| --------------- | ----- |\n"
        "| | ``` error abc ``` |\n\n"
        "Fedora Bodhi update was re-triggered by comment in dist-git PR with id 123.\n\n"
        "You can retrigger the update by adding a comment (`/packit create-update`) "
        "into this issue.\n\n---\n\n"
        "*Get in [touch with us](https://packit.dev/#contact) if you need some help.*\n"
    )
    flexmock(bodhi).should_receive("report_in_issue_repository").with_args(
        issue_repository=None,
        service_config=ServiceConfig,
        title=("Fedora Bodhi update failed to be created"),
        message=msg,
        comment_to_existing=msg,
    ).once()

    error_msg = "error abc"
    dg = flexmock(local_project=flexmock(git_url="an url"))
    packit_api = flexmock(dg=dg)
    flexmock(RetriggerBodhiUpdateHandler).should_receive("packit_api").and_return(
        packit_api
    )
    flexmock(RetriggerBodhiUpdateHandler).should_receive("__next__").and_raise(
        PackitException, error_msg
    )
    flexmock(CeleryTask).should_receive("is_last_try").and_return(True)
    handler = RetriggerBodhiUpdateHandler(package_config, job_config, data, flexmock())
    with pytest.raises(PackitException):
        handler.run()


def test_pull_request_retrigger_bodhi_update_with_koji_data(
    package_config__job_config__pull_request_event,
):
    package_config, job_config, data = package_config__job_config__pull_request_event

    msg = (
        "Packit failed on creating Bodhi update "
        "in dist-git (an url):\n\n"
        "| dist-git branch | error |\n"
        "| --------------- | ----- |\n"
        "| `f36` | ``` error abc ``` |\n\n"
        "Fedora Bodhi update was re-triggered by comment in dist-git PR with id 123.\n\n"
        "You can retrigger the update by adding a comment (`/packit create-update`) "
        "into this issue.\n\n---\n\n"
        "*Get in [touch with us](https://packit.dev/#contact) if you need some help.*\n"
    )
    flexmock(bodhi).should_receive("report_in_issue_repository").with_args(
        issue_repository=None,
        service_config=ServiceConfig,
        title=("Fedora Bodhi update failed to be created"),
        message=msg,
        comment_to_existing=msg,
    ).once()

    error_msg = "error abc"
    dg = flexmock(local_project=flexmock(git_url="an url"))
    packit_api = (
        flexmock(dg=dg)
        .should_receive("create_update")
        .and_raise(PackitException, error_msg)
        .mock()
    )
    flexmock(RetriggerBodhiUpdateHandler).should_receive("packit_api").and_return(
        packit_api
    )
    flexmock(RetriggerBodhiUpdateHandler).should_receive("__next__").and_return(
        KojiBuildData(dist_git_branch="f36", build_id=1, nvr="a_package_1.f36", state=1)
    )
    flexmock(CeleryTask).should_receive("is_last_try").and_return(True)
    handler = RetriggerBodhiUpdateHandler(package_config, job_config, data, flexmock())
    with pytest.raises(PackitException):
        handler.run()


def test_pull_request_retrigger_bodhi_update_auth_err(
    package_config__job_config__pull_request_event,
):
    package_config, job_config, data = package_config__job_config__pull_request_event

    msg = (
        "Packit failed on creating Bodhi update in dist-git (an url):\n\n"
        "| dist-git branch | error |\n| --------------- | ----- |\n"
        "| `f36` | Bodhi update creation failed for `a_package_1.f36` "
        "because of the missing permissions. Please, give packit user `commit`"
        " rights in the [dist-git settings](projec_url/adduser). *Try 2/2.* |\n\n"
        "Fedora Bodhi update was re-triggered by comment in dist-git PR with id 123.\n\n"
        "You can retrigger the update by adding a comment (`/packit create-update`) "
        "into this issue.\n\n---\n\n"
        "*Get in [touch with us](https://packit.dev/#contact) if you need some help.*\n"
    )
    flexmock(bodhi).should_receive("report_in_issue_repository").with_args(
        issue_repository=None,
        service_config=ServiceConfig,
        title=("Fedora Bodhi update failed to be created"),
        message=msg,
        comment_to_existing=msg,
    ).once()

    error_msg = "error abc"
    dg = flexmock(local_project=flexmock(git_url="an url"))
    packit_api = (
        flexmock(dg=dg)
        .should_receive("create_update")
        .and_raise(PackitException, error_msg)
        .mock()
    )
    flexmock(RetriggerBodhiUpdateHandler).should_receive("packit_api").and_return(
        packit_api
    )
    flexmock(RetriggerBodhiUpdateHandler).should_receive("__next__").and_return(
        KojiBuildData(dist_git_branch="f36", build_id=1, nvr="a_package_1.f36", state=1)
    )
    flexmock(CeleryTask).should_receive("is_last_try").and_return(True)
    flexmock(CeleryTask).should_receive("retries").and_return(1)
    flexmock(CeleryTask).should_receive("get_retry_limit").and_return(1)
    flexmock(PackitException).should_receive("__cause__").and_return(
        AuthError("another_error")
    )
    handler = RetriggerBodhiUpdateHandler(package_config, job_config, data, flexmock())
    handler.run()


def test_issue_comment_retrigger_bodhi_update_no_koji_data(package_config__job_config):
    package_config, job_config = package_config__job_config
    flexmock(IssueCommentEvent).should_receive("tag_name").and_return("1")
    flexmock(IssueCommentEvent).should_receive("commit_sha").and_return("abcdef")
    data = IssueCommentEvent(
        issue_id=123,
        action=PullRequestAction.opened,
        repo_namespace="a_namespace",
        repo_name="a_repo_name",
        target_repo="a_target",
        project_url="projec_url",
        actor="actor",
        comment="/packit creat-update",
        comment_id=321,
    ).get_dict()

    msg = (
        "Packit failed on creating Bodhi update "
        "in dist-git (an url):\n\n"
        "| dist-git branch | error |\n"
        "| --------------- | ----- |\n"
        "| | ``` error abc ``` |\n\n"
        "Fedora Bodhi update was re-triggered by comment in issue 123.\n\n"
        "You can retrigger the update by adding a comment (`/packit create-update`) "
        "into this issue.\n\n---\n\n"
        "*Get in [touch with us](https://packit.dev/#contact) if you need some help.*\n"
    )
    flexmock(bodhi).should_receive("report_in_issue_repository").with_args(
        issue_repository=None,
        service_config=ServiceConfig,
        title=("Fedora Bodhi update failed to be created"),
        message=msg,
        comment_to_existing=msg,
    ).once()

    error_msg = "error abc"
    dg = flexmock(local_project=flexmock(git_url="an url"))
    packit_api = flexmock(dg=dg)
    flexmock(IssueCommentRetriggerBodhiUpdateHandler).should_receive(
        "packit_api"
    ).and_return(packit_api)
    flexmock(IssueCommentRetriggerBodhiUpdateHandler).should_receive(
        "__next__"
    ).and_raise(PackitException, error_msg)
    flexmock(CeleryTask).should_receive("is_last_try").and_return(True)
    handler = IssueCommentRetriggerBodhiUpdateHandler(
        package_config, job_config, data, flexmock()
    )
    with pytest.raises(PackitException):
        handler.run()
