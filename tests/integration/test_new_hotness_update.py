# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json
import shutil

import pytest
from celery.canvas import group
from flexmock import flexmock
from github.MainClass import Github
from packit.api import PackitAPI
from packit.config import JobConfigTriggerType
from packit.config.aliases import get_branches
from packit.distgit import DistGit
from packit.local_project import LocalProject, LocalProjectBuilder

import packit_service.worker.checker.distgit
from packit_service.config import ServiceConfig
from packit_service.models import (
    AnityaProjectModel,
    AnityaVersionModel,
    PipelineModel,
    ProjectEventModel,
    ProjectEventModelType,
    ProjectReleaseModel,
    SyncReleaseJobType,
    SyncReleaseModel,
    SyncReleasePullRequestModel,
    SyncReleaseStatus,
    SyncReleaseTargetModel,
    SyncReleaseTargetStatus,
)
from packit_service.service.db_project_events import AddReleaseEventToDb
from packit_service.worker.allowlist import Allowlist
from packit_service.worker.checker.run_condition import IsRunConditionSatisfied
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.reporting.news import DistgitAnnouncement
from packit_service.worker.tasks import run_pull_from_upstream_handler
from tests.spellbook import first_dict_value, get_parameters_from_results


@pytest.fixture(scope="module")
def fedora_branches():
    return sorted(get_branches("fedora-all"))


@pytest.fixture
def sync_release_model():
    db_project_object = flexmock(
        id=12,
        project_event_model_type=ProjectEventModelType.release,
        job_config_trigger_type=JobConfigTriggerType.release,
    )
    project_event = (
        flexmock().should_receive("get_project_event_object").and_return(db_project_object).mock()
    )
    run_model = flexmock(PipelineModel)
    flexmock(ProjectEventModel).should_receive("get_or_create").with_args(
        type=ProjectEventModelType.release,
        event_id=12,
        commit_sha=None,
    ).and_return(project_event)
    flexmock(ProjectReleaseModel).should_receive("get_or_create").with_args(
        tag_name="7.0.3",
        namespace="packit-service",
        repo_name="hello-world",
        project_url="https://github.com/packit-service/hello-world",
        commit_hash=None,
    ).and_return(db_project_object)
    sync_release_model = flexmock(id=123, sync_release_targets=[])
    flexmock(SyncReleaseModel).should_receive("create_with_new_run").with_args(
        status=SyncReleaseStatus.running,
        project_event_model=project_event,
        job_type=SyncReleaseJobType.pull_from_upstream,
        package_name="redis",
    ).and_return(sync_release_model, run_model).once()

    yield sync_release_model


@pytest.fixture
def sync_release_model_non_git():
    class AnityaTestProjectModel(AnityaProjectModel):
        pass

    db_project_object = flexmock(
        id=12,
        project_event_model_type=ProjectEventModelType.release,
        job_config_trigger_type=JobConfigTriggerType.release,
        project=AnityaTestProjectModel(),
    )
    project_event = (
        flexmock().should_receive("get_project_event_object").and_return(db_project_object).mock()
    )
    run_model = flexmock(PipelineModel)
    flexmock(ProjectEventModel).should_receive("get_or_create").with_args(
        type=ProjectEventModelType.release,
        event_id=12,
        commit_sha=None,
    ).and_return(project_event)
    flexmock(AnityaVersionModel).should_receive("get_or_create").with_args(
        version="7.0.3",
        project_name="redis",
        project_id=4181,
        package="redis",
    ).and_return(db_project_object)
    sync_release_model = flexmock(id=123, sync_release_targets=[])
    flexmock(SyncReleaseModel).should_receive("create_with_new_run").with_args(
        status=SyncReleaseStatus.running,
        project_event_model=project_event,
        job_type=SyncReleaseJobType.pull_from_upstream,
        package_name="redis",
    ).and_return(sync_release_model, run_model).once()

    yield sync_release_model


@pytest.fixture
def sync_release_target_models(fedora_branches):
    models = []
    for branch in fedora_branches:
        model = flexmock(status="queued", id=1234, branch=branch)
        models.append(model)
        flexmock(SyncReleaseTargetModel).should_receive("create").with_args(
            status=SyncReleaseTargetStatus.queued,
            branch=branch,
        ).and_return(model)
    yield models


