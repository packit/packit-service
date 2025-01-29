# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
import datetime

import pytest
from flexmock import Mock, flexmock
from packit.config.job_config import JobConfigTriggerType
from requests import HTTPError

import packit_service
from packit_service.config import ServiceConfig
from packit_service.events.vm_image import Result
from packit_service.models import (
    ProjectEventModelType,
    VMImageBuildStatus,
    VMImageBuildTargetModel,
)
from packit_service.worker.handlers import VMImageBuildResultHandler
from packit_service.worker.helpers.build.babysit import (
    UpdateImageBuildHelper,
    check_pending_vm_image_builds,
    update_vm_image_build,
)
from packit_service.worker.monitoring import Pushgateway


def test_check_pending_vm_image_builds():
    flexmock(VMImageBuildTargetModel).should_receive("get_all_by_status").with_args(
        VMImageBuildStatus.pending,
    ).and_return(
        [
            flexmock(
                build_id=1,
                build_submitted_time=datetime.datetime.utcnow() - datetime.timedelta(days=1),
            ),
        ],
    )
    flexmock(packit_service.worker.helpers.build.babysit).should_receive(
        "update_vm_image_build",
    ).with_args(1, Mock)
    check_pending_vm_image_builds()


def test_check_pending_vm_image_builds_timeout():
    flexmock(VMImageBuildTargetModel).should_receive("get_all_by_status").with_args(
        VMImageBuildStatus.pending,
    ).and_return(
        [
            flexmock(
                build_id=1,
                build_submitted_time=datetime.datetime.utcnow() - datetime.timedelta(weeks=2),
            )
            .should_receive("set_status")
            .mock(),
        ],
    )
    flexmock(packit_service.worker.helpers.build.babysit).should_receive(
        "update_vm_image_build",
    ).never()
    check_pending_vm_image_builds()


def test_check_no_pending_vm_image_builds():
    flexmock(VMImageBuildTargetModel).should_receive("get_all_by_status").with_args(
        VMImageBuildStatus.pending,
    ).and_return([])
    flexmock(packit_service.worker.helpers.build.babysit).should_receive(
        "update_vm_image_build",
    ).never()
    check_pending_vm_image_builds()


@pytest.mark.parametrize(
    "stop_babysitting, build_status, vm_image_builder_result",
    (
        pytest.param(
            True,
            "error",
            None,
            id="No result from vm image builder server. An exception was raised.",
        ),
        pytest.param(
            True,
            "failure",
            {"image_status": {"status": "failure", "error": "no dnf package found"}},
            id="Failed build",
        ),
        pytest.param(
            True,
            "success",
            {
                "image_status": {
                    "status": "success",
                    "error": "",
                    "upload_status": {
                        "type": "aws",
                        "options": {
                            "ami": "ami-0c830793775595d4b",
                            "region": "eu-west-1",
                        },
                    },
                },
            },
            id="Successfull build",
        ),
        pytest.param(
            False,
            "building",
            {"image_status": {"status": "building", "error": ""}},
            id="Still in progress build",
        ),
    ),
)
def test_update_vm_image_build(stop_babysitting, build_status, vm_image_builder_result):
    db_project_object = flexmock(
        id=1,
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        project_event_model_type=ProjectEventModelType.pull_request,
    )
    if not vm_image_builder_result:
        flexmock(UpdateImageBuildHelper).should_receive("vm_image_builder").and_return(
            flexmock()
            .should_receive("image_builder_request")
            .and_raise(HTTPError("unknown ex"))
            .mock(),
        )
    else:
        flexmock(UpdateImageBuildHelper).should_receive("vm_image_builder").and_return(
            flexmock()
            .should_receive("image_builder_request")
            .and_return(
                flexmock().should_receive("json").and_return(vm_image_builder_result).mock(),
            )
            .mock(),
        )
    flexmock(Result).should_receive(
        "job_config_trigger_type",
    ).and_return(JobConfigTriggerType.pull_request)
    vm_image_model = (
        flexmock(
            status=None,
            runs=[
                flexmock(
                    project_event=flexmock(
                        packages_config={
                            "downstream_package_name": "package",
                            "specfile_path": "path",
                            "jobs": [
                                {"job": "vm_image_build", "trigger": "pull_request"},
                            ],
                        },
                    ),
                )
                .should_receive("get_project_event_object")
                .and_return(db_project_object)
                .mock(),
            ],
        )
        .should_receive("set_status")
        .with_args(build_status)
        .mock()
    )
    flexmock(VMImageBuildTargetModel).should_receive("get_by_build_id").with_args(
        1,
    ).and_return(vm_image_model)
    flexmock(VMImageBuildTargetModel).should_receive("get_all_by_build_id").with_args(
        1,
    ).and_return([vm_image_model])

    flexmock(VMImageBuildResultHandler).should_receive("report_status")
    flexmock(ServiceConfig).should_receive("get_project").and_return()
    if stop_babysitting:
        flexmock(Pushgateway).should_receive("push").once().and_return()
    assert (
        update_vm_image_build(
            1,
            flexmock(
                build_id=1,
                project_url="an url",
                target="a target",
                get_pr_id=lambda: 21,
                owner="owner",
                commit_sha="123456",
                manual_trigger=False,
            ),
        )
        == stop_babysitting
    )
