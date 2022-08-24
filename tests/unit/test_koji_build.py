# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
from pathlib import Path
from typing import Union

import pytest
from flexmock import flexmock

from ogr.abstract import GitProject
from packit.api import PackitAPI
from packit.config import (
    PackageConfig,
    JobConfig,
    JobType,
    JobConfigTriggerType,
)
from packit.exceptions import PackitCommandFailedError
from packit.upstream import Upstream
from packit_service import sentry_integration
from packit_service.config import ServiceConfig
from packit_service.models import (
    SRPMBuildModel,
    KojiBuildTargetModel,
    KojiBuildGroupModel,
    JobTriggerModel,
    JobTriggerModelType,
)
from packit_service.service.db_triggers import AddPullRequestDbTrigger
from packit_service.worker.events import (
    PullRequestGithubEvent,
    PullRequestCommentGithubEvent,
    PushGitHubEvent,
    ReleaseEvent,
    KojiTaskEvent,
)
from packit_service.service.urls import (
    get_koji_build_info_url,
    get_srpm_build_info_url,
)
from packit_service.worker.helpers.build import koji_build
from packit_service.worker.helpers.build.koji_build import KojiBuildJobHelper
from packit_service.worker.reporting import StatusReporter, BaseCommitStatus


def build_helper(
    event: Union[
        PullRequestGithubEvent,
        PullRequestCommentGithubEvent,
        PushGitHubEvent,
        ReleaseEvent,
    ],
    _targets=None,
    owner=None,
    scratch=None,
    trigger=None,
    jobs=None,
    db_trigger=None,
    build_targets_override=None,
):
    if not _targets:
        _targets = (
            [
                "fedora-29-x86_64",
                "fedora-30-x86_64",
                "fedora-31-x86_64",
                "fedora-rawhide-x86_64",
            ],
        )
    if not owner:
        owner = ("nobody",)
    jobs = jobs or []
    jobs.append(
        JobConfig(
            type=JobType.production_build,
            trigger=trigger or JobConfigTriggerType.pull_request,
            _targets=_targets,
            owner=owner,
            scratch=scratch,
        )
    )

    pkg_conf = PackageConfig(jobs=jobs, downstream_package_name="dummy")
    handler = KojiBuildJobHelper(
        service_config=ServiceConfig(),
        package_config=pkg_conf,
        job_config=pkg_conf.jobs[0],
        project=GitProject(repo=flexmock(), service=flexmock(), namespace=flexmock()),
        metadata=flexmock(
            pr_id=event.pr_id,
            git_ref=event.git_ref,
            commit_sha=event.commit_sha,
            identifier=event.identifier,
        ),
        db_trigger=db_trigger,
        build_targets_override=build_targets_override,
    )
    handler._api = PackitAPI(config=ServiceConfig(), package_config=pkg_conf)
    return handler