def test_new_hotness_update(new_hotness_update, sync_release_model):
    model = flexmock(status="queued", id=1234, branch="main")
    flexmock(SyncReleaseTargetModel).should_receive("create").with_args(
        status=SyncReleaseTargetStatus.queued,
        branch="main",
    ).and_return(model)
    flexmock(SyncReleasePullRequestModel).should_receive("get_or_create").with_args(
        pr_id=21,
        namespace="downstream-namespace",
        repo_name="downstream-repo",
        project_url="https://src.fedoraproject.org/rpms/downstream-repo",
        target_branch=str,
        url=str,
    ).and_return(flexmock(sync_release_targets=[flexmock()]))

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
    flexmock(LocalProjectBuilder, _refresh_the_state=lambda *args: lp)
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
            submodules=[
                flexmock()
                .should_receive("update")
                .with_args(init=True, recursive=True, force=True)
                .once()
                .mock()
            ],
        ),
    )

    flexmock(Allowlist, check_and_report=True)

    service_config = ServiceConfig().get_service_config()
    flexmock(service_config).should_receive("get_project").with_args(
        "https://src.fedoraproject.org/rpms/redis",
    ).and_return(distgit_project)
    flexmock(service_config).should_receive("get_project").with_args(
        "https://github.com/packit-service/hello-world",
        False,
    ).and_return(project)

    target_project = (
        flexmock(namespace="downstream-namespace", repo="downstream-repo")
        .should_receive("get_web_url")
        .and_return("https://src.fedoraproject.org/rpms/downstream-repo")
        .mock()
    )
    pr = (
        flexmock(
            id=21,
            url="some_url",
            target_project=target_project,
            description="some-title",
        )
        .should_receive("comment")
        .mock()
    )
    flexmock(PackitAPI).should_receive("sync_release").with_args(
        dist_git_branch="main",
        tag="7.0.3",
        create_pr=True,
        local_pr_branch_suffix="update-pull_from_upstream",
        use_downstream_specfile=True,
        add_pr_instructions=True,
        resolved_bugs=["rhbz#2106196"],
        release_monitoring_project_id=4181,
        sync_acls=True,
        pr_description_footer=DistgitAnnouncement.get_announcement(),
        add_new_sources=True,
        fast_forward_merge_branches=set(),
        warn_about_koji_build_triggering_bug=False,
    ).and_return((pr, {})).once()
    flexmock(PackitAPI).should_receive("clean")

    flexmock(model).should_receive("set_status").with_args(
        status=SyncReleaseTargetStatus.running,
    ).once()
    flexmock(model).should_receive("set_downstream_pr_url").with_args(
        downstream_pr_url="some_url",
    )
    flexmock(model).should_receive("set_downstream_prs").with_args(
        downstream_prs=list,
    ).once()
    flexmock(model).should_receive("set_status").with_args(
        status=SyncReleaseTargetStatus.submitted,
    ).once()
    flexmock(model).should_receive("set_start_time").once()
    flexmock(model).should_receive("set_finished_time").once()
    flexmock(model).should_receive("set_logs").once()
    flexmock(sync_release_model).should_receive("set_status").with_args(
        status=SyncReleaseStatus.finished,
    ).once()
    sync_release_model.should_receive("get_package_name").and_return(None)

    flexmock(AddReleaseEventToDb).should_receive("db_project_object").and_return(
        flexmock(
            job_config_trigger_type=JobConfigTriggerType.release,
            id=123,
            project_event_model_type=ProjectEventModelType.release,
        ),
    )
    flexmock(group).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    flexmock(shutil).should_receive("rmtree").with_args("")

    flexmock(IsRunConditionSatisfied).should_receive("pre_check").and_return(True)

    processing_results = SteveJobs().process_message(new_hotness_update)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
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

    flexmock(Pushgateway).should_receive("push").times(1).and_return()
    service_config = ServiceConfig().get_service_config()
    flexmock(service_config).should_receive("get_project").with_args(
        "https://src.fedoraproject.org/rpms/redis",
    ).and_return(distgit_project)

    msg = (
        "We were not able to parse repo name or repo namespace from "
        "the upstream_project_url 'https://github.com/packit-service' defined in the config."
    )
    flexmock(packit_service.worker.checker.distgit).should_receive(
        "report_in_issue_repository",
    ).with_args(
        issue_repository="https://github.com/packit/issue_repository",
        service_config=service_config,
        title="Pull from upstream could not be run for update 7.0.3",
        message=msg,
        comment_to_existing=msg,
    )

    SteveJobs().process_message(new_hotness_update)


