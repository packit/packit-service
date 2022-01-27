# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Type

import pytest
from celery import Celery
from copr.v3 import Client
from flexmock import flexmock

import gitlab
from munch import Munch

import packit
import packit_service

from ogr.abstract import GitProject, CommitStatus
from ogr.services.github import GithubProject
from ogr.services.github.check_run import (
    GithubCheckRunStatus,
    GithubCheckRunResult,
    create_github_check_run_output,
)
from ogr.services.gitlab import GitlabProject
from packit.actions import ActionName
from packit.api import PackitAPI
from packit.config import PackageConfig, JobConfig, JobType, JobConfigTriggerType
from packit.config.job_config import JobMetadataConfig
from packit.copr_helper import CoprHelper
from packit.exceptions import FailedCreateSRPM, PackitCoprSettingsException
from packit_service import sentry_integration
from packit_service.config import ServiceConfig, Deployment
from packit_service.models import (
    CoprBuildModel,
    SRPMBuildModel,
    JobTriggerModel,
    JobTriggerModelType,
)
from packit_service.service.db_triggers import (
    AddPullRequestDbTrigger,
    AddBranchPushDbTrigger,
    AddReleaseDbTrigger,
)
from packit_service.worker.events import (
    PullRequestGithubEvent,
    PushGitHubEvent,
    ReleaseEvent,
    PushGitlabEvent,
    MergeRequestGitlabEvent,
)
from packit_service.worker.build import copr_build
from packit_service.worker.build.copr_build import (
    CoprBuildJobHelper,
    BaseBuildJobHelper,
)
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.parser import Parser
from packit_service.worker.reporting import (
    BaseCommitStatus,
    StatusReporterGitlab,
    StatusReporterGithubChecks,
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
    metadata=None,
    trigger=None,
    jobs=None,
    db_trigger=None,
    selected_job=None,
    project_type: Type[GitProject] = GithubProject,
    targets_override=None,
):
    if jobs and metadata:
        raise Exception("Only one of jobs and metadata can be used.")

    if not metadata:
        metadata = JobMetadataConfig(
            _targets=DEFAULT_TARGETS,
            owner="nobody",
        )

    jobs = jobs or [
        JobConfig(
            type=JobType.copr_build,
            trigger=trigger or JobConfigTriggerType.pull_request,
            metadata=metadata,
        )
    ]

    pkg_conf = PackageConfig(jobs=jobs, downstream_package_name="dummy")
    handler = CoprBuildJobHelper(
        service_config=ServiceConfig(),
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
        targets_override=targets_override,
        pushgateway=Pushgateway(),
    )
    handler._api = PackitAPI(ServiceConfig(), pkg_conf)
    return handler


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
        metadata=JobMetadataConfig(_targets=["bright-future-x86_64"], owner="packit"),
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
    flexmock(CoprBuildModel).should_receive("create").and_return(flexmock(id=1))
    flexmock(PullRequestGithubEvent).should_receive("db_trigger").and_return(flexmock())

    flexmock(PackitAPI).should_receive("create_srpm").and_return("my.srpm")

    # copr build
    flexmock(CoprHelper).should_receive("create_copr_project_if_not_exists").with_args(
        project="the-example-namespace-the-example-repo-342-stg",
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
        metadata=JobMetadataConfig(_targets=build_targets, owner="packit"),
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
    flexmock(CoprBuildModel).should_receive("create").and_return(flexmock(id=1))
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
                metadata=JobMetadataConfig(
                    _targets=["fedora-rawhide-x86_64"], owner="nobody"
                ),
                actions={ActionName.post_upstream_clone: "ls /*"},
            ),
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                metadata=JobMetadataConfig(
                    _targets=["fedora-32-x86_64"], owner="nobody"
                ),
                actions={ActionName.post_upstream_clone: 'bash -c "ls /*"'},
            ),
        ],
        db_trigger=trigger,
        selected_job=JobConfig(
            type=JobType.copr_build,
            trigger=JobConfigTriggerType.pull_request,
            metadata=JobMetadataConfig(_targets=["fedora-32-x86_64"], owner="nobody"),
            actions={ActionName.post_upstream_clone: 'bash -c "ls /*"'},
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
    flexmock(CoprBuildModel).should_receive("create").and_return(flexmock(id=1))
    flexmock(PullRequestGithubEvent).should_receive("db_trigger").and_return(flexmock())

    flexmock(PackitAPI).should_receive("create_srpm").and_return("my.srpm")

    # copr build
    flexmock(CoprHelper).should_receive("create_copr_project_if_not_exists").with_args(
        project="the-example-namespace-the-example-repo-342-stg",
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
        metadata=JobMetadataConfig(_targets=["bright-future-x86_64"], owner="nobody"),
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
    flexmock(CoprBuildModel).should_receive("create").and_return(flexmock(id=1))
    flexmock(PullRequestGithubEvent).should_receive("db_trigger").and_return(flexmock())

    flexmock(PackitAPI).should_receive("create_srpm").and_return("my.srpm")

    # copr build
    flexmock(CoprHelper).should_receive("create_copr_project_if_not_exists").with_args(
        project="the-example-namespace-the-example-repo-342-stg",
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
        metadata=JobMetadataConfig(
            owner="nobody", _targets=["bright-future-x86_64", "brightest-future-x86_64"]
        ),
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
    flexmock(CoprBuildModel).should_receive("create").and_return(flexmock(id=1))

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
        metadata=JobMetadataConfig(
            _targets=DEFAULT_TARGETS,
            owner="nobody",
            dist_git_branches=["build-branch"],
        ),
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
    flexmock(CoprBuildModel).should_receive("create").and_return(flexmock(id=1))
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
        metadata=JobMetadataConfig(
            _targets=DEFAULT_TARGETS,
            owner="nobody",
            dist_git_branches=["build-branch"],
        ),
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
            flexmock(status="failure", id=2)
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
    flexmock(CoprBuildModel).should_receive("create").and_return(flexmock(id=1))
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
        type=JobType.build,
        trigger=JobConfigTriggerType.release,
        metadata=JobMetadataConfig(
            _targets=DEFAULT_TARGETS,
            owner="nobody",
            dist_git_branches=["build-branch"],
        ),
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
    flexmock(CoprBuildModel).should_receive("create").and_return(flexmock(id=1))

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
    flexmock(CoprBuildModel).should_receive("create").and_return(flexmock(id=1))
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

    flexmock(packit_service.worker.build.copr_build).should_receive(
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
            flexmock(status="failure", id=2)
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
    flexmock(CoprBuildModel).should_receive("create").and_return(flexmock(id=1))
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
    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=JobTriggerModelType.pull_request, trigger_id=123
    ).and_return(flexmock(id=2, type=JobTriggerModelType.pull_request))
    templ = "rpm-build:fedora-{ver}-x86_64"
    flexmock(copr_build).should_receive("get_srpm_build_info_url").and_return(
        "https://test.url"
    )
    flexmock(packit_service.worker.build.copr_build).should_receive(
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

    flexmock(CoprBuildModel).should_receive("create").and_return(flexmock(id=1))

    flexmock(PackitAPI).should_receive("create_srpm").and_return("my.srpm")
    flexmock(GithubProject).should_receive("get_pr").with_args(342).and_return(
        flexmock()
    )
    flexmock(GithubProject).should_receive("get_pr").with_args(pr_id=342).and_return(
        flexmock(source_project=flexmock())
        .should_receive("comment")
        .with_args(
            body="Based on your Packit configuration the settings of the "
            "nobody/the-example-namespace-the-example-repo-342-stg "
            "Copr project would need to be updated as follows:\n"
            "\n"
            "| field | old value | new value |\n"
            "| ----- | --------- | --------- |\n"
            "| chroots | ['f30', 'f31'] | ['f31', 'f32'] |\n"
            "| description | old | new |\n"
            "\n"
            "\n"
            "Packit was unable to update the settings above "
            "as it is missing `admin` permissions on the "
            "nobody/the-example-namespace-the-example-repo-342-stg Copr project.\n"
            "\n"
            "To fix this you can do one of the following:\n"
            "\n"
            "- Grant Packit `admin` permissions on the "
            "nobody/the-example-namespace-the-example-repo-342-stg "
            "Copr project on the [permissions page](https://copr.fedorainfracloud.org/coprs/nobody/"
            "the-example-namespace-the-example-repo-342-stg/permissions/).\n"
            "- Change the above Copr project settings manually on the "
            "[settings page](https://copr.fedorainfracloud.org/"
            "coprs/nobody/the-example-namespace-the-example-repo-342-stg/edit/) "
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
        "the-example-namespace-the-example-repo-342-stg",
        section="permissions",
    ).and_return(
        "https://copr.fedorainfracloud.org/"
        "coprs/nobody/the-example-namespace-the-example-repo-342-stg/permissions/"
    ).once()

    flexmock(CoprHelper).should_receive("get_copr_settings_url").with_args(
        "nobody",
        "the-example-namespace-the-example-repo-342-stg",
    ).and_return(
        "https://copr.fedorainfracloud.org/"
        "coprs/nobody/the-example-namespace-the-example-repo-342-stg/edit/"
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


def test_copr_build_no_targets(github_pr_event):
    # status is set for each build-target (fedora-stable => 2x):
    #  - Building SRPM ...
    #  - Starting RPM build...
    helper = build_helper(
        event=github_pr_event,
        metadata=JobMetadataConfig(owner="nobody"),
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
    flexmock(CoprBuildModel).should_receive("create").and_return(flexmock(id=1))
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
        metadata=JobMetadataConfig(_targets=["bright-future-x86_64"], owner="nobody"),
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
    flexmock(CoprBuildModel).should_receive("create").and_return(flexmock(id=1))
    flexmock(MergeRequestGitlabEvent).should_receive("db_trigger").and_return(
        flexmock()
    )

    flexmock(PackitAPI).should_receive("create_srpm").and_return("my.srpm")

    flexmock(packit_service.worker.build.copr_build).should_receive(
        "get_valid_build_targets"
    ).and_return(["bright-future-x86_64"])

    # copr build
    flexmock(CoprHelper).should_receive("create_copr_project_if_not_exists").with_args(
        project="git.instance.io-the-example-namespace-the-example-repo-1-stg",
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
        metadata=JobMetadataConfig(
            owner="nobody", _targets=["bright-future-x86_64", "brightest-future-x86_64"]
        ),
    )
    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=JobTriggerModelType.pull_request, trigger_id=123
    ).and_return(flexmock(id=2, type=JobTriggerModelType.pull_request))
    flexmock(CoprBuildJobHelper).should_receive("is_reporting_allowed").and_return(True)
    flexmock(packit_service.worker.build.copr_build).should_receive(
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
    flexmock(CoprBuildModel).should_receive("create").and_return(flexmock(id=1))

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
        metadata=JobMetadataConfig(
            _targets=DEFAULT_TARGETS,
            owner="nobody",
            dist_git_branches=["build-branch"],
        ),
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
    flexmock(CoprBuildModel).should_receive("create").and_return(flexmock(id=1))
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
    flexmock(packit_service.worker.build.copr_build).should_receive(
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
    flexmock(CoprBuildModel).should_receive("create").and_return(flexmock(id=1))
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
    flexmock(packit_service.worker.build.copr_build).should_receive(
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
    flexmock(packit_service.worker.build.copr_build).should_receive(
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
            flexmock(status="failure", id=2)
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
    flexmock(CoprBuildModel).should_receive("create").and_return(flexmock(id=1))
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
    flexmock(pr.source_project).should_receive("set_commit_status").and_raise(
        gitlab.GitlabCreateError(response_code=403)
    )
    flexmock(GitlabProject).should_receive("commit_comment").and_return()
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
    flexmock(CoprBuildModel).should_receive("create").and_return(flexmock(id=1))
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
    flexmock(packit_service.worker.build.copr_build).should_receive(
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
        metadata=JobMetadataConfig(owner="nobody"),
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
    flexmock(CoprBuildModel).should_receive("create").and_return(flexmock(id=1))
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
        metadata=JobMetadataConfig(
            owner="nobody", _targets=["bright-future-x86_64", "brightest-future-x86_64"]
        ),
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
        targets_override={"bright-future-x86_64"},
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
    flexmock(CoprBuildModel).should_receive("create").and_return(flexmock(id=1))

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
                projectname="the-example-namespace-the-example-repo-342-stg",
                path=Path("my.srpm"),
                buildopts={
                    "chroots": ["bright-future-x86_64"],
                },
            )
            .and_return(
                flexmock(
                    id=2,
                    projectname="the-example-namespace-the-example-repo-342-stg",
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


def test_run_copr_build_from_source_script(github_pr_event):
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
    flexmock(CoprBuildModel).should_receive("create").and_return(flexmock(id=1)).times(
        4
    )
    flexmock(PullRequestGithubEvent).should_receive("db_trigger").and_return(flexmock())

    # copr build
    flexmock(CoprHelper).should_receive("create_copr_project_if_not_exists").and_return(
        None
    )
    flexmock(helper).should_receive("get_packit_copr_download_urls").and_return([])
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
    assert helper.run_copr_build_from_source_script()["success"]


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
    urls = [
        "https://results/python3-packit-0.38.0-1.2.noarch.rpm",
        "https://results/packit-0.38.0-1.2.noarch.rpm",
    ]
    helper = build_helper(event=github_pr_event)
    helper.service_config.deployment = Deployment.prod

    assert helper.get_packit_copr_download_urls() == urls
