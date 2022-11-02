# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Type

import gitlab
import pytest
from celery import Celery
from copr.v3 import Client
from copr.v3 import CoprRequestException, CoprAuthException
from copr.v3.proxies.build import BuildProxy
from flexmock import flexmock
from munch import Munch
from ogr.abstract import CommitStatus, GitProject
from ogr.exceptions import GitForgeInternalError, GitlabAPIException, OgrNetworkError
from ogr.services.github import GithubProject
from ogr.services.github.check_run import (
    GithubCheckRunResult,
    GithubCheckRunStatus,
    create_github_check_run_output,
)
from ogr.services.gitlab import GitlabProject

import packit
import packit_service
from packit.actions import ActionName
from packit.api import PackitAPI
from packit.config import (
    CommonPackageConfig,
    JobConfig,
    JobConfigTriggerType,
    JobType,
    PackageConfig,
)
from packit.copr_helper import CoprHelper
from packit.exceptions import (
    FailedCreateSRPM,
    PackitCoprSettingsException,
    PackitCoprProjectException,
)
from packit_service import sentry_integration
from packit_service.config import ServiceConfig
from packit_service.constants import (
    DATE_OF_DEFAULT_SRPM_BUILD_IN_COPR,
    DEFAULT_RETRY_LIMIT,
    DEFAULT_RETRY_LIMIT_OUTAGE,
)
from packit_service.models import (
    CoprBuildTargetModel,
    GithubInstallationModel,
    GitProjectModel,
    JobTriggerModel,
    JobTriggerModelType,
    SRPMBuildModel,
    BuildStatus,
    PullRequestModel,
)
from packit_service.service.db_triggers import (
    AddBranchPushDbTrigger,
    AddPullRequestDbTrigger,
    AddReleaseDbTrigger,
)
from packit_service.worker.celery_task import CeleryTask
from packit_service.worker.checker.copr import IsGitForgeProjectAndEventOk
from packit_service.worker.events import (
    MergeRequestGitlabEvent,
    PullRequestGithubEvent,
    PushGitHubEvent,
    PushGitlabEvent,
    ReleaseEvent,
    EventData,
)
from packit_service.worker.handlers import CoprBuildHandler
from packit_service.worker.helpers.build import copr_build
from packit_service.worker.helpers.build.copr_build import (
    BaseBuildJobHelper,
    CoprBuildJobHelper,
)
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.parser import Parser
from packit_service.worker.reporting import (
    BaseCommitStatus,
    StatusReporterGithubChecks,
    StatusReporterGitlab,
)
from tests.spellbook import DATA_DIR

DEFAULT_TARGETS = [
    "fedora-29-x86_64",
    "fedora-30-x86_64",
    "fedora-31-x86_64",
    "fedora-rawhide-x86_64",
]
CACHE_CLEAR = [
    packit.copr_helper.CoprHelper.get_available_chroots,
]

pytestmark = pytest.mark.usefixtures("cache_clear", "mock_get_aliases")
create_table_content = StatusReporterGithubChecks._create_table


@pytest.fixture(scope="module")
def branch_push_event() -> PushGitHubEvent:
    file_content = (DATA_DIR / "webhooks" / "github" / "push_branch.json").read_text()
    return Parser.parse_github_push_event(json.loads(file_content))


@pytest.fixture(scope="module")
def branch_push_event_gitlab() -> PushGitlabEvent:
    file_content = (DATA_DIR / "webhooks" / "gitlab" / "push_branch.json").read_text()
    return Parser.parse_gitlab_push_event(json.loads(file_content))


def build_helper(
    event,
    _targets=None,
    owner=None,
    trigger=None,
    jobs=None,
    db_trigger=None,
    selected_job=None,
    project_type: Type[GitProject] = GithubProject,
    build_targets_override=None,
    task: Optional[CeleryTask] = None,
) -> CoprBuildJobHelper:
    if jobs and (_targets or owner):
        raise Exception("Only one job description can be used.")

    if not _targets:
        _targets = DEFAULT_TARGETS
    if not owner:
        owner = "nobody"

    jobs = jobs or [
        JobConfig(
            type=JobType.copr_build,
            trigger=trigger or JobConfigTriggerType.pull_request,
            packages={
                "package": CommonPackageConfig(
                    _targets=_targets,
                    owner=owner,
                )
            },
        )
    ]

    pkg_conf = PackageConfig(
        jobs=jobs,
        packages={"package": CommonPackageConfig(downstream_package_name="dummy")},
    )
    helper = CoprBuildJobHelper(
        service_config=ServiceConfig.get_service_config(),
        package_config=pkg_conf,
        job_config=selected_job or jobs[0],
        project=project_type(
            repo="the-example-repo",
            service=flexmock(
                instance_url="git.instance.io", hostname="git.instance.io"
            ),
            namespace="the/example/namespace",
        ),
        metadata=flexmock(
            pr_id=event.pr_id,
            git_ref=event.git_ref,
            commit_sha=event.commit_sha,
            identifier=event.identifier,
            tag_name=None,
            task_accepted_time=datetime.now(timezone.utc),
            project_url="https://git.instance.io/the/example/namespace/the-example-repo",
        ),
        db_trigger=db_trigger,
        build_targets_override=build_targets_override,
        pushgateway=Pushgateway(),
        celery_task=task,
    )
    helper._api = PackitAPI(ServiceConfig(), pkg_conf)
    return helper


def test_copr_build_check_names(github_pr_event):
    trigger = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        id=123,
        job_trigger_model_type=JobTriggerModelType.pull_request,
    )
    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=JobTriggerModelType.pull_request, trigger_id=123
    ).and_return(flexmock(id=2, type=JobTriggerModelType.pull_request))
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    helper = build_helper(
        event=github_pr_event,
        _targets=["bright-future-x86_64"],
        owner="packit",
        db_trigger=trigger,
    )
    # we need to make sure that pr_id is set
    # so we can check it out and add it to spec's release field
    assert helper.metadata.pr_id

    flexmock(copr_build).should_receive("get_copr_build_info_url").and_return(
        "https://test.url"
    )
    flexmock(StatusReporterGithubChecks).should_receive("set_status").with_args(
        state=BaseCommitStatus.running,
        description="Building SRPM ...",
        check_name="rpm-build:bright-future-x86_64",
        url="",
        links_to_external_services=None,
        markdown_content=None,
    ).and_return()
    flexmock(StatusReporterGithubChecks).should_receive("set_status").with_args(
        state=BaseCommitStatus.running,
        description="Starting RPM build...",
        check_name="rpm-build:bright-future-x86_64",
        url="https://test.url",
        links_to_external_services=None,
        markdown_content=None,
    ).and_return()

    flexmock(GithubProject).should_receive("get_pr").and_return(
        flexmock(source_project=flexmock())
    )
    flexmock(GithubProject).should_receive("create_check_run").and_return().never()
    flexmock(SRPMBuildModel).should_receive("create_with_new_run").and_return(
        (
            flexmock(status=BuildStatus.success)
            .should_receive("set_url")
            .with_args("https://some.host/my.srpm")
            .mock()
            .should_receive("set_start_time")
            .mock()
            .should_receive("set_status")
            .mock()
            .should_receive("set_logs")
            .mock()
            .should_receive("set_end_time")
            .mock(),
            flexmock(),
        )
    )
    flexmock(CoprBuildTargetModel).should_receive("create").and_return(flexmock(id=1))
    flexmock(PullRequestGithubEvent).should_receive("db_trigger").and_return(flexmock())

    flexmock(PackitAPI).should_receive("create_srpm").and_return("my.srpm")

    # copr build
    flexmock(CoprHelper).should_receive("create_copr_project_if_not_exists").with_args(
        project="the-example-namespace-the-example-repo-342",
        chroots=["bright-future-x86_64"],
        owner="packit",
        description=None,
        instructions=None,
        preserve_project=False,
        list_on_homepage=False,
        additional_repos=[],
        request_admin_if_needed=True,
    ).and_return(None)

    flexmock(Client).should_receive("create_from_config_file").and_return(
        flexmock(
            config={"copr_url": "https://copr.fedorainfracloud.org/"},
            build_proxy=flexmock()
            .should_receive("create_from_file")
            .and_return(
                flexmock(
                    id=2,
                    projectname="the-project-name",
                    ownername="packit",
                )
            )
            .mock(),
            mock_chroot_proxy=flexmock()
            .should_receive("get_list")
            .and_return({"bright-future-x86_64": "", "__proxy__": "something"})
            .mock(),
        )
    )

    flexmock(Celery).should_receive("send_task").once()
    assert helper.run_copr_build()["success"]


@pytest.mark.parametrize(
    "retry_number,interval,delay,retry",
    [
        (0, "1 minute", 60, True),
        (1, "2 minutes", 120, True),
        (2, None, None, False),
    ],
)
def test_copr_build_copr_outage_retry(
    github_pr_event, retry_number, interval, delay, retry
):
    trigger = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        id=123,
        job_trigger_model_type=JobTriggerModelType.pull_request,
    )
    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=JobTriggerModelType.pull_request, trigger_id=123
    ).and_return(flexmock(id=2, type=JobTriggerModelType.pull_request))
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    helper = build_helper(
        event=github_pr_event,
        _targets=["bright-future-x86_64"],
        owner="packit",
        db_trigger=trigger,
        task=CeleryTask(flexmock(request=flexmock(retries=retry_number))),
    )
    # we need to make sure that pr_id is set
    # so we can check it out and add it to spec's release field
    assert helper.metadata.pr_id

    flexmock(copr_build).should_receive("get_copr_build_info_url").and_return(
        "https://test.url"
    )
    flexmock(StatusReporterGithubChecks).should_receive("set_status").with_args(
        state=BaseCommitStatus.running,
        description="Building SRPM ...",
        check_name="rpm-build:bright-future-x86_64",
        url="",
        links_to_external_services=None,
        markdown_content=None,
    ).and_return()
    flexmock(StatusReporterGithubChecks).should_receive("set_status").with_args(
        state=BaseCommitStatus.running,
        description="Starting RPM build...",
        check_name="rpm-build:bright-future-x86_64",
        url="https://test.url",
        links_to_external_services=None,
        markdown_content=None,
    ).and_return()

    flexmock(GithubProject).should_receive("get_pr").and_return(
        flexmock(source_project=flexmock())
    )
    flexmock(GithubProject).should_receive("create_check_run").and_return().never()
    flexmock(SRPMBuildModel).should_receive("create_with_new_run").and_return(
        (
            flexmock(status="success")
            .should_receive("set_url")
            .with_args("https://some.host/my.srpm")
            .mock()
            .should_receive("set_start_time")
            .mock()
            .should_receive("set_status")
            .mock()
            .should_receive("set_logs")
            .mock()
            .should_receive("set_end_time")
            .mock(),
            flexmock(),
        )
    )
    flexmock(CoprBuildTargetModel).should_receive("create").and_return(flexmock(id=1))
    flexmock(PullRequestGithubEvent).should_receive("db_trigger").and_return(flexmock())

    flexmock(PackitAPI).should_receive("create_srpm").and_return("my.srpm")

    # copr build
    flexmock(CoprHelper).should_receive("create_copr_project_if_not_exists").with_args(
        project="the-example-namespace-the-example-repo-342",
        chroots=["bright-future-x86_64"],
        owner="packit",
        description=None,
        instructions=None,
        preserve_project=False,
        list_on_homepage=False,
        additional_repos=[],
        request_admin_if_needed=True,
    ).and_return(None)

    exc = CoprRequestException("Unable to connect")
    flexmock(Client).should_receive("create_from_config_file").and_return(
        flexmock(
            config={"copr_url": "https://copr.fedorainfracloud.org/"},
            build_proxy=flexmock()
            .should_receive("create_from_file")
            .and_raise(exc)
            .mock(),
            mock_chroot_proxy=flexmock()
            .should_receive("get_list")
            .and_return({"bright-future-x86_64": "", "__proxy__": "something"})
            .mock(),
        )
    )

    if retry:
        flexmock(CeleryTask).should_receive("retry").with_args(
            ex=exc, delay=delay, max_retries=DEFAULT_RETRY_LIMIT_OUTAGE
        ).once()
        flexmock(StatusReporterGithubChecks).should_receive("set_status").with_args(
            state=BaseCommitStatus.pending,
            description=f"Submit of the build failed due to a Copr error, the task will be"
            f" retried in {interval}.",
            check_name="rpm-build:bright-future-x86_64",
            url="",
            links_to_external_services=None,
            markdown_content=None,
        ).and_return()
    else:
        flexmock(StatusReporterGithubChecks).should_receive("set_status").with_args(
            state=BaseCommitStatus.error,
            description="Submit of the build failed: Unable to connect",
            check_name="rpm-build:bright-future-x86_64",
            url="",
            links_to_external_services=None,
            markdown_content=None,
        ).and_return()

    assert helper.run_copr_build()["success"] is retry