def test_new_hotness_update_non_git(new_hotness_update, sync_release_model_non_git):
    model = flexmock(status="queued", id=1234, branch="main")
    flexmock(SyncReleaseTargetModel).should_receive("create").with_args(
        status=SyncReleaseTargetStatus.queued,
        branch="main",
    ).and_return(model)
    flexmock(SyncReleasePullRequestModel).should_receive("get_or_create").with_args(
        pr_id=21,
        namespace="downstream-namespace",
        repo_name="downstream-repo",
        project_url="https://src.fedoraproject.org/rpms/downstream-repo",
        target_branch=str,
        url=str,
    ).and_return(flexmock(sync_release_targets=[flexmock()]))

    packit_yaml = (
        "{'specfile_path': 'hello-world.spec', "
        "jobs: [{trigger: release, job: pull_from_upstream, metadata: {targets:[]}}]}"
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)
    distgit_project = flexmock(
        get_files=lambda ref, recursive: [".packit.yaml"],
        get_file_content=lambda path, ref: packit_yaml,
        full_repo_name="rpms/redis",
        repo="redis",
        namespace="rpms",
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
    flexmock(LocalProjectBuilder, _refresh_the_state=lambda *args: lp)
    lp.working_dir = ""
    lp.git_project = project
    flexmock(DistGit).should_receive("local_project").and_return(lp)

    flexmock(Allowlist, check_and_report=True)

    service_config = ServiceConfig().get_service_config()
    flexmock(service_config).should_receive("get_project").with_args(
        "https://src.fedoraproject.org/rpms/redis",
        required=False,
    ).and_return(distgit_project)
    flexmock(service_config).should_receive("get_project").with_args(
        "https://src.fedoraproject.org/rpms/redis",
    ).and_return(distgit_project)

    target_project = (
        flexmock(namespace="downstream-namespace", repo="downstream-repo")
        .should_receive("get_web_url")
        .and_return("https://src.fedoraproject.org/rpms/downstream-repo")
        .mock()
    )
    pr = (
        flexmock(
            id=21,
            url="some_url",
            target_project=target_project,
            description="some-title",
        )
        .should_receive("comment")
        .mock()
    )
    flexmock(PackitAPI).should_receive("sync_release").with_args(
        dist_git_branch="main",
        versions=["7.0.3"],
        create_pr=True,
        local_pr_branch_suffix="update-pull_from_upstream",
        use_downstream_specfile=True,
        add_pr_instructions=True,
        resolved_bugs=["rhbz#2106196"],
        release_monitoring_project_id=4181,
        sync_acls=True,
        pr_description_footer=DistgitAnnouncement.get_announcement(),
        add_new_sources=True,
        fast_forward_merge_branches=set(),
        warn_about_koji_build_triggering_bug=False,
    ).and_return((pr, {})).once()
    flexmock(PackitAPI).should_receive("clean")

    flexmock(model).should_receive("set_status").with_args(
        status=SyncReleaseTargetStatus.running,
    ).once()
    flexmock(model).should_receive("set_downstream_pr_url").with_args(
        downstream_pr_url="some_url",
    )
    flexmock(model).should_receive("set_downstream_prs").with_args(
        downstream_prs=list,
    ).once()
    flexmock(model).should_receive("set_status").with_args(
        status=SyncReleaseTargetStatus.submitted,
    ).once()
    flexmock(model).should_receive("set_start_time").once()
    flexmock(model).should_receive("set_finished_time").once()
    flexmock(model).should_receive("set_logs").once()
    flexmock(sync_release_model_non_git).should_receive("set_status").with_args(
        status=SyncReleaseStatus.finished,
    ).once()
    sync_release_model_non_git.should_receive("get_package_name").and_return(None)

    flexmock(IsRunConditionSatisfied).should_receive("pre_check").and_return(True)

    flexmock(AddReleaseEventToDb).should_receive("db_project_object").and_return(
        flexmock(
            job_config_trigger_type=JobConfigTriggerType.release,
            id=123,
            project_event_model_type=ProjectEventModelType.release,
        ),
    )
    flexmock(group).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    flexmock(shutil).should_receive("rmtree").with_args("")

    processing_results = SteveJobs().process_message(new_hotness_update)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    results = run_pull_from_upstream_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert first_dict_value(results["job"])["success"]
