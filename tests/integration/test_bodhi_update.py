# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json

import pytest
from celery import Task
from celery.canvas import Signature
from fedora.client import AuthError
from flexmock import flexmock

from ogr.services.github import GithubProject
from packit.exceptions import PackitException

from ogr.services.pagure import PagureProject
from packit.api import PackitAPI
from packit.config import JobConfigTriggerType
from packit.local_project import LocalProject
from packit_service.constants import DEFAULT_RETRY_LIMIT
from packit_service.models import GitBranchModel, KojiBuildTargetModel, PipelineModel
from packit_service.utils import load_job_config, load_package_config
from packit_service.worker.celery_task import CeleryTask
from packit_service.worker.handlers.bodhi import CreateBodhiUpdateHandler
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.tasks import (
    run_bodhi_update,
)
from tests.spellbook import first_dict_value, get_parameters_from_results


def test_bodhi_update_for_unknown_koji_build(koji_build_completed_old_format):

    packit_yaml = (
        "{'specfile_path': 'packit.spec', 'synced_files': [],"
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
        ref="0eb3e12005cb18f15d3054020f7ac934c01eae08", filter_regex=r".+\.spec$"
    ).and_return(["packit.spec"])
    pagure_project.should_receive("get_file_content").with_args(
        path=".distro/source-git.yaml", ref="0eb3e12005cb18f15d3054020f7ac934c01eae08"
    ).and_raise(FileNotFoundError, "Not found.")
    pagure_project.should_receive("get_file_content").with_args(
        path=".packit.yaml", ref="0eb3e12005cb18f15d3054020f7ac934c01eae08"
    ).and_return(packit_yaml)

    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    # 1*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    flexmock(Signature).should_receive("apply_async").times(2)
    flexmock(Pushgateway).should_receive("push").once().and_return()
    flexmock(PackitAPI).should_receive("create_update").with_args(
        dist_git_branch="rawhide",
        update_type="enhancement",
        koji_builds=["packit-0.43.0-1.fc36"],
    )

    # Database structure
    run_model_flexmock = flexmock()
    git_branch_model_flexmock = flexmock(
        id=1, job_config_trigger_type=JobConfigTriggerType.commit
    )
    flexmock(KojiBuildTargetModel).should_receive("get_by_build_id").with_args(
        build_id=1864700
    ).and_return(None)
    flexmock(GitBranchModel).should_receive("get_or_create").and_return(
        git_branch_model_flexmock
    )
    flexmock(PipelineModel).should_receive("create").and_return(run_model_flexmock)
    flexmock(KojiBuildTargetModel).should_receive("create").with_args(
        build_id="1864700",
        commit_sha="0eb3e12005cb18f15d3054020f7ac934c01eae08",
        web_url="https://koji.fedoraproject.org/koji/taskinfo?taskID=79721403",
        target="noarch",
        status="COMPLETE",
        run_model=run_model_flexmock,
    ).and_return(flexmock(get_trigger_object=lambda: git_branch_model_flexmock))

    processing_results = SteveJobs().process_message(koji_build_completed_old_format)
    # 1*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    assert len(processing_results) == 2
    processing_results.pop()
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
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
        ref="0eb3e12005cb18f15d3054020f7ac934c01eae08", filter_regex=r".+\.spec$"
    ).and_return(["packit.spec"])
    pagure_project_mock.should_receive("get_file_content").with_args(
        path=".distro/source-git.yaml", ref="0eb3e12005cb18f15d3054020f7ac934c01eae08"
    ).and_raise(FileNotFoundError, "Not found.")
    pagure_project_mock.should_receive("get_file_content").with_args(
        path=".packit.yaml", ref="0eb3e12005cb18f15d3054020f7ac934c01eae08"
    ).and_return(packit_yaml)

    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    # 1*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    flexmock(Signature).should_receive("apply_async").times(2)
    flexmock(PackitAPI).should_receive("create_update").with_args(
        dist_git_branch="rawhide",
        update_type="enhancement",
        koji_builds=["packit-0.43.0-1.fc36"],
    ).and_raise(PackitException, "Failed to create an update")

    pagure_project_mock.should_receive("get_issue_list").times(0)
    pagure_project_mock.should_receive("create_issue").times(0)

    # Database structure
    run_model_flexmock = flexmock()
    git_branch_model_flexmock = flexmock(
        id=1, job_config_trigger_type=JobConfigTriggerType.commit
    )
    flexmock(KojiBuildTargetModel).should_receive("get_by_build_id").with_args(
        build_id=1864700
    ).and_return(None)
    flexmock(GitBranchModel).should_receive("get_or_create").and_return(
        git_branch_model_flexmock
    )
    flexmock(PipelineModel).should_receive("create").and_return(run_model_flexmock)
    flexmock(KojiBuildTargetModel).should_receive("create").with_args(
        build_id="1864700",
        commit_sha="0eb3e12005cb18f15d3054020f7ac934c01eae08",
        web_url="https://koji.fedoraproject.org/koji/taskinfo?taskID=79721403",
        target="noarch",
        status="COMPLETE",
        run_model=run_model_flexmock,
    ).and_return(flexmock(get_trigger_object=lambda: git_branch_model_flexmock))

    processing_results = SteveJobs().process_message(koji_build_completed_old_format)
    # 1*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    assert len(processing_results) == 2
    processing_results.pop()
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
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
        ref="0eb3e12005cb18f15d3054020f7ac934c01eae08", filter_regex=r".+\.spec$"
    ).and_return(["packit.spec"])
    pagure_project_mock.should_receive("get_file_content").with_args(
        path=".distro/source-git.yaml", ref="0eb3e12005cb18f15d3054020f7ac934c01eae08"
    ).and_raise(FileNotFoundError, "Not found.")
    pagure_project_mock.should_receive("get_file_content").with_args(
        path=".packit.yaml", ref="0eb3e12005cb18f15d3054020f7ac934c01eae08"
    ).and_return(packit_yaml)

    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    # 1*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    flexmock(Signature).should_receive("apply_async").times(2)
    flexmock(PackitAPI).should_receive("create_update").with_args(
        dist_git_branch="rawhide",
        update_type="enhancement",
        koji_builds=["packit-0.43.0-1.fc36"],
    ).and_raise(PackitException, "Failed to create an update")

    issue_project_mock = flexmock(GithubProject)
    issue_project_mock.should_receive("get_issue_list").and_return([]).once()
    issue_project_mock.should_receive("create_issue").and_return(
        flexmock(id=3, url="https://github.com/namespace/project/issues/3")
    ).once()

    # Database structure
    run_model_flexmock = flexmock()
    git_branch_model_flexmock = flexmock(
        id=1, job_config_trigger_type=JobConfigTriggerType.commit
    )
    flexmock(KojiBuildTargetModel).should_receive("get_by_build_id").with_args(
        build_id=1864700
    ).and_return(None)
    flexmock(GitBranchModel).should_receive("get_or_create").and_return(
        git_branch_model_flexmock
    )
    flexmock(PipelineModel).should_receive("create").and_return(run_model_flexmock)
    flexmock(KojiBuildTargetModel).should_receive("create").with_args(
        build_id="1864700",
        commit_sha="0eb3e12005cb18f15d3054020f7ac934c01eae08",
        web_url="https://koji.fedoraproject.org/koji/taskinfo?taskID=79721403",
        target="noarch",
        status="COMPLETE",
        run_model=run_model_flexmock,
    ).and_return(flexmock(get_trigger_object=lambda: git_branch_model_flexmock))

    processing_results = SteveJobs().process_message(koji_build_completed_old_format)
    # 1*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    assert len(processing_results) == 2
    processing_results.pop()
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    with pytest.raises(PackitException):
        CreateBodhiUpdateHandler(
            package_config=load_package_config(package_config),
            job_config=load_job_config(job_config),
            event=event_dict,
            # Needs to be the last try to inform user
            celery_task=flexmock(request=flexmock(retries=DEFAULT_RETRY_LIMIT)),
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
        ref="0eb3e12005cb18f15d3054020f7ac934c01eae08", filter_regex=r".+\.spec$"
    ).and_return(["packit.spec"])
    pagure_project_mock.should_receive("get_file_content").with_args(
        path=".distro/source-git.yaml", ref="0eb3e12005cb18f15d3054020f7ac934c01eae08"
    ).and_raise(FileNotFoundError, "Not found.")
    pagure_project_mock.should_receive("get_file_content").with_args(
        path=".packit.yaml", ref="0eb3e12005cb18f15d3054020f7ac934c01eae08"
    ).and_return(packit_yaml)

    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    # 1*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    flexmock(Signature).should_receive("apply_async").times(2)
    flexmock(PackitAPI).should_receive("create_update").with_args(
        dist_git_branch="rawhide",
        update_type="enhancement",
        koji_builds=["packit-0.43.0-1.fc36"],
    ).and_raise(PackitException, "Failed to create an update")

    issue_project_mock = flexmock(GithubProject)
    issue_project_mock.should_receive("get_issue_list").and_return(
        [
            flexmock(
                id=3,
                title="[packit] Fedora Bodhi update failed to be created",
                url="https://github.com/namespace/project/issues/3",
            )
            .should_receive("comment")
            .once()
            .mock()
        ]
    ).once()
    issue_project_mock.should_receive("create_issue").times(0)

    # Database structure
    run_model_flexmock = flexmock()
    git_branch_model_flexmock = flexmock(
        id=1, job_config_trigger_type=JobConfigTriggerType.commit
    )
    flexmock(KojiBuildTargetModel).should_receive("get_by_build_id").with_args(
        build_id=1864700
    ).and_return(None)
    flexmock(GitBranchModel).should_receive("get_or_create").and_return(
        git_branch_model_flexmock
    )
    flexmock(PipelineModel).should_receive("create").and_return(run_model_flexmock)
    flexmock(KojiBuildTargetModel).should_receive("create").with_args(
        build_id="1864700",
        commit_sha="0eb3e12005cb18f15d3054020f7ac934c01eae08",
        web_url="https://koji.fedoraproject.org/koji/taskinfo?taskID=79721403",
        target="noarch",
        status="COMPLETE",
        run_model=run_model_flexmock,
    ).and_return(flexmock(get_trigger_object=lambda: git_branch_model_flexmock))

    processing_results = SteveJobs().process_message(koji_build_completed_old_format)
    # 1*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    assert len(processing_results) == 2
    processing_results.pop()
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    with pytest.raises(PackitException):
        CreateBodhiUpdateHandler(
            package_config=load_package_config(package_config),
            job_config=load_job_config(job_config),
            event=event_dict,
            # Needs to be the last try to inform user
            celery_task=flexmock(request=flexmock(retries=DEFAULT_RETRY_LIMIT)),
        ).run_job()


