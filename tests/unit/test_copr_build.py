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
import json

import pytest
from celery import Celery
from flexmock import flexmock

from ogr.abstract import GitProject, CommitStatus
from packit.api import PackitAPI
from packit.config import PackageConfig, JobConfig, JobType, JobConfigTriggerType
from packit.config.job_config import JobMetadataConfig
from packit.copr_helper import CoprHelper
from packit.exceptions import FailedCreateSRPM, PackitCoprSettingsException
from packit_service import sentry_integration
from packit_service.config import ServiceConfig
from packit_service.models import CoprBuildModel, SRPMBuildModel
from packit_service.service.db_triggers import (
    AddPullRequestDbTrigger,
    AddBranchPushDbTrigger,
    AddReleaseDbTrigger,
)
from packit_service.service.events import (
    PullRequestGithubEvent,
    PushGitHubEvent,
    ReleaseEvent,
    PushGitlabEvent,
    MergeRequestGitlabEvent,
)
from packit_service.worker.build import copr_build
from packit_service.worker.build.copr_build import CoprBuildJobHelper
from packit_service.worker.parser import Parser
from packit_service.worker.reporting import StatusReporter
from tests.spellbook import DATA_DIR


@pytest.fixture(scope="module")
def branch_push_event() -> PushGitHubEvent:
    file_content = (DATA_DIR / "webhooks" / "github" / "push_branch.json").read_text()
    return Parser.parse_push_event(json.loads(file_content))


@pytest.fixture(scope="module")
def branch_push_event_gitlab() -> PushGitlabEvent:
    file_content = (DATA_DIR / "webhooks" / "gitlab" / "push_branch.json").read_text()
    return Parser.parse_gitlab_push_event(json.loads(file_content))


def build_helper(
    event, metadata=None, trigger=None, jobs=None, db_trigger=None, selected_job=None
):
    if jobs and metadata:
        raise Exception("Only one of jobs and metadata can be used.")

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
        project=GitProject(
            repo="the-example-repo",
            service=flexmock(),
            namespace="the-example-namespace",
        ),
        metadata=flexmock(
            trigger=event.trigger,
            pr_id=event.pr_id,
            git_ref=event.git_ref,
            commit_sha=event.commit_sha,
            identifier=event.identifier,
        ),
        db_trigger=db_trigger,
    )
    handler._api = PackitAPI(ServiceConfig(), pkg_conf)
    return handler


def test_copr_build_check_names(github_pr_event):
    trigger = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request, id=123
    )
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    helper = build_helper(
        event=github_pr_event,
        metadata=JobMetadataConfig(targets=["bright-future-x86_64"], owner="packit"),
        db_trigger=trigger,
    )
    # we need to make sure that pr_id is set
    # so we can check it out and add it to spec's release field
    assert helper.metadata.pr_id

    flexmock(copr_build).should_receive(
        "get_copr_build_info_url_from_flask"
    ).and_return("https://test.url")
    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=CommitStatus.pending,
        description="Building SRPM ...",
        check_name="packit-stg/rpm-build-bright-future-x86_64",
        url="",
    ).and_return()
    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=CommitStatus.pending,
        description="Starting RPM build...",
        check_name="packit-stg/rpm-build-bright-future-x86_64",
        url="https://test.url",
    ).and_return()

    flexmock(GitProject).should_receive("set_commit_status").and_return().never()
    flexmock(SRPMBuildModel).should_receive("create").and_return(
        SRPMBuildModel(success=True)
    )
    flexmock(CoprBuildModel).should_receive("get_or_create").and_return(
        CoprBuildModel(id=1)
    )
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

    flexmock(CoprHelper).should_receive("get_copr_client").and_return(
        flexmock(
            config={"copr_url": "https://copr.fedorainfracloud.org/"},
            build_proxy=flexmock()
            .should_receive("create_from_file")
            .and_return(
                flexmock(id=2, projectname="the-project-name", ownername="packit")
            )
            .mock(),
        )
    )

    flexmock(Celery).should_receive("send_task").once()
    assert helper.run_copr_build()["success"]


