# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging

from packit_service.constants import DOCS_VM_IMAGE_BUILD
from packit_service.models import (
    VMImageBuildStatus,
)
from packit_service.worker.checker.abstract import ActorChecker, Checker
from packit_service.worker.handlers.mixin import (
    GetCoprBuildJobHelperMixin,
    GetVMImageDataMixin,
)
from packit_service.worker.mixin import (
    ConfigFromEventMixin,
    GetReporterFromJobHelperMixin,
)
from packit_service.worker.reporting import BaseCommitStatus

logger = logging.getLogger(__name__)


class GetVMImageBuildReporterFromJobHelperMixin(
    ConfigFromEventMixin,
    GetCoprBuildJobHelperMixin,
    GetReporterFromJobHelperMixin,
    GetVMImageDataMixin,
):
    status_name = "vm-image-build"

    def get_build_check_name(self) -> str:
        if self.identifier:
            return f"{self.status_name}:{self.chroot}:{self.identifier}"

        return f"{self.status_name}:{self.chroot}"

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
        if self.copr_build:
            return True

        owner = self.job_config.owner or self.copr_build_helper.job_owner
        project = self.job_config.project or self.copr_build_helper.default_project_name
        owner_project = f"project {owner}/{project}, " if owner and project else ""

        msg = (
            f"No successful Copr build found for {owner_project}"
            f"commit {self.data.commit_sha} "
            f"and chroot (target) {self.job_config.copr_chroot}"
        )
        logger.debug(msg)
        self.report_pre_check_failure(msg)
        return False


class HasAuthorWriteAccess(ActorChecker, GetVMImageBuildReporterFromJobHelperMixin):
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
