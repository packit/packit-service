# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json

import pytest
from celery import Task
from celery.canvas import Signature, group
from celery.exceptions import Retry
from flexmock import flexmock
from ogr.services.github import GithubProject
from ogr.services.pagure import PagureProject
from packit.api import PackitAPI
from packit.config import JobConfigTriggerType
from packit.exceptions import PackitException
from packit.local_project import LocalProject
from packit.utils.koji_helper import KojiHelper

from packit_service.config import ServiceConfig
from packit_service.constants import DEFAULT_RETRY_LIMIT
from packit_service.models import (
    BodhiUpdateGroupModel,
    BodhiUpdateTargetModel,
    GitBranchModel,
    KojiBuildTagModel,
    KojiBuildTargetModel,
    PipelineModel,
    ProjectEventModel,
    ProjectEventModelType,
    SidetagGroupModel,
    SidetagModel,
)
from packit_service.package_config_getter import PackageConfigGetter
from packit_service.utils import (
    dump_job_config,
    dump_package_config,
    load_job_config,
    load_package_config,
)
from packit_service.worker.celery_task import CeleryTask
from packit_service.worker.handlers.bodhi import CreateBodhiUpdateHandler
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.tasks import (
    BodhiTaskWithRetry,
    run_bodhi_update,
    run_bodhi_update_from_sidetag,
    run_koji_build_tag_handler,
)
from tests.spellbook import first_dict_value, get_parameters_from_results


def test_bodhi_update_for_unknown_koji_build(koji_build_completed_old_format):
    packit_yaml = (
        "{'specfile_path': 'packit.spec',"
        "'jobs': [{'trigger': 'commit', 'job': 'bodhi_update',"
        "'metadata': {'dist_git_branches': ['rawhide']}}],"
        "'downstream_package_name': 'packit'}"
    )
    pagure_project = flexmock(
        PagureProject,
        full_repo_name="rpms/packit",
        get_web_url=lambda: "https://src.fedoraproject.org/rpms/packit",
        default_branch="main",
    )
    pagure_project.should_receive("get_files").with_args(
        ref="main",
        filter_regex=r".+\.spec$",
    ).and_return(["packit.spec"])
    pagure_project.should_receive("get_file_content").with_args(
        path=".packit.yaml",
        ref="main",
    ).and_return(packit_yaml)
    pagure_project.should_receive("get_files").with_args(
        ref="main",
        recursive=False,
    ).and_return(["packit.spec", ".packit.yaml"])

    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    # 1*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    flexmock(group).should_receive("apply_async").times(2)
    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    flexmock(PackitAPI).should_receive("create_update").with_args(
        dist_git_branch="rawhide",
        update_type="enhancement",
        koji_builds=["packit-0.43.0-1.fc36"],
        sidetag=None,
        alias=None,
    ).and_return(("alias", "url"))

    # Database structure
    run_model_flexmock = flexmock()
    git_branch_model_flexmock = flexmock(
        id=1,
        job_config_trigger_type=JobConfigTriggerType.commit,
        project_event_model_type=ProjectEventModelType.branch_push,
    )
    flexmock(KojiBuildTargetModel).should_receive("get_by_task_id").with_args(
        79721403,
    ).and_return(None)
    flexmock(GitBranchModel).should_receive("get_or_create").and_return(
        git_branch_model_flexmock,
    )
    flexmock(PipelineModel).should_receive("create").and_return(run_model_flexmock)
    group_model = flexmock(
        id=23,
        grouped_targets=[
            flexmock(
                target="rawhide",
                koji_nvrs="packit-0.43.0-1.fc36",
                sidetag=None,
                set_status=lambda x: None,
                set_data=lambda x: None,
                set_web_url=lambda x: None,
                set_alias=lambda x: None,
                set_update_creation_time=lambda x: None,
            ),
        ],
    )
    flexmock(ProjectEventModel).should_receive("get_or_create")
    flexmock(BodhiUpdateGroupModel).should_receive("create").and_return(group_model)
    flexmock(BodhiUpdateTargetModel).should_receive(
        "get_all_successful_or_in_progress_by_nvrs",
    ).with_args("packit-0.43.0-1.fc36").and_return(set())
    flexmock(BodhiUpdateTargetModel).should_receive("create").with_args(
        target="rawhide",
        koji_nvrs="packit-0.43.0-1.fc36",
        status="queued",
        bodhi_update_group=group_model,
    ).and_return()

    flexmock(KojiBuildTargetModel).should_receive("create").with_args(
        build_id="1864700",
        commit_sha="0eb3e12005cb18f15d3054020f7ac934c01eae08",
        web_url="https://koji.fedoraproject.org/koji/taskinfo?taskID=79721403",
        target="noarch",
        status="COMPLETE",
        run_model=run_model_flexmock,
    ).and_return(flexmock(get_project_event_object=lambda: git_branch_model_flexmock))

    processing_results = SteveJobs().process_message(koji_build_completed_old_format)
    # 1*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    assert len(processing_results) == 2
    processing_results.pop()
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)
    results = run_bodhi_update(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]


