# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
import json
import shutil

import pytest
from celery.app.task import Context, Task
from celery.canvas import Signature
from flexmock import flexmock
from github import Github
from rebasehelper.exceptions import RebaseHelperError

from packit.api import PackitAPI
from packit.config import JobConfigTriggerType
from packit.config.aliases import get_branches
from packit.distgit import DistGit
from packit.local_project import LocalProject
from packit.pkgtool import PkgTool
from packit_service import sentry_integration
from packit_service.config import ServiceConfig
from packit_service.models import (
    JobTriggerModelType,
    PipelineModel,
    ProjectReleaseModel,
    ProposeDownstreamModel,
    ProposeDownstreamStatus,
    ProposeDownstreamTargetModel,
    ProposeDownstreamTargetStatus,
)
from packit_service.service.db_triggers import AddReleaseDbTrigger
from packit_service.worker.allowlist import Allowlist
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.tasks import run_propose_downstream_handler
from tests.spellbook import first_dict_value, get_parameters_from_results


@pytest.fixture(scope="module")
def fedora_branches():
    return sorted(get_branches("fedora-all"))


@pytest.fixture
def mock_propose_downstream_functionality():
    trigger = flexmock(
        job_trigger_model_type=JobTriggerModelType.release,
        id=12,
    )
    run_model = flexmock(PipelineModel)
    flexmock(ProjectReleaseModel).should_receive("get_or_create").with_args(
        tag_name="0.3.0",
        namespace="packit-service",
        repo_name="hello-world",
        project_url="https://github.com/packit-service/hello-world",
        commit_hash="123456",
    ).and_return(trigger).once()
    propose_downstream_model = flexmock(id=123, propose_downstream_targets=[])
    flexmock(ProposeDownstreamModel).should_receive("create_with_new_run").with_args(
        status=ProposeDownstreamStatus.running,
        trigger_model=trigger,
    ).and_return(propose_downstream_model, run_model).once()

    model = flexmock(status="queued")
    flexmock(ProposeDownstreamTargetModel).should_receive("create").with_args(
        status=ProposeDownstreamTargetStatus.queued
    ).and_return(model)
    yield propose_downstream_model, model


def test_dist_git_push_release_handle(
    github_release_webhook, mock_propose_downstream_functionality
):
    propose_downstream_model, model = mock_propose_downstream_functionality

    packit_yaml = (
        "{'specfile_path': 'hello-world.spec', 'synced_files': []"
        ", jobs: [{trigger: release, job: propose_downstream, metadata: {targets:[]}}]}"
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)
    project = flexmock(
        get_file_content=lambda path, ref: packit_yaml,
        full_repo_name="packit-service/hello-world",
        repo="hello-world",
        namespace="packit-service",
        get_files=lambda ref, filter_regex: [],
        get_sha_from_tag=lambda tag_name: "123456",
        get_web_url=lambda: "https://github.com/packit/hello-world",
        is_private=lambda: False,
        default_branch="main",
    )
    lp = flexmock(LocalProject, refresh_the_arguments=lambda: None)
    lp.working_dir = ""
    lp.git_project = project
    flexmock(DistGit).should_receive("local_project").and_return(lp)
    # reset of the upstream repo
    flexmock(LocalProject).should_receive("reset").with_args("HEAD").once()

    flexmock(Allowlist, check_and_report=True)
    ServiceConfig().get_service_config().get_project = lambda url: project

    flexmock(PackitAPI).should_receive("sync_release").with_args(
        dist_git_branch="main", tag="0.3.0"
    ).and_return(flexmock(url="some_url")).once()
    flexmock(PackitAPI).should_receive("clean")

    flexmock(model).should_receive("set_status").with_args(
        status=ProposeDownstreamTargetStatus.running
    ).once()
    flexmock(model).should_receive("set_branch").with_args(branch="main").once()
    flexmock(model).should_receive("set_downstream_pr_url").with_args(
        downstream_pr_url="some_url"
    ).once()
    flexmock(model).should_receive("set_status").with_args(
        status=ProposeDownstreamTargetStatus.submitted
    ).once()
    flexmock(model).should_receive("set_start_time").once()
    flexmock(model).should_receive("set_finished_time").once()
    flexmock(model).should_receive("set_logs").once()
    flexmock(propose_downstream_model).should_receive("set_status").with_args(
        status=ProposeDownstreamStatus.finished
    ).once()

    flexmock(AddReleaseDbTrigger).should_receive("db_trigger").and_return(
        flexmock(job_config_trigger_type=JobConfigTriggerType.release, id=123)
    )
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").once().and_return()
    flexmock(shutil).should_receive("rmtree").with_args("")

    processing_results = SteveJobs().process_message(github_release_webhook)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)

    results = run_propose_downstream_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert first_dict_value(results["job"])["success"]


