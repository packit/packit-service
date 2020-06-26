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


from typing import Union

import pytest
from flexmock import flexmock

from ogr.abstract import GitProject, CommitStatus
from packit.api import PackitAPI
from packit.config import (
    PackageConfig,
    JobConfig,
    JobType,
    JobConfigTriggerType,
)
from packit.config.job_config import JobMetadataConfig
from packit.exceptions import PackitCommandFailedError
from packit.upstream import Upstream
from packit_service import sentry_integration
from packit_service.config import ServiceConfig
from packit_service.models import SRPMBuildModel, KojiBuildModel
from packit_service.service.db_triggers import AddPullRequestDbTrigger
from packit_service.service.events import (
    PullRequestGithubEvent,
    PullRequestCommentGithubEvent,
    PushGitHubEvent,
    ReleaseEvent,
    KojiBuildEvent,
)
from packit_service.service.urls import (
    get_koji_build_info_url_from_flask,
    get_srpm_log_url_from_flask,
)
from packit_service.worker.build import koji_build
from packit_service.worker.build.koji_build import KojiBuildJobHelper
from packit_service.worker.reporting import StatusReporter


def build_helper(
    event: Union[
        PullRequestGithubEvent,
        PullRequestCommentGithubEvent,
        PushGitHubEvent,
        ReleaseEvent,
    ],
    metadata=None,
    trigger=None,
    jobs=None,
    db_trigger=None,
):
    if not metadata:
        metadata = JobMetadataConfig(
            targets=[
                "fedora-29-x86_64",
                "fedora-30-x86_64",
                "fedora-31-x86_64",
                "fedora-rawhide-x86_64",
            ],
            owner="nobody",
        )
    jobs = jobs or []
    jobs.append(
        JobConfig(
            type=JobType.production_build,
            trigger=trigger or JobConfigTriggerType.pull_request,
            metadata=metadata,
        )
    )

    pkg_conf = PackageConfig(jobs=jobs, downstream_package_name="dummy")
    handler = KojiBuildJobHelper(
        config=ServiceConfig(),
        package_config=pkg_conf,
        job_config=pkg_conf.jobs[0],
        project=GitProject(repo=flexmock(), service=flexmock(), namespace=flexmock()),
        metadata=flexmock(
            trigger=event.trigger,
            pr_id=event.pr_id,
            git_ref=event.git_ref,
            commit_sha=event.commit_sha,
            identifier=event.identifier,
        ),
        db_trigger=db_trigger,
    )
    handler._api = PackitAPI(config=ServiceConfig(), package_config=pkg_conf)
    return handler


def test_koji_build_check_names(github_pr_event):
    trigger = flexmock(job_config_trigger_type=JobConfigTriggerType.release, id=123)
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    helper = build_helper(
        event=github_pr_event,
        metadata=JobMetadataConfig(targets=["bright-future"]),
        db_trigger=trigger,
    )
    flexmock(koji_build).should_receive("get_all_koji_targets").and_return(
        ["dark-past", "bright-future"]
    ).once()

    koji_build_url = get_koji_build_info_url_from_flask(1)
    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=CommitStatus.pending,
        description="Building SRPM ...",
        check_name="packit-stg/production-build-bright-future",
        url="",
    ).and_return()
    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=CommitStatus.pending,
        description="Building RPM ...",
        check_name="packit-stg/production-build-bright-future",
        url=koji_build_url,
    ).and_return()

    flexmock(GitProject).should_receive("set_commit_status").and_return().never()
    flexmock(SRPMBuildModel).should_receive("create").and_return(
        SRPMBuildModel(id=1, success=True)
    )
    flexmock(KojiBuildModel).should_receive("get_or_create").and_return(
        KojiBuildModel(id=1)
    )
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
    trigger = flexmock(job_config_trigger_type=JobConfigTriggerType.release, id=123)
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    helper = build_helper(
        event=github_pr_event,
        metadata=JobMetadataConfig(targets=["bright-future"]),
        db_trigger=trigger,
    )
    flexmock(koji_build).should_receive("get_all_koji_targets").and_return(
        ["dark-past", "bright-future"]
    ).never()

    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=CommitStatus.pending,
        description="Building SRPM ...",
        check_name="packit-stg/production-build-bright-future",
        url="",
    ).and_return()
    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=CommitStatus.error,
        description="Kerberos authentication error: the bad authentication error",
        check_name="packit-stg/production-build-bright-future",
        url=get_srpm_log_url_from_flask(1),
    ).and_return()

    flexmock(GitProject).should_receive("set_commit_status").and_return().never()
    flexmock(SRPMBuildModel).should_receive("create").and_return(
        SRPMBuildModel(id=1, success=True)
    )
    flexmock(KojiBuildModel).should_receive("get_or_create").and_return(
        KojiBuildModel(id=1)
    )
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
    trigger = flexmock(job_config_trigger_type=JobConfigTriggerType.release, id=123)
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    helper = build_helper(
        event=github_pr_event,
        metadata=JobMetadataConfig(targets=["nonexisting-target"]),
        db_trigger=trigger,
    )
    flexmock(koji_build).should_receive("get_all_koji_targets").and_return(
        ["dark-past", "bright-future"]
    ).once()

    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=CommitStatus.pending,
        description="Building SRPM ...",
        check_name="packit-stg/production-build-nonexisting-target",
        url="",
    ).and_return()
    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=CommitStatus.error,
        description="Target not supported: nonexisting-target",
        check_name="packit-stg/production-build-nonexisting-target",
        url=get_srpm_log_url_from_flask(1),
    ).and_return()

    flexmock(GitProject).should_receive("set_commit_status").and_return().never()
    flexmock(SRPMBuildModel).should_receive("create").and_return(
        SRPMBuildModel(id=1, success=True)
    )
    flexmock(KojiBuildModel).should_receive("get_or_create").and_return(
        KojiBuildModel(id=1)
    )
    flexmock(PackitAPI).should_receive("create_srpm").and_return("my.srpm")

    response = helper.run_koji_build()
    assert not response["success"]
    assert (
        "Target not supported: nonexisting-target"
        == response["details"]["errors"]["nonexisting-target"]
    )


