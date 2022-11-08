# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging

from packit_service.models import CoprBuildTargetModel, BuildStatus
from packit_service.worker.checker.abstract import Checker
from packit_service.worker.mixin import (
    GetVMImageDataMixin,
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
        copr_builds = CoprBuildTargetModel.get_all_by_commit(
            commit_sha=self.data.commit_sha,
        )
        if not any(copr_builds):
            msg = f"No Copr build found for commit sha {self.data.commit_sha}"
            logger.debug(msg)
            self.report_pre_check_failure(msg)
            return False

        for build in copr_builds:
            if build.project_name in self.job_config.packages:
                job_config = self.job_config.packages[build.project_name]
                if (
                    build.target == job_config.copr_chroot
                    and build.status == BuildStatus.success
                ):
                    return True
        msg = (
            f"No successfull COPR build found for project {build.project_name}"
            f" and chroot (target) {build.target}"
        )
        logger.debug(msg)
        self.report_pre_check_failure(msg)
        return False