def test_copr_build_check_names_invalid_chroots(github_pr_event):
    build_targets = [
        "bright-future-x86_64",
        "even-brighter-one-aarch64",
        "fedora-32-x86_64",
    ]
    # packit.config.aliases.get_aliases.cache_clear()
    # packit.copr_helper.CoprHelper.get_available_chroots.cache_clear()
    trigger = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        id=123,
        job_trigger_model_type=JobTriggerModelType.pull_request,
    )
    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=JobTriggerModelType.pull_request, trigger_id=123
    ).and_return(flexmock(id=2, type=JobTriggerModelType.pull_request))
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    helper = build_helper(
        event=github_pr_event,
        _targets=build_targets,
        owner="packit",
        db_trigger=trigger,
    )
    # we need to make sure that pr_id is set
    # so we can check it out and add it to spec's release field
    assert helper.metadata.pr_id

    flexmock(copr_build).should_receive("get_copr_build_info_url").and_return(
        "https://test.url"
    )
    flexmock(copr_build).should_receive("get_srpm_build_info_url").and_return(
        "https://test.url"
    )

    for target in build_targets:
        flexmock(StatusReporterGithubChecks).should_receive("set_status").with_args(
            state=BaseCommitStatus.running,
            description="Building SRPM ...",
            check_name=f"rpm-build:{target}",
            url="",
            links_to_external_services=None,
            markdown_content=None,
        ).and_return()

    for not_supported_target in ("bright-future-x86_64", "fedora-32-x86_64"):
        flexmock(StatusReporterGithubChecks).should_receive("set_status").with_args(
            state=BaseCommitStatus.error,
            description=f"Not supported target: {not_supported_target}",
            check_name=f"rpm-build:{not_supported_target}",
            url="https://test.url",
            links_to_external_services=None,
            markdown_content=None,
        ).and_return()

    flexmock(StatusReporterGithubChecks).should_receive("set_status").with_args(
        state=BaseCommitStatus.running,
        description="Starting RPM build...",
        check_name="rpm-build:even-brighter-one-aarch64",
        url="https://test.url",
        links_to_external_services=None,
        markdown_content=None,
    ).and_return()

    flexmock(GithubProject).should_receive("get_pr").and_return(
        flexmock(source_project=flexmock())
        .should_receive("comment")
        .with_args(
            pr_id=342,
            body="There are build targets that are not supported by COPR.\n"
            "<details>\n<summary>Unprocessed build targets</summary>\n\n"
            "```\n"
            "bright-future-x86_64\n"
            "fedora-32-x86_64\n"
            "```\n</details>\n<details>\n"
            "<summary>Available build targets</summary>\n\n"
            "```\n"
            "even-brighter-one-aarch64\n"
            "not-so-bright-future-x86_64\n"
            "```\n</details>",
        )
        .and_return()
    )
    flexmock(SRPMBuildModel).should_receive("create_with_new_run").and_return(
        (
            flexmock(status="success")
            .should_receive("set_url")
            .with_args("https://some.host/my.srpm")
            .mock()
            .should_receive("set_start_time")
            .mock()
            .should_receive("set_status")
            .mock()
            .should_receive("set_logs")
            .mock()
            .should_receive("set_end_time")
            .mock(),
            flexmock(),
        )
    )
    flexmock(CoprBuildTargetModel).should_receive("create").and_return(flexmock(id=1))
    flexmock(PullRequestGithubEvent).should_receive("db_trigger").and_return(flexmock())

    flexmock(PackitAPI).should_receive("create_srpm").and_return("my.srpm")

    # copr build
    flexmock(CoprHelper).should_receive("create_copr_project_if_not_exists").and_return(
        None
    )

    flexmock(Client).should_receive("create_from_config_file").and_return(
        flexmock(
            config={"copr_url": "https://copr.fedorainfracloud.org/"},
            build_proxy=flexmock()
            .should_receive("create_from_file")
            .and_return(
                flexmock(
                    id=2,
                    projectname="the-project-name",
                    ownername="packit",
                )
            )
            .mock(),
            mock_chroot_proxy=flexmock()
            .should_receive("get_list")
            .and_return(
                {
                    "__response__": 200,
                    "not-so-bright-future-x86_64": "",
                    "even-brighter-one-aarch64": "",
                    "__proxy__": "something",
                }
            )
            .mock(),
        )
    )

    flexmock(Celery).should_receive("send_task").once()
    assert helper.run_copr_build()["success"]


def test_copr_build_check_names_multiple_jobs(github_pr_event):
    trigger = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        id=123,
        job_trigger_model_type=JobTriggerModelType.pull_request,
    )
    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=JobTriggerModelType.pull_request, trigger_id=123
    ).and_return(flexmock(id=2, type=JobTriggerModelType.pull_request))
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    helper = build_helper(
        event=github_pr_event,
        jobs=[
            # We run only the job it's config is passed to the handler.
            # Other one(s) has to be run by a different handler instance.
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={
                    "package": CommonPackageConfig(
                        _targets=["fedora-rawhide-x86_64"],
                        owner="nobody",
                        actions={ActionName.post_upstream_clone: "ls /*"},
                    )
                },
            ),
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={
                    "package": CommonPackageConfig(
                        _targets=["fedora-32-x86_64"],
                        owner="nobody",
                        actions={ActionName.post_upstream_clone: 'bash -c "ls /*"'},
                    )
                },
            ),
        ],
        db_trigger=trigger,
        selected_job=JobConfig(
            type=JobType.copr_build,
            trigger=JobConfigTriggerType.pull_request,
            packages={
                "package": CommonPackageConfig(
                    _targets=["fedora-32-x86_64"],
                    owner="nobody",
                    actions={ActionName.post_upstream_clone: 'bash -c "ls /*"'},
                )
            },
        ),
    )
    # we need to make sure that pr_id is set
    # so we can check it out and add it to spec's release field
    assert helper.metadata.pr_id

    flexmock(copr_build).should_receive("get_copr_build_info_url").and_return(
        "https://test.url"
    )
    flexmock(StatusReporterGithubChecks).should_receive("set_status").with_args(
        state=BaseCommitStatus.running,
        description="Building SRPM ...",
        check_name="rpm-build:fedora-32-x86_64",
        url="",
        links_to_external_services=None,
        markdown_content=None,
    ).and_return().once()
    flexmock(StatusReporterGithubChecks).should_receive("set_status").with_args(
        state=BaseCommitStatus.running,
        description="Starting RPM build...",
        check_name="rpm-build:fedora-32-x86_64",
        url="https://test.url",
        links_to_external_services=None,
        markdown_content=None,
    ).and_return().once()

    flexmock(GithubProject).should_receive("get_pr").and_return(
        flexmock(source_project=flexmock())
    )
    flexmock(GithubProject).should_receive("create_check_run").and_return().never()
    flexmock(SRPMBuildModel).should_receive("create_with_new_run").and_return(
        (
            flexmock(status="success")
            .should_receive("set_url")
            .with_args("https://some.host/my.srpm")
            .mock()
            .should_receive("set_start_time")
            .mock()
            .should_receive("set_status")
            .mock()
            .should_receive("set_logs")
            .mock()
            .should_receive("set_end_time")
            .mock(),
            flexmock(),
        )
    )
    flexmock(CoprBuildTargetModel).should_receive("create").and_return(flexmock(id=1))
    flexmock(PullRequestGithubEvent).should_receive("db_trigger").and_return(flexmock())

    flexmock(PackitAPI).should_receive("create_srpm").and_return("my.srpm")

    # copr build
    flexmock(CoprHelper).should_receive("create_copr_project_if_not_exists").with_args(
        project="the-example-namespace-the-example-repo-342",
        chroots=["fedora-32-x86_64"],
        owner="nobody",
        description=None,
        instructions=None,
        preserve_project=None,
        list_on_homepage=None,
        additional_repos=[],
        request_admin_if_needed=True,
    ).and_return(None)

    flexmock(Client).should_receive("create_from_config_file").and_return(
        flexmock(
            config={"copr_url": "https://copr.fedorainfracloud.org/"},
            build_proxy=flexmock()
            .should_receive("create_from_file")
            .and_return(
                flexmock(
                    id=2,
                    projectname="the-project-name",
                    ownername="packit",
                )
            )
            .mock(),
            mock_chroot_proxy=flexmock()
            .should_receive("get_list")
            .and_return({"fedora-32-x86_64": "supported", "__to_be_ignored__": None})
            .mock(),
        )
    )

    flexmock(Celery).should_receive("send_task").once()
    assert helper.run_copr_build()["success"]


def test_copr_build_check_names_custom_owner(github_pr_event):
    trigger = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        id=123,
        job_trigger_model_type=JobTriggerModelType.pull_request,
    )
    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=JobTriggerModelType.pull_request, trigger_id=123
    ).and_return(flexmock(id=2, type=JobTriggerModelType.pull_request))
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    helper = build_helper(
        event=github_pr_event,
        _targets=["bright-future-x86_64"],
        owner="nobody",
        db_trigger=trigger,
    )
    # we need to make sure that pr_id is set
    # so we can check it out and add it to spec's release field
    assert helper.metadata.pr_id

    flexmock(copr_build).should_receive("get_copr_build_info_url").and_return(
        "https://test.url"
    )
    flexmock(StatusReporterGithubChecks).should_receive("set_status").with_args(
        state=BaseCommitStatus.running,
        description="Building SRPM ...",
        check_name="rpm-build:bright-future-x86_64",
        url="",
        links_to_external_services=None,
        markdown_content=None,
    ).and_return()
    flexmock(StatusReporterGithubChecks).should_receive("set_status").with_args(
        state=BaseCommitStatus.running,
        description="Starting RPM build...",
        check_name="rpm-build:bright-future-x86_64",
        url="https://test.url",
        links_to_external_services=None,
        markdown_content=None,
    ).and_return()

    flexmock(GithubProject).should_receive("get_pr").and_return(
        flexmock(source_project=flexmock())
    )
    flexmock(GithubProject).should_receive("create_check_run").and_return().never()
    flexmock(SRPMBuildModel).should_receive("create_with_new_run").and_return(
        (
            flexmock(status="success")
            .should_receive("set_url")
            .with_args("https://some.host/my.srpm")
            .mock()
            .should_receive("set_start_time")
            .mock()
            .should_receive("set_status")
            .mock()
            .should_receive("set_logs")
            .mock()
            .should_receive("set_end_time")
            .mock(),
            flexmock(),
        )
    )
    flexmock(CoprBuildTargetModel).should_receive("create").and_return(flexmock(id=1))
    flexmock(PullRequestGithubEvent).should_receive("db_trigger").and_return(flexmock())

    flexmock(PackitAPI).should_receive("create_srpm").and_return("my.srpm")

    # copr build
    flexmock(CoprHelper).should_receive("create_copr_project_if_not_exists").with_args(
        project="the-example-namespace-the-example-repo-342",
        chroots=["bright-future-x86_64"],
        owner="nobody",
        description=None,
        instructions=None,
        preserve_project=None,
        list_on_homepage=None,
        additional_repos=[],
        request_admin_if_needed=True,
    ).and_return(None)

    flexmock(Client).should_receive("create_from_config_file").and_return(
        flexmock(
            config={"copr_url": "https://copr.fedorainfracloud.org/"},
            build_proxy=flexmock()
            .should_receive("create_from_file")
            .and_return(
                flexmock(
                    id=2,
                    projectname="the-project-name",
                    ownername="nobody",
                )
            )
            .mock(),
            mock_chroot_proxy=flexmock()
            .should_receive("get_list")
            .and_return({"bright-future-x86_64": "", "bright-future-aarch64": ""})
            .mock,
        )
    )

    flexmock(Celery).should_receive("send_task").once()
    assert helper.run_copr_build()["success"]


