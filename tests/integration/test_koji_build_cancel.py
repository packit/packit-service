# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json

import pytest
from celery.canvas import Signature, group
from flexmock import flexmock
from ogr.services.github import GithubProject
from ogr.services.pagure import PagureProject
from packit.api import PackitAPI
from packit.config import Deployment, JobConfigTriggerType
from packit.exceptions import PackitException
from packit.local_project import LocalProjectBuilder
from packit.utils import commands

from packit_service import utils as service_utils
from packit_service.config import ServiceConfig
from packit_service.constants import SANDCASTLE_WORK_DIR
from packit_service.models import (
    GitBranchModel,
    KojiBuildGroupModel,
    KojiBuildTargetModel,
    PipelineModel,
    ProjectEventModel,
    ProjectEventModelType,
    PullRequestModel,
)
from packit_service.worker.checker.run_condition import IsRunConditionSatisfied
from packit_service.worker.handlers import distgit as distgit_handlers
from packit_service.worker.handlers.distgit import (
    DownstreamKojiBuildHandler,
)
from packit_service.worker.helpers.build import koji_build as koji_build_module
from packit_service.worker.helpers.build.koji_build import KojiBuildJobHelper
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.reporting import StatusReporterGithubChecks
from packit_service.worker.result import TaskResults
from packit_service.worker.tasks import (
    run_downstream_koji_build,
    run_downstream_koji_scratch_build_handler,
    run_koji_build_handler,
)
from tests.spellbook import DATA_DIR, first_dict_value, get_parameters_from_results


@pytest.fixture
def mock_distgit_pr_functionality():
    """Set up a Pagure dist-git PR environment for downstream scratch build tests.

    Mocks the Pagure project (rpms/optee_os) with Fedora CI enabled, a PR targeting
    rawhide, database models, and infrastructure (Celery, Prometheus, git).
    Returns the event dict to pass to SteveJobs().process_message().
    """
    distgit_pr_event = json.loads(
        (DATA_DIR / "fedmsg" / "pagure_pr_new.json").read_text(),
    )
    distgit_pr_event["pullrequest"]["branch"] = "rawhide"

    # Pagure project and service config
    pr_object = flexmock(target_branch="rawhide").should_receive("set_flag").mock()
    dg_project = (
        flexmock(
            PagureProject(namespace="rpms", repo="optee_os", service=flexmock(read_only=False))
        )
        .should_receive("is_private")
        .and_return(False)
        .mock()
        .should_receive("get_pr")
        .and_return(pr_object)
        .mock()
        .should_receive("get_git_urls")
        .and_return({"git": "https://src.fedoraproject.org/rpms/optee_os.git"})
        .mock()
    )
    service_config = (
        flexmock(
            enabled_projects_for_fedora_ci="https://src.fedoraproject.org/rpms/optee_os",
            fedora_ci_run_by_default=False,
            disabled_projects_for_fedora_ci=set(),
            command_handler_work_dir=SANDCASTLE_WORK_DIR,
            repository_cache="/tmp/repository-cache",
            add_repositories_to_repository_cache=False,
            deployment=Deployment.stg,
        )
        .should_receive("get_project")
        .and_return(dg_project)
        .mock()
    )
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(service_config)

    # Database models for PR event
    db_project_object = flexmock(
        id=9,
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        project_event_model_type=ProjectEventModelType.pull_request,
        project=flexmock(project_url="https://src.fedoraproject.org/rpms/optee_os"),
    )
    db_project_event = (
        flexmock().should_receive("get_project_event_object").and_return(db_project_object).mock()
    )
    flexmock(ProjectEventModel).should_receive("get_or_create").with_args(
        type=ProjectEventModelType.pull_request,
        event_id=9,
        commit_sha="abcd",
    ).and_return(flexmock())
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=2,
        namespace="rpms",
        repo_name="optee_os",
        project_url="https://src.fedoraproject.org/rpms/optee_os",
    ).and_return(db_project_object)
    flexmock(ProjectEventModel).should_receive("get_or_create").and_return(
        db_project_event,
    )

    # Infrastructure no-ops
    flexmock(PipelineModel).should_receive("create")
    flexmock(service_utils).should_receive("get_eln_packages").and_return([])
    flexmock(LocalProjectBuilder, _refresh_the_state=lambda *args: None)
    flexmock(Signature).should_receive("apply_async")
    flexmock(Pushgateway).should_receive("push").and_return()

    return distgit_pr_event


