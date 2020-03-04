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

from ogr.abstract import GitProject, CommitStatus
from packit.api import PackitAPI
from packit.config import PackageConfig, JobType, JobConfig
from packit.config.aliases import get_build_targets
from packit.local_project import LocalProject

from packit_service.config import ServiceConfig, Deployment
from packit_service.service.events import (
    PullRequestEvent,
    PullRequestCommentEvent,
    CoprBuildEvent,
    PushGitHubEvent,
    ReleaseEvent,
)
from packit_service.worker.reporting import StatusReporter

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
            PullRequestEvent,
            PullRequestCommentEvent,
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
            PullRequestEvent,
            PullRequestCommentEvent,
            CoprBuildEvent,
            PushGitHubEvent,
            ReleaseEvent,
        ] = event
        self.pr_id = (
            self.event.pr_id
            if isinstance(self.event, (PullRequestEvent, PullRequestCommentEvent))
            else None
        )

        # lazy properties
        self._api = None
        self._local_project = None
        self._status_reporter = None
        self._test_check_names: Optional[List[str]] = None
        self._build_check_names: Optional[List[str]] = None

        # lazy properties, current job by default
        self._job_build = job if job and job.job == self.job_type_build else None
        self._job_tests = job if job and job.job == self.job_type_test else None

    @property
    def local_project(self) -> LocalProject:
        if self._local_project is None:
            self._local_project = LocalProject(
                git_project=self.project,
                working_dir=self.config.command_handler_work_dir,
                ref=self.event.git_ref,
                pr_id=self.pr_id,
            )
        return self._local_project

    @property
    def api(self) -> PackitAPI:
        if not self._api:
            self._api = PackitAPI(self.config, self.package_config, self.local_project)
        return self._api

    @property
    def build_chroots(self) -> List[str]:
        """
        Return the chroots to build.

        1. If the job is not defined, use the test_chroots.
        2. If the job is defined, but not the targets, use "fedora-stable" alias otherwise.
        """
        if (
            (not self.job_build or "targets" not in self.job_build.metadata)
            and self.job_tests
            and "targets" in self.job_tests.metadata
        ):
            return self.tests_chroots

        if not self.job_build:
            raw_targets = ["fedora-stable"]
        else:
            raw_targets = self.job_build.metadata.get("targets", ["fedora-stable"])
            if isinstance(raw_targets, str):
                raw_targets = [raw_targets]

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

        if "targets" not in self.job_tests.metadata and self.job_build:
            return self.build_chroots

        configured_targets = self.job_tests.metadata.get("targets", ["fedora-stable"])
        if isinstance(configured_targets, str):
            configured_targets = [configured_targets]

        return list(get_build_targets(*configured_targets))

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
                if job.type == self.job_type_build:
                    self._job_build = job
                    break
        return self._job_build

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
                if job.type == self.job_type_test:
                    self._job_tests = job
                    break
        return self._job_tests

    @property
    def status_reporter(self):
        if not self._status_reporter:
            self._status_reporter = StatusReporter(self.project, self.event.commit_sha)
        return self._status_reporter

    @property
    def test_check_names(self) -> List[str]:
        if not self._test_check_names:
            self._test_check_names = [
                self.get_test_check(chroot) for chroot in self.tests_chroots
            ]
        return self._test_check_names

    @property
    def build_check_names(self) -> List[str]:
        if not self._build_check_names:
            self._build_check_names = [
                self.get_build_check(chroot) for chroot in self.build_chroots
            ]
        return self._build_check_names

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
        if self.job_build and chroot in self.build_chroots:
            cs = self.get_build_check(chroot)
            self._report(
                description=description, state=state, url=url, check_names=cs,
            )

    def report_status_to_test_for_chroot(
        self, description, state, url: str = "", chroot: str = ""
    ) -> None:
        if self.job_tests and chroot in self.tests_chroots:
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
