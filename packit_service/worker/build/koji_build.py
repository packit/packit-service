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
from typing import Optional, Union, Tuple, Dict

from ogr.abstract import CommitStatus, GitProject
from packit.config import JobType, PackageConfig, JobConfig
from packit.exceptions import PackitCommandFailedError
from packit_service import sentry_integration
from packit_service.config import ServiceConfig
from packit_service.constants import MSG_RETRIGGER
from packit_service.models import KojiBuildModel
from packit_service.service.events import (
    PullRequestGithubEvent,
    PullRequestCommentGithubEvent,
    PushGitHubEvent,
    ReleaseEvent,
)
from packit_service.service.urls import (
    get_srpm_log_url_from_flask,
    get_koji_build_log_url_from_flask,
)
from packit_service.worker.build.build_helper import BaseBuildJobHelper
from packit_service.worker.result import HandlerResults

logger = logging.getLogger(__name__)


class KojiBuildJobHelper(BaseBuildJobHelper):
    job_type_build = JobType.production_build
    job_type_test = None
    status_name_build: str = f"production-build"
    status_name_test: str = None

    def __init__(
        self,
        config: ServiceConfig,
        package_config: PackageConfig,
        project: GitProject,
        event: Union[
            PullRequestGithubEvent,
            PullRequestCommentGithubEvent,
            PushGitHubEvent,
            ReleaseEvent,
        ],
        job: Optional[JobConfig] = None,
    ):
        super().__init__(config, package_config, project, event, job)
        self.msg_retrigger: str = MSG_RETRIGGER.format(build="production-build")

    @property
    def is_scratch(self) -> bool:
        return self.job_build and self.job_build.metadata.scratch

    def run_koji_build(self) -> HandlerResults:
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
            return HandlerResults(success=False, details={"msg": msg})

        errors: Dict[str, str] = {}
        for chroot in self.build_chroots:

            try:
                build_id, web_url = self.run_build()
            except Exception as ex:
                sentry_integration.send_to_sentry(ex)
                # TODO: Where can we show more info about failure?
                # TODO: Retry
                self.report_status_to_all(
                    state=CommitStatus.error,
                    description=f"Submit of the build failed: {ex}",
                    url=get_srpm_log_url_from_flask(self.srpm_model.id),
                )
                errors[chroot] = str(ex)
                continue

            koji_build = KojiBuildModel.get_or_create(
                build_id=str(build_id),
                commit_sha=self.event.commit_sha,
                web_url=web_url,
                target=chroot,
                status="pending",
                srpm_build=self.srpm_model,
                trigger_model=self.event.db_trigger,
            )
            url = get_koji_build_log_url_from_flask(id_=koji_build.id)
            self.report_status_to_all_for_chroot(
                state=CommitStatus.pending,
                description="Building RPM ...",
                url=url,
                chroot=chroot,
            )

        if errors:
            return HandlerResults(
                success=False,
                details={
                    "msg": f"Koji build submit was not successful for all chroots.",
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

        return HandlerResults(success=True, details={})

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