def test_dist_git_push_release_handle_multiple_branches(
    github_release_webhook, fedora_branches, mock_propose_downstream_functionality
):
    propose_downstream_model, model = mock_propose_downstream_functionality

    packit_yaml = (
        "{'specfile_path': 'hello-world.spec', 'synced_files': []"
        ", jobs: [{trigger: release, job: propose_downstream, "
        "metadata: {targets:[], dist-git-branch: fedora-all}}]}"
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)
    project = flexmock(
        get_file_content=lambda path, ref: packit_yaml,
        full_repo_name="packit-service/hello-world",
        repo="hello-world",
        namespace="packit-service",
        get_files=lambda ref, filter_regex: [],
        get_sha_from_tag=lambda tag_name: "123456",
        get_web_url=lambda: "https://github.com/packit/hello-world",
        is_private=lambda: False,
        default_branch="main",
    )
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(LocalProject).should_receive("reset").with_args("HEAD").times(
        len(fedora_branches)
    )

    flexmock(Allowlist, check_and_report=True)
    ServiceConfig().get_service_config().get_project = lambda url: project

    for branch in fedora_branches:
        flexmock(PackitAPI).should_receive("sync_release").with_args(
            dist_git_branch=branch, tag="0.3.0"
        ).and_return(flexmock(url="some_url")).once()

    flexmock(model).should_receive("set_status").with_args(
        status=ProposeDownstreamTargetStatus.running
    ).times(len(fedora_branches))
    flexmock(model).should_receive("set_branch").times(len(fedora_branches))
    flexmock(model).should_receive("set_downstream_pr_url").with_args(
        downstream_pr_url="some_url"
    ).times(len(fedora_branches))
    flexmock(model).should_receive("set_status").with_args(
        status=ProposeDownstreamTargetStatus.submitted
    ).times(len(fedora_branches))
    flexmock(model).should_receive("set_start_time").times(len(fedora_branches))
    flexmock(model).should_receive("set_finished_time").times(len(fedora_branches))
    flexmock(model).should_receive("set_logs").times(len(fedora_branches))
    flexmock(propose_downstream_model).should_receive("set_status").with_args(
        status=ProposeDownstreamStatus.finished
    ).once()

    flexmock(PkgTool).should_receive("clone").and_return(None)

    flexmock(AddReleaseDbTrigger).should_receive("db_trigger").and_return(
        flexmock(job_config_trigger_type=JobConfigTriggerType.release, id=123)
    )
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").once().and_return()

    processing_results = SteveJobs().process_message(github_release_webhook)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)

    results = run_propose_downstream_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert first_dict_value(results["job"])["success"]


