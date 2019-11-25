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
from typing import Union, List, Optional

from ogr.abstract import GitProject
from packit.api import PackitAPI
from packit.config import PackageConfig, JobType, JobConfig
from packit.exceptions import FailedCreateSRPM
from packit.local_project import LocalProject
from sandcastle import SandcastleCommandFailed, SandcastleTimeoutReached

from packit_service.config import ServiceConfig, Deployment
from packit_service.service.events import PullRequestEvent, PullRequestCommentEvent
from packit_service.service.models import CoprBuild
from packit_service.worker.copr_db import CoprBuildDB
from packit_service.worker.handler import (
    HandlerResults,
    BuildStatusReporter,
    PRCheckName,
)

logger = logging.getLogger(__name__)


class CoprBuildHandler(object):
    def __init__(
        self,
        config: ServiceConfig,
        package_config: PackageConfig,
        project: GitProject,
        event: Union[PullRequestEvent, PullRequestCommentEvent],
    ):
        self.config: ServiceConfig = config
        self.package_config: PackageConfig = package_config
        self.project: GitProject = project
        self.event: Union[PullRequestEvent, PullRequestCommentEvent] = event
        self._api: PackitAPI = None
        self._local_project: LocalProject = None
        self._copr_build_model: CoprBuild = None
        self.job_project: str = ""
        self.job_owner: str = ""
        self.job_chroots: List[str] = []

    @property
    def local_project(self) -> LocalProject:
        if self._local_project is None:
            self._local_project = LocalProject(
                git_project=self.project,
                working_dir=self.config.command_handler_work_dir,
                ref=self.event.base_ref,
                pr_id=self.event.pr_id,
            )
        return self._local_project

    @property
    def api(self) -> PackitAPI:
        if not self._api:
            self._api = PackitAPI(self.config, self.package_config, self.local_project)
        return self._api

    @property
    def copr_build_model(self) -> CoprBuild:
        if self._copr_build_model is None:
            self._copr_build_model = CoprBuild.create(
                project=self.job_project, owner=self.job_owner, chroots=self.job_chroots
            )
        return self._copr_build_model

    def get_job_copr_build_metadata(self) -> Optional[JobConfig]:
        """
        Check if there are copr_build defined
        :return: JobConfig or None
        """
        for job in self.package_config.jobs:
            if job.job == JobType.copr_build:
                return job
        return None

    def run_copr_build(self) -> HandlerResults:
        # add suffix stg when using stg app
        stg = "-stg" if self.config.deployment == Deployment.stg else ""
        default_project_name = (
            f"{self.project.namespace}-{self.project.repo}-{self.event.pr_id}{stg}"
        )
        job = self.get_job_copr_build_metadata()
        if not job:
            msg = "No copr_build defined"
            # we can't report it to end-user at this stage
            return HandlerResults(success=False, details={"msg": msg})

        self.job_project = job.metadata.get("project") or default_project_name
        self.job_owner = job.metadata.get("owner") or self.api.copr.config.get(
            "username"
        )
        if not job.metadata.get("targets"):
            msg = "'targets' value is required in packit config for copr_build job"
            self.project.pr_comment(self.event.pr_id, msg)
            return HandlerResults(success=False, details={"msg": msg})

        self.job_chroots = job.metadata.get("targets", [])
        for test_check_name in (
            f"{PRCheckName.get_testing_farm_check(x)}" for x in self.job_chroots
        ):
            self.project.set_commit_status(
                self.event.commit_sha,
                "pending",
                "",
                "Waiting for a successful RPM build",
                test_check_name,
                trim=True,
            )
        r = BuildStatusReporter(
            self.project, self.event.commit_sha, self.copr_build_model
        )
        build_check_names = [
            f"{PRCheckName.get_build_check(x)}" for x in self.job_chroots
        ]
        msg_retrigger = (
            f"You can re-trigger copr build by adding a comment (`/packit copr-build`) "
            f"into this pull request."
        )
        try:
            r.report(
                "pending",
                "SRPM build has just started...",
                check_names=PRCheckName.get_srpm_build_check(),
            )
            r.report(
                "pending",
                "RPM build is waiting for succesfull SPRM build",
                check_names=build_check_names,
            )
            build_id, _ = self.api.run_copr_build(
                project=self.job_project, chroots=self.job_chroots, owner=self.job_owner
            )
            r.report(
                "success",
                "SRPM was built successfully.",
                check_names=PRCheckName.get_srpm_build_check(),
            )
            # provide common build url while waiting on response from copr
            url = (
                "https://copr.fedorainfracloud.org/coprs/"
                f"{self.job_owner}/{self.job_project}/build/{build_id}/"
            )
            r.report(
                "pending",
                "RPM build has just started...",
                check_names=build_check_names,
                url=url,
            )
            # Save copr build with commit information to be able to report status back
            # after fedmsg copr.build.end arrives
            copr_build_db = CoprBuildDB()
            copr_build_db.add_build(
                build_id,
                self.event.commit_sha,
                self.event.pr_id,
                self.event.base_repo_name,
                self.event.base_repo_namespace,
                self.event.base_ref,
                self.event.project_url,
            )

        except SandcastleTimeoutReached:
            msg = f"You have reached 10-minute timeout while creating the SRPM. {msg_retrigger}"
            self.project.pr_comment(self.event.pr_id, msg)
            msg = "Timeout reached while creating a SRPM."
            r.report("failure", msg, check_names=build_check_names)
            return HandlerResults(success=False, details={"msg": msg})

        except SandcastleCommandFailed as ex:
            max_log_size = 1024 * 16  # is 16KB enough?
            if len(ex.output) > max_log_size:
                output = "Earlier output was truncated\n\n" + ex.output[-max_log_size:]
            else:
                output = ex.output
            msg = (
                f"There was an error while creating a SRPM. {msg_retrigger}\n"
                "\nOutput:"
                "\n```\n"
                f"{output}"
                "\n```"
                f"\nReturn code: {ex.rc}"
            )
            self.project.pr_comment(self.event.pr_id, msg)
            import sentry_sdk

            sentry_sdk.capture_exception(output)

            msg = "Failed to create a SRPM."
            r.report("failure", msg, check_names=PRCheckName.get_srpm_build_check())
            return HandlerResults(success=False, details={"msg": msg})

        except FailedCreateSRPM as ex:
            # so that we don't have to have sentry sdk installed locally
            import sentry_sdk

            sentry_sdk.capture_exception(ex)

            msg = f"Failed to create a SRPM: {ex}"
            r.report("failure", ex, check_names=PRCheckName.get_srpm_build_check())
            return HandlerResults(success=False, details={"msg": msg})

        except Exception as ex:
            # so that we don't have to have sentry sdk installed locally
            import sentry_sdk

            sentry_sdk.capture_exception(ex)

            msg = f"There was an error while running a copr build:\n```\n{ex}\n```\n"
            logger.error(msg)
            self.project.pr_comment(self.event.pr_id, f"{msg}\n{msg_retrigger}")
            r.report(
                "failure",
                "Build failed, check latest comment for details.",
                check_names=build_check_names,
            )
            return HandlerResults(success=False, details={"msg": msg})

        self.copr_build_model.build_id = build_id
        self.copr_build_model.save()

        return HandlerResults(success=True, details={})
