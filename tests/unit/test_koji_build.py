# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
from pathlib import Path
from typing import Union

import pytest
from flexmock import flexmock
from ogr.abstract import GitProject
from packit.api import PackitAPI
from packit.config import (
    CommonPackageConfig,
    JobConfig,
    JobConfigTriggerType,
    JobType,
    PackageConfig,
)
from packit.exceptions import PackitCommandFailedError
from packit.upstream import GitUpstream

from packit_service import sentry_integration
from packit_service.config import ServiceConfig
from packit_service.events import (
    github,
    koji,
)
from packit_service.models import (
    BuildStatus,
    KojiBuildGroupModel,
    KojiBuildTargetModel,
    SRPMBuildModel,
)
from packit_service.service.urls import (
    get_koji_build_info_url,
    get_srpm_build_info_url,
)
from packit_service.worker.helpers.build import koji_build
from packit_service.worker.helpers.build.koji_build import KojiBuildJobHelper
from packit_service.worker.reporting import BaseCommitStatus, StatusReporter


def build_helper(
    event: Union[
        github.pr.Action,
        github.pr.Comment,
        github.push.Commit,
        github.release.Release,
    ],
    _targets=None,
    owner=None,
    scratch=None,
    trigger=None,
    jobs=None,
    db_project_event=None,
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
            type=JobType.upstream_koji_build,
            trigger=trigger or JobConfigTriggerType.pull_request,
            packages={
                "package": CommonPackageConfig(
                    _targets=_targets,
                    owner=owner,
                    scratch=scratch,
                ),
            },
        ),
    )

    pkg_conf = PackageConfig(
        jobs=jobs,
        packages={"package": CommonPackageConfig(downstream_package_name="dummy")},
    )
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
        db_project_event=db_project_event,
        build_targets_override=build_targets_override,
    )
    handler._api = PackitAPI(config=ServiceConfig(), package_config=pkg_conf)
    return handler


def test_koji_build_check_names(
    github_pr_event,
    add_pull_request_event_with_sha_528b80,
):
    _, db_project_event = add_pull_request_event_with_sha_528b80
    helper = build_helper(
        event=github_pr_event,
        _targets=["bright-future"],
        scratch=True,
        db_project_event=db_project_event,
    )
    flexmock(koji_build).should_receive("get_all_koji_targets").and_return(
        ["dark-past", "bright-future"],
    ).once()

    koji_build_url = get_koji_build_info_url(1)
    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=BaseCommitStatus.running,
        description="Building SRPM ...",
        check_name="koji-build:bright-future",
        url="",
        links_to_external_services=None,
        markdown_content=None,
    ).and_return()
    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=BaseCommitStatus.running,
        description="Building RPM ...",
        check_name="koji-build:bright-future",
        url=koji_build_url,
        links_to_external_services=None,
        markdown_content=None,
    ).and_return()

    flexmock(GitProject).should_receive("get_pr").and_return(
        flexmock(source_project=flexmock(), target_branch="main"),
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
        ),
    )
    flexmock(KojiBuildGroupModel).should_receive("create").and_return(flexmock(id=1))
    flexmock(KojiBuildTargetModel).should_receive("create").and_return(
        flexmock(id=1).should_receive("set_task_id").mock().should_receive("set_web_url").mock(),
    )
    flexmock(PackitAPI).should_receive("create_srpm").and_return("my.srpm")

    # koji build
    flexmock(GitUpstream).should_receive("koji_build").and_return(
        "Uploading srpm: /python-ogr-0.11.1"
        ".dev21+gf2dec9b-1.20200407142424746041.21.gf2dec9b.fc31.src.rpm\n"
        "[====================================] 100% 00:00:11   1.67 MiB 148.10 KiB/sec\n"
        "Created task: 43429338\n"
        "Task info: https://koji.fedoraproject.org/koji/taskinfo?taskID=43429338\n",
    )

    flexmock(PackitAPI).should_receive("init_kerberos_ticket").once()

    assert helper.run_koji_build()["success"]


def test_koji_build_failed_kerberos(
    github_pr_event,
    add_pull_request_event_with_sha_528b80,
):
    _, db_project_event = add_pull_request_event_with_sha_528b80
    helper = build_helper(
        event=github_pr_event,
        _targets=["bright-future"],
        scratch=True,
        db_project_event=db_project_event,
    )
    flexmock(koji_build).should_receive("get_all_koji_targets").and_return(
        ["dark-past", "bright-future"],
    ).never()

    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=BaseCommitStatus.running,
        description="Building SRPM ...",
        check_name="koji-build:bright-future",
        url="",
        links_to_external_services=None,
        markdown_content=None,
    ).and_return()
    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=BaseCommitStatus.error,
        description="Kerberos authentication error: the bad authentication error",
        check_name="koji-build:bright-future",
        url=get_srpm_build_info_url(1),
        links_to_external_services=None,
        markdown_content=None,
    ).and_return()

    flexmock(GitProject).should_receive("get_pr").and_return(
        flexmock(source_project=flexmock(), target_branch="main"),
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
        ),
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
        response["details"]["msg"] == "Kerberos authentication error: the bad authentication error"
    )


