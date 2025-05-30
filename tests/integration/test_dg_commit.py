# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json

import pytest
from celery.canvas import group
from flexmock import flexmock
from ogr.abstract import PRStatus
from ogr.services.github import GithubProject
from ogr.services.pagure import PagureProject
from packit.api import PackitAPI
from packit.config import (
    CommonPackageConfig,
    Deployment,
    JobConfig,
    JobConfigTriggerType,
    JobType,
    PackageConfig,
)
from packit.distgit import DistGit
from packit.exceptions import PackitException
from packit.local_project import LocalProjectBuilder
from packit.utils.koji_helper import KojiHelper
from packit.utils.repo import RepositoryCache

from packit_service.config import ProjectToSync, ServiceConfig
from packit_service.constants import DEFAULT_RETRY_LIMIT, SANDCASTLE_WORK_DIR
from packit_service.models import (
    GitBranchModel,
    GitProjectModel,
    KojiBuildGroupModel,
    KojiBuildTargetModel,
    PipelineModel,
    ProjectEventModel,
    ProjectEventModelType,
    SidetagGroupModel,
    SidetagModel,
)
from packit_service.package_config_getter import PackageConfigGetter
from packit_service.utils import load_job_config, load_package_config
from packit_service.worker.checker.run_condition import IsRunConditionSatisfied
from packit_service.worker.handlers.distgit import DownstreamKojiBuildHandler
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.tasks import (
    run_downstream_koji_build,
    run_sync_from_downstream_handler,
)
from tests.spellbook import DATA_DIR, first_dict_value, get_parameters_from_results


def distgit_commit_event():
    return json.loads((DATA_DIR / "fedmsg" / "distgit_commit.json").read_text())


def test_sync_from_downstream():
    packit_yaml = (
        "{'specfile_path': 'buildah.spec',"
        "'jobs': [{'trigger': 'commit', 'job': 'sync_from_downstream'}],"
        "'downstream_package_name': 'buildah'}"
    )
    pagure_project = flexmock(
        PagureProject,
        full_repo_name="rpms/buildah",
        default_branch="main",
    )
    pagure_project.should_receive("get_files").with_args(
        ref="main",
        filter_regex=r".+\.spec$",
    ).and_return(["buildah.spec"])
    pagure_project.should_receive("get_file_content").with_args(
        path=".packit.yaml",
        ref="main",
    ).and_return(packit_yaml)
    pagure_project.should_receive("get_files").with_args(
        ref="main",
        recursive=False,
    ).and_return(["buildah.spec", ".packit.yaml"])

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
    ).and_return(
        flexmock(
            id=9,
            job_config_trigger_type=JobConfigTriggerType.commit,
            project_event_model_type=ProjectEventModelType.branch_push,
        ),
    )

    flexmock(ServiceConfig).should_receive("get_service_config").and_return(
        ServiceConfig(
            projects_to_sync=[
                ProjectToSync(
                    forge="https://github.com",
                    repo_namespace="example-namespace",
                    repo_name="buildah",
                    branch="aaa",
                    dg_branch="main",
                    dg_repo_name="buildah",
                ),
            ],
            command_handler_work_dir=SANDCASTLE_WORK_DIR,
            repository_cache="/tmp/repository-cache",
            add_repositories_to_repository_cache=False,
            deployment=Deployment.prod,
        ),
    )

    flexmock(LocalProjectBuilder, _refresh_the_state=lambda *args: None)
    flexmock(RepositoryCache).should_call("__init__").once()
    flexmock(group).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    flexmock(PackitAPI).should_receive("sync_from_downstream").with_args(
        dist_git_branch="main",
        upstream_branch="aaa",
        sync_only_specfile=True,
    )

    processing_results = SteveJobs().process_message(distgit_commit_event())
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)
    results = run_sync_from_downstream_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]


