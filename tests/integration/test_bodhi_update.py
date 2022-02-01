# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json

from celery.canvas import Signature
from flexmock import flexmock

from ogr.services.pagure import PagureProject
from packit.api import PackitAPI
from packit.config import JobConfigTriggerType
from packit.constants import DEFAULT_BODHI_NOTE
from packit.local_project import LocalProject
from packit.utils.repo import RepositoryCache
from packit_service.config import ServiceConfig
from packit_service.constants import SANDCASTLE_WORK_DIR
from packit_service.models import GitBranchModel, KojiBuildModel, RunModel
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

    flexmock(ServiceConfig).should_receive("get_service_config").and_return(
        ServiceConfig(
            command_handler_work_dir=SANDCASTLE_WORK_DIR,
            repository_cache="/tmp/repository-cache",
            add_repositories_to_repository_cache=False,
        )
    )

    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(RepositoryCache).should_call("__init__").once()
    # 1*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    flexmock(Signature).should_receive("apply_async").times(2)
    flexmock(Pushgateway).should_receive("push").once().and_return()
    flexmock(PackitAPI).should_receive("create_update").with_args(
        dist_git_branch="rawhide",
        update_type="enhancement",
        update_notes=DEFAULT_BODHI_NOTE,
        koji_builds=["1864700"],
    )

    # Database structure
    run_model_flexmock = flexmock()
    git_branch_model_flexmock = flexmock(
        id=1, job_config_trigger_type=JobConfigTriggerType.commit
    )
    flexmock(KojiBuildModel).should_receive("get_by_build_id").with_args(
        build_id=1864700
    ).and_return(None)
    flexmock(GitBranchModel).should_receive("get_or_create").and_return(
        git_branch_model_flexmock
    )
    flexmock(RunModel).should_receive("create").and_return(run_model_flexmock)
    flexmock(KojiBuildModel).should_receive("create").with_args(
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

    flexmock(ServiceConfig).should_receive("get_service_config").and_return(
        ServiceConfig(
            command_handler_work_dir=SANDCASTLE_WORK_DIR,
            repository_cache="/tmp/repository-cache",
            add_repositories_to_repository_cache=False,
        )
    )

    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(RepositoryCache).should_call("__init__").times(0)
    # 0*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    flexmock(Signature).should_receive("apply_async").times(1)
    flexmock(Pushgateway).should_receive("push").times(0)
    flexmock(PackitAPI).should_receive("create_update").times(0)

    # Database structure
    run_model_flexmock = flexmock()
    git_branch_model_flexmock = flexmock(
        id=1, job_config_trigger_type=JobConfigTriggerType.commit
    )
    flexmock(KojiBuildModel).should_receive("get_by_build_id").with_args(
        build_id=1864700
    ).and_return(None)
    flexmock(GitBranchModel).should_receive("get_or_create").and_return(
        git_branch_model_flexmock
    )
    flexmock(RunModel).should_receive("create").and_return(run_model_flexmock)
    flexmock(KojiBuildModel).should_receive("create").with_args(
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

    flexmock(ServiceConfig).should_receive("get_service_config").and_return(
        ServiceConfig(
            command_handler_work_dir=SANDCASTLE_WORK_DIR,
            repository_cache="/tmp/repository-cache",
            add_repositories_to_repository_cache=False,
        )
    )

    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(RepositoryCache).should_call("__init__").once()
    # 1*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    flexmock(Signature).should_receive("apply_async").times(2)
    flexmock(Pushgateway).should_receive("push").once().and_return()
    flexmock(PackitAPI).should_receive("create_update").with_args(
        dist_git_branch="rawhide",
        update_type="enhancement",
        update_notes=DEFAULT_BODHI_NOTE,
        koji_builds=["1864700"],
    )

    # Database structure
    flexmock(KojiBuildModel).should_receive("get_by_build_id").with_args(
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

    flexmock(ServiceConfig).should_receive("get_service_config").and_return(
        ServiceConfig(
            command_handler_work_dir=SANDCASTLE_WORK_DIR,
            repository_cache="/tmp/repository-cache",
            add_repositories_to_repository_cache=False,
        )
    )

    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(RepositoryCache).should_call("__init__").times(0)
    # 0*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    flexmock(Signature).should_receive("apply_async").times(1)
    flexmock(Pushgateway).should_receive("push").times(0)
    flexmock(PackitAPI).should_receive("create_update").times(0)

    # Database structure
    run_model_flexmock = flexmock()
    git_branch_model_flexmock = flexmock(
        id=1, job_config_trigger_type=JobConfigTriggerType.commit
    )
    flexmock(KojiBuildModel).should_receive("get_by_build_id").with_args(
        build_id=1864700
    ).and_return(None)
    flexmock(GitBranchModel).should_receive("get_or_create").and_return(
        git_branch_model_flexmock
    )
    flexmock(RunModel).should_receive("create").and_return(run_model_flexmock)
    flexmock(KojiBuildModel).should_receive("create").with_args(
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

    flexmock(ServiceConfig).should_receive("get_service_config").and_return(
        ServiceConfig(
            command_handler_work_dir=SANDCASTLE_WORK_DIR,
            repository_cache="/tmp/repository-cache",
            add_repositories_to_repository_cache=False,
        )
    )

    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(RepositoryCache).should_call("__init__").once()
    # 1*CreateBodhiUpdateHandler + 1*KojiBuildReportHandler
    flexmock(Signature).should_receive("apply_async").times(2)
    flexmock(Pushgateway).should_receive("push").once().and_return()
    flexmock(PackitAPI).should_receive("create_update").with_args(
        dist_git_branch="f35",
        update_type="enhancement",
        update_notes=DEFAULT_BODHI_NOTE,
        koji_builds=["1874070"],
    ).once()

    # Database structure
    flexmock(KojiBuildModel).should_receive("get_by_build_id").with_args(
        build_id=1874070
    ).and_return(
        flexmock(
            get_trigger_object=lambda: flexmock(
                id=1, job_config_trigger_type=JobConfigTriggerType.commit
            )
        )
    ).once()

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