def test_koji_build_target_not_supported(
    github_pr_event,
    add_pull_request_event_with_sha_528b80,
):
    _, db_project_event = add_pull_request_event_with_sha_528b80
    helper = build_helper(
        event=github_pr_event,
        _targets=["nonexisting-target"],
        scratch=True,
        db_project_event=db_project_event,
    )
    flexmock(koji_build).should_receive("get_all_koji_targets").and_return(
        ["dark-past", "bright-future"],
    ).once()

    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=BaseCommitStatus.running,
        description="Building SRPM ...",
        check_name="koji-build:nonexisting-target",
        url="",
        links_to_external_services=None,
        markdown_content=None,
    ).and_return()
    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=BaseCommitStatus.error,
        description="Target not supported: nonexisting-target",
        check_name="koji-build:nonexisting-target",
        url=get_srpm_build_info_url(1),
        links_to_external_services=None,
        markdown_content=None,
    ).and_return()

    flexmock(GitProject).should_receive("get_pr").and_return(
        flexmock(source_project=flexmock(), target_branch="main"),
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
        ),
    )
    flexmock(KojiBuildGroupModel).should_receive("create").and_return(flexmock(id=1))
    flexmock(KojiBuildTargetModel).should_receive("create").and_return(flexmock(id=1))
    flexmock(PackitAPI).should_receive("create_srpm").and_return("my.srpm")

    response = helper.run_koji_build()
    assert not response["success"]
    assert (
        response["details"]["errors"]["nonexisting-target"]
        == "Target not supported: nonexisting-target"
    )


def test_koji_build_with_multiple_targets(
    github_pr_event,
    add_pull_request_event_with_sha_528b80,
):
    _, db_project_event = add_pull_request_event_with_sha_528b80
    helper = build_helper(
        event=github_pr_event,
        _targets=["bright-future", "dark-past"],
        scratch=True,
        db_project_event=db_project_event,
    )
    flexmock(koji_build).should_receive("get_all_koji_targets").and_return(
        ["dark-past", "bright-future"],
    ).once()

    # 2x SRPM + 2x RPM
    flexmock(StatusReporter).should_receive("set_status").and_return().times(4)

    flexmock(GitProject).should_receive("get_pr").and_return(
        flexmock(source_project=flexmock(), target_branch="main"),
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
        ),
    )
    flexmock(KojiBuildGroupModel).should_receive("create").and_return(flexmock(id=1))
    flexmock(KojiBuildTargetModel).should_receive("create").and_return(
        flexmock(id=1).should_receive("set_task_id").mock().should_receive("set_web_url").mock(),
    ).and_return(
        flexmock(id=2).should_receive("set_task_id").mock().should_receive("set_web_url").mock(),
    )
    flexmock(PackitAPI).should_receive("create_srpm").and_return("my.srpm")

    # koji build
    flexmock(GitUpstream).should_receive("koji_build").and_return(
        "Uploading srpm: /python-ogr-0.11.1"
        ".dev21+gf2dec9b-1.20200407142424746041.21.gf2dec9b.fc31.src.rpm\n"
        "[====================================] 100% 00:00:11   1.67 MiB 148.10 KiB/sec\n"
        "Created task: 43429338\n"
        "Task info: https://koji.fedoraproject.org/koji/taskinfo?taskID=43429338\n",
    ).and_return(
        "Uploading srpm: /python-ogr-0.11.1"
        ".dev21+gf2dec9b-1.20200407142424746041.21.gf2dec9b.fc31.src.rpm\n"
        "[====================================] 100% 00:00:11   1.67 MiB 148.10 KiB/sec\n"
        "Created task: 43429339\n"
        "Task info: https://koji.fedoraproject.org/koji/taskinfo?taskID=43429339\n",
    )

    assert helper.run_koji_build()["success"]


