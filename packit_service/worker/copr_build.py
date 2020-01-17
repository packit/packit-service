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
import json
import logging
from typing import Union, List, Optional

from kubernetes.client.rest import ApiException
from ogr.abstract import GitProject
from packit.api import PackitAPI
from packit.config import PackageConfig, JobType, JobConfig
from packit.config.aliases import get_build_targets
from packit.exceptions import (
    PackitCoprException,
    PackitCoprProjectException,
)
from packit.local_project import LocalProject
from sandcastle import SandcastleCommandFailed, SandcastleTimeoutReached

from packit_service.config import ServiceConfig, Deployment
from packit_service.service.events import (
    PullRequestEvent,
    PullRequestCommentEvent,
    CoprBuildEvent,
)
from packit_service.service.models import CoprBuild
from packit_service.worker import sentry_integration
from packit_service.worker.copr_db import CoprBuildDB
from packit_service.worker.handler import (
    HandlerResults,
    BuildStatusReporter,
    PRCheckName,
)

try:
    from packit.exceptions import PackitSRPMException
except ImportError:
    # Backwards compatibility
    from packit.exceptions import FailedCreateSRPM as PackitSRPMException

logger = logging.getLogger(__name__)

MSG_RETRIGGER = (
    f"You can re-trigger copr build by adding a comment (`/packit copr-build`) "
    f"into this pull request."
)


class JobHelper:
    def __init__(
        self,
        config: ServiceConfig,
        package_config: PackageConfig,
        project: GitProject,
        event: Union[
            PullRequestEvent,
            PullRequestCommentEvent,
            CoprBuildEvent,
            PullRequestCommentEvent,
        ],
    ):
        self.config: ServiceConfig = config
        self.package_config: PackageConfig = package_config
        self.project: GitProject = project
        self.event: Union[
            PullRequestEvent,
            PullRequestCommentEvent,
            CoprBuildEvent,
            PullRequestCommentEvent,
        ] = event

        # lazy properties
        self._api = None
        self._copr_build_model = None
        self._job_copr_build = None
        self._job_tests = None
        self._local_project = None
        self._status_reporter = None
        self._test_check_names: Optional[List[str]] = None
        self._build_check_names: Optional[List[str]] = None

    @property
    def local_project(self) -> LocalProject:
        if self._local_project is None:
            self._local_project = LocalProject(
                git_project=self.project,
                working_dir=self.config.command_handler_work_dir,
                ref=self.base_ref,
                pr_id=self.event.pr_id,
            )
        return self._local_project

    @property
    def api(self) -> PackitAPI:
        if not self._api:
            self._api = PackitAPI(self.config, self.package_config, self.local_project)
        return self._api

    @property
    def base_ref(self) -> Optional[str]:
        if isinstance(self.event, (PullRequestEvent, PullRequestCommentEvent)):
            return self.event.base_ref
        return None

    @property
    def build_chroots(self) -> List[str]:
        """
        Return the chroots to build.

        1. If the job is not defined, use the test_chroots.
        2. If the job is defined, but not the targets, use "fedora-stable" alias otherwise.
        """
        if (
            (not self.job_copr_build or "targets" not in self.job_copr_build.metadata)
            and self.job_tests
            and "targets" in self.job_tests.metadata
        ):
            return self.tests_chroots

        if not self.job_copr_build:
            raw_targets = ["fedora-stable"]
        else:
            raw_targets = self.job_copr_build.metadata.get("targets", ["fedora-stable"])

        return list(get_build_targets(*raw_targets))

    @property
    def tests_chroots(self) -> List[str]:
        """
        Return the list of chroots used in the testing farm.
        Has to be a sub-set of the `build_chroots`.

        Return an empty list if there is no job configured.

        If not defined:
        1. use the build_chroots if the job si configured
        2. use "fedora-stable" alias otherwise
        """
        if not self.job_tests:
            return []

        if "targets" not in self.job_tests.metadata and self.job_copr_build:
            return self.build_chroots

        configured_targets = self.job_tests.metadata.get("targets", ["fedora-stable"])
        return list(get_build_targets(*configured_targets))

    @property
    def default_project_name(self):
        """
        Project name for copr -- add `-stg` suffix for the stg app.
        """
        stg = "-stg" if self.config.deployment == Deployment.stg else ""
        return f"{self.project.namespace}-{self.project.repo}-{self.event.pr_id}{stg}"

    @property
    def job_copr_build(self) -> Optional[JobConfig]:
        """
        Check if there is JobConfig for copr builds defined
        :return: JobConfig or None
        """
        if not self._job_copr_build:
            for job in self.package_config.jobs:
                if job.job == JobType.copr_build:
                    self._job_copr_build = job
        return self._job_copr_build

    @property
    def job_tests(self) -> Optional[JobConfig]:
        """
        Check if there is JobConfig for tests defined
        :return: JobConfig or None
        """
        if not self._job_tests:
            for job in self.package_config.jobs:
                if job.job == JobType.tests:
                    self._job_tests = job
        return self._job_tests

    @property
    def status_reporter(self):
        if not self._status_reporter:
            self._status_reporter = BuildStatusReporter(
                self.project, self.event.commit_sha, copr_build_model=None
            )
        return self._status_reporter

    @property
    def test_check_names(self) -> List[str]:
        if not self._test_check_names:
            self._test_check_names = [
                PRCheckName.get_testing_farm_check(chroot)
                for chroot in self.tests_chroots
            ]
        return self._test_check_names

    @property
    def build_check_names(self) -> List[str]:
        if not self._build_check_names:
            self._build_check_names = [
                PRCheckName.get_build_check(chroot) for chroot in self.build_chroots
            ]
        return self._build_check_names

    @property
    def job_project(self) -> Optional[str]:
        """
        The job definition from the config file.
        """
        if self.job_copr_build:
            return self.job_copr_build.metadata.get(
                "project", self.default_project_name
            )
        return self.default_project_name

    def report_status_to_all(
        self, description: str, state: str, url: str = None
    ) -> None:
        self.report_status_to_build(description, state, url)
        self.report_status_to_tests(description, state, url)

    def report_status_to_build(self, description, state, url: str = None):
        if self.job_copr_build:
            self.status_reporter.report(
                description=description,
                state=state,
                url=url,
                check_names=self.build_check_names,
            )

    def report_status_to_tests(self, description, state, url: str = None):
        if self.job_tests:
            self.status_reporter.report(
                description=description,
                state=state,
                url=url,
                check_names=self.test_check_names,
            )