def test_copr_build_check_names_custom_owner(github_pr_event):
    trigger = flexmock(job_config_trigger_type=JobConfigTriggerType.release, id=123)
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    helper = build_helper(
        event=github_pr_event,
        metadata=JobMetadataConfig(targets=["bright-future-x86_64"], owner="nobody"),
        db_trigger=trigger,
    )
    # we need to make sure that pr_id is set
    # so we can check it out and add it to spec's release field
    assert helper.metadata.pr_id

    flexmock(copr_build).should_receive(
        "get_copr_build_info_url_from_flask"
    ).and_return("https://test.url")
    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=CommitStatus.pending,
        description="Building SRPM ...",
        check_name="packit-stg/rpm-build-bright-future-x86_64",
        url="",
    ).and_return()
    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=CommitStatus.pending,
        description="Starting RPM build...",
        check_name="packit-stg/rpm-build-bright-future-x86_64",
        url="https://test.url",
    ).and_return()

    flexmock(GitProject).should_receive("set_commit_status").and_return().never()
    flexmock(SRPMBuildModel).should_receive("create").and_return(
        SRPMBuildModel(success=True)
    )
    flexmock(CoprBuildModel).should_receive("get_or_create").and_return(
        CoprBuildModel(id=1)
    )
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

    flexmock(CoprHelper).should_receive("get_copr_client").and_return(
        flexmock(
            config={"copr_url": "https://copr.fedorainfracloud.org/"},
            build_proxy=flexmock()
            .should_receive("create_from_file")
            .and_return(
                flexmock(id=2, projectname="the-project-name", ownername="nobody")
            )
            .mock(),
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
            owner="nobody", targets=["bright-future-x86_64", "brightest-future-x86_64"]
        ),
    )
    trigger = flexmock(job_config_trigger_type=JobConfigTriggerType.release, id=123)
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    helper = build_helper(jobs=[test_job], event=github_pr_event, db_trigger=trigger,)
    flexmock(GitProject).should_receive("set_commit_status").and_return().times(4)
    flexmock(GitProject).should_receive("get_pr").and_return(flexmock())
    flexmock(SRPMBuildModel).should_receive("create").and_return(
        SRPMBuildModel(success=True)
    )
    flexmock(CoprBuildModel).should_receive("get_or_create").and_return(
        CoprBuildModel(id=1)
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
                flexmock(id=2, projectname="the-project-name", ownername="the-owner")
            )
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
            targets=[
                "fedora-29-x86_64",
                "fedora-30-x86_64",
                "fedora-31-x86_64",
                "fedora-rawhide-x86_64",
            ],
            owner="nobody",
            dist_git_branches=["build-branch"],
        ),
    )
    trigger = flexmock(job_config_trigger_type=JobConfigTriggerType.release, id=123)
    flexmock(AddBranchPushDbTrigger).should_receive("db_trigger").and_return(trigger)
    helper = build_helper(
        jobs=[branch_build_job], event=branch_push_event, db_trigger=trigger,
    )
    flexmock(GitProject).should_receive("set_commit_status").and_return().times(8)
    flexmock(SRPMBuildModel).should_receive("create").and_return(
        SRPMBuildModel(success=True)
    )
    flexmock(CoprBuildModel).should_receive("get_or_create").and_return(
        CoprBuildModel(id=1)
    )
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
                flexmock(id=2, projectname="the-project-name", ownername="the-owner")
            )
            .mock(),
        )
    )

    flexmock(Celery).should_receive("send_task").once()
    assert helper.run_copr_build()["success"]


def test_copr_build_for_release(release_event):
    # status is set for each build-target (4x):
    #  - Building SRPM ...
    #  - Starting RPM build...
    branch_build_job = JobConfig(
        type=JobType.build,
        trigger=JobConfigTriggerType.release,
        metadata=JobMetadataConfig(
            targets=[
                "fedora-29-x86_64",
                "fedora-30-x86_64",
                "fedora-31-x86_64",
                "fedora-rawhide-x86_64",
            ],
            owner="nobody",
            dist_git_branches=["build-branch"],
        ),
    )
    trigger = flexmock(job_config_trigger_type=JobConfigTriggerType.release, id=123)
    flexmock(AddReleaseDbTrigger).should_receive("db_trigger").and_return(trigger)
    flexmock(release_event.project).should_receive("get_sha_from_tag").and_return(
        "123456"
    )
    helper = build_helper(
        jobs=[branch_build_job], event=release_event, db_trigger=trigger,
    )
    flexmock(ReleaseEvent).should_receive("get_project").and_return(helper.project)
    flexmock(GitProject).should_receive("set_commit_status").and_return().times(8)
    flexmock(SRPMBuildModel).should_receive("create").and_return(
        SRPMBuildModel(success=True)
    )
    flexmock(CoprBuildModel).should_receive("get_or_create").and_return(
        CoprBuildModel(id=1)
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
                flexmock(id=2, projectname="the-project-name", ownername="the-owner")
            )
            .mock(),
        )
    )

    flexmock(Celery).should_receive("send_task").once()
    assert helper.run_copr_build()["success"]