def test_dist_git_push_release_handle_one_failed(
    github_release_webhook, fedora_branches, mock_propose_downstream_functionality
):
    propose_downstream_model, model = mock_propose_downstream_functionality

    packit_yaml = (
        "{'specfile_path': 'hello-world.spec', 'synced_files': []"
        ", jobs: [{trigger: release, job: propose_downstream, "
        "metadata: {targets:[], dist-git-branch: fedora-all}}]}"
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)
    project = (
        flexmock(
            get_file_content=lambda path, ref: packit_yaml,
            full_repo_name="packit-service/hello-world",
            repo="hello-world",
            namespace="packit-service",
            get_files=lambda ref, filter_regex: [],
            get_sha_from_tag=lambda tag_name: "123456",
            get_web_url=lambda: "https://github.com/packit/hello-world",
            is_private=lambda: False,
            default_branch="main",
        )
        .should_receive("create_issue")
        .once()
        .mock()
    )
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(LocalProject).should_receive("reset").with_args("HEAD").times(
        len(fedora_branches)
    )
    flexmock(Allowlist, check_and_report=True)
    ServiceConfig().get_service_config().get_project = lambda url: project

    for i, branch in enumerate(fedora_branches):
        sync_release = (
            flexmock(PackitAPI)
            .should_receive("sync_release")
            .with_args(dist_git_branch=branch, tag="0.3.0")
            .and_return(flexmock(url="some_url"))
            .once()
        )

        if i == 1:
            sync_release.and_raise(Exception, f"Failed {branch}").once()

    flexmock(model).should_receive("set_status").with_args(
        status=ProposeDownstreamTargetStatus.running
    ).times(len(fedora_branches))
    flexmock(model).should_receive("set_branch").times(len(fedora_branches))
    flexmock(model).should_receive("set_start_time").times(len(fedora_branches))
    flexmock(model).should_receive("set_finished_time").times(len(fedora_branches))
    flexmock(model).should_receive("set_logs").times(len(fedora_branches))
    flexmock(model).should_receive("set_downstream_pr_url").with_args(
        downstream_pr_url="some_url"
    ).times(
        len(fedora_branches) - 1  # one branch failed
    )
    flexmock(model).should_receive("set_status").with_args(
        status=ProposeDownstreamTargetStatus.submitted
    ).times(
        len(fedora_branches) - 1
    )  # one branch failed
    flexmock(model).should_receive("set_status").with_args(
        status=ProposeDownstreamTargetStatus.error
    ).once()  # this is the failed branch
    flexmock(propose_downstream_model).should_receive("set_status").with_args(
        status=ProposeDownstreamStatus.error
    ).once()

    flexmock(PkgTool).should_receive("clone").and_return(None)

    flexmock(sentry_integration).should_receive("send_to_sentry").and_return().once()
    flexmock(AddReleaseDbTrigger).should_receive("db_trigger").and_return(
        flexmock(job_config_trigger_type=JobConfigTriggerType.release, id=123)
    )

    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").once().and_return()
    processing_results = SteveJobs().process_message(github_release_webhook)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)

    results = run_propose_downstream_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert not first_dict_value(results["job"])["success"]


