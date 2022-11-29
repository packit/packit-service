# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
This file defines classes for job handlers specific for Fedmsg events
"""

import logging
from typing import Tuple, Type

from packit.config import (
    JobType,
)
from packit_service.worker.checker.abstract import Checker
from packit_service.worker.events import (
    AbstractPRCommentEvent,
    VMImageBuildResultEvent,
)
from packit_service.worker.handlers.abstract import (
    JobHandler,
    RetriableJobHandler,
    TaskName,
    configured_as,
    reacts_to,
    run_for_comment,
)
from packit_service.worker.result import TaskResults
from packit_service.worker.checker.vm_image import (
    HasAuthorWriteAccess,
    IsCoprBuildForChrootOk,
    GetVMImageBuildReporterFromJobHelperMixin,
)
from packit_service.worker.mixin import (
    ConfigFromEventMixin,
    GetVMImageBuilderMixin,
)
from packit_service.models import (
    VMImageBuildTargetModel,
    VMImageBuildStatus,
    PipelineModel,
)

from packit_service.celerizer import celery_app

logger = logging.getLogger(__name__)


@configured_as(job_type=JobType.vm_image_build)
@run_for_comment(command="vm-image-build")
@reacts_to(AbstractPRCommentEvent)
class VMImageBuildHandler(
    RetriableJobHandler,
    ConfigFromEventMixin,
    GetVMImageBuilderMixin,
    GetVMImageBuildReporterFromJobHelperMixin,
):
    task_name = TaskName.vm_image_build

    @staticmethod
    def get_checkers() -> Tuple[Type[Checker], ...]:
        return (
            HasAuthorWriteAccess,
            IsCoprBuildForChrootOk,
        )

    def run(self) -> TaskResults:
        if not self.job_config:
            return TaskResults(
                success=False,
                details={
                    "msg": f"Job configuration not found for project {self.project.repo}"
                },
            )

        image_id = self.vm_image_builder.create_image(
            self.image_distribution,
            self.image_name,
            self.image_request,
            self.image_customizations,
            self.project_url,
        )

        run_model = PipelineModel.create(
            type=self.data.db_trigger.job_trigger_model_type,
            trigger_id=self.data.db_trigger.id,
        )
        VMImageBuildTargetModel.create(
            build_id=image_id,
            commit_sha=self.data.commit_sha,
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
@reacts_to(VMImageBuildResultEvent)
class VMImageBuildResultHandler(
    JobHandler,
    ConfigFromEventMixin,
    GetVMImageBuilderMixin,
    GetVMImageBuildReporterFromJobHelperMixin,
):
    task_name = TaskName.vm_image_build_result

    @staticmethod
    def get_checkers() -> Tuple[Type[Checker], ...]:
        return ()

    def run(self) -> TaskResults:
        build_id = self.data.event_dict["build_id"]
        models = VMImageBuildTargetModel.get_all_by_build_id(build_id)
        for model in models:
            self.data._db_trigger = model.runs[0].get_trigger_object()
            status = self.data.event_dict["status"]
            model.set_status(status)
            self.report_status(status, "")
            return TaskResults(
                success=True,
                details={},
            )

        msg = f"VM image build model {build_id} not updated. DB model not found"
        return TaskResults(
            success=False,
            details={"msg": msg},
        )
