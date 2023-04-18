# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
import pytest
import packit_service

from requests import HTTPError
from flexmock import flexmock
from flexmock import Mock
from packit.config.job_config import JobConfigTriggerType, JobType
from packit_service.config import ServiceConfig
from packit_service.models import (
    VMImageBuildTargetModel,
    VMImageBuildStatus,
    JobTriggerModelType,
)
from packit_service.worker.helpers.build.babysit import (
    check_pending_vm_image_builds,
    update_vm_image_build,
    UpdateImageBuildHelper,
)
from packit_service.worker.events import VMImageBuildResultEvent
from packit_service.worker.handlers import VMImageBuildResultHandler
from packit_service.worker.monitoring import Pushgateway


def test_check_pending_vm_image_builds():
    flexmock(VMImageBuildTargetModel).should_receive("get_all_by_status").with_args(
        VMImageBuildStatus.pending
    ).and_return([flexmock(build_id=1)])
    flexmock(packit_service.worker.helpers.build.babysit).should_receive(
        "update_vm_image_build"
    ).with_args(1, Mock)
    check_pending_vm_image_builds()


def test_check_no_pending_vm_image_builds():
    flexmock(VMImageBuildTargetModel).should_receive("get_all_by_status").with_args(
        VMImageBuildStatus.pending
    ).and_return([])
    flexmock(packit_service.worker.helpers.build.babysit).should_receive(
        "update_vm_image_build"
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
                }
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
    db_trigger = flexmock(
        id=1,
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        job_trigger_model_type=JobTriggerModelType.pull_request,
    )
    if not vm_image_builder_result:
        flexmock(UpdateImageBuildHelper).should_receive("vm_image_builder").and_return(
            flexmock()
            .should_receive("image_builder_request")
            .and_raise(HTTPError("unknown ex"))
            .mock()
        )
    else:
        flexmock(UpdateImageBuildHelper).should_receive("vm_image_builder").and_return(
            flexmock()
            .should_receive("image_builder_request")
            .and_return(
                flexmock()
                .should_receive("json")
                .and_return(vm_image_builder_result)
                .mock()
            )
            .mock()
        )
    flexmock(VMImageBuildResultEvent).should_receive("get_packages_config").and_return(
        flexmock(
            get_package_config_for=lambda job_config: flexmock(),
            get_job_views=lambda: [
                flexmock(
                    trigger=JobConfigTriggerType.pull_request,
                    type=JobType.vm_image_build,
                )
            ],
        )
    )
    flexmock(VMImageBuildResultEvent).should_receive(
        "job_config_trigger_type"
    ).and_return(JobConfigTriggerType.pull_request)
    flexmock(VMImageBuildTargetModel).should_receive("get_all_by_build_id").with_args(
        1
    ).and_return(
        [
            flexmock(
                status=None,
                runs=[
                    flexmock()
                    .should_receive("get_trigger_object")
                    .and_return(db_trigger)
                    .mock()
                ],
            )
            .should_receive("set_status")
            .with_args(build_status)
            .mock()
        ]
    )

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
            ),
        )
        == stop_babysitting
    )