def test_dist_git_push_release_handle_all_failed(
    github_release_webhook, fedora_branches, mock_propose_downstream_functionality
):
    propose_downstream_model, model = mock_propose_downstream_functionality

    packit_yaml = (
        "{'specfile_path': 'hello-world.spec', 'synced_files': []"
        ", jobs: [{trigger: release, job: propose_downstream, "
        "metadata: {targets:[], dist-git-branch: fedora-all}}]}"
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)
    table_content = ""
    for branch in fedora_branches:
        table_content += f"| `{branch}` | `Failed` |\n"
    project = (
        flexmock(
            get_file_content=lambda path, ref: packit_yaml,
            full_repo_name="packit-service/hello-world",
            repo="hello-world",
            namespace="packit-service",
            get_files=lambda ref, filter_regex: [],
            get_sha_from_tag=lambda tag_name: "123456",
            get_web_url=lambda: "https://github.com/packit/hello-world",
            is_private=lambda: False,
            default_branch="main",
        )
        .should_receive("create_issue")
        .with_args(
            title="[packit] Propose downstream failed for release 0.3.0",
            body="Packit failed on creating pull-requests in dist-git:\n\n"
            "| dist-git branch | error |\n"
            "| --------------- | ----- |\n"
            f"{table_content}\n\n"
            "You can retrigger the update by adding a comment (`/packit propose-downstream`)"
            " into this issue.\n",
        )
        .once()
        .mock()
    )
    lp = flexmock(LocalProject, refresh_the_arguments=lambda: None)
    lp.git_project = project
    lp.working_dir = ""
    flexmock(DistGit).should_receive("local_project").and_return(lp)
    # reset of the upstream repo
    flexmock(LocalProject).should_receive("reset").with_args("HEAD").times(
        len(fedora_branches)
    )

    flexmock(Allowlist, check_and_report=True)
    ServiceConfig().get_service_config().get_project = lambda url: project

    flexmock(PackitAPI).should_receive("sync_release").and_raise(
        Exception, "Failed"
    ).times(len(fedora_branches))
    flexmock(AddReleaseDbTrigger).should_receive("db_trigger").and_return(
        flexmock(job_config_trigger_type=JobConfigTriggerType.release, id=123)
    )

    flexmock(model).should_receive("set_status").with_args(
        status=ProposeDownstreamTargetStatus.running
    ).times(len(fedora_branches))
    flexmock(model).should_receive("set_branch").times(len(fedora_branches))
    flexmock(model).should_receive("set_status").with_args(
        status=ProposeDownstreamTargetStatus.error
    ).times(len(fedora_branches))
    flexmock(model).should_receive("set_start_time").times(len(fedora_branches))
    flexmock(model).should_receive("set_finished_time").times(len(fedora_branches))
    flexmock(model).should_receive("set_logs").times(len(fedora_branches))
    flexmock(propose_downstream_model).should_receive("set_status").with_args(
        status=ProposeDownstreamStatus.error
    ).once()

    flexmock(sentry_integration).should_receive("send_to_sentry").and_return().times(
        len(fedora_branches)
    )
    flexmock(shutil).should_receive("rmtree").with_args("")
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").once().and_return()

    processing_results = SteveJobs().process_message(github_release_webhook)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)

    results = run_propose_downstream_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert not first_dict_value(results["job"])["success"]


def test_retry_propose_downstream_task(
    github_release_webhook, mock_propose_downstream_functionality
):
    propose_downstream_model, model = mock_propose_downstream_functionality

    packit_yaml = (
        "{'specfile_path': 'hello-world.spec', 'synced_files': []"
        ", jobs: [{trigger: release, job: propose_downstream, metadata: {targets:[]}}]}"
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)
    project = flexmock(
        get_file_content=lambda path, ref: packit_yaml,
        full_repo_name="packit-service/hello-world",
        repo="hello-world",
        namespace="packit-service",
        get_files=lambda ref, filter_regex: [],
        get_sha_from_tag=lambda tag_name: "123456",
        get_web_url=lambda: "https://github.com/packit/hello-world",
        is_private=lambda: False,
        default_branch="main",
    )

    lp = flexmock(LocalProject, refresh_the_arguments=lambda: None)
    lp.git_project = project
    lp.working_dir = ""
    flexmock(DistGit).should_receive("local_project").and_return(lp)
    # reset of the upstream repo
    flexmock(LocalProject).should_receive("reset").with_args("HEAD").once()

    flexmock(Allowlist, check_and_report=True)
    ServiceConfig().get_service_config().get_project = lambda url: project

    flexmock(AddReleaseDbTrigger).should_receive("db_trigger").and_return(
        flexmock(job_config_trigger_type=JobConfigTriggerType.release, id=123)
    )
    flexmock(Signature).should_receive("apply_async").once()

    flexmock(PackitAPI).should_receive("sync_release").with_args(
        dist_git_branch="main", tag="0.3.0"
    ).and_raise(
        RebaseHelperError, "Failed to download file from URL example.com"
    ).once()

    flexmock(model).should_receive("set_status").with_args(
        status=ProposeDownstreamTargetStatus.running
    ).once()
    flexmock(model).should_receive("set_branch").with_args(branch="main").once()
    flexmock(model).should_receive("set_status").with_args(
        staus=ProposeDownstreamTargetStatus.retry
    ).once()
    flexmock(model).should_receive("set_start_time").once()
    flexmock(model).should_receive("set_finished_time").once()
    flexmock(model).should_receive("set_logs").once()

    flexmock(shutil).should_receive("rmtree").with_args("")
    flexmock(Task).should_receive("retry").once().and_return()
    flexmock(Pushgateway).should_receive("push").once().and_return()

    processing_results = SteveJobs().process_message(github_release_webhook)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)

    results = run_propose_downstream_handler(event_dict, package_config, job_config)

    assert first_dict_value(results["job"])["success"]  # yes, success, see #1140
    assert "Not able to download" in first_dict_value(results["job"])["details"]["msg"]