def test_bodhi_update_auth_error(
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
        ref="0eb3e12005cb18f15d3054020f7ac934c01eae08", filter_regex=r".+\.spec$"
    ).and_return(["packit.spec"])
    pagure_project_mock.should_receive("get_file_content").with_args(
        path=".distro/source-git.yaml", ref="0eb3e12005cb18f15d3054020f7ac934c01eae08"
    ).and_raise(FileNotFoundError, "Not found.")
    pagure_project_mock.should_receive("get_file_content").with_args(
        path=".packit.yaml", ref="0eb3e12005cb18f15d3054020f7ac934c01eae08"
    ).and_return(packit_yaml)

    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    # 1*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    flexmock(Signature).should_receive("apply_async").times(2)
    flexmock(Pushgateway).should_receive("push").once().and_return()

    bodhi_exception = PackitException("packit exception")
    bodhi_exception.__cause__ = AuthError("auth error")
    flexmock(PackitAPI).should_receive("create_update").with_args(
        dist_git_branch="rawhide",
        update_type="enhancement",
        koji_builds=["packit-0.43.0-1.fc36"],
    ).and_raise(bodhi_exception)

    issue_project_mock = flexmock(GithubProject)
    issue_project_mock.should_receive("get_issue_list").and_return([]).once()
    issue_project_mock.should_receive("create_issue").with_args(
        title="[packit] Fedora Bodhi update failed to be created",
        body="Bodhi update creation failed for `packit-0.43.0-1.fc36` "
        "because of the missing permissions.\n\n"
        "Please, give packit user `commit` rights "
        "in the [dist-git settings](https://src.fedoraproject.org/rpms/packit/adduser).\n\n"
        "*Try 1/3: Task will be retried in 10 minutes.*\n\n"
        "---\n\n"
        "*Get in [touch with us](https://packit.dev/#contact) if you need some help.*",
    ).and_return(
        flexmock(id=3, url="https://github.com/namespace/project/issues/3")
    ).once()

    # Database structure
    run_model_flexmock = flexmock()
    git_branch_model_flexmock = flexmock(
        id=1, job_config_trigger_type=JobConfigTriggerType.commit
    )
    flexmock(KojiBuildTargetModel).should_receive("get_by_build_id").with_args(
        build_id=1864700
    ).and_return(None)
    flexmock(GitBranchModel).should_receive("get_or_create").and_return(
        git_branch_model_flexmock
    )
    flexmock(PipelineModel).should_receive("create").and_return(run_model_flexmock)
    flexmock(KojiBuildTargetModel).should_receive("create").with_args(
        build_id="1864700",
        commit_sha="0eb3e12005cb18f15d3054020f7ac934c01eae08",
        web_url="https://koji.fedoraproject.org/koji/taskinfo?taskID=79721403",
        target="noarch",
        status="COMPLETE",
        run_model=run_model_flexmock,
    ).and_return(flexmock(get_trigger_object=lambda: git_branch_model_flexmock))

    flexmock(Task).should_receive("retry").and_return().once()
    flexmock(CeleryTask).should_call("retry").with_args(
        ex=bodhi_exception, delay=600
    ).once()

    processing_results = SteveJobs().process_message(koji_build_completed_old_format)
    # 1*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    assert len(processing_results) == 2
    processing_results.pop()
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
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
        "{'specfile_path': 'packit.spec', 'synced_files': [],"
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
        ref="0eb3e12005cb18f15d3054020f7ac934c01eae08", filter_regex=r".+\.spec$"
    ).and_return(["packit.spec"])
    pagure_project.should_receive("get_file_content").with_args(
        path=".distro/source-git.yaml", ref="0eb3e12005cb18f15d3054020f7ac934c01eae08"
    ).and_raise(FileNotFoundError, "Not found.")
    pagure_project.should_receive("get_file_content").with_args(
        path=".packit.yaml", ref="0eb3e12005cb18f15d3054020f7ac934c01eae08"
    ).and_return(packit_yaml)

    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    # 0*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    flexmock(Signature).should_receive("apply_async").times(1)
    flexmock(Pushgateway).should_receive("push").times(0)
    flexmock(PackitAPI).should_receive("create_update").times(0)

    # Database structure
    run_model_flexmock = flexmock()
    git_branch_model_flexmock = flexmock(
        id=1, job_config_trigger_type=JobConfigTriggerType.commit
    )
    flexmock(KojiBuildTargetModel).should_receive("get_by_build_id").with_args(
        build_id=1864700
    ).and_return(None)
    flexmock(GitBranchModel).should_receive("get_or_create").and_return(
        git_branch_model_flexmock
    )
    flexmock(PipelineModel).should_receive("create").and_return(run_model_flexmock)
    flexmock(KojiBuildTargetModel).should_receive("create").with_args(
        build_id="1864700",
        commit_sha="0eb3e12005cb18f15d3054020f7ac934c01eae08",
        web_url="https://koji.fedoraproject.org/koji/taskinfo?taskID=79721403",
        target="noarch",
        status="BUILDING",
        run_model=run_model_flexmock,
    ).and_return(flexmock(get_trigger_object=lambda: git_branch_model_flexmock))

    processing_results = SteveJobs().process_message(koji_build_start_old_format)
    # 0*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    assert len(processing_results) == 1


