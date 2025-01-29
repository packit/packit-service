# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from celery import Celery
from flexmock import Mock, flexmock

from packit_service.events import (
    github,
    vm_image,
)
from packit_service.models import (
    PipelineModel,
    ProjectEventModel,
    VMImageBuildStatus,
    VMImageBuildTargetModel,
)
from packit_service.worker.handlers import (
    VMImageBuildHandler,
    VMImageBuildResultHandler,
)
from packit_service.worker.handlers.vm_image import (
    GetVMImageBuildReporterFromJobHelperMixin,
)
from packit_service.worker.reporting import BaseCommitStatus, StatusReporter
from packit_service.worker.result import TaskResults


def test_get_vm_image_build_reporter_from_job_helper_mixin(
    fake_package_config_job_config_project_db_trigger,
):
    class Test(GetVMImageBuildReporterFromJobHelperMixin):
        def __init__(self) -> None:
            super().__init__()
            (
                package_config,
                job_config,
                project,
                db_project_object,
            ) = fake_package_config_job_config_project_db_trigger
            self.package_config = package_config
            self.job_config = job_config
            self._project = project
            self.data = flexmock(
                commit_sha="123456",
                pr_id="21",
                db_project_event=flexmock(id=1)
                .should_receive("get_project_event_object")
                .and_return(db_project_object)
                .mock(),
            )

    mixin = Test()

    flexmock(ProjectEventModel).should_receive("get_or_create").and_return(
        flexmock(id=1),
    )
    flexmock(StatusReporter).should_receive("report").with_args(
        description="Building VM Image...",
        state=BaseCommitStatus.pending,
        url="",
        check_names=["vm-image-build:fedora-36-x86_64"],
        markdown_content="",
        links_to_external_services=None,
        update_feedback_time=None,
    )
    mixin.report_status(VMImageBuildStatus.pending, "")

    flexmock(StatusReporter).should_receive("report").with_args(
        description="VM Image build error...",
        state=BaseCommitStatus.error,
        url="",
        check_names=["vm-image-build:fedora-36-x86_64"],
        markdown_content="",
        links_to_external_services=None,
        update_feedback_time=None,
    )
    mixin.report_status(VMImageBuildStatus.error, "")

    flexmock(StatusReporter).should_receive("report").with_args(
        description="VM Image build failed...",
        state=BaseCommitStatus.failure,
        url="",
        check_names=["vm-image-build:fedora-36-x86_64"],
        markdown_content="",
        links_to_external_services=None,
        update_feedback_time=None,
    )
    mixin.report_status(VMImageBuildStatus.failure, "")

    flexmock(StatusReporter).should_receive("report").with_args(
        description="VM Image build is complete",
        state=BaseCommitStatus.success,
        url="",
        check_names=["vm-image-build:fedora-36-x86_64"],
        markdown_content="",
        links_to_external_services=None,
        update_feedback_time=None,
    )
    mixin.report_status(VMImageBuildStatus.success, "")

    flexmock(StatusReporter).should_receive("report").with_args(
        description="VM Image Build job failed internal checks",
        state=BaseCommitStatus.neutral,
        url="https://packit.dev/docs/cli/build/in-image-builder/",
        check_names=["vm-image-build:fedora-36-x86_64"],
        markdown_content="",
        links_to_external_services=None,
        update_feedback_time=None,
    )
    mixin.report_pre_check_failure("")


def test_vm_image_build_handler(fake_package_config_job_config_project_db_trigger):
    (
        package_config,
        job_config,
        project,
        db_project_object,
    ) = fake_package_config_job_config_project_db_trigger
    handler = VMImageBuildHandler(
        package_config,
        job_config,
        {
            "event_type": github.pr.Comment.event_type(),
            "project_url": "https://github.com/majamassarini/knx-stack",
            "commit_sha": "4321aa",
            "pr_id": 21,
        },
        None,
    )
    flexmock(db_project_object).should_receive("__str__").and_return(
        "db_project_object",
    )
    handler.data._db_project_event = flexmock()
    handler.data._db_project_object = db_project_object
    handler._project = project
    handler._packit_api = flexmock(copr_helper=flexmock())

    repo_download_url = (
        "https://download.copr.fedorainfracloud.org/results/mmassari/knx-stack/fedora-36-x86_64/"
    )
    handler.packit_api.copr_helper.should_receive("get_repo_download_url").with_args(
        owner="mmassari",
        project="knx-stack",
        chroot="fedora-36-x86_64",
    ).and_return(repo_download_url)
    flexmock(handler).should_receive("vm_image_builder").and_return(
        flexmock()
        .should_receive("create_image")
        .with_args(
            "fedora-36",
            "mmassari/knx-stack/21",
            {
                "architecture": "x86_64",
                "image_type": "aws",
                "upload_request": {"type": "aws", "options": {}},
            },
            {"packages": ["python-knx-stack"]},
            repo_download_url,
        )
        .mock(),
    )
    flexmock(handler).should_receive("report_status")

    flexmock(PipelineModel).should_receive("create").and_return(flexmock())
    flexmock(VMImageBuildTargetModel).should_receive("create").with_args(
        build_id=None,
        project_name="knx-stack",
        owner="mmassari",
        project_url="https://github.com/majamassarini/knx-stack",
        target="fedora-36-x86_64",
        status="pending",
        run_model=Mock,
    )

    flexmock(Celery).should_receive("send_task")

    assert handler.run() == TaskResults(success=True, details="")


def test_vm_image_build_result_handler_ok(
    fake_package_config_job_config_project_db_trigger,
):
    (
        package_config,
        job_config,
        project,
        db_project_object,
    ) = fake_package_config_job_config_project_db_trigger
    handler = VMImageBuildResultHandler(
        package_config,
        job_config,
        {
            "event_type": vm_image.Result.event_type(),
            "build_id": 1,
            "status": "error",
            "message": "Build failed bla bla bla",
        },
    )
    handler._project = project

    flexmock(VMImageBuildTargetModel).should_receive("get_all_by_build_id").with_args(
        1,
    ).and_return(
        [
            flexmock(
                status=None,
                runs=[
                    flexmock()
                    .should_receive("get_project_event_object")
                    .and_return(db_project_object)
                    .mock(),
                ],
            )
            .should_receive("set_status")
            .with_args("error")
            .mock(),
        ],
    )

    flexmock(handler).should_receive("report_status")

    assert handler.run() == TaskResults(success=True, details="")


def test_vm_image_build_result_handler_ko(
    fake_package_config_job_config_project_db_trigger,
):
    (
        package_config,
        job_config,
        project,
        db_project_object,
    ) = fake_package_config_job_config_project_db_trigger
    handler = VMImageBuildResultHandler(
        package_config,
        job_config,
        {
            "event_type": vm_image.Result.event_type(),
            "build_id": 1,
            "status": "error",
        },
    )
    handler._project = project

    flexmock(VMImageBuildTargetModel).should_receive("get_all_by_build_id").with_args(
        1,
    ).and_return([])

    flexmock(handler).should_receive("report_status")

    assert handler.run() == TaskResults(
        success=False,
        details={"msg": "VM image build model 1 not updated. DB model not found"},
    )