def test_copr_build_success_set_test_check(github_pr_event):
    # status is set for each test-target (2x):
    #  - Building SRPM ...
    #  - Starting RPM build...
    test_job = JobConfig(
        type=JobType.tests,
        trigger=JobConfigTriggerType.pull_request,
        packages={
            "package": CommonPackageConfig(
                owner="nobody",
                _targets=["bright-future-x86_64", "brightest-future-x86_64"],
            )
        },
    )
    trigger = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        id=123,
        job_trigger_model_type=JobTriggerModelType.pull_request,
    )
    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=JobTriggerModelType.pull_request, trigger_id=123
    ).and_return(flexmock(id=2, type=JobTriggerModelType.pull_request))
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    helper = build_helper(
        jobs=[test_job],
        event=github_pr_event,
        db_trigger=trigger,
    )
    flexmock(GithubProject).should_receive("create_check_run").and_return().times(4)
    flexmock(GithubProject).should_receive("get_pr").and_return(
        flexmock(source_project=flexmock())
    )
    flexmock(SRPMBuildModel).should_receive("create_with_new_run").and_return(
        (
            flexmock(status="success")
            .should_receive("set_url")
            .with_args("https://some.host/my.srpm")
            .mock()
            .should_receive("set_start_time")
            .mock()
            .should_receive("set_status")
            .mock()
            .should_receive("set_logs")
            .mock()
            .should_receive("set_end_time")
            .mock(),
            flexmock(),
        )
    )
    flexmock(CoprBuildTargetModel).should_receive("create").and_return(flexmock(id=1))

    flexmock(PackitAPI).should_receive("create_srpm").and_return("my.srpm")

    # copr build
    flexmock(CoprHelper).should_receive("create_copr_project_if_not_exists").and_return(
        None
    )
    flexmock(Client).should_receive("create_from_config_file").and_return(
        flexmock(
            config={"copr_url": "https://copr.fedorainfracloud.org/"},
            build_proxy=flexmock()
            .should_receive("create_from_file")
            .and_return(
                flexmock(
                    id=2,
                    projectname="the-project-name",
                    ownername="the-owner",
                )
            )
            .mock(),
            mock_chroot_proxy=flexmock()
            .should_receive("get_list")
            .and_return({"bright-future-x86_64": "", "brightest-future-x86_64": ""})
            .mock(),
        )
    )

    flexmock(Celery).should_receive("send_task").once()
    assert helper.run_copr_build()["success"]


def test_copr_build_for_branch(branch_push_event):
    # status is set for each build-target (4x):
    #  - Building SRPM ...
    #  - Starting RPM build...
    branch_build_job = JobConfig(
        type=JobType.build,
        trigger=JobConfigTriggerType.commit,
        packages={
            "package": CommonPackageConfig(
                _targets=DEFAULT_TARGETS,
                owner="nobody",
                dist_git_branches=["build-branch"],
            )
        },
    )
    trigger = flexmock(
        job_config_trigger_type=JobConfigTriggerType.commit,
        id=123,
        job_trigger_model_type=JobTriggerModelType.branch_push,
    )
    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=JobTriggerModelType.branch_push, trigger_id=123
    ).and_return(flexmock(id=2, type=JobTriggerModelType.branch_push))
    flexmock(AddBranchPushDbTrigger).should_receive("db_trigger").and_return(trigger)
    helper = build_helper(
        jobs=[branch_build_job],
        event=branch_push_event,
        db_trigger=trigger,
    )
    flexmock(GithubProject).should_receive("get_pr").and_return(
        flexmock(source_project=flexmock())
    )
    flexmock(GithubProject).should_receive("create_check_run").and_return().times(8)
    flexmock(SRPMBuildModel).should_receive("create_with_new_run").and_return(
        (
            flexmock(status="success")
            .should_receive("set_url")
            .with_args("https://some.host/my.srpm")
            .mock()
            .should_receive("set_start_time")
            .mock()
            .should_receive("set_status")
            .mock()
            .should_receive("set_logs")
            .mock()
            .should_receive("set_end_time")
            .mock(),
            flexmock(),
        )
    )
    flexmock(CoprBuildTargetModel).should_receive("create").and_return(flexmock(id=1))
    flexmock(PushGitHubEvent).should_receive("db_trigger").and_return(flexmock())

    flexmock(PackitAPI).should_receive("create_srpm").and_return("my.srpm")

    # copr build
    flexmock(CoprHelper).should_receive("create_copr_project_if_not_exists").and_return(
        None
    )
    flexmock(Client).should_receive("create_from_config_file").and_return(
        flexmock(
            config={"copr_url": "https://copr.fedorainfracloud.org/"},
            build_proxy=flexmock()
            .should_receive("create_from_file")
            .and_return(
                flexmock(
                    id=2,
                    projectname="the-project-name",
                    ownername="the-owner",
                )
            )
            .mock(),
            mock_chroot_proxy=flexmock()
            .should_receive("get_list")
            .and_return({target: "" for target in DEFAULT_TARGETS})
            .mock(),
        )
    )

    flexmock(Celery).should_receive("send_task").once()
    assert helper.run_copr_build()["success"]


def test_copr_build_for_branch_failed(branch_push_event):
    # status is set for each build-target (4x):
    #  - Building SRPM ...
    #  - Starting RPM build...
    branch_build_job = JobConfig(
        type=JobType.build,
        trigger=JobConfigTriggerType.commit,
        packages={
            "package": CommonPackageConfig(
                _targets=DEFAULT_TARGETS,
                owner="nobody",
                dist_git_branches=["build-branch"],
            )
        },
    )
    trigger = flexmock(
        job_config_trigger_type=JobConfigTriggerType.commit,
        id=123,
        job_trigger_model_type=JobTriggerModelType.branch_push,
    )
    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=JobTriggerModelType.branch_push, trigger_id=123
    ).and_return(flexmock(id=2, type=JobTriggerModelType.branch_push))
    flexmock(AddBranchPushDbTrigger).should_receive("db_trigger").and_return(trigger)
    helper = build_helper(
        jobs=[branch_build_job],
        event=branch_push_event,
        db_trigger=trigger,
    )
    flexmock(GithubProject).should_receive("get_pr").and_return(
        flexmock(source_project=flexmock())
    )
    flexmock(GithubProject).should_receive("create_check_run").and_return().times(8)
    flexmock(GithubProject).should_receive("commit_comment").and_return(flexmock())
    flexmock(SRPMBuildModel).should_receive("create_with_new_run").and_return(
        (
            flexmock(status=BuildStatus.failure, id=2)
            .should_receive("set_url")
            .with_args("https://some.host/my.srpm")
            .mock()
            .should_receive("set_start_time")
            .mock()
            .should_receive("set_status")
            .mock()
            .should_receive("set_logs")
            .mock()
            .should_receive("set_end_time")
            .mock(),
            flexmock(),
        )
    )
    flexmock(CoprBuildTargetModel).should_receive("create").and_return(flexmock(id=1))
    flexmock(PushGitHubEvent).should_receive("db_trigger").and_raise(flexmock())

    flexmock(PackitAPI).should_receive("create_srpm").and_raise(
        FailedCreateSRPM, "some error"
    )

    flexmock(Client).should_receive("create_from_config_file").and_return(
        flexmock(
            config={"copr_url": "https://copr.fedorainfracloud.org/"},
            build_proxy=flexmock()
            .should_receive("create_from_file")
            .and_return(
                flexmock(id=2, projectname="the-project-name", ownername="the-owner")
            )
            .mock(),
            mock_chroot_proxy=flexmock()
            .should_receive("get_list")
            .and_return({target: "" for target in DEFAULT_TARGETS})
            .mock(),
        )
    )

    flexmock(sentry_integration).should_receive("send_to_sentry").and_return().once()
    flexmock(CoprBuildJobHelper).should_receive("run_build").never()

    assert not helper.run_copr_build()["success"]


def test_copr_build_for_release(release_event):
    # status is set for each build-target (4x):
    #  - Building SRPM ...
    #  - Starting RPM build...
    branch_build_job = JobConfig(
        type=JobType.copr_build,
        trigger=JobConfigTriggerType.release,
        packages={
            "package": CommonPackageConfig(
                _targets=DEFAULT_TARGETS,
                owner="nobody",
                dist_git_branches=["build-branch"],
            )
        },
    )
    trigger = flexmock(
        job_config_trigger_type=JobConfigTriggerType.release,
        id=123,
        job_trigger_model_type=JobTriggerModelType.release,
    )
    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=JobTriggerModelType.release, trigger_id=123
    ).and_return(flexmock(id=2, type=JobTriggerModelType.release))
    flexmock(AddReleaseDbTrigger).should_receive("db_trigger").and_return(trigger)
    flexmock(release_event.project).should_receive("get_sha_from_tag").and_return(
        "123456"
    )
    helper = build_helper(
        jobs=[branch_build_job],
        event=release_event,
        db_trigger=trigger,
    )
    flexmock(ReleaseEvent).should_receive("get_project").and_return(helper.project)
    flexmock(GithubProject).should_receive("get_pr").and_return(
        flexmock(source_project=flexmock())
    )
    flexmock(GithubProject).should_receive("create_check_run").and_return().times(8)
    flexmock(SRPMBuildModel).should_receive("create_with_new_run").and_return(
        (
            flexmock(status="success")
            .should_receive("set_url")
            .with_args("https://some.host/my.srpm")
            .mock()
            .should_receive("set_start_time")
            .mock()
            .should_receive("set_status")
            .mock()
            .should_receive("set_logs")
            .mock()
            .should_receive("set_end_time")
            .mock(),
            flexmock(),
        )
    )
    flexmock(CoprBuildTargetModel).should_receive("create").and_return(flexmock(id=1))

    flexmock(PackitAPI).should_receive("create_srpm").with_args(
        srpm_dir=None,
        bump_version=False,
    ).and_return("my.srpm")

    # copr build
    flexmock(CoprHelper).should_receive("create_copr_project_if_not_exists").and_return(
        None
    )
    flexmock(Client).should_receive("create_from_config_file").and_return(
        flexmock(
            config={"copr_url": "https://copr.fedorainfracloud.org/"},
            build_proxy=flexmock()
            .should_receive("create_from_file")
            .and_return(
                flexmock(
                    id=2,
                    projectname="the-project-name",
                    ownername="the-owner",
                )
            )
            .mock(),
            mock_chroot_proxy=flexmock()
            .should_receive("get_list")
            .and_return({target: "" for target in DEFAULT_TARGETS})
            .mock(),
        )
    )

    flexmock(Celery).should_receive("send_task").once()
    assert helper.run_copr_build()["success"]