def test_koji_build_failed(github_pr_event, add_pull_request_event_with_sha_528b80):
    _, db_project_event = add_pull_request_event_with_sha_528b80
    helper = build_helper(
        event=github_pr_event,
        _targets=["bright-future"],
        scratch=True,
        db_project_event=db_project_event,
    )
    flexmock(koji_build).should_receive("get_all_koji_targets").and_return(
        ["dark-past", "bright-future"],
    ).once()

    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=BaseCommitStatus.running,
        description="Building SRPM ...",
        check_name="koji-build:bright-future",
        url="",
        links_to_external_services=None,
        markdown_content=None,
    ).and_return()

    srpm_build_url = get_srpm_build_info_url(2)
    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=BaseCommitStatus.error,
        description="Submit of the build failed: some error",
        check_name="koji-build:bright-future",
        url=srpm_build_url,
        links_to_external_services=None,
        markdown_content=None,
    ).and_return()

    flexmock(GitProject).should_receive("get_pr").and_return(
        flexmock(source_project=flexmock(), target_branch="main"),
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
        ),
    )
    flexmock(KojiBuildGroupModel).should_receive("create").and_return(flexmock(id=1))
    flexmock(KojiBuildTargetModel).should_receive("create").and_return(
        flexmock(id=1).should_receive("set_status").with_args("error").mock(),
    )
    flexmock(PackitAPI).should_receive("create_srpm").and_return("my.srpm")

    # koji build
    flexmock(sentry_integration).should_receive("send_to_sentry").and_return().once()
    flexmock(GitUpstream).should_receive("koji_build").and_raise(
        Exception,
        "some error",
    )

    result = helper.run_koji_build()
    assert not result["success"]
    assert result["details"]["errors"]
    assert result["details"]["errors"]["bright-future"] == "some error"


def test_koji_build_failed_srpm(
    github_pr_event,
    add_pull_request_event_with_sha_528b80,
):
    _, db_project_event = add_pull_request_event_with_sha_528b80
    helper = build_helper(
        event=github_pr_event,
        _targets=["bright-future"],
        scratch=True,
        db_project_event=db_project_event,
    )
    srpm_build_url = get_srpm_build_info_url(2)
    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=BaseCommitStatus.running,
        description="Building SRPM ...",
        check_name="koji-build:bright-future",
        url="",
        links_to_external_services=None,
        markdown_content=None,
    ).and_return()
    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=BaseCommitStatus.failure,
        description="SRPM build failed, check the logs for details.",
        check_name="koji-build:bright-future",
        url=srpm_build_url,
        links_to_external_services=None,
        markdown_content=None,
    ).and_return()

    flexmock(GitProject).should_receive("get_pr").and_return(
        flexmock(source_project=flexmock(), target_branch="main"),
    )
    flexmock(GitProject).should_receive("set_commit_status").and_return().never()
    flexmock(PackitAPI).should_receive("create_srpm").and_raise(Exception, "some error")
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
        ),
    )
    flexmock(KojiBuildTargetModel).should_receive("create").never()
    flexmock(sentry_integration).should_receive("send_to_sentry").and_return().once()

    result = helper.run_koji_build()
    assert not result["success"]
    assert "SRPM build failed" in result["details"]["msg"]


def test_koji_build_targets_override(
    github_pr_event,
    add_pull_request_event_with_sha_528b80,
):
    _, db_project_event = add_pull_request_event_with_sha_528b80
    helper = build_helper(
        event=github_pr_event,
        _targets=["bright-future", "dark-past"],
        scratch=True,
        db_project_event=db_project_event,
        build_targets_override={("bright-future", None)},
    )
    flexmock(koji_build).should_receive("get_all_koji_targets").and_return(
        ["dark-past", "bright-future"],
    ).once()

    # SRPM + RPM
    flexmock(StatusReporter).should_receive("set_status").and_return().times(2)

    flexmock(GitProject).should_receive("get_pr").and_return(
        flexmock(source_project=flexmock(), target_branch="main"),
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
        ),
    )
    flexmock(KojiBuildGroupModel).should_receive("create").and_return(flexmock(id=1))
    flexmock(KojiBuildTargetModel).should_receive("create").and_return(
        flexmock(id=1).should_receive("set_task_id").mock().should_receive("set_web_url").mock(),
    ).and_return(
        flexmock(id=2).should_receive("set_task_id").mock().should_receive("set_web_url").mock(),
    )
    flexmock(PackitAPI).should_receive("create_srpm").and_return("my.srpm")

    # koji build
    flexmock(GitUpstream).should_receive("koji_build").once().with_args(
        scratch=True,
        nowait=True,
        koji_target="bright-future",
        srpm_path=Path("my.srpm"),
    ).and_return(
        "Uploading srpm: /python-ogr-0.11.1"
        ".dev21+gf2dec9b-1.20200407142424746041.21.gf2dec9b.fc31.src.rpm\n"
        "[====================================] 100% 00:00:11   1.67 MiB 148.10 KiB/sec\n"
        "Created task: 43429338\n"
        "Task info: https://koji.fedoraproject.org/koji/taskinfo?taskID=43429338\n",
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
    assert koji.result.Task.get_koji_build_logs_url(rpm_build_task_id=id_) == result


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
    assert koji.result.Task.get_koji_rpm_build_web_url(rpm_build_task_id=id_) == result