def test_do_not_sync_from_downstream_on_a_different_branch():
    packit_yaml = (
        "{'specfile_path': 'buildah.spec',"
        "'jobs': [{'trigger': 'commit', 'job': 'sync_from_downstream'}],"
        "'downstream_package_name': 'buildah'}"
    )
    pagure_project = flexmock(
        PagureProject,
        full_repo_name="rpms/buildah",
        default_branch="main",
    )
    pagure_project.should_receive("get_files").with_args(
        ref="main",
        filter_regex=r".+\.spec$",
    ).and_return(["buildah.spec"])
    pagure_project.should_receive("get_file_content").with_args(
        path=".packit.yaml",
        ref="main",
    ).and_return(packit_yaml)
    pagure_project.should_receive("get_files").with_args(
        ref="main",
        recursive=False,
    ).and_return(["buildah.spec", ".packit.yaml"])

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
    ).and_return(
        flexmock(
            id=9,
            job_config_trigger_type=JobConfigTriggerType.commit,
            project_event_model_type=ProjectEventModelType.branch_push,
        ),
    )

    flexmock(ServiceConfig).should_receive("get_service_config").and_return(
        ServiceConfig(
            projects_to_sync=[
                ProjectToSync(
                    forge="https://github.com",
                    repo_namespace="example-namespace",
                    repo_name="buildah",
                    branch="aaa",
                    dg_branch="different_branch",
                    dg_repo_name="buildah",
                ),
            ],
            command_handler_work_dir=SANDCASTLE_WORK_DIR,
            repository_cache="/tmp/repository-cache",
            add_repositories_to_repository_cache=False,
            deployment=Deployment.prod,
        ),
    )

    flexmock(Pushgateway).should_receive("push").times(1).and_return()
    flexmock(PackitAPI).should_receive("sync_from_downstream").times(0)

    processing_results = SteveJobs().process_message(distgit_commit_event())
    assert not processing_results


@pytest.mark.parametrize(
    "sidetag_group",
    [None, "test"],
)
def test_downstream_koji_build(sidetag_group):
    packit_yaml = (
        "{'specfile_path': 'buildah.spec',"
        "'jobs': [{'trigger': 'commit', 'job': 'koji_build', 'allowed_committers':"
        " ['rhcontainerbot']"
        + (f", 'sidetag_group': '{sidetag_group}'" if sidetag_group else "")
        + "}],"
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
    ).and_return(packit_yaml)
    pagure_project.should_receive("get_files").with_args(
        ref="main",
        recursive=False,
    ).and_return(["buildah.spec", ".packit.yaml"])

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
    flexmock(PipelineModel).should_receive("create")

    if sidetag_group:
        sidetag = "f40-build-side-12345"

        flexmock(SidetagModel).should_receive("get_or_create_for_updating").and_return(
            flexmock(koji_name=sidetag),
        )
        flexmock(SidetagGroupModel).should_receive("get_or_create").and_return(
            flexmock(name="test"),
        )
        flexmock(KojiHelper).should_receive("get_tag_info").and_return(None)
        flexmock(PackitAPI).should_receive("init_kerberos_ticket").and_return(None)
        flexmock(KojiHelper).should_receive("create_sidetag").and_return(
            {"name": sidetag},
        )

    nvr = "package-1.2.3-1.fc40"
    koji_build = flexmock(
        target="main",
        status="queued",
        set_status=lambda x: None,
        set_task_id=lambda x: None,
        set_web_url=lambda x: None,
        set_build_logs_urls=lambda x: None,
        set_data=lambda x: None,
    )

    flexmock(KojiBuildTargetModel).should_receive("create").and_return(koji_build)
    flexmock(KojiBuildGroupModel).should_receive("create").and_return(
        flexmock(grouped_targets=[koji_build]),
    )

    if sidetag_group:
        sidetag = "f40-build-side-12345"

        flexmock(SidetagModel).should_receive("get_or_create_for_updating").and_return(
            flexmock(koji_name=sidetag),
        )
        flexmock(SidetagGroupModel).should_receive("get_or_create").and_return(
            flexmock(name="test"),
        )
        flexmock(KojiHelper).should_receive("get_tag_info").and_return(None)
        flexmock(PackitAPI).should_receive("init_kerberos_ticket").and_return(None)
        flexmock(KojiHelper).should_receive("create_sidetag").and_return(
            {"name": sidetag},
        )

    nvr = "package-1.2.3-1.fc40"
    koji_build = flexmock(
        target="main",
        status="queued",
        sidetag=sidetag if sidetag_group else None,
        nvr=nvr,
        set_status=lambda x: None,
        set_task_id=lambda x: None,
        set_web_url=lambda x: None,
        set_build_logs_urls=lambda x: None,
        set_data=lambda x: None,
    )

    flexmock(DistGit).should_receive("get_nvr").and_return(nvr)

    flexmock(KojiBuildTargetModel).should_receive("create").and_return(koji_build)
    flexmock(KojiBuildTargetModel).should_receive(
        "get_all_successful_or_in_progress_by_nvr",
    ).and_return({koji_build})
    flexmock(KojiBuildGroupModel).should_receive("create").and_return(
        flexmock(grouped_targets=[koji_build]),
    )

    flexmock(IsRunConditionSatisfied).should_receive("pre_check").and_return(True)

    flexmock(LocalProjectBuilder, _refresh_the_state=lambda *args: None)
    flexmock(group).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    flexmock(PackitAPI).should_receive("build").with_args(
        dist_git_branch="main",
        scratch=False,
        nowait=True,
        from_upstream=False,
        koji_target=sidetag if sidetag_group else None,
    ).and_return("")
    processing_results = SteveJobs().process_message(distgit_commit_event())
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)
    results = run_downstream_koji_build(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]