def test_bodhi_update_for_unknown_koji_build_failed(koji_build_completed_old_format):
    packit_yaml = (
        "{'specfile_path': 'packit.spec',"
        "'jobs': [{'trigger': 'commit', 'job': 'bodhi_update',"
        "'dist_git_branches': ['rawhide']}],"
        "'downstream_package_name': 'packit'}"
    )
    pagure_project_mock = flexmock(
        PagureProject,
        full_repo_name="rpms/packit",
        get_web_url=lambda: "https://src.fedoraproject.org/rpms/packit",
        default_branch="main",
    )
    pagure_project_mock.should_receive("get_files").with_args(
        ref="main",
        filter_regex=r".+\.spec$",
    ).and_return(["packit.spec"])
    pagure_project_mock.should_receive("get_file_content").with_args(
        path=".packit.yaml",
        ref="main",
    ).and_return(packit_yaml)
    pagure_project_mock.should_receive("get_files").with_args(
        ref="main",
        recursive=False,
    ).and_return(["packit.spec", ".packit.yaml"])

    flexmock(Pushgateway).should_receive("push").times(1).and_return()
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    # 1*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    flexmock(group).should_receive("apply_async").times(2)
    flexmock(PackitAPI).should_receive("create_update").with_args(
        dist_git_branch="rawhide",
        update_type="enhancement",
        koji_builds=["packit-0.43.0-1.fc36"],
        sidetag=None,
        alias=None,
    ).and_raise(PackitException, "Failed to create an update")

    pagure_project_mock.should_receive("get_issue_list").times(0)
    pagure_project_mock.should_receive("create_issue").times(0)

    # Database structure
    run_model_flexmock = flexmock()
    git_branch_model_flexmock = flexmock(
        id=1,
        job_config_trigger_type=JobConfigTriggerType.commit,
        project_event_model_type=ProjectEventModelType.branch_push,
    )
    flexmock(KojiBuildTargetModel).should_receive("get_by_task_id").with_args(
        79721403,
    ).and_return(None)
    flexmock(GitBranchModel).should_receive("get_or_create").and_return(
        git_branch_model_flexmock,
    )
    flexmock(ProjectEventModel).should_receive("get_or_create")
    flexmock(PipelineModel).should_receive("create").and_return(run_model_flexmock)
    group_model = flexmock(
        id=12,
        grouped_targets=[
            flexmock(
                target="rawhide",
                koji_nvrs="packit-0.43.0-1.fc36",
                sidetag=None,
                set_status=lambda x: None,
                set_data=lambda x: None,
                set_web_url=lambda x: None,
            ),
        ],
    )
    flexmock(BodhiUpdateGroupModel).should_receive("create").and_return(group_model)
    flexmock(BodhiUpdateTargetModel).should_receive(
        "get_all_successful_or_in_progress_by_nvrs",
    ).with_args("packit-0.43.0-1.fc36").and_return(set())
    flexmock(BodhiUpdateTargetModel).should_receive("create").with_args(
        target="rawhide",
        koji_nvrs="packit-0.43.0-1.fc36",
        status="queued",
        bodhi_update_group=group_model,
    ).and_return()
    flexmock(KojiBuildTargetModel).should_receive("create").with_args(
        build_id="1864700",
        commit_sha="0eb3e12005cb18f15d3054020f7ac934c01eae08",
        web_url="https://koji.fedoraproject.org/koji/taskinfo?taskID=79721403",
        target="noarch",
        status="COMPLETE",
        run_model=run_model_flexmock,
    ).and_return(flexmock(get_project_event_object=lambda: git_branch_model_flexmock))

    processing_results = SteveJobs().process_message(koji_build_completed_old_format)
    # 1*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    assert len(processing_results) == 2
    processing_results.pop()
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    with pytest.raises(PackitException):
        run_bodhi_update(
            package_config=package_config,
            event=event_dict,
            job_config=job_config,
        )