def test_copr_build_success(github_pr_event):
    # status is set for each build-target (4x):
    #  - Building SRPM ...
    #  - Starting RPM build...
    helper = build_helper(event=github_pr_event)
    flexmock(GitProject).should_receive("set_commit_status").and_return().times(8)
    flexmock(GitProject).should_receive("get_pr").and_return(flexmock())
    flexmock(SRPMBuildModel).should_receive("create").and_return(
        SRPMBuildModel(success=True)
    )
    flexmock(CoprBuildModel).should_receive("get_or_create").and_return(
        CoprBuildModel(id=1)
    )
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
                flexmock(id=2, projectname="the-project-name", ownername="the-owner")
            )
            .mock(),
        )
    )

    flexmock(Celery).should_receive("send_task").once()
    assert helper.run_copr_build()["success"]


def test_copr_build_fails_in_packit(github_pr_event):
    # status is set for each build-target (4x):
    #  - Building SRPM ...
    #  - Build failed, check latest comment for details.
    helper = build_helper(event=github_pr_event)
    templ = "packit-stg/rpm-build-fedora-{ver}-x86_64"
    flexmock(copr_build).should_receive("get_srpm_log_url_from_flask").and_return(
        "https://test.url"
    )
    for v in ["29", "30", "31", "rawhide"]:
        flexmock(GitProject).should_receive("set_commit_status").with_args(
            "528b803be6f93e19ca4130bf4976f2800a3004c4",
            CommitStatus.pending,
            "",
            "Building SRPM ...",
            templ.format(ver=v),
            trim=True,
        ).and_return().once()
    for v in ["29", "30", "31", "rawhide"]:
        flexmock(GitProject).should_receive("set_commit_status").with_args(
            "528b803be6f93e19ca4130bf4976f2800a3004c4",
            CommitStatus.failure,
            "https://test.url",
            "SRPM build failed, check the logs for details.",
            templ.format(ver=v),
            trim=True,
        ).and_return().once()
    flexmock(GitProject).should_receive("get_pr").and_return(flexmock())
    flexmock(SRPMBuildModel).should_receive("create").and_return(
        SRPMBuildModel(success=False, id=2)
    )
    flexmock(CoprBuildModel).should_receive("get_or_create").and_return(
        CoprBuildModel(id=1)
    )
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
    helper = build_helper(event=github_pr_event)
    templ = "packit-stg/rpm-build-fedora-{ver}-x86_64"
    flexmock(copr_build).should_receive("get_srpm_log_url_from_flask").and_return(
        "https://test.url"
    )
    for v in ["29", "30", "31", "rawhide"]:
        flexmock(GitProject).should_receive("set_commit_status").with_args(
            "528b803be6f93e19ca4130bf4976f2800a3004c4",
            CommitStatus.pending,
            "",
            "Building SRPM ...",
            templ.format(ver=v),
            trim=True,
        ).and_return().once()
    for v in ["29", "30", "31", "rawhide"]:
        flexmock(GitProject).should_receive("set_commit_status").with_args(
            "528b803be6f93e19ca4130bf4976f2800a3004c4",
            CommitStatus.error,
            "",
            "Submit of the build failed: Copr project update failed.",
            templ.format(ver=v),
            trim=True,
        ).and_return().once()
    flexmock(GitProject).should_receive("get_pr").and_return(flexmock())
    flexmock(SRPMBuildModel).should_receive("create").and_return(
        SRPMBuildModel(success=True, id=2)
    )
    flexmock(CoprBuildModel).should_receive("get_or_create").and_return(
        CoprBuildModel(id=1)
    )

    flexmock(PackitAPI).should_receive("create_srpm").and_return("my.srpm")
    flexmock(GitProject).should_receive("pr_comment").with_args(
        pr_id=342,
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
        "nobody/the-example-namespace-the-example-repo-342-stg Copr project on the "
        "[permissions page](https://copr.fedorainfracloud.org/"
        "coprs/nobody/the-example-namespace-the-example-repo-342-stg/permissions/).\n"
        "- Change the above Copr project settings manually on the "
        "[settings page](https://copr.fedorainfracloud.org/"
        "coprs/nobody/the-example-namespace-the-example-repo-342-stg/edit/) "
        "to match the Packit configuration.\n"
        "- Update the Packit configuration to match the Copr project settings.\n"
        "\n"
        "Please re-trigger the build, once the issue above is fixed.\n",
    ).and_return().once()

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
        "nobody", "the-example-namespace-the-example-repo-342-stg",
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
        event=github_pr_event, metadata=JobMetadataConfig(owner="nobody")
    )
    flexmock(GitProject).should_receive("set_commit_status").and_return().times(4)
    flexmock(GitProject).should_receive("get_pr").and_return(flexmock())
    flexmock(SRPMBuildModel).should_receive("create").and_return(
        SRPMBuildModel(success=True)
    )
    flexmock(CoprBuildModel).should_receive("get_or_create").and_return(
        CoprBuildModel(id=1)
    )
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
                flexmock(id=2, projectname="the-project-name", ownername="the-owner")
            )
            .mock(),
        )
    )

    flexmock(Celery).should_receive("send_task").once()
    assert helper.run_copr_build()["success"]


