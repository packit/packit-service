# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
import json
import shutil

import pytest
from celery.app.task import Context, Task
from celery.canvas import Signature
from flexmock import flexmock
from github import Github

from packit.api import PackitAPI
from packit.config import JobConfigTriggerType
from packit.config.aliases import get_branches
from packit.distgit import DistGit
from packit.exceptions import PackitDownloadFailedException
from packit.local_project import LocalProject
from packit.pkgtool import PkgTool
from packit_service import sentry_integration
from packit_service.config import ServiceConfig
from packit_service.constants import TASK_ACCEPTED
from packit_service.models import (
    JobTriggerModelType,
    PipelineModel,
    ProjectReleaseModel,
    SyncReleaseModel,
    SyncReleaseStatus,
    SyncReleaseTargetModel,
    SyncReleaseTargetStatus,
    SyncReleaseJobType,
)
from packit_service.service.db_triggers import AddReleaseDbTrigger
from packit_service.service.urls import get_propose_downstream_info_url
from packit_service.worker.allowlist import Allowlist
from packit_service.worker.helpers.sync_release.propose_downstream import (
    ProposeDownstreamJobHelper,
)
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.reporting import BaseCommitStatus
from packit_service.worker.tasks import run_propose_downstream_handler
from tests.spellbook import first_dict_value, get_parameters_from_results


@pytest.fixture(scope="module")
def fedora_branches():
    return sorted(get_branches("fedora-all"))


@pytest.fixture
def propose_downstream_model():
    trigger = flexmock(
        job_trigger_model_type=JobTriggerModelType.release,
        id=12,
        job_config_trigger_type=JobConfigTriggerType.release,
    )
    run_model = flexmock(PipelineModel)
    flexmock(ProjectReleaseModel).should_receive("get_or_create").with_args(
        tag_name="0.3.0",
        namespace="packit-service",
        repo_name="hello-world",
        project_url="https://github.com/packit-service/hello-world",
        commit_hash="123456",
    ).and_return(trigger).once()
    propose_downstream_model = flexmock(id=123, sync_release_targets=[])
    flexmock(SyncReleaseModel).should_receive("create_with_new_run").with_args(
        status=SyncReleaseStatus.running,
        trigger_model=trigger,
        job_type=SyncReleaseJobType.propose_downstream,
    ).and_return(propose_downstream_model, run_model).once()

    flexmock(ProposeDownstreamJobHelper).should_receive(
        "report_status_to_all"
    ).with_args(
        description=TASK_ACCEPTED,
        state=BaseCommitStatus.pending,
        url="",
        markdown_content=None,
    ).once()
    yield propose_downstream_model


@pytest.fixture
def propose_downstream_target_models(fedora_branches):
    models = []
    for branch in fedora_branches:
        model = flexmock(status="queued", id=1234, branch=branch)
        models.append(model)
        flexmock(SyncReleaseTargetModel).should_receive("create").with_args(
            status=SyncReleaseTargetStatus.queued, branch=branch
        ).and_return(model)
    yield models