def test_bodhi_update_for_unknown_koji_build_failed_issue_created(
    koji_build_completed_old_format,
):
    packit_yaml = (
        "{'specfile_path': 'packit.spec',"
        "'jobs': [{'trigger': 'commit', 'job': 'bodhi_update',"
        "'metadata': {'dist_git_branches': ['rawhide']}}],"
        "'downstream_package_name': 'packit',"
        "'issue_repository': 'https://github.com/namespace/project'}"
    )
    pagure_project_mock = flexmock(
        PagureProject,
        full_repo_name="rpms/packit",
        get_web_url=lambda: "https://src.fedoraproject.org/rpms/packit",
        default_branch="main",
    )
    pagure_project_mock.should_receive("get_files").with_args(
        ref="main",
        filter_regex=r".+\.spec$",
    ).and_return(["packit.spec"])
    pagure_project_mock.should_receive("get_file_content").with_args(
        path=".packit.yaml",
        ref="main",
    ).and_return(packit_yaml)
    pagure_project_mock.should_receive("get_files").with_args(
        ref="main",
        recursive=False,
    ).and_return(["packit.spec", ".packit.yaml"])

    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    # 1*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    flexmock(group).should_receive("apply_async").times(2)
    dg = flexmock(local_project=flexmock(git_url="an url"))
    flexmock(PackitAPI).should_receive("dg").and_return(dg)
    flexmock(PackitAPI).should_receive("create_update").with_args(
        dist_git_branch="rawhide",
        update_type="enhancement",
        koji_builds=["packit-0.43.0-1.fc36"],
        sidetag=None,
        alias=None,
    ).and_raise(PackitException, "Failed to create an update")

    issue_project_mock = flexmock(GithubProject)
    issue_project_mock.should_receive("get_issue_list").and_return([]).once()
    issue_project_mock.should_receive("create_issue").and_return(
        flexmock(id=3, url="https://github.com/namespace/project/issues/3"),
    ).once()

    # Database structure
    run_model_flexmock = flexmock()
    git_branch_model_flexmock = flexmock(
        id=1,
        job_config_trigger_type=JobConfigTriggerType.commit,
        project_event_model_type=ProjectEventModelType.branch_push,
    )
    flexmock(KojiBuildTargetModel).should_receive("get_by_task_id").with_args(
        79721403,
    ).and_return(None)
    flexmock(GitBranchModel).should_receive("get_or_create").and_return(
        git_branch_model_flexmock,
    )
    flexmock(PipelineModel).should_receive("create").and_return(run_model_flexmock)
    flexmock(ProjectEventModel).should_receive("get_or_create")
    group_model = flexmock(
        grouped_targets=[
            flexmock(
                id=12,
                target="rawhide",
                koji_nvrs="packit-0.43.0-1.fc36",
                sidetag=None,
                set_status=lambda x: None,
                set_data=lambda x: None,
                set_web_url=lambda x: None,
            ),
        ],
    )
    flexmock(BodhiUpdateGroupModel).should_receive("create").and_return(group_model)
    flexmock(BodhiUpdateTargetModel).should_receive(
        "get_all_successful_or_in_progress_by_nvrs",
    ).with_args("packit-0.43.0-1.fc36").and_return(set())
    flexmock(BodhiUpdateTargetModel).should_receive("create").with_args(
        target="rawhide",
        koji_nvrs="packit-0.43.0-1.fc36",
        status="queued",
        bodhi_update_group=group_model,
    ).and_return()
    flexmock(KojiBuildTargetModel).should_receive("create").with_args(
        build_id="1864700",
        commit_sha="0eb3e12005cb18f15d3054020f7ac934c01eae08",
        web_url="https://koji.fedoraproject.org/koji/taskinfo?taskID=79721403",
        target="noarch",
        status="COMPLETE",
        run_model=run_model_flexmock,
    ).and_return(flexmock(get_project_event_object=lambda: git_branch_model_flexmock))

    processing_results = SteveJobs().process_message(koji_build_completed_old_format)
    # 1*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    assert len(processing_results) == 2
    processing_results.pop()
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    CreateBodhiUpdateHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event_dict,
        # Needs to be the last try to inform user
        celery_task=flexmock(
            request=flexmock(retries=DEFAULT_RETRY_LIMIT),
            max_retries=DEFAULT_RETRY_LIMIT,
        ),
    ).run_job()


def test_bodhi_update_for_unknown_koji_build_failed_issue_comment(
    koji_build_completed_old_format,
):
    packit_yaml = (
        "{'specfile_path': 'packit.spec',"
        "'jobs': [{'trigger': 'commit', 'job': 'bodhi_update',"
        "'metadata': {'dist_git_branches': ['rawhide']}}],"
        "'downstream_package_name': 'packit',"
        "'issue_repository': 'https://github.com/namespace/project'}"
    )
    pagure_project_mock = flexmock(
        PagureProject,
        full_repo_name="rpms/packit",
        get_web_url=lambda: "https://src.fedoraproject.org/rpms/packit",
        default_branch="main",
    )
    pagure_project_mock.should_receive("get_files").with_args(
        ref="main",
        filter_regex=r".+\.spec$",
    ).and_return(["packit.spec"])
    pagure_project_mock.should_receive("get_file_content").with_args(
        path=".packit.yaml",
        ref="main",
    ).and_return(packit_yaml)
    pagure_project_mock.should_receive("get_files").with_args(
        ref="main",
        recursive=False,
    ).and_return(["packit.spec", ".packit.yaml"])

    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    # 1*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    flexmock(group).should_receive("apply_async").times(2)
    dg = flexmock(local_project=flexmock(git_url="an url"))
    flexmock(PackitAPI).should_receive("dg").and_return(dg)
    flexmock(PackitAPI).should_receive("create_update").with_args(
        dist_git_branch="rawhide",
        update_type="enhancement",
        koji_builds=["packit-0.43.0-1.fc36"],
        sidetag=None,
        alias=None,
    ).and_raise(PackitException, "Failed to create an update")

    issue_project_mock = flexmock(GithubProject)
    issue_project_mock.should_receive("get_issue_list").and_return(
        [
            flexmock(
                id=3,
                title="[packit] Fedora Bodhi update failed to be created",
                url="https://github.com/namespace/project/issues/3",
                get_comments=lambda *args, **kwargs: [],
            )
            .should_receive("comment")
            .once()
            .mock(),
        ],
    ).once()
    issue_project_mock.should_receive("create_issue").times(0)

    # Database structure
    run_model_flexmock = flexmock()
    git_branch_model_flexmock = flexmock(
        id=1,
        job_config_trigger_type=JobConfigTriggerType.commit,
        project_event_model_type=ProjectEventModelType.branch_push,
    )
    flexmock(KojiBuildTargetModel).should_receive("get_by_task_id").with_args(
        79721403,
    ).and_return(None)
    flexmock(GitBranchModel).should_receive("get_or_create").and_return(
        git_branch_model_flexmock,
    )
    flexmock(PipelineModel).should_receive("create").and_return(run_model_flexmock)
    flexmock(ProjectEventModel).should_receive("get_or_create")
    group_model = flexmock(
        grouped_targets=[
            flexmock(
                id=12,
                target="rawhide",
                koji_nvrs="packit-0.43.0-1.fc36",
                sidetag=None,
                set_status=lambda x: None,
                set_data=lambda x: None,
                set_web_url=lambda x: None,
            ),
        ],
    )
    flexmock(BodhiUpdateGroupModel).should_receive("create").and_return(group_model)
    flexmock(BodhiUpdateTargetModel).should_receive(
        "get_all_successful_or_in_progress_by_nvrs",
    ).with_args("packit-0.43.0-1.fc36").and_return(set())
    flexmock(BodhiUpdateTargetModel).should_receive("create").with_args(
        target="rawhide",
        koji_nvrs="packit-0.43.0-1.fc36",
        status="queued",
        bodhi_update_group=group_model,
    ).and_return()
    flexmock(KojiBuildTargetModel).should_receive("create").with_args(
        build_id="1864700",
        commit_sha="0eb3e12005cb18f15d3054020f7ac934c01eae08",
        web_url="https://koji.fedoraproject.org/koji/taskinfo?taskID=79721403",
        target="noarch",
        status="COMPLETE",
        run_model=run_model_flexmock,
    ).and_return(flexmock(get_project_event_object=lambda: git_branch_model_flexmock))

    processing_results = SteveJobs().process_message(koji_build_completed_old_format)
    # 1*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    assert len(processing_results) == 2
    processing_results.pop()
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    CreateBodhiUpdateHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        event=event_dict,
        # Needs to be the last try to inform user
        celery_task=flexmock(
            request=flexmock(retries=BodhiTaskWithRetry.retry_kwargs["max_retries"]),
            max_retries=DEFAULT_RETRY_LIMIT,
        ),
    ).run_job()


