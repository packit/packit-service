# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json

import pytest
from celery.canvas import Signature
from flexmock import flexmock

from ogr.services.pagure import PagureProject
from packit.config import JobConfigTriggerType
from packit_service.config import ServiceConfig
from packit_service.constants import SANDCASTLE_WORK_DIR
from packit_service.models import GitBranchModel, KojiBuildModel, RunModel
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.tasks import (
    run_downstream_koji_build_report,
)
from tests.conftest import koji_build_completed_rawhide, koji_build_start_rawhide
from tests.spellbook import first_dict_value, get_parameters_from_results


@pytest.mark.parametrize(
    "koji_build_fixture", [koji_build_start_rawhide, koji_build_completed_rawhide]
)
def test_downstream_koji_build_report_known_build(koji_build_fixture, request):
    koji_build_event = request.getfixturevalue(koji_build_fixture.__name__)
    packit_yaml = (
        "{'specfile_path': 'python-ogr.spec', 'synced_files': [],"
        "'jobs': [{'trigger': 'commit', 'job': 'koji_build'}],"
        "'downstream_package_name': 'python-ogr'}"
    )
    pagure_project = flexmock(
        PagureProject,
        full_repo_name="rpms/python-ogr",
        get_web_url=lambda: "https://src.fedoraproject.org/rpms/python-ogr",
        default_branch="main",
    )
    pagure_project.should_receive("get_files").with_args(
        ref="e029dd5250dde9a37a2cdddb6d822d973b09e5da", filter_regex=r".+\.spec$"
    ).and_return(["python-ogr.spec"])
    pagure_project.should_receive("get_file_content").with_args(
        path=".distro/source-git.yaml", ref="e029dd5250dde9a37a2cdddb6d822d973b09e5da"
    ).and_raise(FileNotFoundError, "Not found.")
    pagure_project.should_receive("get_file_content").with_args(
        path=".packit.yaml", ref="e029dd5250dde9a37a2cdddb6d822d973b09e5da"
    ).and_return(packit_yaml)

    flexmock(GitBranchModel).should_receive("get_or_create").with_args(
        branch_name="main",
        namespace="rpms",
        repo_name="python-ogr",
        project_url="https://src.fedoraproject.org/rpms/python-ogr",
    ).and_return(flexmock(id=9, job_config_trigger_type=JobConfigTriggerType.commit))

    flexmock(ServiceConfig).should_receive("get_service_config").and_return(
        ServiceConfig(
            command_handler_work_dir=SANDCASTLE_WORK_DIR,
            repository_cache="/tmp/repository-cache",
            add_repositories_to_repository_cache=False,
        )
    )

    # 1*KojiBuildReportHandler
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").once().and_return()

    # Database
    flexmock(KojiBuildModel).should_receive("get_by_build_id").with_args(
        build_id=1874074
    ).and_return(
        flexmock(
            target="target",
            status="pending",
            web_url="some-url",
            build_logs_url=None,
            get_trigger_object=lambda: flexmock(
                id=1, job_config_trigger_type=JobConfigTriggerType.commit
            ),
        )
        .should_receive("set_build_logs_url")
        .with_args()
        .and_return()
        .mock()
    ).once()  # only when running a handler

    processing_results = SteveJobs().process_message(koji_build_event)
    # 1*KojiBuildReportHandler
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)
    results = run_downstream_koji_build_report(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]


@pytest.mark.parametrize(
    "koji_build_fixture", [koji_build_start_rawhide, koji_build_completed_rawhide]
)
def test_downstream_koji_build_report_unknown_build(koji_build_fixture, request):
    koji_build_event = request.getfixturevalue(koji_build_fixture.__name__)

    packit_yaml = (
        "{'specfile_path': 'python-ogr.spec', 'synced_files': [],"
        "'jobs': [{'trigger': 'commit', 'job': 'koji_build'}],"
        "'downstream_package_name': 'python-ogr'}"
    )
    pagure_project = flexmock(
        PagureProject,
        full_repo_name="rpms/python-ogr",
        get_web_url=lambda: "https://src.fedoraproject.org/rpms/python-ogr",
        default_branch="main",
    )
    pagure_project.should_receive("get_files").with_args(
        ref="e029dd5250dde9a37a2cdddb6d822d973b09e5da", filter_regex=r".+\.spec$"
    ).and_return(["python-ogr.spec"])
    pagure_project.should_receive("get_file_content").with_args(
        path=".distro/source-git.yaml", ref="e029dd5250dde9a37a2cdddb6d822d973b09e5da"
    ).and_raise(FileNotFoundError, "Not found.")
    pagure_project.should_receive("get_file_content").with_args(
        path=".packit.yaml", ref="e029dd5250dde9a37a2cdddb6d822d973b09e5da"
    ).and_return(packit_yaml)

    flexmock(GitBranchModel).should_receive("get_or_create").with_args(
        branch_name="main",
        namespace="rpms",
        repo_name="python-ogr",
        project_url="https://src.fedoraproject.org/rpms/python-ogr",
    ).and_return(flexmock(id=9, job_config_trigger_type=JobConfigTriggerType.commit))

    flexmock(ServiceConfig).should_receive("get_service_config").and_return(
        ServiceConfig(
            command_handler_work_dir=SANDCASTLE_WORK_DIR,
            repository_cache="/tmp/repository-cache",
            add_repositories_to_repository_cache=False,
        )
    )

    # 1*KojiBuildReportHandler
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").once().and_return()

    # Database
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
    flexmock(KojiBuildModel).should_receive("get_by_build_id").with_args(
        build_id=1874074
    ).and_return(
        flexmock(
            target="noarch",
            status="BUILDING",
            web_url="https://koji.fedoraproject.org/koji/taskinfo?taskID=79721403",
            build_logs_url=None,
            get_trigger_object=lambda: flexmock(
                id=1, job_config_trigger_type=JobConfigTriggerType.commit
            ),
        )
        .should_receive("set_build_logs_url")
        .with_args()
        .and_return()
        .mock()
    ).once()  # only when running a handler

    processing_results = SteveJobs().process_message(koji_build_event)
    # 1*KojiBuildReportHandler
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)
    results = run_downstream_koji_build_report(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]
