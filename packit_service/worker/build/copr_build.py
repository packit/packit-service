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
from typing import Optional, Tuple, Set, List

from ogr.abstract import GitProject, CommitStatus
from packit.config import JobType, JobConfig
from packit.config.aliases import get_build_targets
from packit.config.package_config import PackageConfig
from packit.exceptions import PackitCoprException

from packit_service import sentry_integration
from packit_service.celerizer import celery_app
from packit_service.config import ServiceConfig, Deployment
from packit_service.constants import MSG_RETRIGGER
from packit_service.models import CoprBuildModel
from packit_service.service.events import EventData
from packit_service.service.urls import (
    get_srpm_log_url_from_flask,
    get_copr_build_info_url_from_flask,
)
from packit_service.worker.build.build_helper import BaseBuildJobHelper
from packit_service.worker.result import HandlerResults

logger = logging.getLogger(__name__)


class CoprBuildJobHelper(BaseBuildJobHelper):
    job_type_build = JobType.copr_build
    job_type_test = JobType.tests
    status_name_build: str = "rpm-build"
    status_name_test: str = "testing-farm"

    def __init__(
        self,
        config: ServiceConfig,
        package_config: PackageConfig,
        project: GitProject,
        metadata: EventData,
        db_trigger,
        job_config: JobConfig,
    ):
        super().__init__(
            config=config,
            package_config=package_config,
            project=project,
            metadata=metadata,
            db_trigger=db_trigger,
            job_config=job_config,
        )

        self.msg_retrigger: str = MSG_RETRIGGER.format(
            build="copr-build" if self.job_build else "build"
        )

    @property
    def default_project_name(self) -> str:
        """
        Project name for copr -- add `-stg` suffix for the stg app.
        """
        stg = "-stg" if self.config.deployment == Deployment.stg else ""
        return f"{self.project.namespace}-{self.project.repo}-{self.metadata.identifier}{stg}"

    @property
    def job_project(self) -> Optional[str]:
        """
        The job definition from the config file.
        """
        if self.job_build and self.job_build.metadata.project:
            return self.job_build.metadata.project

        return self.default_project_name

    @property
    def job_owner(self) -> Optional[str]:
        """
        Owner used for the copr build -- search the config or use the copr's config.
        """
        if self.job_build and self.job_build.metadata.owner:
            return self.job_build.metadata.owner

        return self.api.copr_helper.copr_client.config.get("username")

    @property
    def preserve_project(self) -> Optional[bool]:
        """
        If the project will be preserved or can be removed after 60 days.
        """
        return self.job_build.metadata.preserve_project if self.job_build else None

    @property
    def list_on_homepage(self) -> Optional[bool]:
        """
        If the project will be shown on the copr home page.
        """
        return self.job_build.metadata.list_on_homepage if self.job_build else None

    @property
    def additional_repos(self) -> Optional[List[str]]:
        """
        Additional repos that will be enable for copr build.
        """
        return self.job_build.metadata.additional_repos if self.job_build else None

    @property
    def build_targets(self) -> Set[str]:
        """
        Return the chroots to build.

        (Used when submitting the copr build and as a part of the commit status name.)

        1. If the job is not defined, use the test chroots.
        2. If the job is defined without targets, use "fedora-stable".
        """
        return get_build_targets(*self.configured_build_targets, default=None)

    @property
    def tests_targets(self) -> Set[str]:
        """
        Return the list of chroots used in testing farm.
        Has to be a sub-set of the `build_targets`.

        (Used when submitting the copr build and as a part of the commit status name.)

        Return an empty list if there is no job configured.

        If not defined:
        1. use the build_targets if the job si configured
        2. use "fedora-stable" alias otherwise
        """
        return get_build_targets(*self.configured_tests_targets, default=None)

    def run_copr_build(self) -> HandlerResults:

        if not (self.job_build or self.job_tests):
            msg = "No copr_build or tests job defined."
            # we can't report it to end-user at this stage
            return HandlerResults(success=False, details={"msg": msg})

        self.report_status_to_all(
            description="Building SRPM ...",
            state=CommitStatus.pending,
            # pagure requires "valid url"
            url="",
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

        try:
            build_id, web_url = self.run_build()
        except Exception as ex:
            sentry_integration.send_to_sentry(ex)
            # TODO: Where can we show more info about failure?
            # TODO: Retry
            self.report_status_to_all(
                state=CommitStatus.error,
                description=f"Submit of the build failed: {ex}",
            )
            return HandlerResults(success=False, details={"error": str(ex)})

        for chroot in self.build_targets:
            copr_build = CoprBuildModel.get_or_create(
                build_id=str(build_id),
                commit_sha=self.metadata.commit_sha,
                project_name=self.job_project,
                owner=self.job_owner,
                web_url=web_url,
                target=chroot,
                status="pending",
                srpm_build=self.srpm_model,
                trigger_model=self.db_trigger,
            )
            url = get_copr_build_info_url_from_flask(id_=copr_build.id)
            self.report_status_to_all_for_chroot(
                state=CommitStatus.pending,
                description="Starting RPM build...",
                url=url,
                chroot=chroot,
            )

        # release the hounds!
        celery_app.send_task(
            "task.babysit_copr_build",
            args=(build_id,),
            countdown=120,  # do the first check in 120s
        )

        return HandlerResults(success=True, details={})

    def run_build(
        self, target: Optional[str] = None
    ) -> Tuple[Optional[int], Optional[str]]:
        """
        Trigger the build and return id and web_url
        :param target: str, run for all if not set
        :return: task_id, task_url
        """

        owner = self.job_owner or self.api.copr_helper.configured_owner
        if not owner:
            raise PackitCoprException(
                "Copr owner not set. Use Copr config file or `--owner` when calling packit CLI."
            )

        self.api.copr_helper.create_copr_project_if_not_exists(
            project=self.job_project,
            chroots=list(self.build_targets),
            owner=owner,
            description=None,
            instructions=None,
            list_on_homepage=self.list_on_homepage,
            preserve_project=self.preserve_project,
            additional_repos=self.additional_repos,
            update_additional_values=(owner == "packit"),
        )
        logger.debug(
            f"owner={owner}, project={self.job_project}, path={self.srpm_path}"
        )

        build = self.api.copr_helper.copr_client.build_proxy.create_from_file(
            ownername=owner, projectname=self.job_project, path=self.srpm_path
        )
        return build.id, self.api.copr_helper.copr_web_build_url(build)