def test_bodhi_update_build_not_tagged_yet(
    koji_build_completed_old_format,
):
    """the usual use case: the build is not tagged yet so we need to retry a few times"""

    packit_yaml = (
        "{'specfile_path': 'packit.spec',"
        "'jobs': [{'trigger': 'commit', 'job': 'bodhi_update',"
        "'metadata': {'dist_git_branches': ['rawhide']}}],"
        "'downstream_package_name': 'packit',"
        "'issue_repository': 'https://github.com/namespace/project'}"
    )
    pagure_project_mock = flexmock(
        PagureProject,
        full_repo_name="rpms/packit",
        get_web_url=lambda: "https://src.fedoraproject.org/rpms/packit",
        default_branch="main",
    )
    pagure_project_mock.should_receive("get_files").with_args(
        ref="main",
        filter_regex=r".+\.spec$",
    ).and_return(["packit.spec"])
    pagure_project_mock.should_receive("get_file_content").with_args(
        path=".packit.yaml",
        ref="main",
    ).and_return(packit_yaml)
    pagure_project_mock.should_receive("get_files").with_args(
        ref="main",
        recursive=False,
    ).and_return(["packit.spec", ".packit.yaml"])

    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    # 1*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    flexmock(group).should_receive("apply_async").times(2)
    flexmock(PackitAPI).should_receive("create_update").with_args(
        dist_git_branch="rawhide",
        update_type="enhancement",
        koji_builds=["packit-0.43.0-1.fc36"],
        sidetag=None,
        alias=None,
    ).and_raise(PackitException, "build not tagged")

    # no reporting should be done as the update is created on the second run
    issue_project_mock = flexmock(GithubProject)
    issue_project_mock.should_receive("get_issue_list").times(0)
    issue_project_mock.should_receive("create_issue").times(0)

    # Database structure
    run_model_flexmock = flexmock()
    flexmock(PipelineModel).should_receive("create").and_return(run_model_flexmock)
    group_model = flexmock(
        grouped_targets=[
            flexmock(
                target="rawhide",
                koji_nvrs="packit-0.43.0-1.fc36",
                sidetag=None,
                set_status=lambda x: None,
                set_data=lambda x: None,
                set_web_url=lambda x: None,
            ),
        ],
    )
    flexmock(BodhiUpdateGroupModel).should_receive("create").and_return(group_model)
    flexmock(BodhiUpdateTargetModel).should_receive(
        "get_all_successful_or_in_progress_by_nvrs",
    ).with_args("packit-0.43.0-1.fc36").and_return(set())
    flexmock(BodhiUpdateTargetModel).should_receive("create").with_args(
        target="rawhide",
        koji_nvrs="packit-0.43.0-1.fc36",
        status="queued",
        bodhi_update_group=group_model,
    ).and_return()
    git_branch_model_flexmock = flexmock(
        id=1,
        job_config_trigger_type=JobConfigTriggerType.commit,
        project_event_model_type=ProjectEventModelType.branch_push,
    )
    flexmock(KojiBuildTargetModel).should_receive("get_by_task_id").with_args(
        79721403,
    ).and_return(
        flexmock(
            get_project_event_object=lambda: flexmock(
                id=1,
                job_config_trigger_type=JobConfigTriggerType.commit,
            ),
            group_of_targets=flexmock(runs=[flexmock()]),
        ),
    )
    flexmock(GitBranchModel).should_receive("get_or_create").and_return(
        git_branch_model_flexmock,
    )
    flexmock(PipelineModel).should_receive("create").and_return(run_model_flexmock)

    flexmock(Task).should_receive("retry").and_raise(Retry).once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    processing_results = SteveJobs().process_message(koji_build_completed_old_format)
    # 1*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    assert len(processing_results) == 2
    processing_results.pop()
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    celery_task = flexmock(CeleryTask)
    celery_task.should_receive("is_last_try").and_return(False)
    with pytest.raises(Retry):
        run_bodhi_update(
            package_config=package_config,
            event=event_dict,
            job_config=job_config,
        )
    flexmock(PackitAPI).should_receive("create_update").with_args(
        dist_git_branch="rawhide",
        update_type="enhancement",
        koji_builds=["packit-0.43.0-1.fc36"],
        sidetag=None,
        alias=None,
    ).and_return(
        None,
    )  # tagged now
    results = run_bodhi_update(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert first_dict_value(results["job"])["success"]


def test_bodhi_update_for_unknown_koji_build_not_for_unfinished(
    koji_build_start_old_format,
):
    packit_yaml = (
        "{'specfile_path': 'packit.spec',"
        "'jobs': [{'trigger': 'commit', 'job': 'bodhi_update',"
        "'metadata': {'dist_git_branches': ['rawhide']}}],"
        "'downstream_package_name': 'packit'}"
    )
    pagure_project = flexmock(
        PagureProject,
        full_repo_name="rpms/packit",
        get_web_url=lambda: "https://src.fedoraproject.org/rpms/packit",
        default_branch="main",
    )
    pagure_project.should_receive("get_files").with_args(
        ref="main",
        filter_regex=r".+\.spec$",
    ).and_return(["packit.spec"])
    pagure_project.should_receive("get_file_content").with_args(
        path=".packit.yaml",
        ref="main",
    ).and_return(packit_yaml)
    pagure_project.should_receive("get_files").with_args(
        ref="main",
        recursive=False,
    ).and_return(["packit.spec", ".packit.yaml"])

    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    # 0*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    flexmock(group).should_receive("apply_async").times(2)
    flexmock(Pushgateway).should_receive("push").times(1)
    flexmock(PackitAPI).should_receive("create_update").times(0)

    # Database structure
    run_model_flexmock = flexmock()
    group_model = flexmock(
        grouped_targets=[
            flexmock(
                target="rawhide",
                koji_nvrs="packit-0.43.0-1.fc36",
                sidetag=None,
                set_status=lambda x: None,
                set_data=lambda x: None,
                set_web_url=lambda x: None,
            ),
        ],
    )
    flexmock(BodhiUpdateGroupModel).should_receive("create").and_return(group_model)
    flexmock(BodhiUpdateTargetModel).should_receive(
        "get_all_successful_or_in_progress_by_nvrs",
    ).with_args("packit-0.43.0-1.fc36").and_return(set())
    flexmock(BodhiUpdateTargetModel).should_receive("create").with_args(
        target="rawhide",
        koji_nvrs="packit-0.43.0-1.fc36",
        sidetag=None,
        status="queued",
        bodhi_update_group=group_model,
    ).and_return()
    git_branch_model_flexmock = flexmock(
        id=1,
        job_config_trigger_type=JobConfigTriggerType.commit,
        project_event_model_type=ProjectEventModelType.branch_push,
    )
    flexmock(KojiBuildTargetModel).should_receive("get_by_task_id").with_args(
        79721403,
    ).and_return(None)
    flexmock(GitBranchModel).should_receive("get_or_create").and_return(
        git_branch_model_flexmock,
    )
    flexmock(PipelineModel).should_receive("create").and_return(run_model_flexmock)
    flexmock(ProjectEventModel).should_receive("get_or_create")
    flexmock(KojiBuildTargetModel).should_receive("create").with_args(
        build_id="1864700",
        commit_sha="0eb3e12005cb18f15d3054020f7ac934c01eae08",
        web_url="https://koji.fedoraproject.org/koji/taskinfo?taskID=79721403",
        target="noarch",
        status="BUILDING",
        run_model=run_model_flexmock,
    ).and_return(flexmock(get_project_event_object=lambda: git_branch_model_flexmock))

    processing_results = SteveJobs().process_message(koji_build_start_old_format)
    # 0*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    assert len(processing_results) == 1


def test_bodhi_update_for_known_koji_build(koji_build_completed_old_format):
    packit_yaml = (
        "{'specfile_path': 'packit.spec',"
        "'jobs': [{'trigger': 'commit', 'job': 'bodhi_update',"
        "'metadata': {'dist_git_branches': ['rawhide']}}],"
        "'downstream_package_name': 'packit'}"
    )
    pagure_project = flexmock(
        PagureProject,
        full_repo_name="rpms/packit",
        get_web_url=lambda: "https://src.fedoraproject.org/rpms/packit",
        default_branch="main",
    )
    pagure_project.should_receive("get_files").with_args(
        ref="main",
        filter_regex=r".+\.spec$",
    ).and_return(["packit.spec"])
    pagure_project.should_receive("get_file_content").with_args(
        path=".packit.yaml",
        ref="main",
    ).and_return(packit_yaml)
    pagure_project.should_receive("get_files").with_args(
        ref="main",
        recursive=False,
    ).and_return(["packit.spec", ".packit.yaml"])

    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    # 1*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    flexmock(group).should_receive("apply_async").times(2)
    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    flexmock(PackitAPI).should_receive("create_update").with_args(
        dist_git_branch="rawhide",
        update_type="enhancement",
        koji_builds=["packit-0.43.0-1.fc36"],
        sidetag=None,
        alias=None,
    ).and_return(("alias", "url"))
    group_model = flexmock(
        grouped_targets=[
            flexmock(
                target="rawhide",
                koji_nvrs="packit-0.43.0-1.fc36",
                sidetag=None,
                set_status=lambda x: None,
                set_data=lambda x: None,
                set_web_url=lambda x: None,
                set_alias=lambda x: None,
                set_update_creation_time=lambda x: None,
            ),
        ],
    )
    flexmock(BodhiUpdateGroupModel).should_receive("create").and_return(group_model)
    flexmock(BodhiUpdateTargetModel).should_receive(
        "get_all_successful_or_in_progress_by_nvrs",
    ).with_args("packit-0.43.0-1.fc36").and_return(set())
    flexmock(BodhiUpdateTargetModel).should_receive("create").with_args(
        target="rawhide",
        koji_nvrs="packit-0.43.0-1.fc36",
        status="queued",
        bodhi_update_group=group_model,
    ).and_return()

    # Database structure
    flexmock(KojiBuildTargetModel).should_receive("get_by_task_id").with_args(
        79721403,
    ).and_return(
        flexmock(
            get_project_event_object=lambda: flexmock(
                id=1,
                job_config_trigger_type=JobConfigTriggerType.commit,
            ),
            group_of_targets=flexmock(runs=[flexmock()]),
        ),
    )

    processing_results = SteveJobs().process_message(koji_build_completed_old_format)
    # 1*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    assert len(processing_results) == 2
    processing_results.pop()
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)
    results = run_bodhi_update(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]


