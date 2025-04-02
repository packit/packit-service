# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
import json
import shutil

import pytest
from celery.app.task import Context, Task
from celery.canvas import group
from flexmock import flexmock
from github.MainClass import Github
from packit.api import PackitAPI
from packit.config import JobConfigTriggerType
from packit.config.aliases import get_branches
from packit.distgit import DistGit
from packit.exceptions import PackitDownloadFailedException
from packit.local_project import LocalProject, LocalProjectBuilder
from packit.pkgtool import PkgTool

from packit_service import sentry_integration
from packit_service.config import ServiceConfig
from packit_service.constants import (
    TASK_ACCEPTED,
)
from packit_service.models import (
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
from packit_service.service.urls import get_propose_downstream_info_url
from packit_service.worker.allowlist import Allowlist
from packit_service.worker.handlers.distgit import AbstractSyncReleaseHandler
from packit_service.worker.helpers.sync_release.propose_downstream import (
    ProposeDownstreamJobHelper,
)
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.reporting import BaseCommitStatus
from packit_service.worker.reporting.news import DistgitAnnouncement
from packit_service.worker.tasks import run_propose_downstream_handler
from tests.spellbook import first_dict_value, get_parameters_from_results


@pytest.fixture(scope="module")
def fedora_branches():
    return sorted(get_branches("fedora-all"))


@pytest.fixture
def sync_release_pr_model():
    return flexmock(sync_release_targets=[flexmock(), flexmock()])


@pytest.fixture
def propose_downstream_model(sync_release_pr_model):
    db_project_object = flexmock(
        id=12,
        project_event_model_type=ProjectEventModelType.release,
        job_config_trigger_type=JobConfigTriggerType.release,
    )
    db_project_event = (
        flexmock().should_receive("get_project_event_object").and_return(db_project_object).mock()
    )
    run_model = flexmock(PipelineModel)
    flexmock(ProjectEventModel).should_receive("get_or_create").with_args(
        type=ProjectEventModelType.release,
        event_id=12,
        commit_sha="123456",
    ).and_return(db_project_event)
    flexmock(ProjectReleaseModel).should_receive("get_or_create").with_args(
        tag_name="0.3.0",
        namespace="packit-service",
        repo_name="hello-world",
        project_url="https://github.com/packit-service/hello-world",
        commit_hash="123456",
    ).and_return(db_project_object).twice()
    propose_downstream_model = flexmock(id=123, sync_release_targets=[])
    flexmock(SyncReleaseModel).should_receive("create_with_new_run").with_args(
        status=SyncReleaseStatus.running,
        project_event_model=db_project_event,
        job_type=SyncReleaseJobType.propose_downstream,
        package_name="hello-world",
    ).and_return(propose_downstream_model, run_model).once()
    flexmock(SyncReleasePullRequestModel).should_receive("get_or_create").with_args(
        pr_id=21,
        namespace="downstream-namespace",
        repo_name="downstream-repo",
        project_url="https://src.fedoraproject.org/rpms/downstream-repo",
        target_branch=str,
        url=str,
    ).and_return(sync_release_pr_model)

    flexmock(ProposeDownstreamJobHelper).should_receive(
        "report_status_to_all",
    ).with_args(
        description=TASK_ACCEPTED,
        state=BaseCommitStatus.pending,
        url="",
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()
    yield propose_downstream_model


class ProposeDownstreamTargetModel:
    def __init__(self, status, id_, branch):
        self.status = status
        self.id = id_
        self.branch = branch

    def set_status(self, status):
        self.status = status

    def set_start_time(self):
        pass

    def set_finished_time(self):
        pass

    def set_logs(self):
        pass

    def set_downstream_pr_url(self, downstream_pr_url):
        pass

    def set_downstream_prs(self, downstream_prs):
        pass


@pytest.fixture
def propose_downstream_target_models(fedora_branches):
    models = []
    for i, branch in enumerate(fedora_branches):
        model = ProposeDownstreamTargetModel(status="queued", id_=i, branch=branch)
        models.append(model)
        flexmock(SyncReleaseTargetModel).should_receive("create").with_args(
            status=SyncReleaseTargetStatus.queued,
            branch=branch,
        ).and_return(model)
    yield models


def test_dist_git_push_release_handle(
    github_release_webhook,
    propose_downstream_model,
    sync_release_pr_model,
):
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
    ).and_return(sync_release_pr_model)

    packit_yaml = (
        "{'specfile_path': 'hello-world.spec'"
        ", jobs: [{trigger: release, job: propose_downstream, metadata: {targets:[]}}]}"
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)
    project = flexmock(
        get_file_content=lambda path, ref: packit_yaml,
        full_repo_name="packit-service/hello-world",
        repo="hello-world",
        namespace="packit-service",
        get_files=lambda ref, recursive: ["packit.yaml"],
        get_sha_from_tag=lambda tag_name: "123456",
        get_web_url=lambda: "https://github.com/packit/hello-world",
        is_private=lambda: False,
        default_branch="main",
    )
    lp = flexmock()
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
    ServiceConfig().get_service_config().get_project = lambda url, required=True: project
    target_project = (
        flexmock(namespace="downstream-namespace", repo="downstream-repo")
        .should_receive("get_web_url")
        .and_return("https://src.fedoraproject.org/rpms/downstream-repo")
        .mock()
    )
    pr = (
        flexmock(id=21, url="some_url", target_project=target_project, description="")
        .should_receive("comment")
        .mock()
    )
    flexmock(PackitAPI).should_receive("sync_release").with_args(
        dist_git_branch="main",
        tag="0.3.0",
        create_pr=True,
        local_pr_branch_suffix="update-propose_downstream",
        use_downstream_specfile=False,
        add_pr_instructions=True,
        resolved_bugs=[],
        release_monitoring_project_id=None,
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
        downstream_prs=[sync_release_pr_model],
    ).once()
    flexmock(model).should_receive("set_status").with_args(
        status=SyncReleaseTargetStatus.submitted,
    ).once()
    flexmock(model).should_receive("set_start_time").once()
    flexmock(model).should_receive("set_finished_time").once()
    flexmock(model).should_receive("set_logs").once()
    flexmock(propose_downstream_model).should_receive("set_status").with_args(
        status=SyncReleaseStatus.finished,
    ).once()

    flexmock(group).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    flexmock(shutil).should_receive("rmtree").with_args("")

    url = get_propose_downstream_info_url(model.id)
    flexmock(ProposeDownstreamJobHelper).should_receive(
        "report_status_for_branch",
    ).with_args(
        branch="main",
        description="Starting propose downstream...",
        state=BaseCommitStatus.running,
        url=url,
    ).once()

    flexmock(ProposeDownstreamJobHelper).should_receive(
        "report_status_for_branch",
    ).with_args(
        branch="main",
        description="Propose downstream finished successfully.",
        state=BaseCommitStatus.success,
        url=url,
    ).once()

    processing_results = SteveJobs().process_message(github_release_webhook)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    results = run_propose_downstream_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert first_dict_value(results["job"])["success"]


def test_dist_git_push_release_handle_fast_forward_branches(
    github_release_webhook,
    propose_downstream_model,
    sync_release_pr_model,
):
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
    ).and_return(sync_release_pr_model)

    packit_yaml = (
        "{'specfile_path': 'hello-world.spec'"
        ", jobs: [{trigger: release, job: propose_downstream, metadata: {targets:[]}}]}"
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)
    project = flexmock(
        get_file_content=lambda path, ref: packit_yaml,
        full_repo_name="packit-service/hello-world",
        repo="hello-world",
        namespace="packit-service",
        get_files=lambda ref, recursive: ["packit.yaml"],
        get_sha_from_tag=lambda tag_name: "123456",
        get_web_url=lambda: "https://github.com/packit/hello-world",
        is_private=lambda: False,
        default_branch="main",
    )
    lp = flexmock()
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
    ServiceConfig().get_service_config().get_project = lambda url, required=True: project
    target_project = (
        flexmock(namespace="downstream-namespace", repo="downstream-repo")
        .should_receive("get_web_url")
        .and_return("https://src.fedoraproject.org/rpms/downstream-repo")
        .mock()
    )
    pr = (
        flexmock(id=21, url="some_url", target_project=target_project, description="")
        .should_receive("comment")
        .mock()
    )
    second_pr = flexmock(id=22, url="some_url_2", target_project=target_project, description="")
    second_pr_model = flexmock(sync_release_targets=[flexmock(), flexmock()])
    flexmock(SyncReleasePullRequestModel).should_receive("get_or_create").with_args(
        pr_id=22,
        namespace="downstream-namespace",
        repo_name="downstream-repo",
        project_url="https://src.fedoraproject.org/rpms/downstream-repo",
        target_branch="rawhide",
        is_fast_forward=True,
        url="some_url_2",
    ).and_return(second_pr_model)

    flexmock(PackitAPI).should_receive("sync_release").with_args(
        dist_git_branch="main",
        tag="0.3.0",
        create_pr=True,
        local_pr_branch_suffix="update-propose_downstream",
        use_downstream_specfile=False,
        add_pr_instructions=True,
        resolved_bugs=[],
        release_monitoring_project_id=None,
        sync_acls=True,
        pr_description_footer=DistgitAnnouncement.get_announcement(),
        add_new_sources=True,
        fast_forward_merge_branches=set(),
        warn_about_koji_build_triggering_bug=False,
    ).and_return((pr, {"rawhide": second_pr})).once()
    flexmock(PackitAPI).should_receive("clean")

    flexmock(model).should_receive("set_status").with_args(
        status=SyncReleaseTargetStatus.running,
    ).once()
    flexmock(model).should_receive("set_downstream_pr_url").with_args(
        downstream_pr_url="some_url",
    )
    flexmock(model).should_receive("set_downstream_prs").with_args(
        downstream_prs=[sync_release_pr_model, second_pr_model],
    ).once()
    flexmock(model).should_receive("set_status").with_args(
        status=SyncReleaseTargetStatus.submitted,
    ).once()
    flexmock(model).should_receive("set_start_time").once()
    flexmock(model).should_receive("set_finished_time").once()
    flexmock(model).should_receive("set_logs").once()
    flexmock(propose_downstream_model).should_receive("set_status").with_args(
        status=SyncReleaseStatus.finished,
    ).once()

    flexmock(group).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    flexmock(shutil).should_receive("rmtree").with_args("")

    url = get_propose_downstream_info_url(model.id)
    flexmock(ProposeDownstreamJobHelper).should_receive(
        "report_status_for_branch",
    ).with_args(
        branch="main",
        description="Starting propose downstream...",
        state=BaseCommitStatus.running,
        url=url,
    ).once()

    flexmock(ProposeDownstreamJobHelper).should_receive(
        "report_status_for_branch",
    ).with_args(
        branch="main",
        description="Propose downstream finished successfully.",
        state=BaseCommitStatus.success,
        url=url,
    ).once()

    processing_results = SteveJobs().process_message(github_release_webhook)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
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
    sync_release_pr_model,
):
    packit_yaml = (
        "{'specfile_path': 'hello-world.spec'"
        ", jobs: [{trigger: release, job: propose_downstream, "
        "metadata: {targets:[], dist-git-branch: fedora-all}}]}"
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)
    project = flexmock(
        get_file_content=lambda path, ref: packit_yaml,
        full_repo_name="packit-service/hello-world",
        repo="hello-world",
        namespace="packit-service",
        get_files=lambda ref, recursive: ["packit.yaml"],
        get_sha_from_tag=lambda tag_name: "123456",
        get_web_url=lambda: "https://github.com/packit/hello-world",
        is_private=lambda: False,
        default_branch="main",
    )
    flexmock(LocalProjectBuilder, _refresh_the_state=lambda *args: flexmock())
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(LocalProject).should_receive("git_repo").and_return(
        flexmock(
            head=flexmock()
            .should_receive("reset")
            .with_args("HEAD", index=True, working_tree=True)
            .times(len(fedora_branches))
            .mock(),
            git=flexmock(clear_cache=lambda: None),
            submodules=[
                flexmock()
                .should_receive("update")
                .with_args(init=True, recursive=True, force=True)
                .times(len(fedora_branches))
                .mock()
            ],
        ),
    )

    flexmock(Allowlist, check_and_report=True)
    ServiceConfig().get_service_config().get_project = lambda url, required=True: project

    for model in propose_downstream_target_models:
        url = get_propose_downstream_info_url(model.id)
        flexmock(model).should_receive("set_status").with_args(
            status=SyncReleaseTargetStatus.running,
        ).once()
        flexmock(model).should_receive("set_downstream_pr_url").with_args(
            downstream_pr_url="some_url",
        )
        flexmock(model).should_receive("set_downstream_prs").with_args(
            downstream_prs=[sync_release_pr_model],
        ).once()
        flexmock(model).should_receive("set_status").with_args(
            status=SyncReleaseTargetStatus.submitted,
        ).once()
        flexmock(model).should_receive("set_start_time").once()
        flexmock(model).should_receive("set_finished_time").once()
        flexmock(model).should_receive("set_logs").once()
        target_project = (
            flexmock(namespace="downstream-namespace", repo="downstream-repo")
            .should_receive("get_web_url")
            .and_return("https://src.fedoraproject.org/rpms/downstream-repo")
            .mock()
        )
        pr = (
            flexmock(id=21, url="some_url", target_project=target_project, description="")
            .should_receive("comment")
            .mock()
        )
        flexmock(PackitAPI).should_receive("sync_release").with_args(
            dist_git_branch=model.branch,
            tag="0.3.0",
            create_pr=True,
            local_pr_branch_suffix="update-propose_downstream",
            use_downstream_specfile=False,
            add_pr_instructions=True,
            resolved_bugs=[],
            release_monitoring_project_id=None,
            sync_acls=True,
            pr_description_footer=DistgitAnnouncement.get_announcement(),
            add_new_sources=True,
            fast_forward_merge_branches=set(),
            warn_about_koji_build_triggering_bug=False,
        ).and_return((pr, {})).once()

        flexmock(ProposeDownstreamJobHelper).should_receive(
            "report_status_for_branch",
        ).with_args(
            branch=model.branch,
            description="Starting propose downstream...",
            state=BaseCommitStatus.running,
            url=url,
        ).once()

        flexmock(ProposeDownstreamJobHelper).should_receive(
            "report_status_for_branch",
        ).with_args(
            branch=model.branch,
            description="Propose downstream finished successfully.",
            state=BaseCommitStatus.success,
            url=url,
        ).once()

    flexmock(propose_downstream_model).should_receive("set_status").with_args(
        status=SyncReleaseStatus.finished,
    ).once()

    flexmock(PkgTool).should_receive("clone").and_return(None)

    flexmock(AddReleaseEventToDb).should_receive("db_project_object").and_return(
        flexmock(
            job_config_trigger_type=JobConfigTriggerType.release,
            id=123,
            project_event_model_type=ProjectEventModelType.release,
        ),
    )
    flexmock(group).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    from packit_service.worker.handlers.distgit import shutil

    flexmock(shutil).should_receive("rmtree").and_return()

    processing_results = SteveJobs().process_message(github_release_webhook)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
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
    sync_release_pr_model,
):
    packit_yaml = (
        "{'specfile_path': 'hello-world.spec'"
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
            get_files=lambda ref, recursive: ["packit.yaml"],
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
    lp = flexmock()
    flexmock(LocalProjectBuilder, _refresh_the_state=lambda *args: lp)
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(LocalProject).should_receive("git_repo").and_return(
        flexmock(
            head=flexmock()
            .should_receive("reset")
            .with_args("HEAD", index=True, working_tree=True)
            .times(len(fedora_branches))
            .mock(),
            git=flexmock(clear_cache=lambda: None),
            submodules=[
                flexmock()
                .should_receive("update")
                .with_args(init=True, recursive=True, force=True)
                .times(len(fedora_branches))
                .mock()
            ],
        ),
    )
    flexmock(Allowlist, check_and_report=True)
    ServiceConfig().get_service_config().get_project = lambda url, required=True: project
    failed_branch = fedora_branches[1]

    for model in propose_downstream_target_models:
        url = get_propose_downstream_info_url(model.id)
        flexmock(model).should_receive("set_start_time").once()
        flexmock(model).should_receive("set_finished_time").once()
        flexmock(model).should_receive("set_logs").once()

        flexmock(ProposeDownstreamJobHelper).should_receive(
            "report_status_for_branch",
        ).with_args(
            branch=model.branch,
            description="Starting propose downstream...",
            state=BaseCommitStatus.running,
            url=url,
        ).once()

        if model.branch != failed_branch:
            flexmock(model).should_receive("set_downstream_pr_url").with_args(
                downstream_pr_url="some_url",
            )
            flexmock(model).should_receive("set_downstream_prs").with_args(
                downstream_prs=[sync_release_pr_model],
            )
            target_project = (
                flexmock(namespace="downstream-namespace", repo="downstream-repo")
                .should_receive("get_web_url")
                .and_return("https://src.fedoraproject.org/rpms/downstream-repo")
                .mock()
            )
            pr = (
                flexmock(id=21, url="some_url", target_project=target_project, description="")
                .should_receive("comment")
                .mock()
            )
            flexmock(PackitAPI).should_receive("sync_release").with_args(
                dist_git_branch=model.branch,
                tag="0.3.0",
                create_pr=True,
                local_pr_branch_suffix="update-propose_downstream",
                use_downstream_specfile=False,
                add_pr_instructions=True,
                resolved_bugs=[],
                release_monitoring_project_id=None,
                sync_acls=True,
                pr_description_footer=DistgitAnnouncement.get_announcement(),
                add_new_sources=True,
                fast_forward_merge_branches=set(),
                warn_about_koji_build_triggering_bug=False,
            ).and_return((pr, {})).once()
            flexmock(ProposeDownstreamJobHelper).should_receive(
                "report_status_for_branch",
            ).with_args(
                branch=model.branch,
                description="Propose downstream finished successfully.",
                state=BaseCommitStatus.success,
                url=url,
            ).once()
        else:
            flexmock(PackitAPI).should_receive("sync_release").with_args(
                dist_git_branch=model.branch,
                tag="0.3.0",
                create_pr=True,
                local_pr_branch_suffix="update-propose_downstream",
                use_downstream_specfile=False,
                add_pr_instructions=True,
                resolved_bugs=[],
                release_monitoring_project_id=None,
                sync_acls=True,
                pr_description_footer=DistgitAnnouncement.get_announcement(),
                add_new_sources=True,
                fast_forward_merge_branches=set(),
                warn_about_koji_build_triggering_bug=False,
            ).and_raise(Exception, f"Failed {model.branch}").once()
            flexmock(ProposeDownstreamJobHelper).should_receive(
                "report_status_for_branch",
            ).with_args(
                branch=model.branch,
                description=f"Propose downstream failed: Failed {model.branch}",
                state=BaseCommitStatus.failure,
                url=url,
            ).once()

    flexmock(propose_downstream_model).should_receive("set_status").with_args(
        status=SyncReleaseStatus.error,
    ).once()

    flexmock(PkgTool).should_receive("clone").and_return(None)

    flexmock(sentry_integration).should_receive("send_to_sentry").and_return().once()
    flexmock(AddReleaseEventToDb).should_receive("db_project_object").and_return(
        flexmock(
            job_config_trigger_type=JobConfigTriggerType.release,
            id=123,
            project_event_model_type=ProjectEventModelType.release,
        ),
    )

    flexmock(group).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    from packit_service.worker.handlers.distgit import shutil

    flexmock(shutil).should_receive("rmtree").and_return()

    processing_results = SteveJobs().process_message(github_release_webhook)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
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
        "{'specfile_path': 'hello-world.spec'"
        ", jobs: [{trigger: release, job: propose_downstream, "
        "metadata: {targets:[], dist-git-branch: fedora-all}}]}"
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)
    table_content = ""
    for model in sorted(
        propose_downstream_target_models,
        key=lambda model: model.branch,
    ):
        dashboard_url = get_propose_downstream_info_url(model.id)
        table_content += (
            "<tr>"
            f"<td><code>{model.branch}</code></td>"
            f'<td>See <a href="{dashboard_url}">{dashboard_url}</a></td>'
            "</tr>\n"
        )
    project = (
        flexmock(
            get_file_content=lambda path, ref: packit_yaml,
            full_repo_name="packit-service/hello-world",
            repo="hello-world",
            namespace="packit-service",
            get_files=lambda ref, recursive: ["packit.yaml"],
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
            "<table>"
            "<tr>"
            "<th>dist-git branch</th>"
            "<th>error</th>"
            "</tr>"
            f"{table_content}</table>\n\n\n"
            "You can retrigger the update by adding a comment (`/packit propose-downstream`)"
            " into this issue.\n",
        )
        .once()
        .and_return(flexmock(id="1", url="an url"))
        .mock()
    )
    project.should_receive("get_issue_list").and_return([])
    lp = flexmock()
    flexmock(LocalProjectBuilder, _refresh_the_state=lambda *args: lp)
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
            submodules=[
                flexmock()
                .should_receive("update")
                .with_args(init=True, recursive=True, force=True)
                .times(len(fedora_branches))
                .mock()
            ],
        ),
    )

    flexmock(Allowlist, check_and_report=True)
    ServiceConfig().get_service_config().get_project = lambda url, required=True: project

    flexmock(PackitAPI).should_receive("sync_release").and_raise(
        Exception,
        "Failed",
    ).times(len(fedora_branches))
    flexmock(AddReleaseEventToDb).should_receive("db_project_object").and_return(
        flexmock(
            job_config_trigger_type=JobConfigTriggerType.release,
            id=123,
            project_event_model_type=ProjectEventModelType.release,
        ),
    )

    for model in propose_downstream_target_models:
        url = get_propose_downstream_info_url(model.id)
        flexmock(model).should_receive("set_start_time").once()
        flexmock(model).should_receive("set_finished_time").once()
        flexmock(model).should_receive("set_logs").once()
        flexmock(ProposeDownstreamJobHelper).should_receive(
            "report_status_for_branch",
        ).with_args(
            branch=model.branch,
            description="Starting propose downstream...",
            state=BaseCommitStatus.running,
            url=url,
        ).once()

        flexmock(ProposeDownstreamJobHelper).should_receive(
            "report_status_for_branch",
        ).with_args(
            branch=model.branch,
            description="Propose downstream failed: Failed",
            state=BaseCommitStatus.failure,
            url=url,
        ).once()

    flexmock(propose_downstream_model).should_receive("set_status").with_args(
        status=SyncReleaseStatus.error,
    ).once()

    flexmock(sentry_integration).should_receive("send_to_sentry").and_return().times(
        len(fedora_branches),
    )
    flexmock(shutil).should_receive("rmtree").with_args("")
    flexmock(group).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    processing_results = SteveJobs().process_message(github_release_webhook)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    results = run_propose_downstream_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert not first_dict_value(results["job"])["success"]


