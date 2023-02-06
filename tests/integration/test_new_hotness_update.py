import json
import shutil

import pytest
from celery.canvas import Signature
from flexmock import flexmock
from github import Github

import packit_service.worker.checker.distgit
from packit.api import PackitAPI
from packit.config import JobConfigTriggerType
from packit.config.aliases import get_branches
from packit.distgit import DistGit
from packit.local_project import LocalProject
from packit_service.config import ServiceConfig
from packit_service.models import (
    JobTriggerModelType,
    PipelineModel,
    ProjectReleaseModel,
    SyncReleaseStatus,
    SyncReleaseModel,
    SyncReleaseTargetModel,
    SyncReleaseTargetStatus,
    SyncReleaseJobType,
)
from packit_service.service.db_triggers import AddReleaseDbTrigger
from packit_service.worker.allowlist import Allowlist
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.tasks import run_pull_from_upstream_handler
from tests.spellbook import get_parameters_from_results, first_dict_value


@pytest.fixture(scope="module")
def fedora_branches():
    return sorted(get_branches("fedora-all"))


@pytest.fixture
def sync_release_model():
    trigger = flexmock(
        job_trigger_model_type=JobTriggerModelType.release,
        id=12,
        job_config_trigger_type=JobConfigTriggerType.release,
    )
    run_model = flexmock(PipelineModel)
    flexmock(ProjectReleaseModel).should_receive("get_or_create").with_args(
        tag_name="7.0.3",
        namespace="packit-service",
        repo_name="hello-world",
        project_url="https://github.com/packit-service/hello-world",
        commit_hash=None,
    ).and_return(trigger)
    sync_release_model = flexmock(id=123, sync_release_targets=[])
    flexmock(SyncReleaseModel).should_receive("create_with_new_run").with_args(
        status=SyncReleaseStatus.running,
        trigger_model=trigger,
        job_type=SyncReleaseJobType.pull_from_upstream,
    ).and_return(sync_release_model, run_model).once()

    yield sync_release_model


@pytest.fixture
def sync_release_target_models(fedora_branches):
    models = []
    for branch in fedora_branches:
        model = flexmock(status="queued", id=1234, branch=branch)
        models.append(model)
        flexmock(SyncReleaseTargetModel).should_receive("create").with_args(
            status=SyncReleaseTargetStatus.queued, branch=branch
        ).and_return(model)
    yield models


def test_new_hotness_update(new_hotness_update, sync_release_model):
    model = flexmock(status="queued", id=1234, branch="main")
    flexmock(SyncReleaseTargetModel).should_receive("create").with_args(
        status=SyncReleaseTargetStatus.queued, branch="main"
    ).and_return(model)

    packit_yaml = (
        "{'specfile_path': 'hello-world.spec', 'upstream_project_url': "
        "'https://github.com/packit-service/hello-world'"
        ", jobs: [{trigger: release, job: pull_from_upstream, metadata: {targets:[]}}]}"
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)
    distgit_project = flexmock(
        get_files=lambda ref, recursive: [".packit.yaml"],
        get_file_content=lambda path, ref: packit_yaml,
        full_repo_name="rpms/redis",
        repo="redis",
        is_private=lambda: False,
        default_branch="main",
    )
    project = flexmock(
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

    service_config = ServiceConfig().get_service_config()
    flexmock(service_config).should_receive("get_project").with_args(
        "https://src.fedoraproject.org/rpms/redis"
    ).and_return(distgit_project)
    flexmock(service_config).should_receive("get_project").with_args(
        "https://github.com/packit-service/hello-world"
    ).and_return(project)

    flexmock(PackitAPI).should_receive("sync_release").with_args(
        dist_git_branch="main",
        tag="7.0.3",
        create_pr=True,
        local_pr_branch_suffix="update-pull_from_upstream",
        use_downstream_specfile=True,
        sync_default_files=False,
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
    flexmock(sync_release_model).should_receive("set_status").with_args(
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
    flexmock(Pushgateway).should_receive("push").times(1).and_return()
    flexmock(shutil).should_receive("rmtree").with_args("")

    processing_results = SteveJobs().process_message(new_hotness_update)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)

    results = run_pull_from_upstream_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert first_dict_value(results["job"])["success"]


def test_new_hotness_update_pre_check_fail(new_hotness_update):
    # no repo name
    packit_yaml = (
        "{'specfile_path': 'hello-world.spec', 'upstream_project_url': "
        "'https://github.com/packit-service'"
        ", jobs: [{trigger: release, job: pull_from_upstream, metadata: {targets:[]}}], "
        "'issue_repository': 'https://github.com/packit/issue_repository'}"
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)
    distgit_project = flexmock(
        get_files=lambda ref, recursive: [".packit.yaml"],
        get_file_content=lambda path, ref: packit_yaml,
        full_repo_name="rpms/redis",
        repo="redis",
        is_private=lambda: False,
        default_branch="main",
    )

    flexmock(Allowlist, check_and_report=True)

    service_config = ServiceConfig().get_service_config()
    flexmock(service_config).should_receive("get_project").with_args(
        "https://src.fedoraproject.org/rpms/redis"
    ).and_return(distgit_project)

    msg = (
        "We were not able to parse repo name or repo namespace from "
        "the upstream_project_url 'https://github.com/packit-service' defined in the config."
    )
    flexmock(packit_service.worker.checker.distgit).should_receive(
        "report_in_issue_repository"
    ).with_args(
        issue_repository="https://github.com/packit/issue_repository",
        service_config=service_config,
        title="Pull from upstream could not be run for update 7.0.3",
        message=msg,
        comment_to_existing=msg,
    )

    SteveJobs().process_message(new_hotness_update)