def test_bodhi_update_for_not_configured_branch(koji_build_completed_old_format):
    packit_yaml = (
        "{'specfile_path': 'packit.spec',"
        "'jobs': [{'trigger': 'commit', 'job': 'bodhi_update',"
        "'metadata': {'dist_git_branches': ['a-different-branch']}}],"
        "'downstream_package_name': 'packit'}"
    )
    pagure_project = flexmock(
        PagureProject,
        full_repo_name="rpms/packit",
        get_web_url=lambda: "https://src.fedoraproject.org/rpms/packit",
        default_branch="main",
    )
    pagure_project.should_receive("get_files").with_args(
        ref="main",
        filter_regex=r".+\.spec$",
    ).and_return(["packit.spec"])
    pagure_project.should_receive("get_file_content").with_args(
        path=".packit.yaml",
        ref="main",
    ).and_return(packit_yaml)
    pagure_project.should_receive("get_files").with_args(
        ref="main",
        recursive=False,
    ).and_return(["packit.spec", ".packit.yaml"])

    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    # 0*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    flexmock(group).should_receive("apply_async").times(2)
    flexmock(Pushgateway).should_receive("push").times(1)
    flexmock(PackitAPI).should_receive("create_update").times(0)

    # Database structure
    run_model_flexmock = flexmock()
    git_branch_model_flexmock = flexmock(
        id=1,
        job_config_trigger_type=JobConfigTriggerType.commit,
        project_event_model_type=ProjectEventModelType.branch_push,
    )
    flexmock(KojiBuildTargetModel).should_receive("get_by_task_id").with_args(
        79721403,
    ).and_return(None)
    flexmock(GitBranchModel).should_receive("get_or_create").and_return(
        git_branch_model_flexmock,
    )
    flexmock(PipelineModel).should_receive("create").and_return(run_model_flexmock)
    flexmock(KojiBuildTargetModel).should_receive("create").with_args(
        build_id="1864700",
        commit_sha="0eb3e12005cb18f15d3054020f7ac934c01eae08",
        web_url="https://koji.fedoraproject.org/koji/taskinfo?taskID=79721403",
        target="noarch",
        status="COMPLETE",
        run_model=run_model_flexmock,
    ).and_return(flexmock(get_project_event_object=lambda: git_branch_model_flexmock))

    processing_results = SteveJobs().process_message(koji_build_completed_old_format)
    # 0*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    assert len(processing_results)