def test_copr_build_check_names_gitlab(gitlab_mr_event):
    trigger = flexmock(job_config_trigger_type=JobConfigTriggerType.release, id=123)
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    helper = build_helper(
        event=gitlab_mr_event,
        metadata=JobMetadataConfig(targets=["bright-future-x86_64"], owner="nobody"),
        db_trigger=trigger,
    )

    flexmock(copr_build).should_receive(
        "get_copr_build_info_url_from_flask"
    ).and_return("https://test.url")
    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=CommitStatus.pending,
        description="Building SRPM ...",
        check_name="packit-stg/rpm-build-bright-future-x86_64",
        url="",
    ).and_return()
    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=CommitStatus.pending,
        description="Starting RPM build...",
        check_name="packit-stg/rpm-build-bright-future-x86_64",
        url="https://test.url",
    ).and_return()

    flexmock(GitProject).should_receive("set_commit_status").and_return().never()
    flexmock(SRPMBuildModel).should_receive("create").and_return(
        SRPMBuildModel(success=True)
    )
    flexmock(CoprBuildModel).should_receive("get_or_create").and_return(
        CoprBuildModel(id=1)
    )
    flexmock(MergeRequestGitlabEvent).should_receive("db_trigger").and_return(
        flexmock()
    )

    flexmock(PackitAPI).should_receive("create_srpm").and_return("my.srpm")

    # copr build
    flexmock(CoprHelper).should_receive("create_copr_project_if_not_exists").with_args(
        project="the-example-namespace-the-example-repo-1-stg",
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
                flexmock(id=2, projectname="the-project-name", ownername="nobody")
            )
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
            owner="nobody", targets=["bright-future-x86_64", "brightest-future-x86_64"]
        ),
    )
    trigger = flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request)
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    helper = build_helper(jobs=[test_job], event=gitlab_mr_event, db_trigger=trigger)
    flexmock(GitProject).should_receive("set_commit_status").and_return().times(4)
    flexmock(GitProject).should_receive("get_pr").and_return(flexmock())
    flexmock(SRPMBuildModel).should_receive("create").and_return(
        SRPMBuildModel(success=True)
    )
    flexmock(CoprBuildModel).should_receive("get_or_create").and_return(
        CoprBuildModel(id=1)
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
                flexmock(id=2, projectname="the-project-name", ownername="the-owner")
            )
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
            targets=[
                "fedora-29-x86_64",
                "fedora-30-x86_64",
                "fedora-31-x86_64",
                "fedora-rawhide-x86_64",
            ],
            owner="nobody",
            dist_git_branches=["build-branch"],
        ),
    )
    trigger = flexmock(job_config_trigger_type=JobConfigTriggerType.release)
    flexmock(AddBranchPushDbTrigger).should_receive("db_trigger").and_return(trigger)
    helper = build_helper(
        jobs=[branch_build_job], event=branch_push_event_gitlab, db_trigger=trigger,
    )
    flexmock(GitProject).should_receive("set_commit_status").and_return().times(8)
    flexmock(SRPMBuildModel).should_receive("create").and_return(
        SRPMBuildModel(success=True)
    )
    flexmock(CoprBuildModel).should_receive("get_or_create").and_return(
        CoprBuildModel(id=1)
    )
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
                flexmock(id=2, projectname="the-project-name", ownername="the-owner")
            )
            .mock(),
        )
    )

    flexmock(Celery).should_receive("send_task").once()
    assert helper.run_copr_build()["success"]