def test_bodhi_update_for_known_koji_build(koji_build_completed_old_format):

    packit_yaml = (
        "{'specfile_path': 'packit.spec', 'synced_files': [],"
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
        ref="0eb3e12005cb18f15d3054020f7ac934c01eae08", filter_regex=r".+\.spec$"
    ).and_return(["packit.spec"])
    pagure_project.should_receive("get_file_content").with_args(
        path=".distro/source-git.yaml", ref="0eb3e12005cb18f15d3054020f7ac934c01eae08"
    ).and_raise(FileNotFoundError, "Not found.")
    pagure_project.should_receive("get_file_content").with_args(
        path=".packit.yaml", ref="0eb3e12005cb18f15d3054020f7ac934c01eae08"
    ).and_return(packit_yaml)

    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    # 1*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    flexmock(Signature).should_receive("apply_async").times(2)
    flexmock(Pushgateway).should_receive("push").once().and_return()
    flexmock(PackitAPI).should_receive("create_update").with_args(
        dist_git_branch="rawhide",
        update_type="enhancement",
        koji_builds=["packit-0.43.0-1.fc36"],
    )

    # Database structure
    flexmock(KojiBuildTargetModel).should_receive("get_by_build_id").with_args(
        build_id=1864700
    ).and_return(
        flexmock(
            get_trigger_object=lambda: flexmock(
                id=1, job_config_trigger_type=JobConfigTriggerType.commit
            )
        )
    )

    processing_results = SteveJobs().process_message(koji_build_completed_old_format)
    # 1*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    assert len(processing_results) == 2
    processing_results.pop()
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
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
        "{'specfile_path': 'packit.spec', 'synced_files': [],"
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
        ref="0eb3e12005cb18f15d3054020f7ac934c01eae08", filter_regex=r".+\.spec$"
    ).and_return(["packit.spec"])
    pagure_project.should_receive("get_file_content").with_args(
        path=".distro/source-git.yaml", ref="0eb3e12005cb18f15d3054020f7ac934c01eae08"
    ).and_raise(FileNotFoundError, "Not found.")
    pagure_project.should_receive("get_file_content").with_args(
        path=".packit.yaml", ref="0eb3e12005cb18f15d3054020f7ac934c01eae08"
    ).and_return(packit_yaml)

    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    # 0*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    flexmock(Signature).should_receive("apply_async").times(1)
    flexmock(Pushgateway).should_receive("push").times(0)
    flexmock(PackitAPI).should_receive("create_update").times(0)

    # Database structure
    run_model_flexmock = flexmock()
    git_branch_model_flexmock = flexmock(
        id=1, job_config_trigger_type=JobConfigTriggerType.commit
    )
    flexmock(KojiBuildTargetModel).should_receive("get_by_build_id").with_args(
        build_id=1864700
    ).and_return(None)
    flexmock(GitBranchModel).should_receive("get_or_create").and_return(
        git_branch_model_flexmock
    )
    flexmock(PipelineModel).should_receive("create").and_return(run_model_flexmock)
    flexmock(KojiBuildTargetModel).should_receive("create").with_args(
        build_id="1864700",
        commit_sha="0eb3e12005cb18f15d3054020f7ac934c01eae08",
        web_url="https://koji.fedoraproject.org/koji/taskinfo?taskID=79721403",
        target="noarch",
        status="COMPLETE",
        run_model=run_model_flexmock,
    ).and_return(flexmock(get_trigger_object=lambda: git_branch_model_flexmock))

    processing_results = SteveJobs().process_message(koji_build_completed_old_format)
    # 0*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    assert len(processing_results)