def test_bodhi_update_fedora_stable_by_default(koji_build_completed_f36):
    """(Known build scenario.)"""
    packit_yaml = (
        "{'specfile_path': 'python-ogr.spec',"
        "'jobs': [{'trigger': 'commit', 'job': 'bodhi_update', 'dist_git_branches': ['f36']}],"
        "'downstream_package_name': 'python-ogr'}"
    )
    pagure_project = flexmock(
        PagureProject,
        full_repo_name="rpms/python-ogr",
        get_web_url=lambda: "https://src.fedoraproject.org/rpms/python-ogr",
        default_branch="main",
    )
    pagure_project.should_receive("get_files").with_args(
        ref="main",
        filter_regex=r".+\.spec$",
    ).and_return(["packit.spec"])
    pagure_project.should_receive("get_file_content").with_args(
        path=".packit.yaml",
        ref="main",
    ).and_return(packit_yaml)
    pagure_project.should_receive("get_files").with_args(
        ref="main",
        recursive=False,
    ).and_return(["packit.spec", ".packit.yaml"])

    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    # 1*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    flexmock(group).should_receive("apply_async").times(2)
    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    flexmock(PackitAPI).should_receive("create_update").with_args(
        dist_git_branch="f36",
        update_type="enhancement",
        koji_builds=["python-ogr-0.34.0-1.fc36"],
        sidetag=None,
        alias=None,
    ).once().and_return(("alias", "url"))

    flexmock(PipelineModel).should_receive("create").and_return(flexmock())
    group_model = flexmock(
        grouped_targets=[
            flexmock(
                target="f36",
                koji_nvrs="python-ogr-0.34.0-1.fc36",
                sidetag=None,
                set_status=lambda x: None,
                set_data=lambda x: None,
                set_web_url=lambda x: None,
                set_alias=lambda x: None,
                set_update_creation_time=lambda x: None,
            ),
        ],
    )
    flexmock(BodhiUpdateGroupModel).should_receive("create").and_return(group_model)
    flexmock(BodhiUpdateTargetModel).should_receive(
        "get_all_successful_or_in_progress_by_nvrs",
    ).with_args("python-ogr-0.34.0-1.fc36").and_return(set())
    flexmock(BodhiUpdateTargetModel).should_receive("create").with_args(
        target="f36",
        koji_nvrs="python-ogr-0.34.0-1.fc36",
        status="queued",
        bodhi_update_group=group_model,
    ).and_return()
    flexmock(KojiBuildTargetModel).should_receive("get_by_task_id").with_args(
        80860789,
    ).and_return(
        flexmock(
            get_project_event_object=lambda: flexmock(
                id=1,
                job_config_trigger_type=JobConfigTriggerType.commit,
            ),
            group_of_targets=flexmock(runs=[flexmock()]),
        ),
    )

    processing_results = SteveJobs().process_message(koji_build_completed_f36)
    # 1*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    assert len(processing_results) == 2
    processing_results.pop()
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)
    results = run_bodhi_update(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]