def test_dont_retry_propose_downstream_task(
    github_release_webhook, mock_propose_downstream_functionality
):
    propose_downstream_model, model = mock_propose_downstream_functionality

    packit_yaml = (
        "{'specfile_path': 'hello-world.spec', 'synced_files': []"
        ", jobs: [{trigger: release, job: propose_downstream, metadata: {targets:[]}}]}"
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)
    project = flexmock(
        get_file_content=lambda path, ref: packit_yaml,
        full_repo_name="packit-service/hello-world",
        repo="hello-world",
        namespace="packit-service",
        get_files=lambda ref, filter_regex: [],
        get_sha_from_tag=lambda tag_name: "123456",
        get_web_url=lambda: "https://github.com/packit/hello-world",
        is_private=lambda: False,
        default_branch="main",
    )

    lp = flexmock(LocalProject, refresh_the_arguments=lambda: None)
    lp.git_project = project
    lp.working_dir = ""
    flexmock(DistGit).should_receive("local_project").and_return(lp)

    flexmock(Allowlist, check_and_report=True)
    ServiceConfig().get_service_config().get_project = lambda url: project

    flexmock(AddReleaseDbTrigger).should_receive("db_trigger").and_return(
        flexmock(job_config_trigger_type=JobConfigTriggerType.release, id=123)
    )
    flexmock(Signature).should_receive("apply_async").once()

    flexmock(PackitAPI).should_receive("sync_release").with_args(
        dist_git_branch="main", tag="0.3.0"
    ).and_raise(
        RebaseHelperError, "Failed to download file from URL example.com"
    ).once()

    flexmock(model).should_receive("set_status").with_args(
        status=ProposeDownstreamTargetStatus.running
    ).once()
    flexmock(model).should_receive("set_branch").with_args(branch="main").once()
    flexmock(model).should_receive("set_status").with_args(
        status=ProposeDownstreamTargetStatus.error
    ).once()
    flexmock(model).should_receive("set_start_time").once()
    flexmock(model).should_receive("set_finished_time").once()
    flexmock(model).should_receive("set_logs").once()
    flexmock(propose_downstream_model).should_receive("set_status").with_args(
        status=ProposeDownstreamStatus.error
    ).once()

    flexmock(LocalProject).should_receive("reset").with_args("HEAD").once()
    flexmock(Context, retries=2)
    flexmock(shutil).should_receive("rmtree").with_args("")
    flexmock(Task).should_receive("retry").never()
    flexmock(project).should_receive("create_issue").once()
    flexmock(Pushgateway).should_receive("push").once().and_return()

    processing_results = SteveJobs().process_message(github_release_webhook)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)

    results = run_propose_downstream_handler(event_dict, package_config, job_config)

    assert not first_dict_value(results["job"])["success"]