def test_bodhi_update_fedora_stable_by_default(koji_build_completed_f35):
    """(Known build scenario.)"""
    packit_yaml = (
        "{'specfile_path': 'python-ogr.spec', 'synced_files': [],"
        "'jobs': [{'trigger': 'commit', 'job': 'bodhi_update'}],"
        "'downstream_package_name': 'python-ogr'}"
    )
    pagure_project = flexmock(
        PagureProject,
        full_repo_name="rpms/python-ogr",
        get_web_url=lambda: "https://src.fedoraproject.org/rpms/python-ogr",
        default_branch="main",
    )
    pagure_project.should_receive("get_files").with_args(
        ref="51b57ec04f5e6e9066ac859a1408cfbf1ead307e", filter_regex=r".+\.spec$"
    ).and_return(["packit.spec"])
    pagure_project.should_receive("get_file_content").with_args(
        path=".distro/source-git.yaml", ref="51b57ec04f5e6e9066ac859a1408cfbf1ead307e"
    ).and_raise(FileNotFoundError, "Not found.")
    pagure_project.should_receive("get_file_content").with_args(
        path=".packit.yaml", ref="51b57ec04f5e6e9066ac859a1408cfbf1ead307e"
    ).and_return(packit_yaml)

    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    # 1*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    flexmock(Signature).should_receive("apply_async").times(2)
    flexmock(Pushgateway).should_receive("push").once().and_return()
    flexmock(PackitAPI).should_receive("create_update").with_args(
        dist_git_branch="f35",
        update_type="enhancement",
        koji_builds=["python-ogr-0.34.0-1.fc35"],
    ).once()

    # Database not touched
    flexmock(KojiBuildTargetModel).should_receive("get_by_build_id").with_args(
        build_id=1874070
    ).times(0)

    processing_results = SteveJobs().process_message(koji_build_completed_f35)
    # 1*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    assert len(processing_results) == 2
    processing_results.pop()
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)
    results = run_bodhi_update(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]
