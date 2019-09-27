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
        check_name = PRCheckName.get_build_check()
        # add suffix stg when using stg app
        stg = "-stg" if self.config.deployment == Deployment.stg else ""
        default_project_name = (
            f"{self.project.namespace}-{self.project.repo}-{self.event.pr_id}{stg}"
        )
        job = self.get_job_copr_build_metadata()
        if not job.metadata.get("targets"):
            logger.error(
                "'targets' value is required in packit config for copr_build job"
            )

        self.job_project = job.metadata.get("project") or default_project_name
        self.job_owner = job.metadata.get("owner") or self.api.copr.config.get(
            "username"
        )
        self.job_chroots = job.metadata.get("targets")
        r = BuildStatusReporter(
            self.project, self.event.commit_sha, self.copr_build_model
        )
        msg_retrigger = (
            f"You can re-trigger copr build by adding a comment (`/packit copr-build`) "
            f"into this pull request."
        )
        try:
            r.report("pending", "RPM build has just started...", check_name=check_name)
            build_id, repo_url = self.api.run_copr_build(
                project=self.job_project, chroots=self.job_chroots, owner=self.job_owner
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
            )

        except SandcastleTimeoutReached:
            msg = f"You have reached 10-minute timeout while creating the SRPM. {msg_retrigger}"
            self.project.pr_comment(self.event.pr_id, msg)
            msg = "Timeout reached while creating a SRPM."
            r.report("failure", msg, check_name=check_name)
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
            msg = "Failed to create a SRPM."
            r.report("failure", msg, check_name=check_name)
            return HandlerResults(success=False, details={"msg": msg})

        except FailedCreateSRPM:
            msg = "Failed to create a SRPM."
            r.report("failure", msg, check_name=check_name)
            return HandlerResults(success=False, details={"msg": msg})

        except Exception as ex:
            msg = f"There was an error while running a copr build:\n```\n{ex}\n```\n"
            logger.error(msg)
            self.project.pr_comment(self.event.pr_id, f"{msg}\n{msg_retrigger}")
            r.report(
                "failure",
                "Build failed, check latest comment for details.",
                check_name=check_name,
            )
            return HandlerResults(success=False, details={"msg": msg})

        self.copr_build_model.build_id = build_id
        self.copr_build_model.save()

        return HandlerResults(success=True, details={})