def test_downstream_koji_build_failure_no_issue():
    packit_yaml = (
        "{'specfile_path': 'buildah.spec',"
        "'jobs': [{'trigger': 'commit', 'job': 'koji_build', 'allowed_committers': "
        "['rhcontainerbot']}],"
        "'downstream_package_name': 'buildah'}"
    )
    pagure_project_mock = flexmock(
        PagureProject,
        full_repo_name="rpms/buildah",
        get_web_url=lambda: "https://src.fedoraproject.org/rpms/buildah",
        default_branch="main",
    )
    flexmock(PagureProject).should_receive("get_pr").never()
    pagure_project_mock.should_receive("get_files").with_args(
        ref="main",
        filter_regex=r".+\.spec$",
    ).and_return(["buildah.spec"])
    pagure_project_mock.should_receive("get_file_content").with_args(
        path=".packit.yaml",
        ref="main",
    ).and_return(packit_yaml)
    pagure_project_mock.should_receive("get_files").with_args(
        ref="main",
        recursive=False,
    ).and_return(["buildah.spec", ".packit.yaml"])

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
    flexmock(PipelineModel).should_receive("create")

    nvr = "package-1.2.3-1.fc40"

    koji_build = flexmock(
        target="main",
        status="queued",
        sidetag=None,
        nvr=nvr,
        set_status=lambda x: None,
        set_task_id=lambda x: None,
        set_web_url=lambda x: None,
        set_build_logs_urls=lambda x: None,
        set_data=lambda x: None,
    )

    flexmock(DistGit).should_receive("get_nvr").and_return(nvr)

    flexmock(KojiBuildTargetModel).should_receive("create").and_return(koji_build)
    flexmock(KojiBuildTargetModel).should_receive(
        "get_all_successful_or_in_progress_by_nvr",
    ).and_return({koji_build})
    flexmock(KojiBuildGroupModel).should_receive("create").and_return(
        flexmock(id=1, grouped_targets=[koji_build]),
    )

    flexmock(IsRunConditionSatisfied).should_receive("pre_check").and_return(True)

    flexmock(Pushgateway).should_receive("push").times(1).and_return()
    flexmock(LocalProjectBuilder, _refresh_the_state=lambda *args: None)
    flexmock(group).should_receive("apply_async").once()
    flexmock(PackitAPI).should_receive("build").with_args(
        dist_git_branch="main",
        scratch=False,
        nowait=True,
        from_upstream=False,
        koji_target=None,
    ).and_raise(PackitException, "Some error")

    pagure_project_mock.should_receive("get_issue_list").times(0)
    pagure_project_mock.should_receive("create_issue").times(0)

    processing_results = SteveJobs().process_message(distgit_commit_event())
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)
    with pytest.raises(PackitException):
        run_downstream_koji_build(
            package_config=package_config,
            event=event_dict,
            job_config=job_config,
        )