@pytest.mark.parametrize(
    "missing_dependency, non_unique_builds, existing_update",
    [
        (False, False, None),
        (
            False,
            False,
            flexmock(
                alias="FEDORA-2024-abcdef1234",
                koji_nvrs="python-specfile-0.31.0-1.fc40 packit-0.99.0-1.fc40",
            ),
        ),
        (
            False,
            False,
            flexmock(alias="FEDORA-2024-abcdef1234", koji_nvrs="packit-0.99.0-1.fc40"),
        ),
        (False, True, None),
        (True, False, None),
    ],
)
def test_bodhi_update_from_sidetag(
    koji_build_tagged,
    missing_dependency,
    non_unique_builds,
    existing_update,
):
    """(Sidetag scenario.)"""

    build_id = 1234567
    task_id = 7654321
    dg_branch = "f40"
    sidetag_group_name = "test"
    sidetag_name = "f40-build-side-12345"

    flexmock(KojiHelper).should_receive("get_build_info").with_args(
        build_id,
    ).and_return({"task_id": task_id})

    sidetag_group = flexmock(name=sidetag_group_name)
    sidetag = flexmock(
        sidetag_group=sidetag_group,
        target=dg_branch,
        koji_name=sidetag_name,
        delete=lambda: None,
    )
    sidetag_group.should_receive("get_sidetag_by_target").with_args(
        dg_branch,
    ).and_return(sidetag)

    flexmock(SidetagGroupModel).should_receive("get_by_name").with_args(
        sidetag_group_name,
    ).and_return(sidetag_group)
    flexmock(SidetagModel).should_receive("get_by_koji_name").with_args(
        sidetag_name,
    ).and_return(sidetag)

    builds_in_sidetag = [
        {"package_name": "python-specfile", "nvr": "python-specfile-0.31.0-1.fc40"},
        {"package_name": "packit", "nvr": "packit-0.99.0-1.fc40"},
    ]

    if missing_dependency:
        builds_in_sidetag.pop()

    flexmock(KojiHelper).should_receive("get_tag_info").with_args(
        sidetag_name,
    ).and_return({"name": sidetag_name})

    flexmock(KojiHelper).should_receive("get_builds_in_tag").with_args(
        sidetag_name,
    ).and_return(builds_in_sidetag)

    flexmock(KojiHelper).should_receive("get_latest_stable_nvr").with_args(
        "python-specfile",
        "f40",
    ).and_return("python-specfile-0.30.0-1.fc40")
    flexmock(KojiHelper).should_receive("get_latest_stable_nvr").with_args(
        "packit",
        "f40",
    ).and_return("packit-0.98.0-1.fc40")

    flexmock(KojiBuildTargetModel).should_receive("get_by_task_id").with_args(
        task_id=task_id,
    ).and_return(flexmock(target=dg_branch, get_project_event_model=lambda: None))

    flexmock(BodhiUpdateTargetModel).should_receive(
        "get_last_successful_by_sidetag",
    ).with_args(sidetag_name).and_return(existing_update)

    specfile_packit_yaml = (
        "{'specfile_path': 'python-specfile.spec',"
        "'jobs': [{'trigger': 'commit', 'job': 'koji_build', 'sidetag_group': 'test',"
        "'dependents': ['packit'], 'dist_git_branches': ['f40']}],"
        "'downstream_package_name': 'python-specfile'}"
    )
    specfile_pagure_project = flexmock(
        namespace="rpms",
        repo="python-specfile",
        full_repo_name="rpms/python-specfile",
        get_web_url=lambda: "https://src.fedoraproject.org/rpms/python-specfile",
        default_branch="main",
    )
    specfile_pagure_project.should_receive("get_files").with_args(
        ref=None,
        filter_regex=r".+\.spec$",
    ).and_return(["python-specfile.spec"])
    specfile_pagure_project.should_receive("get_file_content").with_args(
        path=".packit.yaml",
        ref=None,
    ).and_return(specfile_packit_yaml)
    specfile_pagure_project.should_receive("get_files").with_args(
        ref=None,
        recursive=False,
    ).and_return(["python-specfile.spec", ".packit.yaml"])

    flexmock(ServiceConfig).should_receive("get_project").with_args(
        url="https://src.fedoraproject.org/rpms/python-specfile",
    ).and_return(specfile_pagure_project)
    flexmock(ServiceConfig).should_receive("get_project").with_args(
        url="https://src.fedoraproject.org/rpms/python-specfile",
        required=True,
    ).and_return(specfile_pagure_project)

    packit_packit_yaml = (
        "{'specfile_path': 'packit.spec',"
        "'jobs': [{'trigger': 'koji_build', 'job': 'bodhi_update', 'sidetag_group': 'test',"
        "'dependencies': ['python-specfile'], 'dist_git_branches': ['f40']}],"
        "'downstream_package_name': 'packit'}"
    )
    packit_pagure_project = flexmock(
        namespace="rpms",
        repo="packit",
        full_repo_name="rpms/packit",
        get_web_url=lambda: "https://src.fedoraproject.org/rpms/packit",
        default_branch="main",
    )
    packit_pagure_project.should_receive("get_files").with_args(
        ref=None,
        filter_regex=r".+\.spec$",
    ).and_return(["packit.spec"])
    packit_pagure_project.should_receive("get_file_content").with_args(
        path=".packit.yaml",
        ref=None,
    ).and_return(packit_packit_yaml)
    packit_pagure_project.should_receive("get_files").with_args(
        ref=None,
        recursive=False,
    ).and_return(["packit.spec", ".packit.yaml"])

    flexmock(ServiceConfig).should_receive("get_project").with_args(
        url="https://src.fedoraproject.org/rpms/packit",
    ).and_return(packit_pagure_project)

    flexmock(group).should_receive("apply_async").once()
    flexmock(Signature).should_receive("apply_async").once()

    flexmock(KojiBuildTagModel).should_receive("get_or_create").with_args(
        task_id=str(task_id),
        koji_tag_name=sidetag_name,
        target="f40",
        namespace="rpms",
        repo_name="python-specfile",
        project_url="https://src.fedoraproject.org/rpms/python-specfile",
    ).and_return(flexmock(id=1, project_event_model_type="koji_build_tag"))

    flexmock(ProjectEventModel).should_receive("get_or_create").with_args(
        type="koji_build_tag",
        event_id=1,
        commit_sha=None,
    ).and_return(flexmock())

    flexmock(LocalProject, refresh_the_arguments=lambda: None)

    flexmock(PackitAPI).should_receive("init_kerberos_ticket").and_return()

    def _create_update(dist_git_branch, update_type, koji_builds, sidetag, alias):
        assert dist_git_branch == dg_branch
        assert update_type == "enhancement"
        assert set(koji_builds) == {
            "python-specfile-0.31.0-1.fc40",
            "packit-0.99.0-1.fc40",
        }
        assert sidetag == sidetag_name
        assert alias == (existing_update.alias if existing_update else None)
        return "alias", "url"

    flexmock(PackitAPI).should_receive("create_update").replace_with(
        _create_update,
    ).times(
        (
            0
            if missing_dependency
            or non_unique_builds
            or (existing_update and " " in existing_update.koji_nvrs)
            else 1
        ),
    )

    flexmock(PipelineModel).should_receive("create").and_return(flexmock())
    update_model = flexmock(
        target=dg_branch,
        koji_nvrs="python-specfile-0.31.0-1.fc40 packit-0.99.0-1.fc40",
        sidetag=sidetag_name,
        set_status=lambda x: None,
        set_data=lambda x: None,
        set_web_url=lambda x: None,
        set_alias=lambda x: None,
        set_update_creation_time=lambda x: None,
    )
    group_model = flexmock(grouped_targets=[update_model])
    flexmock(BodhiUpdateGroupModel).should_receive("create").and_return(group_model)

    flexmock(BodhiUpdateTargetModel).should_receive(
        "get_all_successful_or_in_progress_by_nvrs",
    ).with_args(
        "python-specfile-0.31.0-1.fc40 packit-0.99.0-1.fc40",
    ).and_return(
        {flexmock(), update_model} if non_unique_builds else {update_model},
    )

    def _create(target, koji_nvrs, sidetag, status, bodhi_update_group):
        assert target == dg_branch
        assert set(koji_nvrs.split()) == {
            "python-specfile-0.31.0-1.fc40",
            "packit-0.99.0-1.fc40",
        }
        assert sidetag == sidetag_name
        assert status == "queued"
        assert bodhi_update_group == group_model

    flexmock(BodhiUpdateTargetModel).should_receive("create").replace_with(_create)

    processing_results = SteveJobs().process_message(koji_build_tagged)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    results = run_koji_build_tag_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]

    pc = PackageConfigGetter.get_package_config_from_repo(project=packit_pagure_project)
    package_config = dump_package_config(pc)
    job_config = dump_job_config(pc.jobs[0])

    results = run_bodhi_update_from_sidetag(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]