def test_koji_build_check_names(github_pr_event):
    trigger = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        id=123,
        job_trigger_model_type=JobTriggerModelType.pull_request,
    )
    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=JobTriggerModelType.pull_request, trigger_id=123
    ).and_return(flexmock(id=2, type=JobConfigTriggerType.pull_request))
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    helper = build_helper(
        event=github_pr_event,
        _targets=["bright-future"],
        scratch=True,
        db_trigger=trigger,
    )
    flexmock(koji_build).should_receive("get_all_koji_targets").and_return(
        ["dark-past", "bright-future"]
    ).once()

    koji_build_url = get_koji_build_info_url(1)
    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=BaseCommitStatus.running,
        description="Building SRPM ...",
        check_name="production-build:bright-future",
        url="",
        links_to_external_services=None,
        markdown_content=None,
    ).and_return()
    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=BaseCommitStatus.running,
        description="Building RPM ...",
        check_name="production-build:bright-future",
        url=koji_build_url,
        links_to_external_services=None,
        markdown_content=None,
    ).and_return()

    flexmock(GitProject).should_receive("get_pr").and_return(
        flexmock(source_project=flexmock())
    )
    flexmock(GitProject).should_receive("set_commit_status").and_return().never()
    flexmock(SRPMBuildModel).should_receive("create_with_new_run").and_return(
        (
            flexmock(status="success", id=1)
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
    flexmock(KojiBuildGroupModel).should_receive("create").and_return(flexmock(id=1))
    flexmock(KojiBuildTargetModel).should_receive("create").and_return(flexmock(id=1))
    flexmock(PackitAPI).should_receive("create_srpm").and_return("my.srpm")

    # koji build
    flexmock(Upstream).should_receive("koji_build").and_return(
        "Uploading srpm: /python-ogr-0.11.1"
        ".dev21+gf2dec9b-1.20200407142424746041.21.gf2dec9b.fc31.src.rpm\n"
        "[====================================] 100% 00:00:11   1.67 MiB 148.10 KiB/sec\n"
        "Created task: 43429338\n"
        "Task info: https://koji.fedoraproject.org/koji/taskinfo?taskID=43429338\n"
    )

    flexmock(PackitAPI).should_receive("init_kerberos_ticket").once()

    assert helper.run_koji_build()["success"]


def test_koji_build_failed_kerberos(github_pr_event):
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
        _targets=["bright-future"],
        scratch=True,
        db_trigger=trigger,
    )
    flexmock(koji_build).should_receive("get_all_koji_targets").and_return(
        ["dark-past", "bright-future"]
    ).never()

    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=BaseCommitStatus.running,
        description="Building SRPM ...",
        check_name="production-build:bright-future",
        url="",
        links_to_external_services=None,
        markdown_content=None,
    ).and_return()
    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=BaseCommitStatus.error,
        description="Kerberos authentication error: the bad authentication error",
        check_name="production-build:bright-future",
        url=get_srpm_build_info_url(1),
        links_to_external_services=None,
        markdown_content=None,
    ).and_return()

    flexmock(GitProject).should_receive("get_pr").and_return(
        flexmock(source_project=flexmock())
    )
    flexmock(GitProject).should_receive("set_commit_status").and_return().never()
    flexmock(SRPMBuildModel).should_receive("create_with_new_run").and_return(
        (
            flexmock(status="success", id=1)
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
    flexmock(KojiBuildTargetModel).should_receive("create").and_return(flexmock(id=1))
    flexmock(PackitAPI).should_receive("create_srpm").and_return("my.srpm")

    flexmock(PackitAPI).should_receive("init_kerberos_ticket").and_raise(
        PackitCommandFailedError,
        "Command failed",
        stdout_output="",
        stderr_output="the bad authentication error",
    )

    response = helper.run_koji_build()
    assert not response["success"]
    assert (
        "Kerberos authentication error: the bad authentication error"
        == response["details"]["msg"]
    )


def test_koji_build_target_not_supported(github_pr_event):
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
        _targets=["nonexisting-target"],
        scratch=True,
        db_trigger=trigger,
    )
    flexmock(koji_build).should_receive("get_all_koji_targets").and_return(
        ["dark-past", "bright-future"]
    ).once()

    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=BaseCommitStatus.running,
        description="Building SRPM ...",
        check_name="production-build:nonexisting-target",
        url="",
        links_to_external_services=None,
        markdown_content=None,
    ).and_return()
    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=BaseCommitStatus.error,
        description="Target not supported: nonexisting-target",
        check_name="production-build:nonexisting-target",
        url=get_srpm_build_info_url(1),
        links_to_external_services=None,
        markdown_content=None,
    ).and_return()

    flexmock(GitProject).should_receive("get_pr").and_return(
        flexmock(source_project=flexmock())
    )
    flexmock(GitProject).should_receive("set_commit_status").and_return().never()
    flexmock(SRPMBuildModel).should_receive("create_with_new_run").and_return(
        (
            flexmock(status="success", id=1)
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
    flexmock(KojiBuildTargetModel).should_receive("create").and_return(flexmock(id=1))
    flexmock(KojiBuildGroupModel).should_receive("create").and_return(flexmock(id=1))
    flexmock(PackitAPI).should_receive("create_srpm").and_return("my.srpm")

    response = helper.run_koji_build()
    assert not response["success"]
    assert (
        "Target not supported: nonexisting-target"
        == response["details"]["errors"]["nonexisting-target"]
    )


def test_koji_build_with_multiple_targets(github_pr_event):
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
        _targets=["bright-future", "dark-past"],
        scratch=True,
        db_trigger=trigger,
    )
    flexmock(koji_build).should_receive("get_all_koji_targets").and_return(
        ["dark-past", "bright-future"]
    ).once()

    # 2x SRPM + 2x RPM
    flexmock(StatusReporter).should_receive("set_status").and_return().times(4)

    flexmock(GitProject).should_receive("get_pr").and_return(
        flexmock(source_project=flexmock())
    )
    flexmock(GitProject).should_receive("set_commit_status").and_return().never()
    flexmock(SRPMBuildModel).should_receive("create_with_new_run").and_return(
        (
            flexmock(status="success", id=1)
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
    flexmock(KojiBuildGroupModel).should_receive("create").and_return(flexmock(id=1))
    flexmock(KojiBuildTargetModel).should_receive("create").and_return(
        flexmock(id=1)
    ).and_return(flexmock(id=2))
    flexmock(PackitAPI).should_receive("create_srpm").and_return("my.srpm")

    # koji build
    flexmock(Upstream).should_receive("koji_build").and_return(
        "Uploading srpm: /python-ogr-0.11.1"
        ".dev21+gf2dec9b-1.20200407142424746041.21.gf2dec9b.fc31.src.rpm\n"
        "[====================================] 100% 00:00:11   1.67 MiB 148.10 KiB/sec\n"
        "Created task: 43429338\n"
        "Task info: https://koji.fedoraproject.org/koji/taskinfo?taskID=43429338\n"
    ).and_return(
        "Uploading srpm: /python-ogr-0.11.1"
        ".dev21+gf2dec9b-1.20200407142424746041.21.gf2dec9b.fc31.src.rpm\n"
        "[====================================] 100% 00:00:11   1.67 MiB 148.10 KiB/sec\n"
        "Created task: 43429339\n"
        "Task info: https://koji.fedoraproject.org/koji/taskinfo?taskID=43429339\n"
    )

    assert helper.run_koji_build()["success"]


def test_koji_build_failed(github_pr_event):
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
        _targets=["bright-future"],
        scratch=True,
        db_trigger=trigger,
    )
    flexmock(koji_build).should_receive("get_all_koji_targets").and_return(
        ["dark-past", "bright-future"]
    ).once()

    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=BaseCommitStatus.running,
        description="Building SRPM ...",
        check_name="production-build:bright-future",
        url="",
        links_to_external_services=None,
        markdown_content=None,
    ).and_return()

    srpm_build_url = get_srpm_build_info_url(2)
    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=BaseCommitStatus.error,
        description="Submit of the build failed: some error",
        check_name="production-build:bright-future",
        url=srpm_build_url,
        links_to_external_services=None,
        markdown_content=None,
    ).and_return()

    flexmock(GitProject).should_receive("get_pr").and_return(
        flexmock(source_project=flexmock())
    )
    flexmock(GitProject).should_receive("set_commit_status").and_return().never()
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
    flexmock(KojiBuildGroupModel).should_receive("create").and_return(flexmock(id=1))
    flexmock(KojiBuildTargetModel).should_receive("create").and_return(flexmock(id=1))
    flexmock(PackitAPI).should_receive("create_srpm").and_return("my.srpm")

    # koji build
    flexmock(sentry_integration).should_receive("send_to_sentry").and_return().once()
    flexmock(Upstream).should_receive("koji_build").and_raise(Exception, "some error")

    result = helper.run_koji_build()
    assert not result["success"]
    assert result["details"]["errors"]
    assert result["details"]["errors"]["bright-future"] == "some error"


