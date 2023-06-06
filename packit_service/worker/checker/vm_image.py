# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging

from packit_service.models import CoprBuildTargetModel, BuildStatus
from packit_service.worker.checker.abstract import Checker, ActorChecker
from packit_service.worker.mixin import (
    GetVMImageDataMixin,
    ConfigFromEventMixin,
    GetReporterFromJobHelperMixin,
)
from packit_service.models import (
    VMImageBuildStatus,
)

from packit_service.worker.reporting import BaseCommitStatus

from packit_service.constants import DOCS_VM_IMAGE_BUILD

logger = logging.getLogger(__name__)


class GetVMImageBuildReporterFromJobHelperMixin(
    GetReporterFromJobHelperMixin, GetVMImageDataMixin
):
    status_name = "vm-image-build"

    def get_build_check_name(self) -> str:
        if self.identifier:
            return f"{self.status_name}-{self.chroot}-{self.identifier}"
        else:
            return f"{self.status_name}-{self.chroot}"

    def report_pre_check_failure(self, markdown_content):
        self.report(
            state=BaseCommitStatus.neutral,
            description="VM Image Build job failed internal checks",
            url=DOCS_VM_IMAGE_BUILD,
            check_names=[self.get_build_check_name()],
            markdown_content=markdown_content,
        )

    def report_status(self, status: VMImageBuildStatus, markdown_content: str):
        if status in (
            VMImageBuildStatus.pending,
            VMImageBuildStatus.building,
            VMImageBuildStatus.uploading,
            VMImageBuildStatus.registering,
        ):
            report = BaseCommitStatus.pending
            description = "Building VM Image..."
        elif status == VMImageBuildStatus.failure:
            report = BaseCommitStatus.failure
            description = "VM Image build failed..."
        elif status == VMImageBuildStatus.error:
            report = BaseCommitStatus.error
            description = "VM Image build error..."
        elif status == VMImageBuildStatus.success:
            report = BaseCommitStatus.success
            description = "VM Image build is complete"
        self.report(
            state=report,
            description=description,
            url="",
            check_names=[self.get_build_check_name()],
            markdown_content=markdown_content,
        )


class IsCoprBuildForChrootOk(Checker, GetVMImageBuildReporterFromJobHelperMixin):
    def pre_check(
        self,
    ) -> bool:
        copr_builds = CoprBuildTargetModel.get_all_by(
            project_name=self.job_config.project,
            commit_sha=self.data.commit_sha,
            owner=self.job_config.owner,
            target=self.job_config.copr_chroot,
            status=BuildStatus.success,
        )

        if copr_builds:
            return True

        project = (
            f"project {self.job_config.owner}/{self.job_config.project}, "
            if self.job_config.owner and self.job_config.project
            else ""
        )
        msg = (
            f"No successful Copr build found for {project}"
            f"commit {self.data.commit_sha} "
            f"and chroot (target) {self.job_config.copr_chroot}"
        )
        logger.debug(msg)
        self.report_pre_check_failure(msg)
        return False


class HasAuthorWriteAccess(
    ActorChecker, ConfigFromEventMixin, GetVMImageBuildReporterFromJobHelperMixin
):
    def _pre_check(self) -> bool:
        if not self.project.has_write_access(user=self.actor):
            msg = (
                f"User {self.actor} is not allowed to build a VM Image "
                f"for PR#{self.data.pr_id} and "
                f"project {self.project.namespace}/{self.project.repo}."
            )
            logger.info(msg)
            self.report_pre_check_failure(msg)
            return False

        return True