def test_copr_build_success(github_pr_event):
    # status is set for each build-target (4x):
    #  - Building SRPM ...
    #  - Starting RPM build...
    helper = build_helper(
        event=github_pr_event,
        db_trigger=flexmock(
            job_config_trigger_type=JobConfigTriggerType.pull_request,
            id=123,
            job_trigger_model_type=JobTriggerModelType.pull_request,
        ),
    )
    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=JobTriggerModelType.pull_request, trigger_id=123
    ).and_return(flexmock(id=2, type=JobTriggerModelType.pull_request))
    flexmock(GithubProject).should_receive("create_check_run").and_return().times(8)
    flexmock(GithubProject).should_receive("get_pr").and_return(
        flexmock(source_project=flexmock())
    )
    flexmock(SRPMBuildModel).should_receive("create_with_new_run").and_return(
        (
            flexmock(status="success")
            .should_receive("set_url")
            .with_args("https://some.host/my.srpm")
            .mock()
            .should_receive("set_start_time")
            .mock()
            .should_receive("set_status")
            .mock()
            .should_receive("set_logs")
            .mock()
            .should_receive("set_end_time")
            .mock(),
            flexmock(),
        )
    )
    flexmock(CoprBuildTargetModel).should_receive("create").and_return(flexmock(id=1))
    flexmock(PullRequestGithubEvent).should_receive("db_trigger").and_return(flexmock())

    flexmock(PackitAPI).should_receive("create_srpm").and_return("my.srpm")

    # copr build
    flexmock(CoprHelper).should_receive("create_copr_project_if_not_exists").and_return(
        None
    )
    flexmock(Client).should_receive("create_from_config_file").and_return(
        flexmock(
            config={"copr_url": "https://copr.fedorainfracloud.org/"},
            build_proxy=flexmock()
            .should_receive("create_from_file")
            .and_return(
                flexmock(
                    id=2,
                    projectname="the-project-name",
                    ownername="the-owner",
                )
            )
            .mock(),
            mock_chroot_proxy=flexmock()
            .should_receive("get_list")
            .and_return({target: "" for target in DEFAULT_TARGETS})
            .mock(),
        )
    )

    flexmock(Celery).should_receive("send_task").once()
    assert helper.run_copr_build()["success"]


def test_copr_build_fails_in_packit(github_pr_event):
    # status is set for each build-target (4x):
    #  - Building SRPM ...
    #  - Build failed, check latest comment for details.
    helper = build_helper(
        event=github_pr_event,
        db_trigger=flexmock(
            job_config_trigger_type=JobConfigTriggerType.pull_request,
            id=123,
            job_trigger_model_type=JobTriggerModelType.pull_request,
        ),
    )
    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=JobTriggerModelType.pull_request, trigger_id=123
    ).and_return(flexmock(id=2, type=JobTriggerModelType.pull_request))

    flexmock(packit_service.worker.helpers.build.copr_build).should_receive(
        "get_valid_build_targets"
    ).and_return({"fedora-31-x86_64", "fedora-rawhide-x86_64"})
    templ = "rpm-build:fedora-{ver}-x86_64"
    flexmock(copr_build).should_receive("get_srpm_build_info_url").and_return(
        "https://test.url"
    )
    for v in ["31", "rawhide"]:
        flexmock(GithubProject).should_receive("create_check_run").with_args(
            name=templ.format(ver=v),
            commit_sha="528b803be6f93e19ca4130bf4976f2800a3004c4",
            url=None,
            external_id="2",
            status=GithubCheckRunStatus.in_progress,
            conclusion=None,
            output=create_github_check_run_output("Building SRPM ...", ""),
        ).and_return().once()
    for v in ["31", "rawhide"]:
        flexmock(GithubProject).should_receive("create_check_run").with_args(
            name=templ.format(ver=v),
            commit_sha="528b803be6f93e19ca4130bf4976f2800a3004c4",
            url="https://test.url",
            external_id="2",
            status=GithubCheckRunStatus.completed,
            conclusion=GithubCheckRunResult.failure,
            output=create_github_check_run_output(
                "SRPM build failed, check the logs for details.",
                create_table_content(
                    url="https://test.url", links_to_external_services=None
                ),
            ),
        ).and_return().once()
    flexmock(GithubProject).should_receive("get_pr").and_return(
        flexmock(source_project=flexmock())
    )
    flexmock(SRPMBuildModel).should_receive("create_with_new_run").and_return(
        (
            flexmock(status=BuildStatus.failure, id=2)
            .should_receive("set_url")
            .with_args("https://some.host/my.srpm")
            .mock()
            .should_receive("set_start_time")
            .mock()
            .should_receive("set_status")
            .mock()
            .should_receive("set_logs")
            .mock()
            .should_receive("set_end_time")
            .mock(),
            flexmock(),
        )
    )
    flexmock(CoprBuildTargetModel).should_receive("create").and_return(flexmock(id=1))
    flexmock(sentry_integration).should_receive("send_to_sentry").and_return().once()

    flexmock(PackitAPI).should_receive("create_srpm").and_raise(
        FailedCreateSRPM, "some error"
    )

    flexmock(CoprBuildJobHelper).should_receive("run_build").never()

    assert not helper.run_copr_build()["success"]


def test_copr_build_fails_to_update_copr_project(github_pr_event):
    # status is set for each build-target (4x):
    #  - Building SRPM ...
    #  - Build failed, check latest comment for details.
    helper = build_helper(
        event=github_pr_event,
        db_trigger=flexmock(
            job_config_trigger_type=JobConfigTriggerType.pull_request,
            id=123,
            job_trigger_model_type=JobTriggerModelType.pull_request,
        ),
    )
    helper.celery_task = CeleryTask(
        flexmock(request=flexmock(retries=DEFAULT_RETRY_LIMIT))
    )
    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=JobTriggerModelType.pull_request, trigger_id=123
    ).and_return(flexmock(id=2, type=JobTriggerModelType.pull_request))
    templ = "rpm-build:fedora-{ver}-x86_64"
    flexmock(copr_build).should_receive("get_srpm_build_info_url").and_return(
        "https://test.url"
    )
    flexmock(packit_service.worker.helpers.build.copr_build).should_receive(
        "get_valid_build_targets"
    ).and_return({"fedora-31-x86_64", "fedora-rawhide-x86_64"})
    for v in ["31", "rawhide"]:
        flexmock(GithubProject).should_receive("create_check_run").with_args(
            name=templ.format(ver=v),
            commit_sha="528b803be6f93e19ca4130bf4976f2800a3004c4",
            url=None,
            external_id="2",
            status=GithubCheckRunStatus.in_progress,
            conclusion=None,
            output=create_github_check_run_output("Building SRPM ...", ""),
        ).and_return().once()
    for v in ["31", "rawhide"]:
        flexmock(GithubProject).should_receive("create_check_run").with_args(
            name=templ.format(ver=v),
            commit_sha="528b803be6f93e19ca4130bf4976f2800a3004c4",
            url=None,
            external_id="2",
            status=GithubCheckRunStatus.completed,
            conclusion=GithubCheckRunResult.failure,
            output=create_github_check_run_output(
                "Submit of the build failed: Copr project update failed.", ""
            ),
        ).and_return().once()
    flexmock(SRPMBuildModel).should_receive("create_with_new_run").and_return(
        (
            flexmock(status="success", id=2)
            .should_receive("set_url")
            .with_args("https://some.host/my.srpm")
            .mock()
            .should_receive("set_start_time")
            .mock()
            .should_receive("set_status")
            .mock()
            .should_receive("set_logs")
            .mock()
            .should_receive("set_end_time")
            .mock(),
            flexmock(),
        )
    )

    flexmock(CoprBuildTargetModel).should_receive("create").and_return(flexmock(id=1))

    flexmock(PackitAPI).should_receive("create_srpm").and_return("my.srpm")
    flexmock(GithubProject).should_receive("get_pr").with_args(342).and_return(
        flexmock()
    )
    flexmock(GithubProject).should_receive("get_pr").with_args(pr_id=342).and_return(
        flexmock(source_project=flexmock())
        .should_receive("comment")
        .with_args(
            body="Based on your Packit configuration the settings of the "
            "nobody/the-example-namespace-the-example-repo-342 "
            "Copr project would need to be updated as follows:\n"
            "\n"
            "| field | old value | new value |\n"
            "| ----- | --------- | --------- |\n"
            "| chroots | ['f30', 'f31'] | ['f31', 'f32'] |\n"
            "| description | old | new |\n"
            "\n"
            "Diff of chroots:\n"
            "```diff\n"
            "-f30\n"
            "+f32\n"
            "```\n"
            "\n"
            "Packit was unable to update the settings above "
            "as it is missing `admin` permissions on the "
            "nobody/the-example-namespace-the-example-repo-342 Copr project.\n"
            "\n"
            "To fix this you can do one of the following:\n"
            "\n"
            "- Grant Packit `admin` permissions on the "
            "nobody/the-example-namespace-the-example-repo-342 "
            "Copr project on the [permissions page](https://copr.fedorainfracloud.org/coprs/nobody/"
            "the-example-namespace-the-example-repo-342/permissions/).\n"
            "- Change the above Copr project settings manually on the "
            "[settings page](https://copr.fedorainfracloud.org/"
            "coprs/nobody/the-example-namespace-the-example-repo-342/edit/) "
            "to match the Packit configuration.\n"
            "- Update the Packit configuration to match the Copr project settings.\n"
            "\n"
            "Please retrigger the build, once the issue above is fixed.\n",
        )
        .and_return()
        .mock()
    )

    flexmock(sentry_integration).should_receive("send_to_sentry").and_return().once()
    # copr build
    flexmock(CoprHelper).should_receive("get_copr_settings_url").with_args(
        "nobody",
        "the-example-namespace-the-example-repo-342",
        section="permissions",
    ).and_return(
        "https://copr.fedorainfracloud.org/"
        "coprs/nobody/the-example-namespace-the-example-repo-342/permissions/"
    ).once()

    flexmock(CoprHelper).should_receive("get_copr_settings_url").with_args(
        "nobody",
        "the-example-namespace-the-example-repo-342",
    ).and_return(
        "https://copr.fedorainfracloud.org/"
        "coprs/nobody/the-example-namespace-the-example-repo-342/edit/"
    ).once()

    flexmock(CoprHelper).should_receive("create_copr_project_if_not_exists").and_raise(
        PackitCoprSettingsException,
        "Copr project update failed.",
        fields_to_change={
            "chroots": (["f30", "f31"], ["f31", "f32"]),
            "description": ("old", "new"),
        },
    )

    assert not helper.run_copr_build()["success"]