def test_koji_build_failed_srpm(github_pr_event):
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
        _targets=["bright-future"],
        scratch=True,
        db_trigger=trigger,
    )
    srpm_build_url = get_srpm_build_info_url(2)
    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=BaseCommitStatus.running,
        description="Building SRPM ...",
        check_name="production-build:bright-future",
        url="",
        links_to_external_services=None,
        markdown_content=None,
    ).and_return()
    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=BaseCommitStatus.failure,
        description="SRPM build failed, check the logs for details.",
        check_name="production-build:bright-future",
        url=srpm_build_url,
        links_to_external_services=None,
        markdown_content=None,
    ).and_return()

    flexmock(GitProject).should_receive("get_pr").and_return(
        flexmock(source_project=flexmock())
    )
    flexmock(GitProject).should_receive("set_commit_status").and_return().never()
    flexmock(PackitAPI).should_receive("create_srpm").and_raise(Exception, "some error")
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
    flexmock(KojiBuildTargetModel).should_receive("create").never()
    flexmock(sentry_integration).should_receive("send_to_sentry").and_return().once()

    result = helper.run_koji_build()
    assert not result["success"]
    assert "SRPM build failed" in result["details"]["msg"]


def test_koji_build_targets_override(github_pr_event):
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
        _targets=["bright-future", "dark-past"],
        scratch=True,
        db_trigger=trigger,
        build_targets_override={"bright-future"},
    )
    flexmock(koji_build).should_receive("get_all_koji_targets").and_return(
        ["dark-past", "bright-future"]
    ).once()

    # SRPM + RPM
    flexmock(StatusReporter).should_receive("set_status").and_return().times(2)

    flexmock(GitProject).should_receive("get_pr").and_return(
        flexmock(source_project=flexmock())
    )
    flexmock(GitProject).should_receive("set_commit_status").and_return().never()
    flexmock(SRPMBuildModel).should_receive("create_with_new_run").and_return(
        (
            flexmock(status="success", id=1)
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
    flexmock(KojiBuildGroupModel).should_receive("create").and_return(flexmock(id=1))
    flexmock(KojiBuildTargetModel).should_receive("create").and_return(
        flexmock(id=1)
    ).and_return(flexmock(id=2))
    flexmock(PackitAPI).should_receive("create_srpm").and_return("my.srpm")

    # koji build
    flexmock(Upstream).should_receive("koji_build").once().with_args(
        scratch=True,
        nowait=True,
        koji_target="bright-future",
        srpm_path=Path("my.srpm"),
    ).and_return(
        "Uploading srpm: /python-ogr-0.11.1"
        ".dev21+gf2dec9b-1.20200407142424746041.21.gf2dec9b.fc31.src.rpm\n"
        "[====================================] 100% 00:00:11   1.67 MiB 148.10 KiB/sec\n"
        "Created task: 43429338\n"
        "Task info: https://koji.fedoraproject.org/koji/taskinfo?taskID=43429338\n"
    )

    assert helper.run_koji_build()["success"]


@pytest.mark.parametrize(
    "id_,result",
    [
        (
            45270227,
            "https://kojipkgs.fedoraproject.org//work/tasks/227/45270227/build.log",
        ),
        (
            45452270,
            "https://kojipkgs.fedoraproject.org//work/tasks/2270/45452270/build.log",
        ),
    ],
)
def test_get_koji_build_logs_url(id_, result):
    assert KojiTaskEvent.get_koji_build_logs_url(rpm_build_task_id=id_) == result


@pytest.mark.parametrize(
    "id_,result",
    [
        (
            45270227,
            "https://koji.fedoraproject.org/koji/taskinfo?taskID=45270227",
        ),
        (
            45452270,
            "https://koji.fedoraproject.org/koji/taskinfo?taskID=45452270",
        ),
    ],
)
def test_get_koji_rpm_build_web_url(id_, result):
    assert KojiTaskEvent.get_koji_rpm_build_web_url(rpm_build_task_id=id_) == result