@pytest.mark.parametrize(
    "mock_pr_functionality",
    (
        [
            [
                {
                    "trigger": "pull_request",
                    "job": "upstream_koji_build",
                    "metadata": {"targets": "fedora-all", "scratch": "true"},
                },
            ],
        ]
    ),
    indirect=True,
)
def test_upstream_koji_build_cancel_running(mock_pr_functionality, monkeypatch):
    """Test that KojiBuildHandler calls cancel_running_builds.

    Simulates a GitHub check run re-request (user clicks "Re-run" on a
    koji-build check). The mock_pr_functionality fixture sets up the GitHub
    project and database models. We mock the build helper to skip the actual
    build and verify that cancel_running_builds is called before proceeding.
    """
    monkeypatch.setenv("CANCEL_RUNNING_JOBS", "1")

    # Infrastructure mocks — prevent real calls to GitHub API, Celery, and Prometheus
    flexmock(GithubProject).should_receive("get_files").and_return(["foo.spec"])
    flexmock(GithubProject).should_receive("get_files").and_return(
        ["foo.spec", ".packit.yaml"],
    )
    flexmock(GithubProject).should_receive("get_web_url").and_return(
        "https://github.com/the-namespace/the-repo",
    )
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(koji_build_module).should_receive("get_koji_targets").and_return(
        {"rawhide", "f34"},
    )
    flexmock(StatusReporterGithubChecks).should_receive("set_status")
    flexmock(group).should_receive("apply_async")
    flexmock(Pushgateway).should_receive("push").and_return()

    # Mock the build to succeed without actually running it
    flexmock(KojiBuildJobHelper).should_receive("run_koji_build").and_return(
        TaskResults(success=True, details={}),
    )

    # The key assertion: cancel_running_builds must be called exactly once
    flexmock(KojiBuildJobHelper).should_receive("cancel_running_builds").once()

    # Load a GitHub "check run re-requested" webhook and set the check name
    # so it routes to KojiBuildHandler
    check_rerun_event = json.loads(
        (DATA_DIR / "webhooks" / "github" / "checkrun_rerequested.json").read_text(),
    )
    check_rerun_event["check_run"]["name"] = "koji-build:f34"

    processing_results = SteveJobs().process_message(check_rerun_event)
    event_dict, _, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    results = run_koji_build_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert first_dict_value(results["job"])["success"]


def test_downstream_koji_scratch_build_cancel_running(mock_distgit_pr_functionality, monkeypatch):
    """Test that DownstreamKojiScratchBuildHandler calls cancel_running_builds.

    Simulates a PR on a Fedora dist-git repo (rpms/optee_os) targeting rawhide.
    The fixture sets up the Pagure environment. We mock the build execution
    (kerberos, koji CLI, model creation) and verify cancellation is invoked.
    """
    monkeypatch.setenv("CANCEL_RUNNING_JOBS", "1")

    # Mocks for the build execution flow inside _run():
    # kerberos auth, DB model creation, koji CLI call, and output parsing
    flexmock(PackitAPI).should_receive("init_kerberos_ticket")
    koji_build_target = flexmock(
        id=123,
        target="main",
        status="queued",
        set_status=lambda x: None,
        set_task_id=lambda x: None,
        set_web_url=lambda x: None,
        set_build_logs_urls=lambda x: None,
        set_data=lambda x: None,
        set_build_submission_stdout=lambda x: None,
    )
    flexmock(KojiBuildTargetModel).should_receive("create").and_return(koji_build_target)
    flexmock(KojiBuildGroupModel).should_receive("create").and_return(
        flexmock(grouped_targets=[koji_build_target]),
    )
    # mock actuall "koji build..." shell command
    flexmock(commands).should_receive("run_command_remote").and_return(
        flexmock(stdout="some output"),
    )
    flexmock(distgit_handlers).should_receive("get_koji_task_id_and_url_from_stdout").and_return(
        (123, "koji-web-url"),
    )

    # The key assertion: cancel_running_builds must be called exactly once
    flexmock(KojiBuildJobHelper).should_receive("cancel_running_builds").once()

    processing_results = SteveJobs().process_message(mock_distgit_pr_functionality)
    event_dict, _, job_config, package_config = get_parameters_from_results(
        processing_results[:1],
    )
    results = run_downstream_koji_scratch_build_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]