def test_copr_build_fails_chroot_update(github_pr_event):
    """Verify that comment we post when we fail to update chroots on our projects
    is correct and not the one about permissions"""
    helper = build_helper(
        event=github_pr_event,
        db_trigger=flexmock(
            job_config_trigger_type=JobConfigTriggerType.pull_request,
            id=123,
            job_trigger_model_type=JobTriggerModelType.pull_request,
        ),
    )
    # enforce that we are reporting on our own Copr project
    helper.job_build.owner = "packit"
    flexmock(copr_build).should_receive("get_valid_build_targets").and_return(
        {"f31", "f32"}
    )
    flexmock(CoprHelper).should_receive("create_copr_project_if_not_exists").and_raise(
        PackitCoprSettingsException,
        "Copr project update failed.",
        fields_to_change={
            "chroots": (["f30", "f31"], ["f31", "f32"]),
            "description": ("old", "new"),
        },
    )
    status_reporter = (
        flexmock()
        .should_receive("comment")
        .with_args(
            body="Settings of a Copr project packit/the-example-namespace-the-example-repo-342"
            " need to be updated, but Packit can't do that when there are previous "
            "builds still in progress.\n"
            "You should be able to resolve the problem by recreating this pull request "
            "or running `/packit build` after all builds finished.\n\n"
            "This was the change Packit tried to do:\n"
            "\n"
            "| field | old value | new value |\n"
            "| ----- | --------- | --------- |\n"
            "| chroots | ['f30', 'f31'] | ['f31', 'f32'] |\n"
            "| description | old | new |\n"
            "\n"
            "Diff of chroots:\n"
            "```diff\n"
            "-f30\n"
            "+f32\n"
            "```\n"
        )
        .and_return()
        .mock()
    )

    flexmock(BaseBuildJobHelper).should_receive("status_reporter").and_return(
        status_reporter
    )
    with pytest.raises(PackitCoprSettingsException):
        helper.create_copr_project_if_not_exists()


def test_copr_build_no_targets(github_pr_event):
    # status is set for each build-target (fedora-stable => 2x):
    #  - Building SRPM ...
    #  - Starting RPM build...
    helper = build_helper(
        event=github_pr_event,
        owner="nobody",
        db_trigger=flexmock(
            job_config_trigger_type=JobConfigTriggerType.pull_request,
            id=123,
            job_trigger_model_type=JobTriggerModelType.pull_request,
        ),
    )
    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=JobTriggerModelType.pull_request, trigger_id=123
    ).and_return(flexmock(id=2, type=JobTriggerModelType.pull_request))

    flexmock(copr_build).should_receive("get_srpm_build_info_url").and_return(
        "https://test.url"
    )
    flexmock(copr_build).should_receive("get_valid_build_targets").and_return(
        {"fedora-32-x86_64", "fedora-31-x86_64"}
    )
    flexmock(GithubProject).should_receive("create_check_run").and_return().times(4)
    flexmock(GithubProject).should_receive("get_pr").and_return(
        flexmock(source_project=flexmock())
    )
    flexmock(SRPMBuildModel).should_receive("create_with_new_run").and_return(
        (
            flexmock(status="success")
            .should_receive("set_url")
            .with_args("https://some.host/my.srpm")
            .mock()
            .should_receive("set_start_time")
            .mock()
            .should_receive("set_status")
            .mock()
            .should_receive("set_logs")
            .mock()
            .should_receive("set_end_time")
            .mock(),
            flexmock(),
        )
    )
    flexmock(CoprBuildTargetModel).should_receive("create").and_return(flexmock(id=1))
    flexmock(PullRequestGithubEvent).should_receive("db_trigger").and_return(flexmock())

    flexmock(PackitAPI).should_receive("create_srpm").and_return("my.srpm")

    # copr build
    flexmock(CoprHelper).should_receive("create_copr_project_if_not_exists").and_return(
        None
    )
    flexmock(CoprHelper).should_receive("get_copr_client").and_return(
        flexmock(
            config={"copr_url": "https://copr.fedorainfracloud.org/"},
            build_proxy=flexmock()
            .should_receive("create_from_file")
            .and_return(
                flexmock(
                    id=2,
                    projectname="the-project-name",
                    ownername="the-owner",
                )
            )
            .mock(),
            mock_chroot_proxy=flexmock()
            .should_receive("get_list")
            .and_return(
                {target: "" for target in {"fedora-32-x86_64", "fedora-31-x86_64"}}
            )
            .mock(),
        )
    )

    flexmock(Celery).should_receive("send_task").once()

    assert helper.run_copr_build()["success"]


def test_copr_build_check_names_gitlab(gitlab_mr_event):
    trigger = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        id=123,
        job_trigger_model_type=JobTriggerModelType.pull_request,
    )
    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=JobTriggerModelType.pull_request, trigger_id=123
    ).and_return(flexmock(id=2, type=JobTriggerModelType.pull_request))
    flexmock(CoprBuildJobHelper).should_receive("is_reporting_allowed").and_return(True)
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    helper = build_helper(
        event=gitlab_mr_event,
        _targets=["bright-future-x86_64"],
        owner="nobody",
        db_trigger=trigger,
        project_type=GitlabProject,
    )

    flexmock(copr_build).should_receive("get_copr_build_info_url").and_return(
        "https://test.url"
    )

    flexmock(StatusReporterGitlab).should_receive("set_status").with_args(
        state=BaseCommitStatus.running,
        description="Building SRPM ...",
        check_name="rpm-build:bright-future-x86_64",
        url="",
        links_to_external_services=None,
        markdown_content=None,
    ).and_return()
    flexmock(StatusReporterGitlab).should_receive("set_status").with_args(
        state=BaseCommitStatus.running,
        description="Starting RPM build...",
        check_name="rpm-build:bright-future-x86_64",
        url="https://test.url",
        links_to_external_services=None,
        markdown_content=None,
    ).and_return()

    mr = flexmock(source_project=flexmock())
    flexmock(GitlabProject).should_receive("get_pr").and_return(mr)
    flexmock(mr.source_project).should_receive("set_commit_status").and_return().never()

    flexmock(SRPMBuildModel).should_receive("create_with_new_run").and_return(
        (
            flexmock(status="success")
            .should_receive("set_url")
            .with_args("https://some.host/my.srpm")
            .mock()
            .should_receive("set_start_time")
            .mock()
            .should_receive("set_status")
            .mock()
            .should_receive("set_logs")
            .mock()
            .should_receive("set_end_time")
            .mock(),
            flexmock(),
        )
    )
    flexmock(CoprBuildTargetModel).should_receive("create").and_return(flexmock(id=1))
    flexmock(MergeRequestGitlabEvent).should_receive("db_trigger").and_return(
        flexmock()
    )

    flexmock(PackitAPI).should_receive("create_srpm").and_return("my.srpm")

    flexmock(packit_service.worker.helpers.build.copr_build).should_receive(
        "get_valid_build_targets"
    ).and_return(["bright-future-x86_64"])

    # copr build
    flexmock(CoprHelper).should_receive("create_copr_project_if_not_exists").with_args(
        project="git.instance.io-the-example-namespace-the-example-repo-1",
        chroots=["bright-future-x86_64"],
        owner="nobody",
        description=None,
        instructions=None,
        preserve_project=None,
        list_on_homepage=None,
        additional_repos=[],
        request_admin_if_needed=True,
    ).and_return(None)

    flexmock(CoprHelper).should_receive("get_copr_client").and_return(
        flexmock(
            config={"copr_url": "https://copr.fedorainfracloud.org/"},
            build_proxy=flexmock()
            .should_receive("create_from_file")
            .and_return(
                flexmock(
                    id=2,
                    projectname="the-project-name",
                    ownername="nobody",
                )
            )
            .mock(),
            mock_chroot_proxy=flexmock()
            .should_receive("get_list")
            .and_return({"bright-future-x86_64": ""})
            .mock(),
        )
    )

    flexmock(Celery).should_receive("send_task").once()

    assert helper.run_copr_build()["success"]


def test_copr_build_success_set_test_check_gitlab(gitlab_mr_event):
    # status is set for each test-target (2x):
    #  - Building SRPM ...
    #  - Starting RPM build...
    test_job = JobConfig(
        type=JobType.tests,
        trigger=JobConfigTriggerType.pull_request,
        packages={
            "package": CommonPackageConfig(
                owner="nobody",
                _targets=["bright-future-x86_64", "brightest-future-x86_64"],
            )
        },
    )
    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=JobTriggerModelType.pull_request, trigger_id=123
    ).and_return(flexmock(id=2, type=JobTriggerModelType.pull_request))
    flexmock(CoprBuildJobHelper).should_receive("is_reporting_allowed").and_return(True)
    flexmock(packit_service.worker.helpers.build.copr_build).should_receive(
        "get_valid_build_targets"
    ).and_return(["bright-future-x86_64", "brightest-future-x86_64"])
    trigger = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        id=123,
        job_trigger_model_type=JobTriggerModelType.pull_request,
    )
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    helper = build_helper(
        jobs=[test_job],
        event=gitlab_mr_event,
        db_trigger=trigger,
        project_type=GitlabProject,
    )
    mr = flexmock(source_project=flexmock())
    flexmock(GitlabProject).should_receive("get_pr").and_return(mr)
    flexmock(mr.source_project).should_receive("set_commit_status").and_return().times(
        4
    )

    flexmock(SRPMBuildModel).should_receive("create_with_new_run").and_return(
        (
            flexmock(status="success")
            .should_receive("set_url")
            .with_args("https://some.host/my.srpm")
            .mock()
            .should_receive("set_start_time")
            .mock()
            .should_receive("set_status")
            .mock()
            .should_receive("set_logs")
            .mock()
            .should_receive("set_end_time")
            .mock(),
            flexmock(),
        )
    )
    flexmock(CoprBuildTargetModel).should_receive("create").and_return(flexmock(id=1))

    flexmock(PackitAPI).should_receive("create_srpm").and_return("my.srpm")

    # copr build
    flexmock(CoprHelper).should_receive("create_copr_project_if_not_exists").and_return(
        None
    )
    flexmock(CoprHelper).should_receive("get_copr_client").and_return(
        flexmock(
            config={"copr_url": "https://copr.fedorainfracloud.org/"},
            build_proxy=flexmock()
            .should_receive("create_from_file")
            .and_return(
                flexmock(
                    id=2,
                    projectname="the-project-name",
                    ownername="the-owner",
                )
            )
            .mock(),
            mock_chroot_proxy=flexmock()
            .should_receive("get_list")
            .and_return({"bright-future-x86_64": "", "brightest-future-x86_64": ""})
            .mock(),
        )
    )

    flexmock(Celery).should_receive("send_task").once()
    assert helper.run_copr_build()["success"]