def test_downstream_koji_build_failure_issue_created():
    packit_yaml = (
        "{'specfile_path': 'buildah.spec',"
        "'jobs': [{'trigger': 'commit', 'job': 'koji_build', 'allowed_committers': "
        "['rhcontainerbot']}],"
        "'downstream_package_name': 'buildah',"
        "'issue_repository': 'https://github.com/namespace/project'}"
    )
    pagure_project_mock = flexmock(
        PagureProject,
        full_repo_name="rpms/buildah",
        get_web_url=lambda: "https://src.fedoraproject.org/rpms/buildah",
        default_branch="main",
    )
    flexmock(PagureProject).should_receive("get_pr").never()
    pagure_project_mock.should_receive("get_files").with_args(
        ref="main",
        filter_regex=r".+\.spec$",
    ).and_return(["buildah.spec"])
    pagure_project_mock.should_receive("get_file_content").with_args(
        path=".packit.yaml",
        ref="main",
    ).and_return(packit_yaml)
    pagure_project_mock.should_receive("get_files").with_args(
        ref="main",
        recursive=False,
    ).and_return(["buildah.spec", ".packit.yaml"])

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
    flexmock(PipelineModel).should_receive("create")

    nvr = "package-1.2.3-1.fc40"

    koji_build = flexmock(
        id=12,
        target="main",
        status="queued",
        sidetag=None,
        nvr=nvr,
        set_status=lambda x: None,
        set_task_id=lambda x: None,
        set_web_url=lambda x: None,
        set_build_logs_urls=lambda x: None,
        set_data=lambda x: None,
    )

    flexmock(DistGit).should_receive("get_nvr").and_return(nvr)

    flexmock(IsRunConditionSatisfied).should_receive("pre_check").and_return(True)

    flexmock(KojiBuildTargetModel).should_receive("create").and_return(koji_build)
    flexmock(KojiBuildTargetModel).should_receive(
        "get_all_successful_or_in_progress_by_nvr",
    ).and_return({koji_build})
    flexmock(KojiBuildGroupModel).should_receive("create").and_return(
        flexmock(grouped_targets=[koji_build]),
    )
    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    flexmock(LocalProjectBuilder, _refresh_the_state=lambda *args: None)
    flexmock(group).should_receive("apply_async").once()
    flexmock(PackitAPI).should_receive("build").with_args(
        dist_git_branch="main",
        scratch=False,
        nowait=True,
        from_upstream=False,
        koji_target=None,
    ).and_raise(PackitException, "Some error")

    issue_project_mock = flexmock(GithubProject)
    issue_project_mock.should_receive("get_issue_list").and_return([]).once()
    issue_project_mock.should_receive("create_issue").and_return(
        flexmock(id=3, url="https://github.com/namespace/project/issues/3"),
    ).once()

    processing_results = SteveJobs().process_message(distgit_commit_event())
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)
    DownstreamKojiBuildHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event_dict,
        # Needs to be the last try to inform user
        celery_task=flexmock(
            request=flexmock(retries=DEFAULT_RETRY_LIMIT),
            max_retries=DEFAULT_RETRY_LIMIT,
        ),
    ).run_job()


def test_downstream_koji_build_failure_issue_comment():
    packit_yaml = (
        "{'specfile_path': 'buildah.spec',"
        "'jobs': [{'trigger': 'commit', 'job': 'koji_build', 'allowed_committers': "
        "['rhcontainerbot']}],"
        "'downstream_package_name': 'buildah',"
        "'issue_repository': 'https://github.com/namespace/project'}"
    )
    pagure_project_mock = flexmock(
        PagureProject,
        full_repo_name="rpms/buildah",
        get_web_url=lambda: "https://src.fedoraproject.org/rpms/buildah",
        default_branch="main",
    )
    flexmock(PagureProject).should_receive("get_pr").never()
    pagure_project_mock.should_receive("get_files").with_args(
        ref="main",
        filter_regex=r".+\.spec$",
    ).and_return(["buildah.spec"])
    pagure_project_mock.should_receive("get_file_content").with_args(
        path=".packit.yaml",
        ref="main",
    ).and_return(packit_yaml)
    pagure_project_mock.should_receive("get_files").with_args(
        ref="main",
        recursive=False,
    ).and_return(["buildah.spec", ".packit.yaml"])

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
    flexmock(PipelineModel).should_receive("create")

    nvr = "package-1.2.3-1.fc40"

    koji_build = flexmock(
        id=12,
        target="main",
        status="queued",
        sidetag=None,
        nvr=nvr,
        set_status=lambda x: None,
        set_task_id=lambda x: None,
        set_web_url=lambda x: None,
        set_build_logs_urls=lambda x: None,
        set_data=lambda x: None,
    )

    flexmock(DistGit).should_receive("get_nvr").and_return(nvr)

    flexmock(KojiBuildTargetModel).should_receive("create").and_return(koji_build)
    flexmock(KojiBuildTargetModel).should_receive(
        "get_all_successful_or_in_progress_by_nvr",
    ).and_return({koji_build})
    flexmock(KojiBuildGroupModel).should_receive("create").and_return(
        flexmock(grouped_targets=[koji_build]),
    )

    flexmock(IsRunConditionSatisfied).should_receive("pre_check").and_return(True)

    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    flexmock(LocalProjectBuilder, _refresh_the_state=lambda *args: None)
    flexmock(group).should_receive("apply_async").once()
    flexmock(PackitAPI).should_receive("build").with_args(
        dist_git_branch="main",
        scratch=False,
        nowait=True,
        from_upstream=False,
        koji_target=None,
    ).and_raise(PackitException, "Some error")

    issue_project_mock = flexmock(GithubProject)
    issue_project_mock.should_receive("get_issue_list").and_return(
        [
            flexmock(
                id=3,
                title="[packit] Fedora Koji build failed to be triggered",
                url="https://github.com/namespace/project/issues/3",
                get_comments=lambda *args, **kwargs: [],
            )
            .should_receive("comment")
            .once()
            .mock(),
        ],
    ).once()
    issue_project_mock.should_receive("create_issue").times(0)

    processing_results = SteveJobs().process_message(distgit_commit_event())
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)
    DownstreamKojiBuildHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event_dict,
        # Needs to be the last try to inform user
        celery_task=flexmock(
            request=flexmock(retries=DEFAULT_RETRY_LIMIT),
            max_retries=DEFAULT_RETRY_LIMIT,
        ),
    ).run_job()