def test_retry_propose_downstream_task(
    github_release_webhook,
    propose_downstream_model,
):
    model = flexmock(status="queued", id=1234, branch="main")
    flexmock(SyncReleaseTargetModel).should_receive("create").with_args(
        status=SyncReleaseTargetStatus.queued,
        branch="main",
    ).and_return(model)

    packit_yaml = (
        "{'specfile_path': 'hello-world.spec'"
        ", jobs: [{trigger: release, job: propose_downstream, metadata: {targets:[]}}]}"
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)
    project = flexmock(
        get_file_content=lambda path, ref: packit_yaml,
        full_repo_name="packit-service/hello-world",
        repo="hello-world",
        namespace="packit-service",
        get_files=lambda ref, recursive: ["packit.yaml"],
        get_sha_from_tag=lambda tag_name: "123456",
        get_web_url=lambda: "https://github.com/packit/hello-world",
        is_private=lambda: False,
        default_branch="main",
    )

    lp = flexmock()
    flexmock(LocalProjectBuilder, _refresh_the_state=lambda *args: lp)
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
    ServiceConfig().get_service_config().get_project = lambda url, required=True: project

    flexmock(AddReleaseEventToDb).should_receive("db_project_object").and_return(
        flexmock(
            job_config_trigger_type=JobConfigTriggerType.release,
            id=123,
            project_event_model_type=ProjectEventModelType.release,
        ),
    )
    flexmock(group).should_receive("apply_async").once()

    flexmock(PackitAPI).should_receive("sync_release").with_args(
        dist_git_branch="main",
        tag="0.3.0",
        create_pr=True,
        local_pr_branch_suffix="update-propose_downstream",
        use_downstream_specfile=False,
        add_pr_instructions=True,
        resolved_bugs=[],
        release_monitoring_project_id=None,
        sync_acls=True,
        pr_description_footer=DistgitAnnouncement.get_announcement(),
        add_new_sources=True,
        fast_forward_merge_branches=set(),
        warn_about_koji_build_triggering_bug=False,
    ).and_raise(
        PackitDownloadFailedException,
        "Failed to download source from example.com",
    ).once()

    flexmock(model).should_receive("set_status").with_args(
        status=SyncReleaseTargetStatus.running,
    ).once()
    flexmock(model).should_receive("set_status").with_args(
        status=SyncReleaseTargetStatus.retry,
    ).once()
    flexmock(model).should_receive("set_start_time").once()
    flexmock(model).should_receive("set_finished_time").once()
    flexmock(model).should_receive("set_logs").once()

    flexmock(shutil).should_receive("rmtree").with_args("")
    flexmock(Task).should_receive("retry").once().and_return()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    url = get_propose_downstream_info_url(model.id)
    flexmock(ProposeDownstreamJobHelper).should_receive(
        "report_status_for_branch",
    ).with_args(
        branch="main",
        description="Starting propose downstream...",
        state=BaseCommitStatus.running,
        url=url,
    ).once()
    flexmock(ProposeDownstreamJobHelper).should_receive(
        "report_status_to_all",
    ).with_args(
        description="Propose downstream is being retried because "
        "we were not able yet to download the archive. ",
        state=BaseCommitStatus.pending,
        url="",
    ).once()

    processing_results = SteveJobs().process_message(github_release_webhook)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    results = run_propose_downstream_handler(event_dict, package_config, job_config)

    assert first_dict_value(results["job"])["success"]  # yes, success, see #1140
    assert "Not able to download" in first_dict_value(results["job"])["details"]["msg"]


