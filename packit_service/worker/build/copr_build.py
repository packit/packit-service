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
from io import StringIO
from typing import Union, Optional

from kubernetes.client.rest import ApiException
from ogr.abstract import GitProject
from packit.config import PackageConfig, JobType
from packit.exceptions import (
    PackitCoprException,
    PackitCoprProjectException,
)
from packit.utils import PackitFormatter
from sandcastle import SandcastleCommandFailed, SandcastleTimeoutReached

from packit_service.config import ServiceConfig, Deployment
from packit_service.constants import MSG_RETRIGGER
from packit_service.models import CoprBuild, SRPMBuild
from packit_service.service.events import (
    PullRequestEvent,
    PullRequestCommentEvent,
    CoprBuildEvent,
)
from packit_service.service.models import CoprBuild as RedisCoprBuild
from packit_service.service.urls import get_log_url
from packit_service.worker import sentry_integration
from packit_service.worker.build.build_helper import BaseBuildJobHelper
from packit_service.worker.reporting import StatusReporter
from packit_service.worker.handler import HandlerResults
from packit_service.worker.utils import get_copr_build_url_for_values

try:
    from packit.exceptions import PackitSRPMException
except ImportError:
    # Backwards compatibility
    from packit.exceptions import FailedCreateSRPM as PackitSRPMException

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
        event: Union[
            PullRequestEvent,
            PullRequestCommentEvent,
            CoprBuildEvent,
            PullRequestCommentEvent,
        ],
    ):
        super().__init__(config, package_config, project, event)

        self.msg_retrigger: str = MSG_RETRIGGER.format(
            build="copr-build" if self.job_build else "build"
        )

        # lazy properties
        self._copr_build_model = None

    @property
    def status_reporter(self):
        if not self._status_reporter:
            self._status_reporter = StatusReporter(self.project, self.event.commit_sha)
        return self._status_reporter

    @property
    def default_project_name(self):
        """
        Project name for copr -- add `-stg` suffix for the stg app.
        """
        stg = "-stg" if self.config.deployment == Deployment.stg else ""
        return f"{self.project.namespace}-{self.project.repo}-{self.event.pr_id}{stg}"

    @property
    def job_project(self) -> Optional[str]:
        """
        The job definition from the config file.
        """
        if self.job_build:
            return self.job_build.metadata.get("project", self.default_project_name)
        return self.default_project_name

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

    @property
    def job_owner(self) -> Optional[str]:
        """
        Owner used for the copr build -- search the config or use the copr's config.
        """
        if self.job_build:
            owner = self.job_build.metadata.get("owner")
            if owner:
                return owner

        return self.api.copr_helper.copr_client.config.get("username")

    def run_copr_build(self) -> HandlerResults:

        if not (self.job_build or self.job_tests):
            msg = "No copr_build or tests job defined."
            # we can't report it to end-user at this stage
            return HandlerResults(success=False, details={"msg": msg})

        try:
            self.report_status_to_all(description="Building SRPM ...", state="pending")

            # we want to get packit logs from the SRPM creation process
            # so we stuff them into a StringIO buffer
            stream = StringIO()
            handler = logging.StreamHandler(stream)
            packit_logger = logging.getLogger("packit")
            packit_logger.setLevel(logging.DEBUG)
            packit_logger.addHandler(handler)
            formatter = PackitFormatter(None, "%H:%M:%S")
            handler.setFormatter(formatter)

            build_id, _ = self.api.run_copr_build(
                project=self.job_project,
                chroots=self.build_chroots,
                owner=self.job_owner,
            )

            packit_logger.removeHandler(handler)
            stream.seek(0)
            logs = stream.read()
            web_url = get_copr_build_url_for_values(
                self.job_owner, self.job_project, build_id
            )

            srpm_build = SRPMBuild.create(logs)

            status = "pending"
            description = "Building RPM ..."
            for chroot in self.build_chroots:
                copr_build = CoprBuild.get_or_create(
                    pr_id=self.event.pr_id,
                    build_id=str(build_id),
                    commit_sha=self.event.commit_sha,
                    repo_name=self.event.base_repo_name,
                    namespace=self.event.base_repo_namespace,
                    web_url=web_url,
                    target=chroot,
                    status=status,
                    srpm_build=srpm_build,
                )
                url = get_log_url(id_=copr_build.id)
                self.report_status_to_all_for_chroot(
                    state=status, description=description, url=url, chroot=chroot,
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
        self.project.pr_comment(self.event.pr_id, f"{msg}\n{self.msg_retrigger}")
        self.report_status_to_all(
            state="error",
            description="Submit of the build failed, check comments for details.",
        )
        return HandlerResults(success=False, details={"msg": msg})

    def _process_general_exception(self, ex):
        sentry_integration.send_to_sentry(ex)
        msg = f"There was an error while running a copr build:\n```\n{ex}\n```\n"
        logger.error(msg)
        self.project.pr_comment(self.event.pr_id, f"{msg}\n{self.msg_retrigger}")
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
            f"There was an error while creating SRPM. {self.msg_retrigger}\n"
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
            f"There was an error while creating SRPM. {self.msg_retrigger}\n"
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
        msg = f"You have reached 10-minute timeout while creating SRPM. {self.msg_retrigger}"
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
            f"{self.msg_retrigger}\n\n"
            "Please, contact "
            "[Packit team](https://github.com/orgs/packit-service/teams/the-packit-team) "
            "if the re-trigger did not help."
        )
        self.project.pr_comment(self.event.pr_id, comment_msg)

        self.report_status_to_all(
            state="error", description="Build failed, check the comments for details.",
        )
        return HandlerResults(success=False, details={"msg": msg})