def test_downstream_koji_build_no_config():
    pagure_project = flexmock(
        PagureProject,
        full_repo_name="rpms/buildah",
        get_web_url=lambda: "https://src.fedoraproject.org/rpms/buildah",
        default_branch="main",
    )
    flexmock(PagureProject).should_receive("get_pr").never()
    pagure_project.should_receive("get_files").with_args(
        ref="main",
        filter_regex=r".+\.spec$",
    ).and_return(["buildah.spec"])
    pagure_project.should_receive("get_files").with_args(
        ref="main",
        recursive=False,
    ).and_return(["buildah.spec", "Makefile"])
    flexmock(PackageConfigGetter).should_call("get_package_config_from_repo").once()

    flexmock(GitBranchModel).should_receive("get_or_create").with_args(
        branch_name="main",
        namespace="rpms",
        repo_name="buildah",
        project_url="https://src.fedoraproject.org/rpms/buildah",
    ).and_return(flexmock(id=9, job_config_trigger_type=JobConfigTriggerType.commit))

    flexmock(ServiceConfig).should_receive("get_service_config").and_return(
        ServiceConfig(
            command_handler_work_dir=SANDCASTLE_WORK_DIR,
            repository_cache="/tmp/repository-cache",
            add_repositories_to_repository_cache=False,
        ),
    )

    flexmock(Pushgateway).should_receive("push").times(1).and_return()
    flexmock(LocalProjectBuilder, _refresh_the_state=lambda *args: None)
    flexmock(group).should_receive("apply_async").times(0)

    processing_results = SteveJobs().process_message(distgit_commit_event())
    assert processing_results[0]["details"]["msg"] == "No packit config found in the repository."