def test_dont_retry_propose_downstream_task(
    github_release_webhook,
    propose_downstream_model,
):
    model = ProposeDownstreamTargetModel(status="queued", id_=1234, branch="main")
    flexmock(SyncReleaseTargetModel).should_receive("create").with_args(
        status=SyncReleaseTargetStatus.queued,
        branch="main",
    ).and_return(model)
    packit_yaml = (
        "{'specfile_path': 'hello-world.spec'"
        ", jobs: [{trigger: release, job: propose_downstream, metadata: {targets:[]}}]}"
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)
    project = (
        flexmock(
            get_file_content=lambda path, ref: packit_yaml,
            full_repo_name="packit-service/hello-world",
            repo="hello-world",
            namespace="packit-service",
            get_files=lambda ref, recursive: ["packit.yaml"],
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

    lp = flexmock()
    flexmock(LocalProjectBuilder, _refresh_the_state=lambda *args: lp)
    lp.git_project = project
    lp.git_url = "https://src.fedoraproject.org/rpms/hello-world.git"
    lp.working_dir = ""
    flexmock(DistGit).should_receive("local_project").and_return(lp)

    flexmock(Allowlist, check_and_report=True)
    ServiceConfig().get_service_config().get_project = lambda url, required=True: project

    flexmock(AddReleaseEventToDb).should_receive("db_project_object").and_return(
        flexmock(
            job_config_trigger_type=JobConfigTriggerType.release,
            id=123,
            project_event_model_type=ProjectEventModelType.release,
        ),
    )
    flexmock(group).should_receive("apply_async").once()

    flexmock(PackitAPI).should_receive("sync_release").with_args(
        dist_git_branch="main",
        tag="0.3.0",
        create_pr=True,
        local_pr_branch_suffix="update-propose_downstream",
        use_downstream_specfile=False,
        add_pr_instructions=True,
        resolved_bugs=[],
        release_monitoring_project_id=None,
        sync_acls=True,
        pr_description_footer=DistgitAnnouncement.get_announcement(),
        add_new_sources=True,
        fast_forward_merge_branches=set(),
        warn_about_koji_build_triggering_bug=False,
    ).and_raise(
        PackitDownloadFailedException,
        "Failed to download source from example.com",
    ).once()

    flexmock(model).should_receive("set_start_time").once()
    flexmock(model).should_receive("set_finished_time").once()
    flexmock(model).should_receive("set_logs").once()
    flexmock(propose_downstream_model).should_receive("set_status").with_args(
        status=SyncReleaseStatus.error,
    ).once()

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
    flexmock(Context, retries=6)
    flexmock(shutil).should_receive("rmtree").with_args("")
    flexmock(Task).should_receive("retry").never()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    url = get_propose_downstream_info_url(model.id)
    flexmock(ProposeDownstreamJobHelper).should_receive(
        "report_status_for_branch",
    ).with_args(
        branch="main",
        description="Starting propose downstream...",
        state=BaseCommitStatus.running,
        url=url,
    ).once()
    flexmock(ProposeDownstreamJobHelper).should_receive(
        "report_status_for_branch",
    ).with_args(
        branch="main",
        description="Propose downstream failed: Failed to download source from example.com",
        state=BaseCommitStatus.failure,
        url=url,
    ).once()

    processing_results = SteveJobs().process_message(github_release_webhook)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    results = run_propose_downstream_handler(event_dict, package_config, job_config)

    assert not first_dict_value(results["job"])["success"]


def test_dist_git_push_release_failed_issue_creation_disabled(
    github_release_webhook,
    fedora_branches,
    propose_downstream_model,
    propose_downstream_target_models,
):
    packit_yaml = (
        "{'specfile_path': 'hello-world.spec'"
        ", jobs: ["
        "{trigger: release, job: propose_downstream, "
        "targets:[], dist_git_branches: fedora-all, notifications: "
        "{failure_issue: {create: false}}}]}"
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)
    table_content = ""
    for model in propose_downstream_target_models:
        model.set_status(SyncReleaseTargetStatus.error)

    for model in sorted(
        propose_downstream_target_models,
        key=lambda model: model.branch,
    ):
        table_content += f"| `{model.branch}` | See {get_propose_downstream_info_url(model.id)} |\n"
    project = (
        flexmock(
            get_file_content=lambda path, ref: packit_yaml,
            full_repo_name="packit-service/hello-world",
            repo="hello-world",
            namespace="packit-service",
            get_files=lambda ref, recursive: ["packit.yaml"],
            get_sha_from_tag=lambda tag_name: "123456",
            get_web_url=lambda: "https://github.com/packit/hello-world",
            is_private=lambda: False,
            default_branch="main",
        )
        .should_receive("create_issue")
        .never()
        .mock()
    )
    lp = flexmock()
    flexmock(LocalProjectBuilder, _refresh_the_state=lambda *args: lp)
    lp.git_project = project
    lp.git_url = "https://src.fedoraproject.org/rpms/hello-world.git"
    lp.working_dir = ""
    flexmock(DistGit).should_receive("local_project").and_return(lp)
    # reset of the upstream repo
    flexmock(LocalProject).should_receive("git_repo").and_return(
        flexmock(
            head=flexmock(),
            git=flexmock(clear_cache=lambda: None),
        ),
    )

    flexmock(Allowlist, check_and_report=True)
    ServiceConfig().get_service_config().get_project = lambda url, required=True: project

    flexmock(AddReleaseEventToDb).should_receive("db_project_object").and_return(
        flexmock(
            job_config_trigger_type=JobConfigTriggerType.release,
            id=123,
            project_event_model_type=ProjectEventModelType.release,
        ),
    )

    for _ in propose_downstream_target_models:
        flexmock(AbstractSyncReleaseHandler).should_receive(
            "run_for_target",
        ).and_return("some error")
    flexmock(propose_downstream_model).should_receive("set_status").with_args(
        status=SyncReleaseStatus.error,
    ).once()

    flexmock(shutil).should_receive("rmtree").with_args("")
    flexmock(group).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    processing_results = SteveJobs().process_message(github_release_webhook)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    results = run_propose_downstream_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert not first_dict_value(results["job"])["success"]