def test_koji_build_with_multiple_targets(github_pr_event):
    trigger = flexmock(job_config_trigger_type=JobConfigTriggerType.release, id=123)
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    helper = build_helper(
        event=github_pr_event,
        metadata=JobMetadataConfig(targets=["bright-future", "dark-past"]),
        db_trigger=trigger,
    )
    flexmock(koji_build).should_receive("get_all_koji_targets").and_return(
        ["dark-past", "bright-future"]
    ).once()

    # 2x SRPM + 2x RPM
    flexmock(StatusReporter).should_receive("set_status").and_return().times(4)

    flexmock(GitProject).should_receive("set_commit_status").and_return().never()
    flexmock(SRPMBuildModel).should_receive("create").and_return(
        SRPMBuildModel(id=1, success=True)
    )
    flexmock(KojiBuildModel).should_receive("get_or_create").and_return(
        KojiBuildModel(id=1)
    ).and_return(KojiBuildModel(id=2))
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
    trigger = flexmock(job_config_trigger_type=JobConfigTriggerType.release, id=123)
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    helper = build_helper(
        event=github_pr_event,
        metadata=JobMetadataConfig(targets=["bright-future"]),
        db_trigger=trigger,
    )
    flexmock(koji_build).should_receive("get_all_koji_targets").and_return(
        ["dark-past", "bright-future"]
    ).once()

    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=CommitStatus.pending,
        description="Building SRPM ...",
        check_name="packit-stg/production-build-bright-future",
        url="",
    ).and_return()

    srpm_build_url = get_srpm_log_url_from_flask(2)
    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=CommitStatus.error,
        description="Submit of the build failed: some error",
        check_name="packit-stg/production-build-bright-future",
        url=srpm_build_url,
    ).and_return()

    flexmock(GitProject).should_receive("set_commit_status").and_return().never()
    flexmock(SRPMBuildModel).should_receive("create").and_return(
        SRPMBuildModel(id=2, success=True)
    )
    flexmock(KojiBuildModel).should_receive("get_or_create").and_return(
        KojiBuildModel(id=1)
    )
    flexmock(PackitAPI).should_receive("create_srpm").and_return("my.srpm")

    # koji build
    flexmock(sentry_integration).should_receive("send_to_sentry").and_return().once()
    flexmock(Upstream).should_receive("koji_build").and_raise(Exception, "some error")

    result = helper.run_koji_build()
    assert not result["success"]
    assert result["details"]["errors"]
    assert result["details"]["errors"]["bright-future"] == "some error"


def test_koji_build_failed_srpm(github_pr_event):
    trigger = flexmock(job_config_trigger_type=JobConfigTriggerType.release, id=123)
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    helper = build_helper(
        event=github_pr_event,
        metadata=JobMetadataConfig(targets=["bright-future"]),
        db_trigger=trigger,
    )
    srpm_build_url = get_srpm_log_url_from_flask(2)
    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=CommitStatus.pending,
        description="Building SRPM ...",
        check_name="packit-stg/production-build-bright-future",
        url="",
    ).and_return()
    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=CommitStatus.failure,
        description="SRPM build failed, check the logs for details.",
        check_name="packit-stg/production-build-bright-future",
        url=srpm_build_url,
    ).and_return()

    flexmock(GitProject).should_receive("set_commit_status").and_return().never()
    flexmock(PackitAPI).should_receive("create_srpm").and_raise(Exception, "some error")
    flexmock(SRPMBuildModel).should_receive("create").and_return(
        SRPMBuildModel(id=2, success=False)
    )
    flexmock(KojiBuildModel).should_receive("get_or_create").never()
    flexmock(sentry_integration).should_receive("send_to_sentry").and_return().once()

    result = helper.run_koji_build()
    assert not result["success"]
    assert "SRPM build failed" in result["details"]["msg"]


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
    event = KojiBuildEvent(build_id=flexmock(), state=flexmock(), rpm_build_task_id=id_)
    assert event.get_koji_build_logs_url() == result


@pytest.mark.parametrize(
    "id_,result",
    [
        (45270227, "https://koji.fedoraproject.org/koji/taskinfo?taskID=45270227",),
        (45452270, "https://koji.fedoraproject.org/koji/taskinfo?taskID=45452270",),
    ],
)
def test_get_koji_rpm_build_web_url(id_, result):
    event = KojiBuildEvent(build_id=flexmock(), state=flexmock(), rpm_build_task_id=id_)
    assert event.get_koji_rpm_build_web_url() == result