def test_copr_build_for_branch_gitlab(branch_push_event_gitlab):
    # status is set for each build-target (4x):
    #  - Building SRPM ...
    #  - Starting RPM build...
    branch_build_job = JobConfig(
        type=JobType.build,
        trigger=JobConfigTriggerType.commit,
        packages={
            "package": CommonPackageConfig(
                _targets=DEFAULT_TARGETS,
                owner="nobody",
                dist_git_branches=["build-branch"],
            )
        },
    )
    flexmock(CoprBuildJobHelper).should_receive("is_reporting_allowed").and_return(True)
    trigger = flexmock(
        job_config_trigger_type=JobConfigTriggerType.commit,
        id=123,
        job_trigger_model_type=JobTriggerModelType.branch_push,
    )
    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=JobTriggerModelType.branch_push, trigger_id=123
    ).and_return(flexmock(id=2, type=JobTriggerModelType.branch_push))
    flexmock(AddBranchPushDbTrigger).should_receive("db_trigger").and_return(trigger)
    helper = build_helper(
        jobs=[branch_build_job],
        event=branch_push_event_gitlab,
        db_trigger=trigger,
        project_type=GitlabProject,
    )
    flexmock(GitlabProject).should_receive("set_commit_status").and_return().times(8)
    flexmock(SRPMBuildModel).should_receive("create_with_new_run").and_return(
        (
            flexmock(status="success")
            .should_receive("set_url")
            .with_args("https://some.host/my.srpm")
            .mock()
            .should_receive("set_start_time")
            .mock()
            .should_receive("set_status")
            .mock()
            .should_receive("set_logs")
            .mock()
            .should_receive("set_end_time")
            .mock(),
            flexmock(),
        )
    )
    flexmock(CoprBuildTargetModel).should_receive("create").and_return(flexmock(id=1))
    flexmock(PushGitHubEvent).should_receive("db_trigger").and_return(flexmock())

    flexmock(PackitAPI).should_receive("create_srpm").and_return("my.srpm")

    # copr build
    flexmock(CoprHelper).should_receive("create_copr_project_if_not_exists").and_return(
        None
    )
    flexmock(CoprHelper).should_receive("get_copr_client").and_return(
        flexmock(
            config={"copr_url": "https://copr.fedorainfracloud.org/"},
            build_proxy=flexmock()
            .should_receive("create_from_file")
            .and_return(
                flexmock(
                    id=2,
                    projectname="the-project-name",
                    ownername="the-owner",
                )
            )
            .mock(),
            mock_chroot_proxy=flexmock()
            .should_receive("get_list")
            .and_return({target: "" for target in DEFAULT_TARGETS})
            .mock(),
        )
    )
    flexmock(packit_service.worker.helpers.build.copr_build).should_receive(
        "get_valid_build_targets"
    ).and_return(DEFAULT_TARGETS)

    flexmock(Celery).should_receive("send_task").once()
    assert helper.run_copr_build()["success"]


def test_copr_build_success_gitlab(gitlab_mr_event):
    # status is set for each build-target (4x):
    #  - Building SRPM ...
    #  - Starting RPM build...
    helper = build_helper(
        event=gitlab_mr_event,
        db_trigger=flexmock(
            job_config_trigger_type=JobConfigTriggerType.pull_request,
            id=123,
            job_trigger_model_type=JobTriggerModelType.pull_request,
        ),
        project_type=GitlabProject,
    )
    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=JobTriggerModelType.pull_request, trigger_id=123
    ).and_return(flexmock(id=2, type=JobTriggerModelType.pull_request))
    flexmock(CoprBuildJobHelper).should_receive("is_reporting_allowed").and_return(True)
    mr = flexmock(source_project=flexmock())
    flexmock(GitlabProject).should_receive("get_pr").and_return(mr)
    flexmock(mr.source_project).should_receive("set_commit_status").and_return().times(
        8
    )

    flexmock(SRPMBuildModel).should_receive("create_with_new_run").and_return(
        (
            flexmock(status="success")
            .should_receive("set_url")
            .with_args("https://some.host/my.srpm")
            .mock()
            .should_receive("set_start_time")
            .mock()
            .should_receive("set_status")
            .mock()
            .should_receive("set_logs")
            .mock()
            .should_receive("set_end_time")
            .mock(),
            flexmock(),
        )
    )
    flexmock(CoprBuildTargetModel).should_receive("create").and_return(flexmock(id=1))
    flexmock(MergeRequestGitlabEvent).should_receive("db_trigger").and_return(
        flexmock()
    )

    flexmock(PackitAPI).should_receive("create_srpm").and_return("my.srpm")

    # copr build
    flexmock(CoprHelper).should_receive("create_copr_project_if_not_exists").and_return(
        None
    )
    flexmock(CoprHelper).should_receive("get_copr_client").and_return(
        flexmock(
            config={"copr_url": "https://copr.fedorainfracloud.org/"},
            build_proxy=flexmock()
            .should_receive("create_from_file")
            .and_return(
                flexmock(
                    id=2,
                    projectname="the-project-name",
                    ownername="the-owner",
                )
            )
            .mock(),
            mock_chroot_proxy=flexmock()
            .should_receive("get_list")
            .and_return({target: "" for target in DEFAULT_TARGETS})
            .mock(),
        )
    )
    flexmock(packit_service.worker.helpers.build.copr_build).should_receive(
        "get_valid_build_targets"
    ).and_return(DEFAULT_TARGETS)

    flexmock(Celery).should_receive("send_task").once()
    assert helper.run_copr_build()["success"]


def test_copr_build_fails_in_packit_gitlab(gitlab_mr_event):
    # status is set for each build-target (4x):
    #  - Building SRPM ...
    #  - Build failed, check latest comment for details.
    helper = build_helper(
        event=gitlab_mr_event,
        db_trigger=flexmock(
            job_config_trigger_type=JobConfigTriggerType.pull_request,
            id=123,
            job_trigger_model_type=JobTriggerModelType.pull_request,
        ),
        project_type=GitlabProject,
    )
    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=JobTriggerModelType.pull_request, trigger_id=123
    ).and_return(flexmock(id=2, type=JobTriggerModelType.pull_request))
    flexmock(CoprBuildJobHelper).should_receive("is_reporting_allowed").and_return(True)
    templ = "rpm-build:fedora-{ver}-x86_64"
    flexmock(copr_build).should_receive("get_srpm_build_info_url").and_return(
        "https://test.url"
    )
    flexmock(packit_service.worker.helpers.build.copr_build).should_receive(
        "get_valid_build_targets"
    ).and_return({"fedora-31-x86_64", "fedora-rawhide-x86_64"})

    mr = flexmock(source_project=flexmock())
    flexmock(GitlabProject).should_receive("get_pr").and_return(mr)

    for v in ["31", "rawhide"]:
        flexmock(mr.source_project).should_receive("set_commit_status").with_args(
            "1f6a716aa7a618a9ffe56970d77177d99d100022",
            CommitStatus.running,
            "",
            "Building SRPM ...",
            templ.format(ver=v),
            trim=True,
        ).and_return().once()
    for v in ["31", "rawhide"]:
        flexmock(mr.source_project).should_receive("set_commit_status").with_args(
            "1f6a716aa7a618a9ffe56970d77177d99d100022",
            CommitStatus.failure,
            "https://test.url",
            "SRPM build failed, check the logs for details.",
            templ.format(ver=v),
            trim=True,
        ).and_return().once()
    flexmock(SRPMBuildModel).should_receive("create_with_new_run").and_return(
        (
            flexmock(status=BuildStatus.failure, id=2)
            .should_receive("set_url")
            .with_args("https://some.host/my.srpm")
            .mock()
            .should_receive("set_start_time")
            .mock()
            .should_receive("set_status")
            .mock()
            .should_receive("set_logs")
            .mock()
            .should_receive("set_end_time")
            .mock(),
            flexmock(),
        )
    )
    flexmock(CoprBuildTargetModel).should_receive("create").and_return(flexmock(id=1))
    flexmock(sentry_integration).should_receive("send_to_sentry").and_return().once()

    flexmock(PackitAPI).should_receive("create_srpm").and_raise(
        FailedCreateSRPM, "some error"
    )

    flexmock(CoprBuildJobHelper).should_receive("run_build").never()

    assert not helper.run_copr_build()["success"]


def test_copr_build_success_gitlab_comment(gitlab_mr_event):
    helper = build_helper(
        event=gitlab_mr_event,
        db_trigger=flexmock(
            job_config_trigger_type=JobConfigTriggerType.pull_request,
            id=123,
            job_trigger_model_type=JobTriggerModelType.pull_request,
        ),
        project_type=GitlabProject,
    )
    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=JobTriggerModelType.pull_request, trigger_id=123
    ).and_return(flexmock(id=2, type=JobTriggerModelType.pull_request))
    flexmock(BaseBuildJobHelper).should_receive("is_gitlab_instance").and_return(True)
    flexmock(BaseBuildJobHelper).should_receive("base_project").and_return(
        GitlabProject(
            repo="the-example-repo",
            service=flexmock(),
            namespace="the-example-namespace",
        )
    )
    flexmock(GitlabProject).should_receive("request_access").and_return()
    flexmock(CoprBuildJobHelper).should_receive("is_reporting_allowed").and_return(
        False
    )
    pr = flexmock(
        comment=flexmock().should_receive("comment").and_return().mock(),
        source_project=flexmock(),
    )
    flexmock(GitlabProject).should_receive("get_pr").and_return(pr)
    exception = GitlabAPIException()
    exception.__cause__ = gitlab.GitlabError(response_code=403)
    flexmock(pr.source_project).should_receive("set_commit_status").and_raise(exception)
    flexmock(GitlabProject).should_receive("commit_comment").and_return()
    flexmock(GitlabProject).should_receive("get_commit_comments").and_return([])
    flexmock(SRPMBuildModel).should_receive("create_with_new_run").and_return(
        (
            flexmock(status="success", id=42)
            .should_receive("set_url")
            .with_args("https://some.host/my.srpm")
            .mock()
            .should_receive("set_start_time")
            .mock()
            .should_receive("set_status")
            .mock()
            .should_receive("set_logs")
            .mock()
            .should_receive("set_end_time")
            .mock(),
            flexmock(),
        )
    )
    flexmock(CoprBuildTargetModel).should_receive("create").and_return(flexmock(id=1))
    flexmock(MergeRequestGitlabEvent).should_receive("db_trigger").and_return(
        flexmock()
    )

    flexmock(PackitAPI).should_receive("create_srpm").and_return("my.srpm")

    # copr build
    flexmock(CoprHelper).should_receive("create_copr_project_if_not_exists").and_return(
        None
    )
    flexmock(CoprHelper).should_receive("get_copr_client").and_return(
        flexmock(
            config={"copr_url": "https://copr.fedorainfracloud.org/"},
            build_proxy=flexmock()
            .should_receive("create_from_file")
            .and_return(
                flexmock(
                    id=2,
                    projectname="the-project-name",
                    ownername="the-owner",
                )
            )
            .mock(),
            mock_chroot_proxy=flexmock()
            .should_receive("get_list")
            .and_return({target: "" for target in DEFAULT_TARGETS})
            .mock(),
        )
    )
    flexmock(packit_service.worker.helpers.build.copr_build).should_receive(
        "get_valid_build_targets"
    ).and_return(
        {
            "fedora-33-x86_64",
            "fedora-32-x86_64",
            "fedora-31-x86_64",
            "fedora-rawhide-x86_64",
        }
    )

    flexmock(Celery).should_receive("send_task").once()
    assert helper.run_copr_build()["success"]


