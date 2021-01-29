# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from typing import List, Tuple, TYPE_CHECKING

import pytest
from copr.v3 import Client
from fedora.client import AuthError, FedoraServiceError
from fedora.client.fas2 import AccountSystem
from flexmock import flexmock

import packit_service
from ogr.abstract import GitProject, GitService, CommitStatus
from ogr.services.github import GithubProject, GithubService
from packit.config import JobType, JobConfig, JobConfigTriggerType
from packit.config.job_config import JobMetadataConfig
from packit.copr_helper import CoprHelper
from packit.local_project import LocalProject
from packit_service.config import Deployment
from packit_service.constants import FAQ_URL
from packit_service.models import (
    AllowlistModel as DBAllowlist,
    AllowlistStatus,
    PullRequestModel,
)
from packit_service.service.events import (
    ReleaseEvent,
    PullRequestGithubEvent,
    PullRequestCommentGithubEvent,
    PullRequestAction,
    PullRequestCommentAction,
    IssueCommentEvent,
    IssueCommentAction,
    AbstractGithubEvent,
)
from packit_service.service.events import EventData
from packit_service.worker.reporting import StatusReporter
from packit_service.worker.allowlist import Allowlist

EXPECTED_TESTING_FARM_CHECK_NAME = "packit-stg/testing-farm-fedora-rawhide-x86_64"


@pytest.fixture()
def allowlist():
    w = Allowlist()
    return w


@pytest.mark.parametrize(
    "account_name, model, is_approved",
    (
        (
            "fero",
            flexmock(
                id=1,
                account_name="fero",
                status=AllowlistStatus.approved_manually.value,
            ),
            True,
        ),
        (
            "lojzo",
            flexmock(
                id=2,
                account_name="lojzo",
                status=AllowlistStatus.approved_automatically.value,
            ),
            True,
        ),
        (
            "konipas",
            flexmock(
                id=3, account_name="konipas", status=AllowlistStatus.waiting.value
            ),
            False,
        ),
        ("krasomila", None, False),
    ),
)
def test_is_approved(allowlist, account_name, model, is_approved):
    flexmock(DBAllowlist).should_receive("get_account").and_return(model)
    assert allowlist.is_approved(account_name) == is_approved


@pytest.mark.parametrize(
    "account_name,person_object,raises,signed_fpca",
    [
        (
            "me",
            {
                "memberships": [
                    {"name": "unicorns"},
                    {"name": "cla_fpca"},
                    {"name": "builder"},
                ]
            },
            None,
            True,
        ),
        ("you", {"memberships": [{"name": "packager"}]}, None, False),
        ("they", {}, None, False),
        ("parrot", {"some": "data"}, None, False),
        ("we", None, AuthError, False),
        ("bear", None, FedoraServiceError, False),
    ],
)
def test_signed_fpca(allowlist, account_name, person_object, raises, signed_fpca):
    fas = (
        flexmock(AccountSystem)
        .should_receive("person_by_username")
        .with_args(account_name)
        .once()
    )
    if person_object is not None:
        fas.and_return(person_object)
    if raises is not None:
        fas.and_raise(raises)

    assert allowlist._signed_fpca(account_name) is signed_fpca


@pytest.mark.parametrize(
    "event, method, approved",
    [
        (
            PullRequestCommentGithubEvent(
                action=PullRequestCommentAction.created,
                pr_id=0,
                base_repo_namespace="base",
                base_repo_name="",
                base_ref="",
                target_repo_namespace="foo",
                target_repo_name="",
                project_url="",
                user_login="bar",
                comment="",
            ),
            "pr_comment",
            False,
        ),
        (
            IssueCommentEvent(
                IssueCommentAction.created,
                0,
                "foo",
                "",
                "",
                "",
                "bar",
                "",
            ),
            "issue_comment",
            False,
        ),
        (
            PullRequestCommentGithubEvent(
                action=PullRequestCommentAction.created,
                pr_id=0,
                base_repo_namespace="foo",
                base_repo_name="",
                base_ref="",
                target_repo_namespace="",
                target_repo_name="",
                project_url="",
                user_login="lojzo",
                comment="",
            ),
            "pr_comment",
            True,
        ),
        (
            IssueCommentEvent(
                IssueCommentAction.created,
                0,
                "",
                "",
                "",
                "",
                "lojzo",
                "",
            ),
            "issue_comment",
            True,
        ),
        (
            PullRequestCommentGithubEvent(
                action=PullRequestCommentAction.created,
                pr_id=0,
                base_repo_namespace="banned_namespace",
                base_repo_name="",
                base_ref="",
                target_repo_namespace="",
                target_repo_name="",
                project_url="",
                user_login="admin",
                comment="",
            ),
            "pr_comment",
            True,
        ),
    ],
)
def test_check_and_report_calls_method(allowlist, event, method, approved):
    gp = GitProject("", GitService(), "")
    mocked_gp = (
        flexmock(gp)
        .should_receive(method)
        .with_args(0, "Namespace foo is not on our allowlist!")
    )
    mocked_gp.never() if approved else mocked_gp.once()
    flexmock(gp).should_receive("can_merge_pr").with_args(event.user_login).and_return(
        approved
    )
    flexmock(gp).should_receive("get_pr").and_return(flexmock(author=None))
    allowlist_mock = flexmock(DBAllowlist).should_receive("get_account")
    if approved:
        allowlist_mock.and_return(DBAllowlist(status="approved_manually"))
    else:
        allowlist_mock.and_return(None)
    assert (
        allowlist.check_and_report(
            event,
            gp,
            service_config=flexmock(deployment=Deployment.stg, admins=["admin"]),
            job_configs=[],
        )
        is approved
    )


