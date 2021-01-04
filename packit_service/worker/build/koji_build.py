# MIT License
#
# Copyright (c) 2018-2019 Red Hat, Inc.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
import logging
from re import search
from typing import Dict, Optional, Set, Tuple

from ogr.abstract import CommitStatus, GitProject
from packit.config import JobConfig, JobType
from packit.config.aliases import get_all_koji_targets, get_koji_targets
from packit.config.package_config import PackageConfig
from packit.exceptions import PackitCommandFailedError
from packit_service import sentry_integration
from packit_service.config import ServiceConfig
from packit_service.constants import KOJI_PRODUCTION_BUILDS_ISSUE, MSG_RETRIGGER
from packit_service.models import KojiBuildModel
from packit_service.service.events import EventData
from packit_service.service.urls import (
    get_koji_build_info_url_from_flask,
    get_srpm_log_url_from_flask,
)
from packit_service.worker.build.build_helper import BaseBuildJobHelper
from packit_service.worker.result import TaskResults

logger = logging.getLogger(__name__)


class KojiBuildJobHelper(BaseBuildJobHelper):
    job_type_build = JobType.production_build
    job_type_test = None
    status_name_build: str = "production-build"
    status_name_test: str = None

    def __init__(
        self,
        service_config: ServiceConfig,
        package_config: PackageConfig,
        project: GitProject,
        metadata: EventData,
        db_trigger,
        job_config: JobConfig,
    ):
        super().__init__(
            service_config=service_config,
            package_config=package_config,
            project=project,
            metadata=metadata,
            db_trigger=db_trigger,
            job_config=job_config,
        )
        self.msg_retrigger: str = MSG_RETRIGGER.format(
            job="build", command="production-build", place="pull request"
        )

        # Lazy properties
        self._supported_koji_targets = None

    @property
    def is_scratch(self) -> bool:
        return self.job_build and self.job_build.metadata.scratch

    @property
    def build_targets(self) -> Set[str]:
        """
        Return the targets/chroots to build.

        (Used when submitting the koji/copr build and as a part of the commit status name.)

        1. If the job is not defined, use the test chroots.
        2. If the job is defined without targets, use "fedora-stable".
        """
        return get_koji_targets(*self.configured_build_targets)

    @property
    def tests_targets(self) -> Set[str]:
        """
        [not used now]

        Return the list of targets/chroots used in testing farm.
        Has to be a sub-set of the `build_targets`.

        (Used when submitting the koji/copr build and as a part of the commit status name.)

        Return an empty list if there is no job configured.

        If not defined:
        1. use the build_targets if the job si configured
        2. use "fedora-stable" alias otherwise
        """
        return get_koji_targets(*self.configured_tests_targets)

    @property
    def supported_koji_targets(self):
        if self._supported_koji_targets is None:
            self._supported_koji_targets = get_all_koji_targets()
        return self._supported_koji_targets

    def run_koji_build(self) -> TaskResults:
        if not self.is_scratch:
            msg = "Non-scratch builds not possible from upstream."
            self.report_status_to_all(
                description=msg,
                state=CommitStatus.error,
                url=KOJI_PRODUCTION_BUILDS_ISSUE,
            )
            return TaskResults(success=True, details={"msg": msg})

        self.report_status_to_all(
            description="Building SRPM ...", state=CommitStatus.pending
        )
        self.create_srpm_if_needed()

        if not self.srpm_model.success:
            msg = "SRPM build failed, check the logs for details."
            self.report_status_to_all(
                state=CommitStatus.failure,
                description=msg,
                url=get_srpm_log_url_from_flask(self.srpm_model.id),
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
                state=CommitStatus.error,
                description=msg,
                url=get_srpm_log_url_from_flask(self.srpm_model.id),
            )
            return TaskResults(success=False, details={"msg": msg})

        errors: Dict[str, str] = {}
        for target in self.build_targets:

            if target not in self.supported_koji_targets:
                msg = f"Target not supported: {target}"
                self.report_status_to_all_for_chroot(
                    state=CommitStatus.error,
                    description=msg,
                    url=get_srpm_log_url_from_flask(self.srpm_model.id),
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
                    state=CommitStatus.error,
                    description=f"Submit of the build failed: {ex}",
                    url=get_srpm_log_url_from_flask(self.srpm_model.id),
                    chroot=target,
                )
                errors[target] = str(ex)
                continue

            koji_build = KojiBuildModel.get_or_create(
                build_id=str(build_id),
                commit_sha=self.metadata.commit_sha,
                web_url=web_url,
                target=target,
                status="pending",
                srpm_build=self.srpm_model,
                trigger_model=self.db_trigger,
            )
            url = get_koji_build_info_url_from_flask(id_=koji_build.id)
            self.report_status_to_all_for_chroot(
                state=CommitStatus.pending,
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
            pattern=r"(https://koji\.fedoraproject\.org/koji/taskinfo\?taskID=\d+)",
            string=out,
        )
        if task_url_match:
            task_url = task_url_match.group(0)

        return task_id, task_url