def test_copr_build_no_targets_gitlab(gitlab_mr_event):
    # status is set for each build-target (fedora-stable => 2x):
    #  - Building SRPM ...
    #  - Starting RPM build...
    helper = build_helper(
        event=gitlab_mr_event,
        owner="nobody",
        db_trigger=flexmock(
            job_config_trigger_type=JobConfigTriggerType.pull_request,
            id=123,
            job_trigger_model_type=JobTriggerModelType.pull_request,
        ),
        project_type=GitlabProject,
    )
    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=JobTriggerModelType.pull_request, trigger_id=123
    ).and_return(flexmock(id=2, type=JobTriggerModelType.pull_request))
    flexmock(CoprBuildJobHelper).should_receive("is_reporting_allowed").and_return(True)
    flexmock(copr_build).should_receive("get_valid_build_targets").and_return(
        {"fedora-32-x86_64", "fedora-31-x86_64"}
    )
    mr = flexmock(source_project=flexmock())
    flexmock(GitlabProject).should_receive("get_pr").and_return(mr)
    flexmock(mr.source_project).should_receive("set_commit_status").and_return().times(
        4
    )
    flexmock(SRPMBuildModel).should_receive("create_with_new_run").and_return(
        (
            flexmock(status="success")
            .should_receive("set_url")
            .with_args("https://some.host/my.srpm")
            .mock()
            .should_receive("set_start_time")
            .mock()
            .should_receive("set_status")
            .mock()
            .should_receive("set_logs")
            .mock()
            .should_receive("set_end_time")
            .mock(),
            flexmock(),
        )
    )
    flexmock(CoprBuildTargetModel).should_receive("create").and_return(flexmock(id=1))
    flexmock(MergeRequestGitlabEvent).should_receive("db_trigger").and_return(
        flexmock()
    )

    flexmock(PackitAPI).should_receive("create_srpm").and_return("my.srpm")

    # copr build
    flexmock(CoprHelper).should_receive("create_copr_project_if_not_exists").and_return(
        None
    )
    flexmock(CoprHelper).should_receive("get_copr_client").and_return(
        flexmock(
            config={"copr_url": "https://copr.fedorainfracloud.org/"},
            build_proxy=flexmock()
            .should_receive("create_from_file")
            .and_return(
                flexmock(
                    id=2,
                    projectname="the-project-name",
                    ownername="the-owner",
                )
            )
            .mock(),
            mock_chroot_proxy=flexmock()
            .should_receive("get_list")
            .and_return(
                {target: "" for target in {"fedora-32-x86_64", "fedora-31-x86_64"}}
            )
            .mock(),
        )
    )

    flexmock(Celery).should_receive("send_task").once()
    assert helper.run_copr_build()["success"]


def test_copr_build_targets_override(github_pr_event):
    # status is set for only one test-target defined in targets_override (2x):
    #  - Building SRPM ...
    #  - Starting RPM build...
    test_job = JobConfig(
        type=JobType.tests,
        trigger=JobConfigTriggerType.pull_request,
        packages={
            "package": CommonPackageConfig(
                owner="nobody",
                _targets=["bright-future-x86_64", "brightest-future-x86_64"],
            )
        },
    )
    trigger = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        id=123,
        job_trigger_model_type=JobTriggerModelType.pull_request,
    )
    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=JobTriggerModelType.pull_request, trigger_id=123
    ).and_return(flexmock(id=2, type=JobTriggerModelType.pull_request))
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    helper = build_helper(
        jobs=[test_job],
        event=github_pr_event,
        db_trigger=trigger,
        build_targets_override={"bright-future-x86_64"},
    )
    flexmock(GithubProject).should_receive("create_check_run").and_return().times(2)
    flexmock(GithubProject).should_receive("get_pr").and_return(
        flexmock(source_project=flexmock())
    )
    flexmock(SRPMBuildModel).should_receive("create_with_new_run").and_return(
        (
            flexmock(status="success")
            .should_receive("set_url")
            .with_args("https://some.host/my.srpm")
            .mock()
            .should_receive("set_start_time")
            .mock()
            .should_receive("set_status")
            .mock()
            .should_receive("set_logs")
            .mock()
            .should_receive("set_end_time")
            .mock(),
            flexmock(),
        )
    )
    flexmock(CoprBuildTargetModel).should_receive("create").and_return(flexmock(id=1))

    flexmock(PackitAPI).should_receive("create_srpm").and_return("my.srpm")

    # copr build
    flexmock(CoprHelper).should_receive("create_copr_project_if_not_exists").and_return(
        None
    )
    flexmock(Client).should_receive("create_from_config_file").and_return(
        flexmock(
            config={"copr_url": "https://copr.fedorainfracloud.org/"},
            build_proxy=flexmock()
            .should_receive("create_from_file")
            .with_args(
                ownername="nobody",
                projectname="the-example-namespace-the-example-repo-342",
                path=Path("my.srpm"),
                buildopts={
                    "chroots": ["bright-future-x86_64"],
                    "enable_net": True,
                    "packit_forge_project": helper.forge_project,
                },
            )
            .and_return(
                flexmock(
                    id=2,
                    projectname="the-example-namespace-the-example-repo-342",
                    ownername="nobody",
                )
            )
            .mock(),
            mock_chroot_proxy=flexmock()
            .should_receive("get_list")
            .and_return({"bright-future-x86_64": ""})
            .mock(),
        )
    )

    flexmock(Celery).should_receive("send_task").once()
    assert helper.run_copr_build()["success"]


@pytest.mark.parametrize(
    "srpm_build_deps,installation_date",
    [
        pytest.param(
            None,
            DATE_OF_DEFAULT_SRPM_BUILD_IN_COPR + timedelta(days=1),
            id="new_installation",
        ),
        pytest.param(
            None,
            DATE_OF_DEFAULT_SRPM_BUILD_IN_COPR.replace(tzinfo=None) + timedelta(days=1),
            id="new_installation_without_timezone",
        ),
        pytest.param(
            [], DATE_OF_DEFAULT_SRPM_BUILD_IN_COPR, id="explicitly_defined_empty_key"
        ),  # user defines this key (it's None by default)
        pytest.param(
            ["make", "findutils"],
            DATE_OF_DEFAULT_SRPM_BUILD_IN_COPR,
            id="explicitly_defined_key_with_custom_deps",
        ),
    ],
)
def test_run_copr_build_from_source_script(
    github_pr_event, srpm_build_deps, installation_date
):
    helper = build_helper(
        event=github_pr_event,
        db_trigger=flexmock(
            job_config_trigger_type=JobConfigTriggerType.pull_request,
            id=123,
            job_trigger_model_type=JobTriggerModelType.pull_request,
        ),
    )
    helper.package_config.srpm_build_deps = srpm_build_deps
    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=JobTriggerModelType.pull_request, trigger_id=123
    ).and_return(flexmock(id=2, type=JobTriggerModelType.pull_request))
    flexmock(GithubInstallationModel).should_receive("get_by_account_login").with_args(
        account_login="packit-service"
    ).and_return(
        flexmock(
            created_at=installation_date,
            repositories=[flexmock(repo_name="packit")],
        )
    )
    flexmock(GitProjectModel).should_receive("get_by_id").and_return(
        flexmock(repo_name="packit")
    )
    flexmock(GithubProject).should_receive("create_check_run").and_return().times(4)
    flexmock(GithubProject).should_receive("get_pr").and_return(
        flexmock(source_project=flexmock(), target_branch="main")
    )
    flexmock(SRPMBuildModel).should_receive("create_with_new_run").and_return(
        (
            flexmock(status="success", id=1),
            flexmock(),
        )
    )
    flexmock(CoprBuildTargetModel).should_receive("create").and_return(
        flexmock(id=1)
    ).times(4)
    flexmock(PullRequestGithubEvent).should_receive("db_trigger").and_return(flexmock())

    # copr build
    flexmock(CoprHelper).should_receive("create_copr_project_if_not_exists").and_return(
        None
    )
    flexmock(helper).should_receive("get_packit_copr_download_urls").and_return([])
    flexmock(helper).should_receive("get_latest_fedora_stable_chroot").and_return(
        "fedora-35-x86_64"
    )

    flexmock(helper).should_call("run_copr_build").times(0)
    flexmock(helper).should_call("run_copr_build_from_source_script").once()

    flexmock(Client).should_receive("create_from_config_file").and_return(
        flexmock(
            config={"copr_url": "https://copr.fedorainfracloud.org/"},
            build_proxy=flexmock()
            .should_receive("create_from_custom")
            .and_return(
                flexmock(
                    id=2,
                    projectname="the-project-name",
                    ownername="the-owner",
                )
            )
            .mock(),
            mock_chroot_proxy=flexmock()
            .should_receive("get_list")
            .and_return({target: "" for target in DEFAULT_TARGETS})
            .mock(),
        )
    )

    flexmock(Celery).should_receive("send_task").once()
    handler = CoprBuildHandler(
        package_config=helper.package_config,
        job_config=helper.job_config,
        event=github_pr_event.get_dict(),
        celery_task=flexmock(),
    )
    handler._copr_build_helper = helper
    assert handler.run()["success"]


@pytest.mark.parametrize(
    "retry_number,interval,delay,retry, exc",
    [
        (0, "1 minute", 60, True, OgrNetworkError("Get PR failed")),
        (1, "2 minutes", 120, True, OgrNetworkError("Get PR failed")),
        (2, None, None, False, OgrNetworkError("Get PR failed")),
        (0, "10 seconds", 10, True, GitForgeInternalError("Get PR failed")),
        (1, "20 seconds", 20, True, GitForgeInternalError("Get PR failed")),
        (2, None, None, False, GitForgeInternalError("Get PR failed")),
    ],
)
def test_run_copr_build_from_source_script_github_outage_retry(
    github_pr_event, retry_number, interval, delay, retry, exc
):
    helper = build_helper(
        event=github_pr_event,
        db_trigger=flexmock(
            job_config_trigger_type=JobConfigTriggerType.pull_request,
            id=123,
            job_trigger_model_type=JobTriggerModelType.pull_request,
        ),
        task=CeleryTask(flexmock(request=flexmock(retries=retry_number))),
    )
    helper.package_config.srpm_build_deps = ["make", "findutils"]
    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=JobTriggerModelType.pull_request, trigger_id=123
    ).and_return(flexmock(id=2, type=JobTriggerModelType.pull_request))
    flexmock(GithubProject).should_receive("get_pr").and_raise(exc)
    flexmock(SRPMBuildModel).should_receive("create_with_new_run").and_return(
        (
            flexmock(status="success", id=1),
            flexmock(),
        )
    )
    flexmock(PullRequestGithubEvent).should_receive("db_trigger").and_return(flexmock())

    # copr build
    flexmock(CoprHelper).should_receive("create_copr_project_if_not_exists").and_return(
        None
    )
    flexmock(helper).should_receive("get_packit_copr_download_urls").and_return([])
    flexmock(helper).should_receive("get_latest_fedora_stable_chroot").and_return(
        "fedora-35-x86_64"
    )
    flexmock(Client).should_receive("create_from_config_file").and_return(
        flexmock(
            config={"copr_url": "https://copr.fedorainfracloud.org/"},
            build_proxy=flexmock()
            .should_receive("create_from_custom")
            .and_return(
                flexmock(
                    id=2,
                    projectname="the-project-name",
                    ownername="the-owner",
                )
            )
            .mock(),
            mock_chroot_proxy=flexmock()
            .should_receive("get_list")
            .and_return({"bright-future-x86_64": "", "__proxy__": "something"})
            .mock(),
        )
    )
    if retry:
        flexmock(CeleryTask).should_receive("retry").with_args(
            ex=exc,
            delay=delay,
            max_retries=DEFAULT_RETRY_LIMIT_OUTAGE
            if exc.__class__ is OgrNetworkError
            else None,
        ).once()
        flexmock(StatusReporterGithubChecks).should_receive("set_status").with_args(
            state=BaseCommitStatus.pending,
            description=f"Submit of the build failed due to a Git forge error, the task will be"
            f" retried in {interval}.",
            check_name="rpm-build:bright-future-x86_64",
            url="",
            links_to_external_services=None,
            markdown_content=None,
        ).and_return()
    else:
        flexmock(StatusReporterGithubChecks).should_receive("set_status").with_args(
            state=BaseCommitStatus.error,
            description=f"Submit of the build failed: {exc}",
            check_name="rpm-build:bright-future-x86_64",
            url="",
            links_to_external_services=None,
            markdown_content=None,
        ).and_return()

    assert helper.run_copr_build_from_source_script()["success"] is retry


