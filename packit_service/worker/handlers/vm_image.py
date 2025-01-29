# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
This file defines classes for job handlers specific for Fedmsg events
"""

import logging

from packit.config import (
    JobType,
)

from packit_service.celerizer import celery_app
from packit_service.events import (
    github,
    gitlab,
    vm_image,
)
from packit_service.models import (
    PipelineModel,
    VMImageBuildStatus,
    VMImageBuildTargetModel,
)
from packit_service.worker.checker.abstract import Checker
from packit_service.worker.checker.vm_image import (
    GetVMImageBuildReporterFromJobHelperMixin,
    HasAuthorWriteAccess,
    IsCoprBuildForChrootOk,
)
from packit_service.worker.handlers.abstract import (
    JobHandler,
    RetriableJobHandler,
    TaskName,
    configured_as,
    reacts_to,
    run_for_comment,
)
from packit_service.worker.handlers.mixin import (
    GetVMImageBuilderMixin,
)
from packit_service.worker.mixin import (
    PackitAPIWithDownstreamMixin,
)
from packit_service.worker.result import TaskResults

logger = logging.getLogger(__name__)


@configured_as(job_type=JobType.vm_image_build)
@run_for_comment(command="vm-image-build")
@reacts_to(github.pr.Comment)
@reacts_to(gitlab.mr.Comment)
class VMImageBuildHandler(
    RetriableJobHandler,
    PackitAPIWithDownstreamMixin,
    GetVMImageBuilderMixin,
    GetVMImageBuildReporterFromJobHelperMixin,
):
    task_name = TaskName.vm_image_build

    @staticmethod
    def get_checkers() -> tuple[type[Checker], ...]:
        return (
            HasAuthorWriteAccess,
            # [NOTE] We require Copr repository being present for the VM image
            # builds via Packit Service as there are no common use cases that
            # would benefit from relaxing this condition.
            IsCoprBuildForChrootOk,
        )

    def run(self) -> TaskResults:
        if not self.job_config:
            return TaskResults(
                success=False,
                details={
                    "msg": f"Job configuration not found for project {self.project.repo}",
                },
            )

        repo_url = self.packit_api.copr_helper.get_repo_download_url(
            owner=self.owner,
            project=self.project_name,
            chroot=self.chroot,
        )
        image_id = self.vm_image_builder.create_image(
            self.image_distribution,
            self.image_name,
            self.image_request,
            self.image_customizations,
            repo_url,
        )

        run_model = PipelineModel.create(
            project_event=self.data.db_project_event,
            package_name=self.get_package_name(),
        )
        VMImageBuildTargetModel.create(
            build_id=image_id,
            project_name=self.project_name,
            owner=self.owner,
            project_url=self.project_url,
            target=self.chroot,
            status=VMImageBuildStatus.pending,
            run_model=run_model,
        )

        celery_app.send_task(
            "task.babysit_vm_image_build",
            args=(image_id,),
            countdown=10,  # do the first check in 10s
        )

        self.report_status(VMImageBuildStatus.pending, "")

        return TaskResults(
            success=True,
            details={},
        )


@configured_as(job_type=JobType.vm_image_build)
@reacts_to(vm_image.Result)
class VMImageBuildResultHandler(
    JobHandler,
    PackitAPIWithDownstreamMixin,
    GetVMImageBuilderMixin,
    GetVMImageBuildReporterFromJobHelperMixin,
):
    task_name = TaskName.vm_image_build_result

    @staticmethod
    def get_checkers() -> tuple[type[Checker], ...]:
        return ()

    def run(self) -> TaskResults:
        build_id = self.data.event_dict["build_id"]
        models = VMImageBuildTargetModel.get_all_by_build_id(build_id)
        status = self.data.event_dict["status"]

        for model in models:
            if model.status == status:
                logger.debug(
                    "Status was already processed (status in the DB is the same "
                    "as the one about to report)",
                )
                return TaskResults(
                    success=True,
                    details={"msg": "State change already processed"},
                )

            self.data._db_project_object = model.runs[0].get_project_event_object()
            self.report_status(status, self.data.event_dict["message"])
            model.set_status(status)
            return TaskResults(
                success=True,
                details={},
            )

        msg = f"VM image build model {build_id} not updated. DB model not found"
        return TaskResults(
            success=False,
            details={"msg": msg},
        )