def test_dist_git_push_release_handle(github_release_webhook, propose_downstream_model):
    model = flexmock(status="queued", id=1234, branch="main")
    flexmock(SyncReleaseTargetModel).should_receive("create").with_args(
        status=SyncReleaseTargetStatus.queued, branch="main"
    ).and_return(model)

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
    flexmock(LocalProject).should_receive("git_repo").and_return(
        flexmock(
            head=flexmock()
            .should_receive("reset")
            .with_args("HEAD", index=True, working_tree=True)
            .once()
            .mock(),
            git=flexmock(clear_cache=lambda: None),
        )
    )

    flexmock(Allowlist, check_and_report=True)
    ServiceConfig().get_service_config().get_project = lambda url: project

    flexmock(PackitAPI).should_receive("sync_release").with_args(
        dist_git_branch="main",
        tag="0.3.0",
        create_pr=True,
        local_pr_branch_suffix="update-propose_downstream",
        use_downstream_specfile=False,
    ).and_return(flexmock(url="some_url")).once()
    flexmock(PackitAPI).should_receive("clean")

    flexmock(model).should_receive("set_status").with_args(
        status=SyncReleaseTargetStatus.running
    ).once()
    flexmock(model).should_receive("set_downstream_pr_url").with_args(
        downstream_pr_url="some_url"
    ).once()
    flexmock(model).should_receive("set_status").with_args(
        status=SyncReleaseTargetStatus.submitted
    ).once()
    flexmock(model).should_receive("set_start_time").once()
    flexmock(model).should_receive("set_finished_time").once()
    flexmock(model).should_receive("set_logs").once()
    flexmock(propose_downstream_model).should_receive("set_status").with_args(
        status=SyncReleaseStatus.finished
    ).once()

    flexmock(AddReleaseDbTrigger).should_receive("db_trigger").and_return(
        flexmock(
            job_config_trigger_type=JobConfigTriggerType.release,
            id=123,
            job_trigger_model_type=JobTriggerModelType.release,
        )
    )
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    flexmock(shutil).should_receive("rmtree").with_args("")

    url = get_propose_downstream_info_url(model.id)
    flexmock(ProposeDownstreamJobHelper).should_receive(
        "report_status_for_branch"
    ).with_args(
        branch="main",
        description="Starting propose downstream...",
        state=BaseCommitStatus.running,
        url=url,
    ).once()

    flexmock(ProposeDownstreamJobHelper).should_receive(
        "report_status_for_branch"
    ).with_args(
        branch="main",
        description="Propose downstream finished successfully.",
        state=BaseCommitStatus.success,
        url=url,
    ).once()

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
    github_release_webhook,
    fedora_branches,
    propose_downstream_model,
    propose_downstream_target_models,
):

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
    flexmock(LocalProject).should_receive("git_repo").and_return(
        flexmock(
            head=flexmock()
            .should_receive("reset")
            .with_args("HEAD", index=True, working_tree=True)
            .times(len(fedora_branches))
            .mock(),
            git=flexmock(clear_cache=lambda: None),
        )
    )

    flexmock(Allowlist, check_and_report=True)
    ServiceConfig().get_service_config().get_project = lambda url: project

    for branch in fedora_branches:
        flexmock(PackitAPI).should_receive("sync_release").with_args(
            dist_git_branch=branch,
            tag="0.3.0",
            create_pr=True,
            local_pr_branch_suffix="update-propose_downstream",
            use_downstream_specfile=False,
        ).and_return(flexmock(url="some_url")).once()
    for model in propose_downstream_target_models:
        flexmock(model).should_receive("set_status").with_args(
            status=SyncReleaseTargetStatus.running
        ).once()
        flexmock(model).should_receive("set_downstream_pr_url").with_args(
            downstream_pr_url="some_url"
        ).once()
        flexmock(model).should_receive("set_status").with_args(
            status=SyncReleaseTargetStatus.submitted
        ).once()
        flexmock(model).should_receive("set_start_time").once()
        flexmock(model).should_receive("set_finished_time").once()
        flexmock(model).should_receive("set_logs").once()
    flexmock(propose_downstream_model).should_receive("set_status").with_args(
        status=SyncReleaseStatus.finished
    ).once()

    flexmock(PkgTool).should_receive("clone").and_return(None)

    flexmock(AddReleaseDbTrigger).should_receive("db_trigger").and_return(
        flexmock(
            job_config_trigger_type=JobConfigTriggerType.release,
            id=123,
            job_trigger_model_type=JobTriggerModelType.release,
        )
    )
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    url = get_propose_downstream_info_url(model.id)
    for branch in fedora_branches:
        flexmock(ProposeDownstreamJobHelper).should_receive(
            "report_status_for_branch"
        ).with_args(
            branch=branch,
            description="Starting propose downstream...",
            state=BaseCommitStatus.running,
            url=url,
        ).once()

    for branch in fedora_branches:
        flexmock(ProposeDownstreamJobHelper).should_receive(
            "report_status_for_branch"
        ).with_args(
            branch=branch,
            description="Propose downstream finished successfully.",
            state=BaseCommitStatus.success,
            url=url,
        ).once()

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
    github_release_webhook,
    fedora_branches,
    propose_downstream_model,
    propose_downstream_target_models,
):

    packit_yaml = (
        "{'specfile_path': 'hello-world.spec', 'synced_files': []"
        ", jobs: [{trigger: release, job: propose_downstream, "
        "targets:[], dist_git_branches: [fedora-all,]}]}"
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
        .and_return(flexmock(id="1", url="an url"))
        .mock()
    )
    project.should_receive("get_issue_list").and_return([])
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(LocalProject).should_receive("git_repo").and_return(
        flexmock(
            head=flexmock()
            .should_receive("reset")
            .with_args("HEAD", index=True, working_tree=True)
            .times(len(fedora_branches))
            .mock(),
            git=flexmock(clear_cache=lambda: None),
        )
    )
    flexmock(Allowlist, check_and_report=True)
    ServiceConfig().get_service_config().get_project = lambda url: project
    failed_branch = None
    for i, branch in enumerate(fedora_branches):
        if i == 1:
            failed_branch = branch
            flexmock(PackitAPI).should_receive("sync_release").with_args(
                dist_git_branch=branch,
                tag="0.3.0",
                create_pr=True,
                local_pr_branch_suffix="update-propose_downstream",
                use_downstream_specfile=False,
            ).and_raise(Exception, f"Failed {branch}").once()
        else:
            flexmock(PackitAPI).should_receive("sync_release").with_args(
                dist_git_branch=branch,
                tag="0.3.0",
                create_pr=True,
                local_pr_branch_suffix="update-propose_downstream",
                use_downstream_specfile=False,
            ).and_return(flexmock(url="some_url")).once()
    for model in propose_downstream_target_models:
        url = get_propose_downstream_info_url(model.id)
        flexmock(model).should_receive("set_status").with_args(
            status=SyncReleaseTargetStatus.running
        ).once()
        flexmock(model).should_receive("set_start_time").once()
        flexmock(model).should_receive("set_finished_time").once()
        flexmock(model).should_receive("set_logs").once()
        if model.branch != failed_branch:
            flexmock(model).should_receive("set_downstream_pr_url").with_args(
                downstream_pr_url="some_url"
            )
            flexmock(model).should_receive("set_status").with_args(
                status=SyncReleaseTargetStatus.submitted
            ).once()
        else:
            flexmock(model).should_receive("set_status").with_args(
                status=SyncReleaseTargetStatus.error
            ).once()  # this is the failed branch

    flexmock(propose_downstream_model).should_receive("set_status").with_args(
        status=SyncReleaseStatus.error
    ).once()

    flexmock(PkgTool).should_receive("clone").and_return(None)

    flexmock(sentry_integration).should_receive("send_to_sentry").and_return().once()
    flexmock(AddReleaseDbTrigger).should_receive("db_trigger").and_return(
        flexmock(
            job_config_trigger_type=JobConfigTriggerType.release,
            id=123,
            job_trigger_model_type=JobTriggerModelType.release,
        )
    )

    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    for branch in fedora_branches:
        flexmock(ProposeDownstreamJobHelper).should_receive(
            "report_status_for_branch"
        ).with_args(
            branch=branch,
            description="Starting propose downstream...",
            state=BaseCommitStatus.running,
            url=url,
        ).once()

    for i in range(len(fedora_branches)):
        if i == 1:
            flexmock(ProposeDownstreamJobHelper).should_receive(
                "report_status_for_branch"
            ).with_args(
                branch=fedora_branches[i],
                description=f"Propose downstream failed: Failed {fedora_branches[i]}",
                state=BaseCommitStatus.failure,
                url=url,
            ).once()
        else:
            flexmock(ProposeDownstreamJobHelper).should_receive(
                "report_status_for_branch"
            ).with_args(
                branch=fedora_branches[i],
                description="Propose downstream finished successfully.",
                state=BaseCommitStatus.success,
                url=url,
            ).once()

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
    github_release_webhook,
    fedora_branches,
    propose_downstream_model,
    propose_downstream_target_models,
):
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
            body="Packit failed on creating pull-requests in dist-git "
            "(https://src.fedoraproject.org/rpms/hello-world.git):\n\n"
            "| dist-git branch | error |\n"
            "| --------------- | ----- |\n"
            f"{table_content}\n\n"
            "You can retrigger the update by adding a comment (`/packit propose-downstream`)"
            " into this issue.\n",
        )
        .once()
        .and_return(flexmock(id="1", url="an url"))
        .mock()
    )
    project.should_receive("get_issue_list").and_return([])
    lp = flexmock(LocalProject, refresh_the_arguments=lambda: None)
    lp.git_project = project
    lp.git_url = "https://src.fedoraproject.org/rpms/hello-world.git"
    lp.working_dir = ""
    flexmock(DistGit).should_receive("local_project").and_return(lp)
    # reset of the upstream repo
    flexmock(LocalProject).should_receive("git_repo").and_return(
        flexmock(
            head=flexmock()
            .should_receive("reset")
            .with_args("HEAD", index=True, working_tree=True)
            .times(len(fedora_branches))
            .mock(),
            git=flexmock(clear_cache=lambda: None),
        )
    )

    flexmock(Allowlist, check_and_report=True)
    ServiceConfig().get_service_config().get_project = lambda url: project

    flexmock(PackitAPI).should_receive("sync_release").and_raise(
        Exception, "Failed"
    ).times(len(fedora_branches))
    flexmock(AddReleaseDbTrigger).should_receive("db_trigger").and_return(
        flexmock(
            job_config_trigger_type=JobConfigTriggerType.release,
            id=123,
            job_trigger_model_type=JobTriggerModelType.release,
        )
    )
    for model in propose_downstream_target_models:
        url = get_propose_downstream_info_url(model.id)
        flexmock(model).should_receive("set_status").with_args(
            status=SyncReleaseTargetStatus.running
        ).once()
        flexmock(model).should_receive("set_status").with_args(
            status=SyncReleaseTargetStatus.error
        ).once()
        flexmock(model).should_receive("set_start_time").once()
        flexmock(model).should_receive("set_finished_time").once()
        flexmock(model).should_receive("set_logs").once()
    flexmock(propose_downstream_model).should_receive("set_status").with_args(
        status=SyncReleaseStatus.error
    ).once()

    flexmock(sentry_integration).should_receive("send_to_sentry").and_return().times(
        len(fedora_branches)
    )
    flexmock(shutil).should_receive("rmtree").with_args("")
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    for branch in fedora_branches:
        flexmock(ProposeDownstreamJobHelper).should_receive(
            "report_status_for_branch"
        ).with_args(
            branch=branch,
            description="Starting propose downstream...",
            state=BaseCommitStatus.running,
            url=url,
        ).once()

    for branch in fedora_branches:
        flexmock(ProposeDownstreamJobHelper).should_receive(
            "report_status_for_branch"
        ).with_args(
            branch=branch,
            description="Propose downstream failed: Failed",
            state=BaseCommitStatus.failure,
            url=url,
        ).once()

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
    github_release_webhook, propose_downstream_model
):
    model = flexmock(status="queued", id=1234, branch="main")
    flexmock(SyncReleaseTargetModel).should_receive("create").with_args(
        status=SyncReleaseTargetStatus.queued, branch="main"
    ).and_return(model)

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
    flexmock(LocalProject).should_receive("git_repo").and_return(
        flexmock(
            head=flexmock()
            .should_receive("reset")
            .with_args("HEAD", index=True, working_tree=True)
            .once()
            .mock(),
            git=flexmock(clear_cache=lambda: None),
        )
    )

    flexmock(Allowlist, check_and_report=True)
    ServiceConfig().get_service_config().get_project = lambda url: project

    flexmock(AddReleaseDbTrigger).should_receive("db_trigger").and_return(
        flexmock(
            job_config_trigger_type=JobConfigTriggerType.release,
            id=123,
            job_trigger_model_type=JobTriggerModelType.release,
        )
    )
    flexmock(Signature).should_receive("apply_async").once()

    flexmock(PackitAPI).should_receive("sync_release").with_args(
        dist_git_branch="main",
        tag="0.3.0",
        create_pr=True,
        local_pr_branch_suffix="update-propose_downstream",
        use_downstream_specfile=False,
    ).and_raise(
        PackitDownloadFailedException, "Failed to download source from example.com"
    ).once()

    flexmock(model).should_receive("set_status").with_args(
        status=SyncReleaseTargetStatus.running
    ).once()
    flexmock(model).should_receive("set_status").with_args(
        status=SyncReleaseTargetStatus.retry
    ).once()
    flexmock(model).should_receive("set_start_time").once()
    flexmock(model).should_receive("set_finished_time").once()
    flexmock(model).should_receive("set_logs").once()

    flexmock(shutil).should_receive("rmtree").with_args("")
    flexmock(Task).should_receive("retry").once().and_return()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    url = get_propose_downstream_info_url(model.id)
    flexmock(ProposeDownstreamJobHelper).should_receive(
        "report_status_for_branch"
    ).with_args(
        branch="main",
        description="Starting propose downstream...",
        state=BaseCommitStatus.running,
        url=url,
    ).once()
    flexmock(ProposeDownstreamJobHelper).should_receive(
        "report_status_for_branch"
    ).with_args(
        branch="main",
        description="Propose downstream is being retried because "
        "we were not able yet to download the archive. ",
        state=BaseCommitStatus.pending,
        url=url,
    ).once()

    processing_results = SteveJobs().process_message(github_release_webhook)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)

    results = run_propose_downstream_handler(event_dict, package_config, job_config)

    assert first_dict_value(results["job"])["success"]  # yes, success, see #1140
    assert "Not able to download" in first_dict_value(results["job"])["details"]["msg"]