@pytest.fixture()
def events(request) -> List[Tuple[AbstractGithubEvent, bool]]:
    """
    :param request: event type to create Event instances of that type
    :return: list of Events that check_and_report accepts together with whether they should pass
    """
    approved_accounts = [
        ("foo", "bar", False),
        ("foo", "lojzo", True),
        ("lojzo", "bar", True),
        ("lojzo", "fero", True),
    ]

    if request.param == "release":
        return [
            (ReleaseEvent("foo", "", "", ""), False),
            (ReleaseEvent("lojzo", "", "", ""), True),
        ]
    elif request.param == "pr":
        return [
            (
                PullRequestGithubEvent(
                    action=PullRequestAction.opened,
                    pr_id=1,
                    base_repo_namespace="",
                    base_repo_name="",
                    target_repo_namespace=namespace,
                    target_repo_name="",
                    project_url="example-url",
                    commit_sha="",
                    user_login=login,
                    base_ref="",
                ),
                approved,
            )
            for namespace, login, approved in approved_accounts
        ]
    elif request.param == "pr_comment":
        return [
            (
                PullRequestCommentGithubEvent(
                    action=PullRequestCommentAction.created,
                    pr_id=1,
                    base_repo_namespace=namespace,
                    base_repo_name="",
                    base_ref="",
                    target_repo_namespace="",
                    target_repo_name="",
                    project_url="",
                    user_login=login,
                    comment="",
                ),
                approved,
            )
            for namespace, login, approved in approved_accounts
        ]
    elif request.param == "issue_comment":
        return [
            (
                IssueCommentEvent(
                    action=IssueCommentAction.created,
                    issue_id=1,
                    repo_namespace=namespace,
                    repo_name="",
                    target_repo="",
                    project_url="",
                    user_login=login,
                    comment="",
                ),
                approved,
            )
            for namespace, login, approved in approved_accounts
        ]
    elif request.param == "admin":
        return [
            (
                PullRequestCommentGithubEvent(
                    action=PullRequestCommentAction.created,
                    pr_id=1,
                    base_repo_namespace="unapproved_namespace_override",
                    base_repo_name="",
                    base_ref="",
                    target_repo_namespace="",
                    target_repo_name="",
                    project_url="",
                    user_login="admin",
                    comment="",
                ),
                True,
            )
        ]
    return []


# https://stackoverflow.com/questions/35413134/what-does-indirect-true-false-in-pytest-mark-parametrize-do-mean
@pytest.mark.parametrize(
    "events",
    ["release", "pr", "pr_comment", "issue_comment", "admin"],
    indirect=True,
)
def test_check_and_report(
    allowlist: Allowlist, events: List[Tuple[AbstractGithubEvent, bool]]
):
    """
    :param allowlist: fixture
    :param events: fixture: [(Event, should-be-approved)]
    """
    flexmock(
        GithubProject,
        pr_comment=lambda *args, **kwargs: None,
        set_commit_status=lambda *args, **kwargs: None,
        issue_comment=lambda *args, **kwargs: None,
        get_pr=lambda *args, **kwargs: flexmock(source_project=flexmock(), author=None),
    )
    job_configs = [
        JobConfig(
            type=JobType.tests,
            trigger=JobConfigTriggerType.pull_request,
            metadata=JobMetadataConfig(targets=["fedora-rawhide"]),
        )
    ]
    flexmock(PullRequestGithubEvent).should_receive("get_package_config").and_return(
        flexmock(
            jobs=job_configs,
        )
    )
    flexmock(PullRequestModel).should_receive("get_or_create").and_return(
        flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request)
    )

    git_project = GithubProject("", GithubService(), "")
    for event, is_valid in events:
        flexmock(GithubProject, can_merge_pr=lambda username: is_valid)
        flexmock(event, project=git_project).should_receive("get_dict").and_return(None)
        # needs to be included when running only `test_allowlist`
        # flexmock(event).should_receive("db_trigger").and_return(
        #     flexmock(job_config_trigger_type=job_configs[0].trigger).mock()
        # )
        flexmock(EventData).should_receive("from_event_dict").and_return(
            flexmock(commit_sha="0000000", pr_id="0")
        )

        if isinstance(event, PullRequestGithubEvent) and not is_valid:
            # Report the status
            flexmock(CoprHelper).should_receive("get_copr_client").and_return(
                Client(
                    config={
                        "copr_url": "https://copr.fedorainfracloud.org",
                        "username": "some-owner",
                    }
                )
            )
            flexmock(LocalProject).should_receive("refresh_the_arguments").and_return(
                None
            )
            flexmock(LocalProject).should_receive("checkout_pr").and_return(None)
            flexmock(StatusReporter).should_receive("report").with_args(
                description="Namespace is not allowed!",
                state=CommitStatus.error,
                url=FAQ_URL,
                check_names=[EXPECTED_TESTING_FARM_CHECK_NAME],
            ).once()
        flexmock(packit_service.worker.build.copr_build).should_receive(
            "get_valid_build_targets"
        ).and_return(
            {
                "fedora-rawhide-x86_64",
            }
        )

        # get_account returns the allowlist object if it exists
        # returns nothing if it isn't allowlisted
        # then inside the allowlist.py file, a function checks if the status is
        # one of the approved statuses

        # this exact code is used twice above but mypy has an issue with this one only
        allowlist_mock = flexmock(DBAllowlist).should_receive("get_account")
        if not TYPE_CHECKING:
            if is_valid:
                allowlist_mock.and_return(DBAllowlist(status="approved_manually"))
            else:
                allowlist_mock.and_return(None)

        assert (
            allowlist.check_and_report(
                event,
                git_project,
                service_config=flexmock(
                    deployment=Deployment.stg,
                    command_handler_work_dir="",
                    admins=["admin"],
                ),
                job_configs=job_configs,
            )
            is is_valid
        )
