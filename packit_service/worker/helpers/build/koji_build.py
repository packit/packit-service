# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from re import search
from typing import Dict, Optional, Set, Tuple

from ogr.abstract import GitProject
from packit.config import JobConfig, JobType
from packit.config.aliases import get_all_koji_targets, get_koji_targets
from packit.config.package_config import PackageConfig
from packit.exceptions import PackitCommandFailedError
from packit_service import sentry_integration
from packit_service.config import ServiceConfig
from packit_service.constants import MSG_RETRIGGER
from packit_service.models import KojiBuildTargetModel, BuildStatus
from packit_service.worker.events import EventData
from packit_service.service.urls import (
    get_koji_build_info_url,
    get_srpm_build_info_url,
)
from packit_service.worker.helpers.build.build_helper import BaseBuildJobHelper
from packit_service.worker.result import TaskResults
from packit_service.worker.reporting import BaseCommitStatus

logger = logging.getLogger(__name__)


class KojiBuildJobHelper(BaseBuildJobHelper):
    job_type_build = JobType.production_build
    job_type_test = None
    status_name_build: str = "koji-build"
    status_name_test: str = None

    def __init__(
        self,
        service_config: ServiceConfig,
        package_config: PackageConfig,
        project: GitProject,
        metadata: EventData,
        db_trigger,
        job_config: JobConfig,
        build_targets_override: Optional[Set[str]] = None,
        tests_targets_override: Optional[Set[str]] = None,
    ):
        super().__init__(
            service_config=service_config,
            package_config=package_config,
            project=project,
            metadata=metadata,
            db_trigger=db_trigger,
            job_config=job_config,
            build_targets_override=build_targets_override,
            tests_targets_override=tests_targets_override,
        )

        # Lazy properties
        self._supported_koji_targets = None

    @property
    def msg_retrigger(self) -> str:
        return MSG_RETRIGGER.format(
            job="build",
            command="upstream-koji-build",
            place="pull request",
            packit_comment_command_prefix=self.service_config.comment_command_prefix,
        )

    @property
    def is_scratch(self) -> bool:
        return self.job_build and self.job_build.scratch

    @property
    def build_targets_all(self) -> Set[str]:
        """
        Return all valid Koji targets/chroots from config.
        """
        return get_koji_targets(*self.configured_build_targets)

    @property
    def supported_koji_targets(self):
        if self._supported_koji_targets is None:
            self._supported_koji_targets = get_all_koji_targets()
        return self._supported_koji_targets

    def run_koji_build(self) -> TaskResults:
        self.report_status_to_all(
            description="Building SRPM ...", state=BaseCommitStatus.running
        )
        if results := self.create_srpm_if_needed():
            return results

        if self.srpm_model.status != BuildStatus.success:
            msg = "SRPM build failed, check the logs for details."
            self.report_status_to_all(
                state=BaseCommitStatus.failure,
                description=msg,
                url=get_srpm_build_info_url(self.srpm_model.id),
            )
            return TaskResults(success=False, details={"msg": msg})

        try:
            # We need to do it manually
            # because we don't use PackitAPI.build, but PackitAPI.up.koji_build
            self.api.init_kerberos_ticket()
        except PackitCommandFailedError as ex:
            msg = f"Kerberos authentication error: {ex.stderr_output}"
            logger.error(msg)
            self.report_status_to_all(
                state=BaseCommitStatus.error,
                description=msg,
                url=get_srpm_build_info_url(self.srpm_model.id),
            )
            return TaskResults(success=False, details={"msg": msg})

        errors: Dict[str, str] = {}
        for target in self.build_targets:

            if target not in self.supported_koji_targets:
                msg = f"Target not supported: {target}"
                self.report_status_to_all_for_chroot(
                    state=BaseCommitStatus.error,
                    description=msg,
                    url=get_srpm_build_info_url(self.srpm_model.id),
                    chroot=target,
                )
                errors[target] = msg
                continue

            try:
                build_id, web_url = self.run_build(target=target)
            except Exception as ex:
                sentry_integration.send_to_sentry(ex)
                # TODO: Where can we show more info about failure?
                # TODO: Retry
                self.report_status_to_all_for_chroot(
                    state=BaseCommitStatus.error,
                    description=f"Submit of the build failed: {ex}",
                    url=get_srpm_build_info_url(self.srpm_model.id),
                    chroot=target,
                )
                errors[target] = str(ex)
                continue

            koji_build = KojiBuildTargetModel.create(
                build_id=str(build_id),
                commit_sha=self.metadata.commit_sha,
                web_url=web_url,
                target=target,
                status="pending",
                scratch=self.is_scratch,
                run_model=self.run_model,
            )
            url = get_koji_build_info_url(id_=koji_build.id)
            self.report_status_to_all_for_chroot(
                state=BaseCommitStatus.running,
                description="Building RPM ...",
                url=url,
                chroot=target,
            )

        if errors:
            return TaskResults(
                success=False,
                details={
                    "msg": "Koji build submit was not successful for all chroots.",
                    "errors": errors,
                },
            )

        # TODO: release the hounds!
        """
        celery_app.send_task(
            "task.babysit_koji_build",
            args=(build_metadata.build_id,),
            countdown=120,  # do the first check in 120s
        )
        """

        return TaskResults(success=True, details={})

    def run_build(
        self, target: Optional[str] = None
    ) -> Tuple[Optional[int], Optional[str]]:
        if not target:
            logger.debug("No targets set for koji build, using rawhide.")
            target = "rawhide"

        try:
            out = self.api.up.koji_build(
                scratch=self.is_scratch,
                nowait=True,
                koji_target=target,
                srpm_path=self.srpm_path,
            )
        except PackitCommandFailedError as ex:
            logger.warning(
                f"Koji build failed for {target}:\n"
                f"\t stdout: {ex.stdout_output}\n"
                f"\t stderr: {ex.stderr_output}\n"
            )
            raise

        if not out:
            return None, None

        # packit does not return any info about build.
        # TODO: move the parsing to packit
        task_id, task_url = None, None

        task_id_match = search(pattern=r"Created task: (\d+)", string=out)
        if task_id_match:
            task_id = int(task_id_match.group(1))

        task_url_match = search(
            pattern=r"(https://.+/koji/taskinfo\?taskID=\d+)",
            string=out,
        )
        if task_url_match:
            task_url = task_url_match.group(0)

        return task_id, task_url
