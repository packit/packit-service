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
from io import StringIO
from typing import Union, Optional

from kubernetes.client.rest import ApiException
from ogr.abstract import GitProject, CommitStatus
from packit.config import PackageConfig, JobType, JobConfig
from packit.utils import PackitFormatter
from sandcastle import SandcastleTimeoutReached

from packit_service.celerizer import celery_app
from packit_service import sentry_integration
from packit_service.config import ServiceConfig, Deployment
from packit_service.constants import MSG_RETRIGGER
from packit_service.models import CoprBuildModel, SRPMBuildModel
from packit_service.service.events import (
    PullRequestEvent,
    PullRequestCommentEvent,
    CoprBuildEvent,
    PushGitHubEvent,
    ReleaseEvent,
)
from packit_service.service.models import CoprBuild as RedisCoprBuild
from packit_service.service.urls import get_log_url, get_srpm_log_url
from packit_service.worker.build.build_helper import BaseBuildJobHelper
from packit_service.worker.result import HandlerResults

logger = logging.getLogger(__name__)


class BuildMetadata:
    """ metadata of this class represent srpm + copr build """

    srpm_logs: str
    srpm_failed: bool  # did the srpm phase failed?
    copr_build_id: Optional[int]
    copr_web_url: Optional[str]


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
        event: Union[
            PullRequestEvent,
            PullRequestCommentEvent,
            CoprBuildEvent,
            PushGitHubEvent,
            ReleaseEvent,
        ],
        job: Optional[JobConfig] = None,
    ):
        super().__init__(config, package_config, project, event, job)

        self.msg_retrigger: str = MSG_RETRIGGER.format(
            build="copr-build" if self.job_build else "build"
        )

        # lazy properties
        self._copr_build_model = None

    @property
    def default_project_name(self) -> str:
        """
        Project name for copr -- add `-stg` suffix for the stg app.
        """
        stg = "-stg" if self.config.deployment == Deployment.stg else ""
        return (
            f"{self.project.namespace}-{self.project.repo}-{self.event.identifier}{stg}"
        )

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

    # TODO: remove this once we're fully on psql
    @property
    def copr_build_model(self) -> RedisCoprBuild:
        if self._copr_build_model is None:
            self._copr_build_model = RedisCoprBuild.create(
                project=self.job_project,
                owner=self.job_owner,
                chroots=self.build_chroots,
            )
        return self._copr_build_model

    def run_copr_build(self) -> HandlerResults:

        if not (self.job_build or self.job_tests):
            msg = "No copr_build or tests job defined."
            # we can't report it to end-user at this stage
            return HandlerResults(success=False, details={"msg": msg})

        self.report_status_to_all(
            description="Building SRPM ...", state=CommitStatus.pending
        )

        build_metadata = self._run_copr_build_and_save_output()

        srpm_build_model = SRPMBuildModel.create(build_metadata.srpm_logs)

        if build_metadata.srpm_failed:
            msg = "SRPM build failed, check the logs for details."
            self.report_status_to_all(
                state=CommitStatus.failure,
                description=msg,
                url=get_srpm_log_url(srpm_build_model.id),
            )
            return HandlerResults(success=False, details={"msg": msg})

        for chroot in self.build_chroots:
            copr_build = CoprBuildModel.get_or_create(
                build_id=str(build_metadata.copr_build_id),
                commit_sha=self.event.commit_sha,
                project_name=self.job_project,
                owner=self.job_owner,
                web_url=build_metadata.copr_web_url,
                target=chroot,
                status="pending",
                srpm_build=srpm_build_model,
                trigger_model=self.event.db_trigger,
            )
            url = get_log_url(id_=copr_build.id)
            self.report_status_to_all_for_chroot(
                state=CommitStatus.pending,
                description="Building RPM ...",
                url=url,
                chroot=chroot,
            )

        self.copr_build_model.build_id = build_metadata.copr_build_id
        self.copr_build_model.save()

        # release the hounds!
        celery_app.send_task(
            "task.babysit_copr_build",
            args=(build_metadata.copr_build_id,),
            countdown=120,  # do the first check in 120s
        )

        return HandlerResults(success=True, details={})

    def _run_copr_build_and_save_output(self) -> BuildMetadata:
        # we want to get packit logs from the SRPM creation process
        # so we stuff them into a StringIO buffer
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        packit_logger = logging.getLogger("packit")
        packit_logger.setLevel(logging.DEBUG)
        packit_logger.addHandler(handler)
        formatter = PackitFormatter(None, "%H:%M:%S")
        handler.setFormatter(formatter)

        c = BuildMetadata()
        c.srpm_failed = False
        ex: Optional[Exception] = None  # shut up pycharm
        extra_logs: str = ""

        try:
            c.copr_build_id, c.copr_web_url = self.api.run_copr_build(
                project=self.job_project,
                chroots=self.build_chroots,
                owner=self.job_owner,
            )
        except SandcastleTimeoutReached as e:
            ex = e
            extra_logs = f"\nYou have reached 10-minute timeout while creating SRPM.\n"
        except ApiException as e:
            ex = e
            # this is an internal error: let's not expose anything to public
            extra_logs = (
                "\nThere was a problem in the environment the packit-service is running in.\n"
                "Please hang tight, the help is coming."
            )
        except Exception as e:
            ex = e  # shut up mypy

        # collect the logs now
        packit_logger.removeHandler(handler)
        stream.seek(0)
        c.srpm_logs = stream.read()

        if ex:
            logger.info(f"exception while running a copr build: {ex}")
            logger.debug(f"{ex!r}")

            c.srpm_failed = True

            # when do we NOT want to send stuff to sentry?
            sentry_integration.send_to_sentry(ex)

            # this needs to be done AFTER we gather logs
            # so that extra logs are after actual logs
            c.srpm_logs += extra_logs
            if hasattr(ex, "output"):
                output = getattr(ex, "output", "")  # mypy
                c.srpm_logs += f"\nOutput of the command in the sandbox:\n{output}\n"

            c.srpm_logs += (
                f"\nMessage: {ex}\nException: {ex!r}\n{self.msg_retrigger}"
                "\nPlease join the freenode IRC channel #packit for the latest info.\n"
            )

        return c