def test_copr_build_success_gitlab(gitlab_mr_event):
    # status is set for each build-target (4x):
    #  - Building SRPM ...
    #  - Starting RPM build...
    helper = build_helper(event=gitlab_mr_event)
    flexmock(GitProject).should_receive("set_commit_status").and_return().times(8)
    flexmock(GitProject).should_receive("get_pr").and_return(flexmock())
    flexmock(SRPMBuildModel).should_receive("create").and_return(
        SRPMBuildModel(success=True)
    )
    flexmock(CoprBuildModel).should_receive("get_or_create").and_return(
        CoprBuildModel(id=1)
    )
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
                flexmock(id=2, projectname="the-project-name", ownername="the-owner")
            )
            .mock(),
        )
    )

    flexmock(Celery).should_receive("send_task").once()
    assert helper.run_copr_build()["success"]


def test_copr_build_fails_in_packit_gitlab(gitlab_mr_event):
    # status is set for each build-target (4x):
    #  - Building SRPM ...
    #  - Build failed, check latest comment for details.
    helper = build_helper(event=gitlab_mr_event)
    templ = "packit-stg/rpm-build-fedora-{ver}-x86_64"
    flexmock(copr_build).should_receive("get_srpm_log_url_from_flask").and_return(
        "https://test.url"
    )
    for v in ["29", "30", "31", "rawhide"]:
        flexmock(GitProject).should_receive("set_commit_status").with_args(
            "1f6a716aa7a618a9ffe56970d77177d99d100022",
            CommitStatus.pending,
            "",
            "Building SRPM ...",
            templ.format(ver=v),
            trim=True,
        ).and_return().once()
    for v in ["29", "30", "31", "rawhide"]:
        flexmock(GitProject).should_receive("set_commit_status").with_args(
            "1f6a716aa7a618a9ffe56970d77177d99d100022",
            CommitStatus.failure,
            "https://test.url",
            "SRPM build failed, check the logs for details.",
            templ.format(ver=v),
            trim=True,
        ).and_return().once()
    flexmock(GitProject).should_receive("get_pr").and_return(flexmock())
    flexmock(SRPMBuildModel).should_receive("create").and_return(
        SRPMBuildModel(success=False, id=2)
    )
    flexmock(CoprBuildModel).should_receive("get_or_create").and_return(
        CoprBuildModel(id=1)
    )
    flexmock(sentry_integration).should_receive("send_to_sentry").and_return().once()

    flexmock(PackitAPI).should_receive("create_srpm").and_raise(
        FailedCreateSRPM, "some error"
    )

    flexmock(CoprBuildJobHelper).should_receive("run_build").never()

    assert not helper.run_copr_build()["success"]


def test_copr_build_no_targets_gitlab(gitlab_mr_event):
    # status is set for each build-target (fedora-stable => 2x):
    #  - Building SRPM ...
    #  - Starting RPM build...
    helper = build_helper(
        event=gitlab_mr_event, metadata=JobMetadataConfig(owner="nobody")
    )
    flexmock(GitProject).should_receive("set_commit_status").and_return().times(4)
    flexmock(GitProject).should_receive("get_pr").and_return(flexmock())
    flexmock(SRPMBuildModel).should_receive("create").and_return(
        SRPMBuildModel(success=True)
    )
    flexmock(CoprBuildModel).should_receive("get_or_create").and_return(
        CoprBuildModel(id=1)
    )
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
                flexmock(id=2, projectname="the-project-name", ownername="the-owner")
            )
            .mock(),
        )
    )

    flexmock(Celery).should_receive("send_task").once()
    assert helper.run_copr_build()["success"]