@pytest.mark.parametrize(
    "jobs_config",
    [
        pytest.param(
            "["
            "{'trigger': 'commit', 'job': 'koji_build', 'allowed_committers': "
            "['rhcontainerbot'],"
            "'metadata': {'dist_git_branches': ['a-different-branch']}},"
            "{'trigger': 'commit', 'job': 'koji_build', 'allowed_committers':"
            " ['rhcontainerbot'], "
            "'metadata': {'dist_git_branches': ['main']}}"
            "]",
            id="multiple_jobs",
        ),
        pytest.param(
            "["
            "{'trigger': 'commit', 'job': 'koji_build', 'allowed_committers': "
            "['rhcontainerbot'], "
            "'metadata': {'dist_git_branches': ['a-different-branch', 'main', 'other_branch']}}"
            "]",
            id="multiple_branches",
        ),
        pytest.param(
            "["
            "{'trigger': 'commit', 'job': 'koji_build', 'allowed_committers': "
            "['rhcontainerbot'] ,"
            "'metadata': {'dist_git_branches': ['fedora-all']}}"
            "]",
            id="aliases",
        ),
    ],
)
def test_downstream_koji_build_where_multiple_branches_defined(jobs_config):
    packit_yaml = (
        "{'specfile_path': 'buildah.spec',"
        f"'jobs': {jobs_config},"
        "'downstream_package_name': 'buildah'}"
    )
    flexmock(PagureProject).should_receive("get_pr").never()
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
    ).and_return(packit_yaml)
    pagure_project.should_receive("get_files").with_args(
        ref="main",
        recursive=False,
    ).and_return(["buildah.spec", ".packit.yaml"])

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
    flexmock(PipelineModel).should_receive("create")

    nvr = "package-1.2.3-1.fc40"

    koji_build = flexmock(
        target="main",
        status="queued",
        sidetag=None,
        nvr=nvr,
        set_status=lambda x: None,
        set_task_id=lambda x: None,
        set_web_url=lambda x: None,
        set_build_logs_urls=lambda x: None,
        set_data=lambda x: None,
    )

    flexmock(DistGit).should_receive("get_nvr").and_return(nvr)

    flexmock(IsRunConditionSatisfied).should_receive("pre_check").and_return(True)

    flexmock(KojiBuildTargetModel).should_receive("create").and_return(koji_build)
    flexmock(KojiBuildTargetModel).should_receive(
        "get_all_successful_or_in_progress_by_nvr",
    ).and_return({koji_build})
    flexmock(KojiBuildGroupModel).should_receive("create").and_return(
        flexmock(grouped_targets=[koji_build]),
    )

    flexmock(LocalProjectBuilder, _refresh_the_state=lambda *args: None)
    flexmock(group).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    flexmock(PackitAPI).should_receive("build").with_args(
        dist_git_branch="a-different-branch",
        scratch=False,
        nowait=True,
        from_upstream=False,
        koji_target=None,
    ).times(0)
    flexmock(PackitAPI).should_receive("build").with_args(
        dist_git_branch="main",
        scratch=False,
        nowait=True,
        from_upstream=False,
        koji_target=None,
    ).once().and_return("")

    processing_results = SteveJobs().process_message(distgit_commit_event())
    assert len(processing_results) == 1
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)
    results = run_downstream_koji_build(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]


@pytest.mark.parametrize(
    "jobs_config",
    [
        pytest.param(
            "["
            "{'trigger': 'commit', 'job': 'koji_build', 'allowed_committers': "
            "['rhcontainerbot'] ,"
            "'dist_git_branches': ['a-different-branch']},"
            "{'trigger': 'commit', 'job': 'koji_build', "
            "'dist_git_branches': ['other_branch']}"
            "]",
            id="multiple_jobs",
        ),
        pytest.param(
            "["
            "{'trigger': 'commit', 'job': 'koji_build', 'allowed_committers': "
            "['rhcontainerbot'],"
            "'dist_git_branches': ['a-different-branch', 'other_branch']}"
            "]",
            id="multiple_branches",
        ),
        pytest.param(
            "["
            "{'trigger': 'commit', 'job': 'koji_build', 'allowed_committers': "
            "['rhcontainerbot'] ,"
            "'metadata': {'dist_git_branches': ['fedora-stable']}}"
            "]",
            id="aliases",
        ),
    ],
)
def test_do_not_run_downstream_koji_build_for_a_different_branch(jobs_config):
    packit_yaml = (
        "{'specfile_path': 'buildah.spec',"
        f"'jobs': {jobs_config},"
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
    ).and_return(packit_yaml)
    pagure_project.should_receive("get_files").with_args(
        ref="main",
        recursive=False,
    ).and_return(["buildah.spec", ".packit.yaml"])

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
    ).and_return(
        flexmock(
            id=9,
            job_config_trigger_type=JobConfigTriggerType.commit,
            project_event_model_type=ProjectEventModelType.branch_push,
        ),
    )

    flexmock(Pushgateway).should_receive("push").times(1).and_return()
    flexmock(LocalProjectBuilder, _refresh_the_state=lambda *args: None)
    flexmock(PackitAPI).should_receive("build").times(0)

    processing_results = SteveJobs().process_message(distgit_commit_event())
    assert not processing_results