class CoprBuildJobHelper(JobHelper):
    @property
    def status_reporter(self):
        if not self._status_reporter:
            self._status_reporter = BuildStatusReporter(
                self.project, self.event.commit_sha, self.copr_build_model
            )
        return self._status_reporter

    @property
    def copr_build_model(self) -> CoprBuild:
        if self._copr_build_model is None:
            self._copr_build_model = CoprBuild.create(
                project=self.job_project,
                owner=self.job_owner,
                chroots=self.build_chroots,
            )
        return self._copr_build_model

    @property
    def job_owner(self) -> Optional[str]:
        """
        Owner used for the copr build -- search the config or use the copr's config.
        """
        if self.job_copr_build:
            owner = self.job_copr_build.metadata.get("owner")
            if owner:
                return owner

        return self.api.copr_helper.copr_client.config.get("username")

    def run_copr_build(self) -> HandlerResults:

        if not (self.job_copr_build or self.job_tests):
            msg = "No copr_build or tests job defined."
            # we can't report it to end-user at this stage
            return HandlerResults(success=False, details={"msg": msg})

        try:
            self.report_status_to_all(description="Building SRPM ...", state="pending")
            build_id, _ = self.api.run_copr_build(
                project=self.job_project,
                chroots=self.build_chroots,
                owner=self.job_owner,
            )
            self.report_status_to_all(
                description="Building RPM ...",
                state="pending",
                url="https://copr.fedorainfracloud.org/coprs/"
                f"{self.job_owner}/{self.job_project}/build/{build_id}/",
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
                self.base_ref,
                self.event.project_url,
            )

        except SandcastleTimeoutReached:
            return self._process_timeout()

        except SandcastleCommandFailed as ex:
            return self._process_failed_command(ex)

        except ApiException as ex:
            return self._process_openshift_error(ex)

        except PackitSRPMException as ex:
            return self._process_failed_srpm_build(ex)

        except PackitCoprProjectException as ex:
            return self._process_copr_submit_exception(ex)

        except PackitCoprException as ex:
            return self._process_general_exception(ex)

        except Exception as ex:
            return self._process_general_exception(ex)

        self.copr_build_model.build_id = build_id
        self.copr_build_model.save()

        return HandlerResults(success=True, details={})

    def _process_copr_submit_exception(self, ex):
        sentry_integration.send_to_sentry(ex)
        msg = (
            f"There was an error while submitting a Copr build:\n"
            f"```\n"
            f"{ex}\n"
            f"```\n"
            f"Check carefully your configuration.\n"
        )
        logger.error(msg)
        self.project.pr_comment(self.event.pr_id, f"{msg}\n{MSG_RETRIGGER}")
        self.report_status_to_all(
            state="error",
            description="Submit of the build failed, check comments for details.",
        )
        return HandlerResults(success=False, details={"msg": msg})

    def _process_general_exception(self, ex):
        sentry_integration.send_to_sentry(ex)
        msg = f"There was an error while running a copr build:\n```\n{ex}\n```\n"
        logger.error(msg)
        self.project.pr_comment(self.event.pr_id, f"{msg}\n{MSG_RETRIGGER}")
        self.report_status_to_build(
            state="failure",
            description="Build failed, check latest comment for details.",
        )
        self.report_status_to_tests(
            state="error",
            description="Build failed, check latest comment for details.",
        )
        return HandlerResults(success=False, details={"msg": msg})

    def _process_failed_srpm_build(self, ex):
        sentry_integration.send_to_sentry(ex)
        msg = (
            f"There was an error while creating the SRPM. {MSG_RETRIGGER}\n"
            "\nOutput:"
            "\n```\n"
            f"{ex}"
            "\n```"
        )
        self.project.pr_comment(self.event.pr_id, msg)
        short_msg = "Failed to create SRPM."
        self.report_status_to_all(description=short_msg, state="error")
        return HandlerResults(success=False, details={"msg": short_msg})

    def _process_failed_command(self, ex):
        max_log_size = 1024 * 16  # is 16KB enough?
        if len(ex.output) > max_log_size:
            output = "Earlier output was truncated\n\n" + ex.output[-max_log_size:]
        else:
            output = ex.output
        msg = (
            f"There was an error while creating the SRPM. {MSG_RETRIGGER}\n"
            "\nOutput:"
            "\n```\n"
            f"{output}"
            "\n```"
            f"\nReturn code: {ex.rc}"
        )
        self.project.pr_comment(self.event.pr_id, msg)
        sentry_integration.send_to_sentry(output)
        msg = "Failed to create SRPM."
        self.report_status_to_all(
            state="error", description=msg,
        )
        return HandlerResults(success=False, details={"msg": msg})

    def _process_timeout(self):
        msg = f"You have reached 10-minute timeout while creating the SRPM. {MSG_RETRIGGER}"
        self.project.pr_comment(self.event.pr_id, msg)
        msg = "Timeout reached while creating a SRPM."
        self.report_status_to_all(
            state="error", description=msg,
        )
        return HandlerResults(success=False, details={"msg": msg})

    def _process_openshift_error(self, ex: ApiException):
        sentry_integration.send_to_sentry(ex)

        error_message = f"({ex.status})\nReason: {ex.reason}\n"
        if ex.headers:
            error_message += f"HTTP response headers: {ex.headers}\n"

        if ex.body:
            try:
                json_content = json.loads(ex.body)
                formatted_json = json.dumps(json_content, indent=2)
                error_message += f"HTTP response body:\n{formatted_json}\n"
            except json.JSONDecodeError:
                error_message += f"HTTP response body: {ex.body}\n"

        msg = (
            f"There was a problem in the environment the service is running in:\n"
            f"```\n"
            f"{error_message}\n"
            f"```\n"
        )

        logger.error(msg)
        comment_msg = (
            f"{msg}\n"
            f"{MSG_RETRIGGER}\n\n"
            "Please, contact "
            "[Packit team](https://github.com/orgs/packit-service/teams/the-packit-team) "
            "if the re-trigger did not help."
        )
        self.project.pr_comment(self.event.pr_id, comment_msg)

        self.report_status_to_all(
            state="error", description="Build failed, check the comments for details.",
        )
        return HandlerResults(success=False, details={"msg": msg})