def test_dont_retry_propose_downstream_task(
    github_release_webhook, propose_downstream_model
):
    model = flexmock(status="queued", id=1234, branch="main")
    flexmock(SyncReleaseTargetModel).should_receive("create").with_args(
        status=SyncReleaseTargetStatus.queued, branch="main"
    ).and_return(model)
    packit_yaml = (
        "{'specfile_path': 'hello-world.spec', 'synced_files': []"
        ", jobs: [{trigger: release, job: propose_downstream, metadata: {targets:[]}}]}"
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
        .and_return(flexmock(id="1", url="an url"))
        .mock()
    )
    project.should_receive("get_issue_list").and_return([]).once()

    lp = flexmock(LocalProject, refresh_the_arguments=lambda: None)
    lp.git_project = project
    lp.git_url = "https://src.fedoraproject.org/rpms/hello-world.git"
    lp.working_dir = ""
    flexmock(DistGit).should_receive("local_project").and_return(lp)

    flexmock(Allowlist, check_and_report=True)
    ServiceConfig().get_service_config().get_project = lambda url: project

    flexmock(AddReleaseDbTrigger).should_receive("db_trigger").and_return(
        flexmock(
            job_config_trigger_type=JobConfigTriggerType.release,
            id=123,
            job_trigger_model_type=JobTriggerModelType.release,
        )
    )
    flexmock(Signature).should_receive("apply_async").once()

    flexmock(PackitAPI).should_receive("sync_release").with_args(
        dist_git_branch="main",
        tag="0.3.0",
        create_pr=True,
        local_pr_branch_suffix="update-propose_downstream",
        use_downstream_specfile=False,
    ).and_raise(
        PackitDownloadFailedException, "Failed to download source from example.com"
    ).once()

    flexmock(model).should_receive("set_status").with_args(
        status=SyncReleaseTargetStatus.running
    ).once()
    flexmock(model).should_receive("set_status").with_args(
        status=SyncReleaseTargetStatus.error
    ).once()
    flexmock(model).should_receive("set_start_time").once()
    flexmock(model).should_receive("set_finished_time").once()
    flexmock(model).should_receive("set_logs").once()
    flexmock(propose_downstream_model).should_receive("set_status").with_args(
        status=SyncReleaseStatus.error
    ).once()

    flexmock(LocalProject).should_receive("git_repo").and_return(
        flexmock(
            head=flexmock()
            .should_receive("reset")
            .with_args("HEAD", index=True, working_tree=True)
            .once()
            .mock(),
            git=flexmock(clear_cache=lambda: None),
        )
    )
    flexmock(Context, retries=2)
    flexmock(shutil).should_receive("rmtree").with_args("")
    flexmock(Task).should_receive("retry").never()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    url = get_propose_downstream_info_url(model.id)
    flexmock(ProposeDownstreamJobHelper).should_receive(
        "report_status_for_branch"
    ).with_args(
        branch="main",
        description="Starting propose downstream...",
        state=BaseCommitStatus.running,
        url=url,
    ).once()
    flexmock(ProposeDownstreamJobHelper).should_receive(
        "report_status_for_branch"
    ).with_args(
        branch="main",
        description="Propose downstream failed: Failed to download source from example.com",
        state=BaseCommitStatus.failure,
        url=url,
    ).once()

    processing_results = SteveJobs().process_message(github_release_webhook)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)

    results = run_propose_downstream_handler(event_dict, package_config, job_config)

    assert not first_dict_value(results["job"])["success"]