@pytest.mark.parametrize(
    "push_username, allowed_committers, should_pass",
    (
        ("sakamoto", [], False),
        ("packit", ["packit"], True),
        ("packit-stg", ["packit"], False),
    ),
)
def test_precheck_koji_build_push(
    distgit_push_event,
    push_username,
    allowed_committers,
    should_pass,
):
    distgit_push_event.committer = push_username
    distgit_push_event = flexmock(distgit_push_event, _pr_id=None)
    flexmock(PagureProject).should_receive("get_pr").never()

    flexmock(GitProjectModel).should_receive("get_or_create").with_args(
        namespace="rpms",
        project_url="https://src.fedoraproject.org/rpms/packit",
        repo_name="packit",
    ).and_return(
        flexmock(
            id=342,
        ),
    )
    flexmock(GitBranchModel).should_receive("get_or_create").with_args(
        branch_name="f36",
        namespace="rpms",
        project_url="https://src.fedoraproject.org/rpms/packit",
        repo_name="packit",
    ).and_return(
        flexmock(
            id=13,
            job_config_trigger_type=JobConfigTriggerType.commit,
            project_event_model_type=ProjectEventModelType.branch_push,
        ),
    )

    # flexmock(ProjectEventModel).should_receive("get_or_create").with_args(
    #     type=ProjectEventModel.pull_request, event_id=342
    # ).and_return(flexmock(id=2, type=ProjectEventModel.pull_request))
    # flexmock(GithubProject).should_receive("can_merge_pr").and_return(True)
    jobs = [
        JobConfig(
            type=JobType.koji_build,
            trigger=JobConfigTriggerType.commit,
            packages={
                "package": CommonPackageConfig(
                    dist_git_branches=["f36"],
                    allowed_committers=allowed_committers,
                ),
            },
        ),
    ]
    package_config = (
        PackageConfig(
            jobs=jobs,
            packages={"package": CommonPackageConfig()},
        ),
    )
    job_config = jobs[0]
    event = distgit_push_event.get_dict()
    assert DownstreamKojiBuildHandler.pre_check(package_config, job_config, event) == should_pass


@pytest.mark.parametrize(
    "pr_author, allowed_pr_authors, should_pass",
    (
        ("packit", ["packit"], True),
        ("packit-stg", ["packit"], False),
        ("packit-stg", [], False),
    ),
)
def test_precheck_koji_build_push_pr(
    distgit_push_event,
    pr_author,
    allowed_pr_authors,
    should_pass,
):
    flexmock(GitProjectModel).should_receive("get_or_create").with_args(
        namespace="rpms",
        project_url="https://src.fedoraproject.org/rpms/packit",
        repo_name="packit",
    ).and_return(
        flexmock(
            id=342,
        ),
    )
    flexmock(GitBranchModel).should_receive("get_or_create").with_args(
        branch_name="f36",
        namespace="rpms",
        project_url="https://src.fedoraproject.org/rpms/packit",
        repo_name="packit",
    ).and_return(
        flexmock(
            id=13,
            job_config_trigger_type=JobConfigTriggerType.commit,
            project_event_model_type=ProjectEventModelType.branch_push,
        ),
    )

    # flexmock(ProjectEventModel).should_receive("get_or_create").with_args(
    #     type=ProjectEventModel.pull_request, event_id=342
    # ).and_return(flexmock(id=2, type=ProjectEventModel.pull_request))
    # flexmock(GithubProject).should_receive("can_merge_pr").and_return(True)
    jobs = [
        JobConfig(
            type=JobType.koji_build,
            trigger=JobConfigTriggerType.commit,
            packages={
                "package": CommonPackageConfig(
                    dist_git_branches=["f36"],
                    allowed_pr_authors=allowed_pr_authors,
                ),
            },
        ),
    ]
    flexmock(PagureProject).should_receive("get_pr").and_return(
        flexmock(
            id=5,
            author=pr_author,
            head_commit="ad0c308af91da45cf40b253cd82f07f63ea9cbbf",
            status=PRStatus.open,
            target_branch="f36",
        ),
    )
    flexmock(PagureProject).should_receive("get_pr_files_diff").with_args(
        5,
        retries=int,
        wait_seconds=int,
    ).and_return({"package.spec": []})
    package_config = (
        PackageConfig(
            jobs=jobs,
            packages={"package": CommonPackageConfig()},
        ),
    )
    job_config = jobs[0]
    event = distgit_push_event.get_dict()
    assert DownstreamKojiBuildHandler.pre_check(package_config, job_config, event) == should_pass