def test_get_latest_fedora_stable_chroot(github_pr_event):
    flexmock(packit_service.worker.helpers.build.copr_build).should_receive(
        "get_aliases"
    ).and_return({"fedora-stable": ["fedora-34", "fedora-35"]})
    flexmock(packit_service.worker.helpers.build.copr_build).should_receive(
        "get_valid_build_targets"
    ).with_args("fedora-35").and_return({"fedora-35-x86_64"})
    assert (
        build_helper(github_pr_event).get_latest_fedora_stable_chroot()
        == "fedora-35-x86_64"
    )


def test_get_packit_copr_download_urls(github_pr_event):
    copr_response_built_packages = Munch(
        {
            "packages": [
                {
                    "arch": "noarch",
                    "epoch": 0,
                    "name": "python3-packit",
                    "release": "1.2",
                    "version": "0.38.0",
                },
                {
                    "arch": "src",
                    "epoch": 0,
                    "name": "packit",
                    "release": "1.2",
                    "version": "0.38.0",
                },
                {
                    "arch": "noarch",
                    "epoch": 0,
                    "name": "packit",
                    "release": "1.2",
                    "version": "0.38.0",
                },
            ],
        }
    )

    flexmock(Client).should_receive("create_from_config_file").and_return(
        flexmock(
            config={"copr_url": "https://copr.fedorainfracloud.org/"},
            package_proxy=flexmock()
            .should_receive("get")
            .with_args(
                ownername="packit",
                projectname="packit-stable",
                packagename="packit",
                with_latest_succeeded_build=True,
            )
            .and_return(Munch({"builds": {"latest_succeeded": {"id": 123}}}))
            .mock(),
            build_chroot_proxy=flexmock()
            .should_receive("get")
            .with_args(123, "fedora-35-x86_64")
            .and_return(Munch({"result_url": "https://results/"}))
            .mock()
            .should_receive("get_built_packages")
            .with_args(123, "fedora-35-x86_64")
            .and_return(copr_response_built_packages)
            .mock(),
        )
    )
    helper = build_helper(event=github_pr_event)
    flexmock(helper).should_receive("get_latest_fedora_stable_chroot").and_return(
        "fedora-35-x86_64"
    )
    urls = [
        "https://results/python3-packit-0.38.0-1.2.noarch.rpm",
        "https://results/packit-0.38.0-1.2.noarch.rpm",
    ]

    assert helper.get_packit_copr_download_urls() == urls


@pytest.mark.parametrize(
    "package_config,job_config,result",
    [
        (
            PackageConfig(
                jobs=[
                    JobConfig(
                        type=JobType.copr_build,
                        trigger=JobConfigTriggerType.pull_request,
                        packages={
                            "package": CommonPackageConfig(
                                _targets=["fedora-all"],
                            )
                        },
                    ),
                    JobConfig(
                        type=JobType.tests,
                        trigger=JobConfigTriggerType.pull_request,
                        packages={"package": CommonPackageConfig()},
                    ),
                ],
                packages={"package": CommonPackageConfig()},
            ),
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={
                    "package": CommonPackageConfig(
                        _targets=["fedora-all"],
                    )
                },
            ),
            0,
        ),
        (
            PackageConfig(
                jobs=[
                    JobConfig(
                        type=JobType.tests,
                        trigger=JobConfigTriggerType.commit,
                        packages={"package": CommonPackageConfig()},
                    ),
                    JobConfig(
                        type=JobType.copr_build,
                        trigger=JobConfigTriggerType.commit,
                        packages={
                            "package": CommonPackageConfig(
                                _targets=["fedora-all"],
                            )
                        },
                    ),
                    JobConfig(
                        type=JobType.copr_build,
                        trigger=JobConfigTriggerType.pull_request,
                        packages={
                            "package": CommonPackageConfig(
                                _targets=["fedora-all"],
                            )
                        },
                    ),
                    JobConfig(
                        type=JobType.tests,
                        trigger=JobConfigTriggerType.pull_request,
                        packages={"package": CommonPackageConfig()},
                    ),
                ],
                packages={"package": CommonPackageConfig()},
            ),
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={
                    "package": CommonPackageConfig(
                        _targets=["fedora-all"],
                    )
                },
            ),
            2,
        ),
    ],
)
def test_get_job_config_index(package_config, job_config, result):
    assert (
        CoprBuildJobHelper(
            package_config=package_config,
            job_config=job_config,
            service_config=ServiceConfig.get_service_config(),
            project=None,
            metadata=None,
            db_trigger=None,
        ).get_job_config_index()
        == result
    )


@pytest.mark.parametrize(
    "is_custom_copr_project,is_project_allowed_in_copr_by_config,copr_server_raise_exc,buildopts",
    [
        (True, True, False, {"chroots": [], "enable_net": True}),
        (False, True, False, {"chroots": [], "enable_net": True}),
        (
            True,
            False,
            False,
            {"chroots": [], "enable_net": True, "packit_forge_project": ""},
        ),
        (False, False, False, {"chroots": [], "enable_net": True}),
        (False, False, True, {"chroots": [], "enable_net": True}),
    ],
)
def test_submit_copr_build(
    github_pr_event,
    is_custom_copr_project,
    is_project_allowed_in_copr_by_config,
    copr_server_raise_exc,
    buildopts,
):
    helper = build_helper(event=github_pr_event)
    flexmock(helper).should_receive("create_copr_project_if_not_exists").and_return("")
    flexmock(helper).should_receive("is_custom_copr_project_defined").and_return(
        is_custom_copr_project
    )
    flexmock(helper).should_receive(
        "is_forge_project_allowed_to_build_in_copr_by_config"
    ).and_return(is_project_allowed_in_copr_by_config)
    flexmock(helper).should_receive("job_project").and_return("")
    flexmock(helper).should_receive("srpm_path").and_return("")
    flexmock(helper).should_receive("forge_project").and_return("")
    flexmock(helper).should_receive("configured_copr_project").and_return("")
    flexmock(CoprHelper).should_receive("get_copr_settings_url").and_return(
        "https://copr.fedorainfracloud.org/coprs//edit/"
    )
    flexmock(helper).should_receive("status_reporter").and_return(
        flexmock()
        .should_receive("comment")
        .with_args(
            body="Your git-forge project is not allowed to use the configured `` Copr project.\n\n"
            "Please, add this git-forge project `` to `Packit allowed forge projects`in the "
            "[Copr project settings]"
            "(https://copr.fedorainfracloud.org/coprs//edit/#packit_forge_projects_allowed). "
        )
        .mock()
    )
    if copr_server_raise_exc:
        flexmock(BuildProxy).should_receive("create_from_file").and_raise(
            CoprAuthException("Forge project .... can't build in this Copr via Packit.")
        )
        with pytest.raises(CoprAuthException):
            helper.submit_copr_build()

    else:
        flexmock(BuildProxy).should_receive("create_from_file").with_args(
            ownername="", projectname="", path="", buildopts=buildopts
        ).and_return(flexmock(id=0))
        helper.submit_copr_build()


@pytest.mark.parametrize(
    "raw_name,expected_name",
    [
        ("packit-specfile-91-fedora-epel", "packit-specfile-91-fedora-epel"),
        ("packit-specfile-91-fedora+epel", "packit-specfile-91-fedora-epel"),
        ("packit-specfile-my@fancy@branch", "packit-specfile-my-fancy-branch"),
        ("packit-specfile-v23:1", "packit-specfile-v23-1"),
    ],
)
def test_normalise_copr_project_name(raw_name, expected_name):
    assert CoprBuildJobHelper.normalise_copr_project_name(raw_name) == expected_name


def test_copr_build_invalid_copr_project_name(github_pr_event):
    """Verify that comment we post when we fail to update chroots on our projects
    is correct and not the one about permissions"""
    helper = build_helper(
        event=github_pr_event,
        db_trigger=flexmock(
            job_config_trigger_type=JobConfigTriggerType.pull_request,
            id=123,
            job_trigger_model_type=JobTriggerModelType.pull_request,
        ),
    )
    # enforce that we are reporting on our own Copr project
    helper.job_build.owner = "packit"
    flexmock(copr_build).should_receive("get_valid_build_targets").and_return(
        {"f31", "f32"}
    )
    flexmock(CoprHelper).should_receive("create_copr_project_if_not_exists").and_raise(
        PackitCoprProjectException(
            "Cannot create a new Copr project (owner=packit-stg project="
            "packit-specfile-91-fedora+epel chroots=['fedora-rawhide-x86_64', "
            "'epel-9-x86_64', 'fedora-36-x86_64', 'fedora-35-x86_64']): name: "
            "Name must contain only letters, digits, underscores, dashes and dots.",
        )
    )
    expected_body = (
        "We were not able to find or create Copr project "
        "`packit/the-example-namespace-the-example-repo-342` "
        "specified in the config with the following error:\n"
        "```\nCannot create a new Copr project (owner=packit-stg project="
        "packit-specfile-91-fedora+epel chroots=['fedora-rawhide-x86_64', "
        "'epel-9-x86_64', 'fedora-36-x86_64', 'fedora-35-x86_64']): name: "
        "Name must contain only letters, digits, underscores, dashes and dots.\n```\n---\n"
        "Please check your configuration for:\n\n"
        "1. typos in owner and project name (groups need to be prefixed with `@`)\n"
        "2. whether the project name doesn't contain not allowed characters (only letters, "
        "digits, underscores, dashes and dots must be used)\n"
        "3. whether the project itself exists (Packit creates projects"
        " only in its own namespace)\n"
        "4. whether Packit is allowed to build in your Copr project\n"
        "5. whether your Copr project/group is not private"
    )
    status_reporter = (
        flexmock()
        .should_receive("comment")
        .with_args(body=expected_body)
        .and_return()
        .mock()
    )

    flexmock(CoprBuildJobHelper).should_receive("status_reporter").and_return(
        status_reporter
    )
    with pytest.raises(PackitCoprProjectException):
        helper.create_copr_project_if_not_exists()


@pytest.mark.parametrize(
    "jobs,should_pass",
    [
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            use_internal_tf=True,
                        )
                    },
                ),
            ],
            False,
            id="one_internal_test_job",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            identifier="public",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            use_internal_tf=True,
                        )
                    },
                ),
            ],
            False,
            id="multiple_test_jobs_one_internal",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            identifier="public",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            use_internal_tf=True,
                            skip_build=True,
                        )
                    },
                ),
            ],
            True,
            id="multiple_test_jobs_one_internal_skip_build",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            skip_build=True,
                            identifier="public",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            use_internal_tf=True,
                        )
                    },
                ),
            ],
            False,
            id="multiple_test_jobs_one_internal_another_skip_build",
        ),
    ],
)
def test_check_if_actor_can_run_job_and_report(jobs, should_pass):
    package_config = PackageConfig(packages={"package": CommonPackageConfig()})
    package_config.jobs = jobs

    flexmock(PullRequestModel).should_receive("get_or_create").and_return(
        flexmock(
            job_config_trigger_type=JobConfigTriggerType.pull_request,
            id=123,
            job_trigger_model_type=JobTriggerModelType.pull_request,
        )
    )

    gh_project = flexmock(namespace="n", repo="r")
    gh_project.should_receive("can_merge_pr").with_args("actor").and_return(False)
    flexmock(EventData).should_receive("get_project").and_return(gh_project)
    flexmock(ServiceConfig).should_receive("get_project").and_return(gh_project)

    flexmock(IsGitForgeProjectAndEventOk).should_receive("pre_check").and_return(True)

    if not should_pass:
        flexmock(CoprBuildJobHelper).should_receive("report_status_to_build").once()

    assert (
        CoprBuildHandler.pre_check(
            package_config,
            jobs[0],
            {
                "event_type": "PullRequestGithubEvent",
                "actor": "actor",
                "project_url": "url",
            },
        )
        == should_pass
    )
