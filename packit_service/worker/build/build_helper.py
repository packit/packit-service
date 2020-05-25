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
from pathlib import Path
from typing import Union, List, Optional, Tuple, Set

from kubernetes.client.rest import ApiException

from ogr.abstract import GitProject, CommitStatus
from packit.api import PackitAPI
from packit.config import PackageConfig, JobType, JobConfig
from packit.local_project import LocalProject
from packit.utils import PackitFormatter
from packit_service import sentry_integration
from packit_service.config import ServiceConfig, Deployment
from packit_service.models import SRPMBuildModel
from packit_service.service.events import (
    PullRequestGithubEvent,
    PullRequestCommentGithubEvent,
    CoprBuildEvent,
    PushGitHubEvent,
    ReleaseEvent,
    PullRequestPagureEvent,
    PullRequestCommentPagureEvent,
)
from packit_service.trigger_mapping import (
    is_trigger_matching_job_config,
    are_job_types_same,
)
from packit_service.worker.reporting import StatusReporter
from sandcastle import SandcastleTimeoutReached

logger = logging.getLogger(__name__)


class BaseBuildJobHelper:
    job_type_build: Optional[JobType] = None
    job_type_test: Optional[JobType] = None
    status_name_build: str = "base-build-status"
    status_name_test: str = "base-test-status"

    def __init__(
        self,
        config: ServiceConfig,
        package_config: PackageConfig,
        project: GitProject,
        event: Union[
            PullRequestGithubEvent,
            PullRequestPagureEvent,
            PullRequestCommentGithubEvent,
            PullRequestCommentPagureEvent,
            CoprBuildEvent,
            PushGitHubEvent,
            ReleaseEvent,
        ],
        job: Optional[JobConfig] = None,
    ):
        self.config: ServiceConfig = config
        self.package_config: PackageConfig = package_config
        self.project: GitProject = project
        self.event: Union[
            PullRequestGithubEvent,
            PullRequestPagureEvent,
            PullRequestCommentGithubEvent,
            PullRequestCommentPagureEvent,
            CoprBuildEvent,
            PushGitHubEvent,
            ReleaseEvent,
        ] = event
        self.msg_retrigger: Optional[str] = ""

        # lazy properties
        self._api = None
        self._local_project = None
        self._status_reporter: Optional[StatusReporter] = None
        self._test_check_names: Optional[List[str]] = None
        self._build_check_names: Optional[List[str]] = None
        self._srpm_model: Optional[SRPMBuildModel] = None
        self._srpm_path: Optional[Path] = None

        # lazy properties, current job by default
        self._job_build = (
            job if job and are_job_types_same(job.type, self.job_type_build) else None
        )
        self._job_tests = (
            job if job and are_job_types_same(job.type, self.job_type_test) else None
        )

    @property
    def local_project(self) -> LocalProject:
        if self._local_project is None:
            self._local_project = LocalProject(
                git_project=self.project,
                working_dir=self.config.command_handler_work_dir,
                ref=self.event.git_ref,
                pr_id=self.event.pr_id,
            )
        return self._local_project

    @property
    def api(self) -> PackitAPI:
        if not self._api:
            self._api = PackitAPI(self.config, self.package_config, self.local_project)
        return self._api

    @property
    def api_url(self) -> str:
        return (
            "https://prod.packit.dev/api"
            if self.config.deployment == Deployment.prod
            else "https://stg.packit.dev/api"
        )

    @property
    def configured_build_targets(self) -> Set[str]:
        """
        Return the targets to build.

        1. If the job is not defined, use the test_targets.
        2. If the job is defined, but not the targets, use "fedora-stable" alias otherwise.
        """
        if (
            (not self.job_build or not self.job_build.metadata.targets)
            and self.job_tests
            and self.job_tests.metadata.targets
        ):
            return self.configured_tests_targets

        if self.job_build and self.job_build.metadata.targets:
            return self.job_build.metadata.targets

        return {"fedora-stable"}

    @property
    def configured_tests_targets(self) -> Set[str]:
        """
        Return the list of chroots used in the testing farm.
        Has to be a sub-set of the `build_chroots`.

        Return an empty list if there is no job configured.

        If not defined:
        1. use the build_chroots if the job si configured
        2. use "fedora-stable" alias otherwise
        """
        if not self.job_tests:
            return set()

        if not self.job_tests.metadata.targets and self.job_build:
            return self.configured_build_targets

        return self.job_tests.metadata.targets or {"fedora-stable"}

    @property
    def job_build(self) -> Optional[JobConfig]:
        """
        Check if there is JobConfig for builds defined
        :return: JobConfig or None
        """
        if not self.job_type_build:
            return None

        if not self._job_build:
            for job in self.package_config.jobs:
                if are_job_types_same(job.type, self.job_type_build) and (
                    (
                        self.event.db_trigger
                        and self.event.db_trigger.job_config_trigger_type == job.trigger
                    )
                    or is_trigger_matching_job_config(
                        trigger=self.event.trigger, job_config=job
                    )
                ):
                    self._job_build = job
                    break
        return self._job_build

    @property
    def job_build_branch(self) -> Optional[str]:
        """
        Branch used for the build job or "master".
        """
        if self.job_build and self.job_build.metadata.branch:
            return self.job_build.metadata.branch

        return "master"

    @property
    def job_tests(self) -> Optional[JobConfig]:
        """
        Check if there is JobConfig for tests defined
        :return: JobConfig or None
        """
        if not self.job_type_test:
            return None

        if not self._job_tests:
            for job in self.package_config.jobs:
                if are_job_types_same(job.type, self.job_type_test) and (
                    (
                        self.event.db_trigger
                        and self.event.db_trigger.job_config_trigger_type == job.trigger
                    )
                    or is_trigger_matching_job_config(
                        trigger=self.event.trigger, job_config=job
                    )
                ):
                    self._job_tests = job
                    break
        return self._job_tests

    @property
    def status_reporter(self) -> StatusReporter:
        if not self._status_reporter:
            self._status_reporter = StatusReporter(
                self.project, self.event.commit_sha, self.event.pr_id
            )
        return self._status_reporter

    @property
    def build_targets(self) -> Set[str]:
        """
        Return the targets/chroots to build.

        (Used when submitting the koji/copr build and as a part of the commit status name.)

        1. If the job is not defined, use the test chroots.
        2. If the job is defined, but not the targets, use "fedora-stable" alias otherwise.
        """
        raise NotImplementedError("Use subclass instead.")

    @property
    def tests_targets(self) -> Set[str]:
        """
        Return the list of targets/chroots used in the testing farm.
        Has to be a sub-set of the `build_targets`.

        (Used when submitting the koji/copr build and as a part of the commit status name.)

        Return an empty list if there is no job configured.

        If not defined:
        1. use the build_targets if the job si configured
        2. use "fedora-stable" alias otherwise
        """
        raise NotImplementedError("Use subclass instead.")

    @property
    def test_check_names(self) -> List[str]:
        """
        List of full names of the commit statuses.

        e.g. ["packit/copr-build-fedora-rawhide-x86_64"]
        or ["packit-stg/production-build-f31", "packit-stg/production-build-f32"]
        """
        if not self._test_check_names:
            self._test_check_names = [
                self.get_test_check(target) for target in self.tests_targets
            ]
        return self._test_check_names

    @property
    def build_check_names(self) -> List[str]:
        """
        List of full names of the commit statuses.

        e.g. ["packit/copr-build-fedora-rawhide-x86_64"]
        or ["packit-stg/production-build-f31", "packit-stg/production-build-f32"]
        """
        if not self._build_check_names:
            self._build_check_names = [
                self.get_build_check(target) for target in self.build_targets
            ]
        return self._build_check_names

    @property
    def srpm_model(self) -> SRPMBuildModel:
        if not self._srpm_model:
            self._create_srpm()
        return self._srpm_model

    @property
    def srpm_path(self) -> Optional[Path]:
        self.create_srpm_if_needed()
        return self._srpm_path

    @classmethod
    def get_build_check(cls, chroot: str = None) -> str:
        config = ServiceConfig.get_service_config()
        deployment_str = (
            "packit" if config.deployment == Deployment.prod else "packit-stg"
        )
        chroot_str = f"-{chroot}" if chroot else ""
        return f"{deployment_str}/{cls.status_name_build}{chroot_str}"

    @classmethod
    def get_test_check(cls, chroot: str = None) -> str:
        config = ServiceConfig.get_service_config()
        deployment_str = (
            "packit" if config.deployment == Deployment.prod else "packit-stg"
        )
        chroot_str = f"-{chroot}" if chroot else ""
        return f"{deployment_str}/{cls.status_name_test}{chroot_str}"

    def create_srpm_if_needed(self) -> None:
        """If you want to be sure we already created the SRPM."""
        if not (self._srpm_path or self._srpm_model):
            self._create_srpm()

    def _create_srpm(self):
        # we want to get packit logs from the SRPM creation process
        # so we stuff them into a StringIO buffer
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        packit_logger = logging.getLogger("packit")
        packit_logger.setLevel(logging.DEBUG)
        packit_logger.addHandler(handler)
        formatter = PackitFormatter(None, "%H:%M:%S")
        handler.setFormatter(formatter)

        srpm_success = True
        exception: Optional[Exception] = None
        extra_logs: str = ""

        try:
            self._srpm_path = Path(
                self.api.create_srpm(srpm_dir=self.api.up.local_project.working_dir)
            )
        except SandcastleTimeoutReached as ex:
            exception = ex
            extra_logs = f"\nYou have reached 10-minute timeout while creating SRPM.\n"
        except ApiException as ex:
            exception = ex
            # this is an internal error: let's not expose anything to public
            extra_logs = (
                "\nThere was a problem in the environment the packit-service is running in.\n"
                "Please hang tight, the help is coming."
            )
        except Exception as ex:
            exception = ex

        # collect the logs now
        packit_logger.removeHandler(handler)
        stream.seek(0)
        srpm_logs = stream.read()

        if exception:
            logger.info(f"exception while running SRPM build: {exception}")
            logger.debug(f"{exception!r}")

            srpm_success = False

            # when do we NOT want to send stuff to sentry?
            sentry_integration.send_to_sentry(exception)

            # this needs to be done AFTER we gather logs
            # so that extra logs are after actual logs
            srpm_logs += extra_logs
            if hasattr(exception, "output"):
                output = getattr(exception, "output", "")  # mypy
                srpm_logs += f"\nOutput of the command in the sandbox:\n{output}\n"

            srpm_logs += (
                f"\nMessage: {exception}\nException: {exception!r}\n{self.msg_retrigger}"
                "\nPlease join the freenode IRC channel #packit for the latest info.\n"
            )

        self._srpm_model = SRPMBuildModel.create(logs=srpm_logs, success=srpm_success)

    def _report(
        self,
        state: CommitStatus,
        description: str,
        url: str = "",
        check_names: Union[str, list, None] = None,
    ) -> None:
        """
        The status reporting should be done through this method
        so we can extend it in subclasses easily.
        """
        self.status_reporter.report(
            description=description, state=state, url=url, check_names=check_names,
        )

    def report_status_to_all(
        self, description: str, state: CommitStatus, url: str = ""
    ) -> None:
        self.report_status_to_build(description, state, url)
        self.report_status_to_tests(description, state, url)

    def report_status_to_build(self, description, state, url: str = "") -> None:
        if self.job_build:
            self._report(
                description=description,
                state=state,
                url=url,
                check_names=self.build_check_names,
            )

    def report_status_to_tests(self, description, state, url: str = "") -> None:
        if self.job_tests:
            self._report(
                description=description,
                state=state,
                url=url,
                check_names=self.test_check_names,
            )

    def report_status_to_build_for_chroot(
        self, description, state, url: str = "", chroot: str = ""
    ) -> None:
        if self.job_build and chroot in self.build_targets:
            cs = self.get_build_check(chroot)
            self._report(
                description=description, state=state, url=url, check_names=cs,
            )

    def report_status_to_test_for_chroot(
        self, description, state, url: str = "", chroot: str = ""
    ) -> None:
        if self.job_tests and chroot in self.tests_targets:
            self._report(
                description=description,
                state=state,
                url=url,
                check_names=self.get_test_check(chroot),
            )

    def report_status_to_all_for_chroot(
        self, description, state, url: str = "", chroot: str = ""
    ):
        self.report_status_to_build_for_chroot(description, state, url, chroot)
        self.report_status_to_test_for_chroot(description, state, url, chroot)

    def run_build(
        self, target: Optional[str] = None
    ) -> Tuple[Optional[int], Optional[str]]:
        """
        Trigger the build and return id and web_url
        :param target: str, run for all if not set
        :return: task_id, task_url
        """
        raise NotImplementedError()