def test_downstream_koji_build_cancel_running(monkeypatch):
    """Test that DownstreamKojiBuildHandler calls cancel_running_builds.

    Simulates a commit push to a Fedora dist-git repo (rpms/buildah) on the main
    branch. We short-circuit _run() by making _get_or_create_koji_group_model raise
    PackitException — this is caught by the handler's try/except and returns success,
    but happens after cancel_running_builds is called.
    """
    monkeypatch.setenv("CANCEL_RUNNING_JOBS", "1")

    # Pagure project with a koji_build job in .packit.yaml
    packit_yaml = (
        "{'specfile_path': 'buildah.spec',"
        "'jobs': [{'trigger': 'commit', 'job': 'koji_build', 'allowed_committers':"
        " ['rhcontainerbot']}],"
        "'downstream_package_name': 'buildah'}"
    )
    pagure_project = flexmock(
        PagureProject,
        full_repo_name="rpms/buildah",
        get_web_url=lambda: "https://src.fedoraproject.org/rpms/buildah",
        default_branch="main",
    )
    pagure_project.should_receive("get_files").with_args(
        ref="main",
        filter_regex=r".+\.spec$",
    ).and_return(["buildah.spec"])
    pagure_project.should_receive("get_file_content").with_args(
        path=".packit.yaml",
        ref="main",
        headers=dict,
    ).and_return(packit_yaml)
    pagure_project.should_receive("get_files").with_args(
        ref="main",
        recursive=False,
    ).and_return(["buildah.spec", ".packit.yaml"])

    # Database models for branch push event
    db_project_object = flexmock(
        id=9,
        job_config_trigger_type=JobConfigTriggerType.commit,
        project_event_model_type=ProjectEventModelType.branch_push,
    )
    db_project_event = (
        flexmock().should_receive("get_project_event_object").and_return(db_project_object).mock()
    )
    flexmock(ProjectEventModel).should_receive("get_or_create").with_args(
        type=ProjectEventModelType.branch_push,
        event_id=9,
        commit_sha="abcd",
    ).and_return(flexmock())
    flexmock(GitBranchModel).should_receive("get_or_create").with_args(
        branch_name="main",
        namespace="rpms",
        repo_name="buildah",
        project_url="https://src.fedoraproject.org/rpms/buildah",
    ).and_return(db_project_object)
    flexmock(ProjectEventModel).should_receive("get_or_create").and_return(
        db_project_event,
    )

    # Infrastructure no-ops
    flexmock(PipelineModel).should_receive("create")
    flexmock(LocalProjectBuilder, _refresh_the_state=lambda *args: None)
    flexmock(group).should_receive("apply_async")
    flexmock(Pushgateway).should_receive("push").and_return()
    # Skip the run-condition checker (it tries to read the specfile)
    flexmock(IsRunConditionSatisfied).should_receive("pre_check").and_return(True)

    # Short-circuit _run() right after cancel_running_builds — the handler catches
    # PackitException from _get_or_create_koji_group_model and returns success,
    # so we skip the entire build flow
    flexmock(DownstreamKojiBuildHandler).should_receive(
        "_get_or_create_koji_group_model",
    ).and_raise(PackitException, "mock error")

    # The key assertion: cancel_running_builds must be called exactly once
    flexmock(KojiBuildJobHelper).should_receive("cancel_running_builds").once()

    # Load a dist-git commit push event for rpms/buildah
    distgit_commit = json.loads(
        (DATA_DIR / "fedmsg" / "distgit_commit.json").read_text(),
    )

    processing_results = SteveJobs().process_message(distgit_commit)
    event_dict, _, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    results = run_downstream_koji_build(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]
