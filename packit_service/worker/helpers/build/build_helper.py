# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
import datetime
import logging
from io import StringIO
from pathlib import Path
from typing import List, Optional, Set, Tuple

from kubernetes.client.rest import ApiException
from ogr.abstract import GitProject
from packit.config import JobConfig, JobType, JobConfigTriggerType
from packit.config.aliases import DEFAULT_VERSION
from packit.config.package_config import PackageConfig
from packit.exceptions import PackitMergeException
from packit.utils import PackitFormatter
from packit_service import sentry_integration
from packit_service.config import ServiceConfig
from packit_service.constants import PG_BUILD_STATUS_SUCCESS, PG_BUILD_STATUS_FAILURE
from packit_service.models import PipelineModel, SRPMBuildModel
from packit_service.service.urls import get_srpm_build_info_url
from packit_service.trigger_mapping import are_job_types_same
from packit_service.worker.helpers.job_helper import BaseJobHelper
from packit_service.worker.events import EventData
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.reporting import BaseCommitStatus
from packit_service.worker.result import TaskResults
from sandcastle import SandcastleTimeoutReached

logger = logging.getLogger(__name__)


class BaseBuildJobHelper(BaseJobHelper):
    job_type_build: Optional[JobType] = None
    job_type_test: Optional[JobType] = None
    status_name_build: str = "base-build-status"
    status_name_test: str = "base-test-status"

    def __init__(
        self,
        service_config: ServiceConfig,
        package_config: PackageConfig,
        project: GitProject,
        metadata: EventData,
        db_trigger,
        job_config: JobConfig,
        build_targets_override: Optional[Set[str]] = None,
        tests_targets_override: Optional[Set[str]] = None,
        pushgateway: Optional[Pushgateway] = None,
    ):
        super().__init__(
            service_config=service_config,
            package_config=package_config,
            project=project,
            metadata=metadata,
            db_trigger=db_trigger,
            job_config=job_config,
            pushgateway=pushgateway,
        )
        self.run_model: Optional[PipelineModel] = None
        self.build_targets_override: Optional[Set[str]] = build_targets_override
        self.tests_targets_override: Optional[Set[str]] = tests_targets_override
        self.pushgateway = pushgateway

        # lazy properties
        self._test_check_names: Optional[List[str]] = None
        self._build_check_names: Optional[List[str]] = None
        self._srpm_model: Optional[SRPMBuildModel] = None
        self._srpm_path: Optional[Path] = None
        self._job_tests: Optional[JobConfig] = None
        self._job_build: Optional[JobConfig] = None

    @property
    def configured_build_targets(self) -> Set[str]:
        """
        Return the configured targets for build job.

        1. Use targets defined for build job and targets defined for test job.
        2. Use "fedora-stable" alias if neither defined.
        """
        targets = set()
        if self.job_build:
            targets.update(self.job_build.targets)

        if self.job_tests and not self.job_tests.skip_build:
            targets.update(self.job_tests.targets)

        if (
            self.job_type_build == JobType.copr_build
            and (self.job_build and not self.job_build.targets)
            and self.is_custom_copr_project_defined()  # type: ignore
        ):
            copr_targets = self.get_configured_targets()  # type: ignore
            targets.update(copr_targets)

        return targets or {DEFAULT_VERSION}

    @property
    def configured_tests_targets(self) -> Set[str]:
        """
        Return the configured targets for test job.
        Has to be a sub-set of the `configured_build_targets`.

        Return an empty set if there is no test job configured.

        If not defined:
        1. use the `configured_build_targets` if the build job is configured
        2. use "fedora-stable" alias otherwise
        """
        if not self.job_tests:
            return set()

        if not self.job_tests.targets and self.job_build:
            return self.configured_build_targets

        return self.job_tests.targets or {DEFAULT_VERSION}

    @property
    def job_build(self) -> Optional[JobConfig]:
        """
        Check if there is JobConfig for builds defined
        :return: JobConfig or None
        """
        if not self.job_type_build:
            return None
        if not self._job_build:
            for job in [self.job_config] + self.package_config.jobs:
                if are_job_types_same(job.type, self.job_type_build) and (
                    self.db_trigger
                    and self.db_trigger.job_config_trigger_type == job.trigger
                ):
                    self._job_build = job
                    break
        return self._job_build

    @property
    def job_build_branch(self) -> Optional[str]:
        """
        Branch used for the build job or project's default branch.
        """
        if self.job_build and self.job_build.branch:
            return self.job_build.branch

        return self.project.default_branch

    @property
    def job_tests(self) -> Optional[JobConfig]:
        """
        Check if there is JobConfig for tests defined
        :return: JobConfig or None
        """
        if not self.job_type_test:
            return None

        if not self._job_tests:
            for job in [self.job_config] + self.package_config.jobs:
                if are_job_types_same(job.type, self.job_type_test) and (
                    self.db_trigger
                    and self.db_trigger.job_config_trigger_type == job.trigger
                ):
                    self._job_tests = job
                    break
        return self._job_tests

    @property
    def build_targets_all(self) -> Set[str]:
        """
        Return all valid build targets/chroots from config.
        """
        raise NotImplementedError("Use subclass instead.")

    @property
    def tests_targets_all(self) -> Set[str]:
        """
        Return all valid test targets/chroots from config.
        Has to be a sub-set of the `build_targets_all`.

        Return an empty set if there is no job configured.
        """
        raise NotImplementedError("Use subclass instead.")

    @property
    def tests_targets_all_mapped(self) -> Set[str]:
        """
        Return all valid mapped test targets from config.
        """
        raise NotImplementedError("Use subclass instead.")

    def get_build_targets(self, configured_targets: Set[str]) -> Set[str]:
        """
        Return valid targets/chroots to build from configured targets.
        If there are build_targets_override or tests_targets_override defined,
        configured targets ∩ (build_targets_override ∪ mapped tests_targets_override)
        will be returned.

        Example:
        build and test job configuration:
          - job: build
            trigger: pull_request
            metadata:
                targets:
                      fedora-35-x86_64

          - job: tests
            trigger: pull_request
            metadata:
                targets:
                      epel-7-x86_64:
                        distros: [centos-7, rhel-7]

        helper.build_targets_override: None
        helper.test_targets_override = {"centos-7-x86_64"}
        helper.get_build_targets({"fedora-35-x86_64", "epel-7-x86_64"})-> {"epel-7-x86_64"}
        """
        targets_override = set()

        if self.build_targets_override:
            logger.debug(f"Build targets override: {self.build_targets_override}")
            targets_override.update(self.build_targets_override)

        if self.tests_targets_override:
            logger.debug(f"Test targets override: {self.tests_targets_override}")
            targets_override.update(
                self.test_target2build_target(target)
                for target in self.tests_targets_override
            )

        return (
            configured_targets & targets_override
            if targets_override
            else configured_targets
        )

    @property
    def build_targets(self) -> Set[str]:
        """
        Return valid targets/chroots to build.

        (Used when submitting the koji/copr build and as a part of the commit status name.)
        """
        return self.get_build_targets(self.build_targets_all)

    @property
    def build_targets_for_tests(self) -> Set[str]:
        """
        Return valid targets/chroots to build in needed to run testing farm.

        (Used when submitting the tests and as a part of the commit status name.)
        """
        return self.get_build_targets(self.tests_targets_all)

    @property
    def tests_targets(self) -> Set[str]:
        """
        Return valid targets/chroots to use in testing farm.
        If there are build_targets_override or tests_targets_override defined,
        configured targets ∩ (mapped build_targets_override ∪ tests_targets_override)
        will be returned.

        Example:
        test job configuration:
          - job: tests
            trigger: pull_request
            metadata:
                targets:
                      epel-7-x86_64:
                        distros: [centos-7, rhel-7]
                      fedora-35-x86_64: {}

        helper.build_targets_override: {"epel-7-x86_64}
        helper.test_targets_override = None
        helper.tests_targets-> {"centos-7-x86_64", "rhel-7-x86_64"}

        (Used when submitting the tests and as a part of the commit status name.)
        """
        configured_targets = self.tests_targets_all_mapped

        targets_override = set()

        if self.build_targets_override:
            logger.debug(f"Build targets override: {self.build_targets_override}")
            for target in self.build_targets_override:
                targets_override.update(self.build_target2test_targets(target))

        if self.tests_targets_override:
            logger.debug(f"Test targets override: {self.tests_targets_override}")
            targets_override.update(self.tests_targets_override)

        return (
            configured_targets & targets_override
            if targets_override
            else configured_targets
        )

    def build_target2test_targets(self, build_target: str) -> Set[str]:
        """
        Return all test targets defined for the build target
        (from configuration or from default mapping).
        """
        raise NotImplementedError("Use subclass instead.")

    def test_target2build_target(self, test_target) -> str:
        """
        Return build target to be built for a given test target
        (from configuration or from default mapping).
        """
        raise NotImplementedError("Use subclass instead.")

    @property
    def test_check_names(self) -> List[str]:
        """
        List of full names of the commit statuses.

        e.g. ["testing-farm:fedora-rawhide-x86_64"]
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

        e.g. ["copr-build:fedora-rawhide-x86_64"]
        or ["production-build:f31", "production-build:f32"]
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
    def get_build_check_cls(
        cls, chroot: str = None, identifier: Optional[str] = None
    ) -> str:
        chroot_str = f":{chroot}" if chroot else ""
        optional_suffix = f":{identifier}" if identifier else ""
        return f"{cls.status_name_build}{chroot_str}{optional_suffix}"

    def get_build_check(self, chroot: str = None) -> str:
        return self.get_build_check_cls(chroot, identifier=self.job_config.identifier)

    @classmethod
    def get_test_check_cls(
        cls, chroot: str = None, identifier: Optional[str] = None
    ) -> str:
        chroot_str = f":{chroot}" if chroot else ""
        optional_suffix = f":{identifier}" if identifier else ""
        return f"{cls.status_name_test}{chroot_str}{optional_suffix}"

    def get_test_check(self, chroot: str = None) -> str:
        return self.get_test_check_cls(chroot, self.job_config.identifier)

    def create_srpm_if_needed(self) -> Optional[TaskResults]:
        """
        Create SRPM if is needed.

        Returns:
            Task results if job is cancelled because of merge conflicts, `None`
        otherwise.
        """
        if self._srpm_path or self._srpm_model:
            return None

        results = self._create_srpm()
        if results:
            # merge conflict occurred
            self.report_status_to_all(
                state=BaseCommitStatus.neutral,
                description="Merge conflicts present",
                url=get_srpm_build_info_url(self.srpm_model.id),
            )
        return results

    def _create_srpm(self):
        """
        Create SRPM.

        Returns:
            Task results if job is done because of merge conflicts, `None`
        otherwise.
        """
        # we want to get packit logs from the SRPM creation process
        # so we stuff them into a StringIO buffer
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        packit_logger = logging.getLogger("packit")
        packit_logger.setLevel(logging.DEBUG)
        packit_logger.addHandler(handler)
        formatter = PackitFormatter()
        handler.setFormatter(formatter)

        srpm_success = True
        exception: Optional[Exception] = None
        extra_logs: str = ""
        results: Optional[TaskResults] = None

        self._srpm_model, self.run_model = SRPMBuildModel.create_with_new_run(
            trigger_model=self.db_trigger, commit_sha=self.metadata.commit_sha
        )
        self._srpm_model.set_start_time(datetime.datetime.utcnow())

        try:
            self._srpm_path = Path(
                self.api.create_srpm(
                    srpm_dir=self.api.up.local_project.working_dir,
                    bump_version=self.job_config.trigger
                    != JobConfigTriggerType.release,
                    release_suffix=self.job_config.release_suffix,
                )
            )
        except SandcastleTimeoutReached as ex:
            exception = ex
            extra_logs = "\nYou have reached 10-minute timeout while creating SRPM.\n"
        except ApiException as ex:
            exception = ex
            # this is an internal error: let's not expose anything to public
            extra_logs = (
                "\nThere was a problem in the environment the packit-service is running in.\n"
                "Please hang tight, the help is coming."
            )
        except PackitMergeException as ex:
            exception = ex
            results = TaskResults(
                success=True,
                details={
                    "msg": "Merge conflicts were detected, cannot build SRPM.",
                    "exception": str(ex),
                },
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
            if not isinstance(exception, PackitMergeException):
                sentry_integration.send_to_sentry(exception)

            # this needs to be done AFTER we gather logs
            # so that extra logs are after actual logs
            srpm_logs += extra_logs
            if hasattr(exception, "output"):
                output = getattr(exception, "output", "")  # mypy
                srpm_logs += f"\nOutput of the command in the sandbox:\n{output}\n"

            srpm_logs += (
                f"\nMessage: {exception}\nException: {exception!r}\n{self.msg_retrigger}"
                "\nPlease join #packit on irc.libera.chat if you need help with the error above.\n"
            )
        pg_status = PG_BUILD_STATUS_SUCCESS if srpm_success else PG_BUILD_STATUS_FAILURE
        self._srpm_model.set_status(pg_status)

        self._srpm_model.set_logs(srpm_logs)
        self._srpm_model.set_end_time(datetime.datetime.utcnow())

        return results

    def report_status_to_all(
        self,
        description: str,
        state: BaseCommitStatus,
        url: str = "",
        markdown_content: str = None,
    ) -> None:
        self.report_status_to_build(
            description=description,
            state=state,
            url=url,
            markdown_content=markdown_content,
        )
        self.report_status_to_tests(
            description=description,
            state=state,
            url=url,
            markdown_content=markdown_content,
        )

    def report_status_to_build(
        self, description, state, url: str = "", markdown_content: str = None
    ) -> None:
        if self.job_build:
            self._report(
                description=description,
                state=state,
                url=url,
                check_names=self.build_check_names,
                markdown_content=markdown_content,
            )

    def report_status_to_tests(
        self, description, state, url: str = "", markdown_content: str = None
    ) -> None:
        if self.job_tests:
            self._report(
                description=description,
                state=state,
                url=url,
                check_names=self.test_check_names,
                markdown_content=markdown_content,
            )

    def report_status_to_build_for_chroot(
        self,
        description,
        state,
        url: str = "",
        chroot: str = "",
        markdown_content: str = None,
    ) -> None:
        if self.job_build and chroot in self.build_targets:
            cs = self.get_build_check(chroot)
            self._report(
                description=description,
                state=state,
                url=url,
                check_names=cs,
                markdown_content=markdown_content,
            )

    def report_status_to_test_for_chroot(
        self,
        description,
        state,
        url: str = "",
        chroot: str = "",
        markdown_content: str = None,
    ) -> None:
        if self.job_tests and chroot in self.build_targets_for_tests:
            test_targets = self.build_target2test_targets(chroot)
            for target in test_targets:
                self._report(
                    description=description,
                    state=state,
                    url=url,
                    check_names=self.get_test_check(target),
                    markdown_content=markdown_content,
                )

    def report_status_to_test_for_test_target(
        self,
        description,
        state,
        url: str = "",
        target: str = "",
        markdown_content: str = None,
    ) -> None:
        if self.job_tests and target in self.tests_targets:
            self._report(
                description=description,
                state=state,
                url=url,
                check_names=self.get_test_check(target),
                markdown_content=markdown_content,
            )

    def report_status_to_all_for_chroot(
        self,
        description,
        state,
        url: str = "",
        chroot: str = "",
        markdown_content: str = None,
    ):
        self.report_status_to_build_for_chroot(
            description=description,
            state=state,
            url=url,
            chroot=chroot,
            markdown_content=markdown_content,
        )
        self.report_status_to_test_for_chroot(
            description=description,
            state=state,
            url=url,
            chroot=chroot,
            markdown_content=markdown_content,
        )

    def run_build(
        self, target: Optional[str] = None
    ) -> Tuple[Optional[int], Optional[str]]:
        """
        Trigger the build and return id and web_url
        :param target: str, run for all if not set
        :return: task_id, task_url
        """
        raise NotImplementedError()
