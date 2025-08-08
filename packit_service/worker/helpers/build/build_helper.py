# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import copy
import datetime
import logging
import re
from abc import abstractmethod
from collections.abc import Iterable
from io import StringIO
from pathlib import Path
from typing import Any, Callable, Optional

from kubernetes.client.rest import ApiException
from ogr.abstract import GitProject
from packit.config import JobConfig, JobConfigTriggerType, JobType
from packit.config.aliases import DEFAULT_VERSION
from packit.config.package_config import PackageConfig
from packit.exceptions import PackitMergeException
from packit.utils import PackitFormatter
from sandcastle import SandcastleTimeoutReached

from packit_service import sentry_integration
from packit_service.config import ServiceConfig
from packit_service.constants import FAILURE_COMMENT_MESSAGE_VARIABLES
from packit_service.events.event_data import EventData
from packit_service.models import (
    BuildStatus,
    GitBranchModel,
    PipelineModel,
    ProjectEventModel,
    ProjectReleaseModel,
    SRPMBuildModel,
)
from packit_service.service.urls import get_srpm_build_info_url
from packit_service.worker.helpers.job_helper import BaseJobHelper
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.reporting import BaseCommitStatus, DuplicateCheckMode
from packit_service.worker.result import TaskResults

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
        db_project_event: ProjectEventModel,
        job_config: JobConfig,
        build_targets_override: Optional[set[tuple[str, str]]] = None,
        tests_targets_override: Optional[set[tuple[str, str]]] = None,
        pushgateway: Optional[Pushgateway] = None,
    ):
        super().__init__(
            service_config=service_config,
            package_config=package_config,
            project=project,
            metadata=metadata,
            db_project_event=db_project_event,
            job_config=job_config,
            pushgateway=pushgateway,
        )
        self.run_model: Optional[PipelineModel] = None
        self.build_targets_override: Optional[set[tuple[str, str]]] = build_targets_override
        self.tests_targets_override: Optional[set[tuple[str, str]]] = tests_targets_override
        self.pushgateway = pushgateway

        # lazy properties
        self._build_check_names: Optional[list[str]] = None
        self._srpm_model: Optional[SRPMBuildModel] = None
        self._srpm_path: Optional[Path] = None
        self._job_tests: Optional[JobConfig] = None
        self._job_build: Optional[JobConfig] = None
        self._job_tests_all: Optional[list[JobConfig]] = None

    @property
    def configured_build_targets(self) -> set[str]:
        """
        Return the configured targets for build job.

        1. Use targets defined for build job.
        2. Use "fedora-stable" alias if neither defined.
        """
        targets = set()
        if self.job_build:
            targets.update(self.job_build.targets)

        if (
            self.job_type_build == JobType.copr_build
            and (self.job_build and not self.job_build.targets)
            and self.is_custom_copr_project_defined()  # type: ignore
        ):
            copr_targets = self.get_configured_targets()  # type: ignore
            targets.update(copr_targets)

        return targets or {DEFAULT_VERSION}

    def is_job_config_trigger_matching(self, job_config: JobConfig) -> bool:
        """
        Check whether the job config matches the DB trigger type.
        In case the job config trigger is commit, check that the branch
        matches. In case the job config trigger is pull request, check
        if the branch is configured and if yes, check whether it matches
        the target branch of the pull request.
        """
        if (
            not self._db_project_object
            or self._db_project_object.job_config_trigger_type != job_config.trigger
        ):
            return False

        if job_config.trigger == JobConfigTriggerType.commit:
            configured_branch = job_config.branch or self.project.default_branch
            logger.info(
                f"Configured branch: {configured_branch}, branch from trigger: "
                f"{self._db_project_object.name}",  # type: ignore
            )
            return bool(re.match(configured_branch, self._db_project_object.name))  # type: ignore

        if job_config.trigger == JobConfigTriggerType.pull_request:
            configured_branch = job_config.branch
            if not configured_branch:
                return True
            target_branch = self.pull_request_object.target_branch
            logger.info(
                f"Configured branch: {configured_branch}, PR target branch: {target_branch}",
            )
            return bool(re.match(configured_branch, target_branch))

        return True

    @property
    def job_build(self) -> Optional[JobConfig]:
        """
        Check if there is JobConfig for builds defined
        :return: JobConfig or None
        """
        if not self.job_type_build:
            return None
        if not self._job_build:
            for job in [self.job_config, *self.package_config.jobs]:
                if job.type == self.job_type_build and self.is_job_config_trigger_matching(job):
                    self._job_build = job
                    break
        return self._job_build

    @property
    def job_build_or_job_config(self):
        return self.job_build or self.job_config

    @property
    def job_tests_all(self) -> list[JobConfig]:
        """
        Get all JobConfig for tests defined for the given trigger
        :return: List of JobConfig or None
        """
        if not self.job_type_test:
            return []

        if not self._job_tests_all:
            self._job_tests_all = [
                job
                for job in self.package_config.jobs
                if (job.type == self.job_type_test and self.is_job_config_trigger_matching(job))
            ]

        return self._job_tests_all

    @property
    def build_targets_all(self) -> set[str]:
        """
        Return all valid build targets/chroots from config.
        """
        raise NotImplementedError("Use subclass instead.")

    @property
    def build_targets(self) -> set[str]:
        """
        Return valid targets/chroots to build.

        (Used when submitting the koji/copr build and as a part of the commit status name.)
        """
        if self.build_targets_override:
            logger.debug(f"Build targets override: {self.build_targets_override}")
            return self.build_targets_all & {target for target, _ in self.build_targets_override}

        return self.build_targets_all

    def configured_targets_for_tests_job(self, test_job_config: JobConfig) -> set[str]:
        """
        Return the configured targets for the particular test job.
        Has to be a sub-set of the `configured_build_targets`.

        If not defined:
        1. use the `configured_build_targets` if the build job is configured
        2. use "fedora-stable" alias otherwise
        """
        if not self.is_job_config_trigger_matching(test_job_config):
            return set()

        if not test_job_config.targets and self.job_build and not test_job_config.skip_build:
            return self.configured_build_targets

        return test_job_config.targets or {DEFAULT_VERSION}

    def build_targets_for_test_job_all(self, test_job_config: JobConfig) -> set[str]:
        """
        Return valid targets/chroots to build in needed to run the particular test job.
        """
        raise NotImplementedError("Use subclass instead.")

    def tests_targets_for_test_job_all(self, test_job_config: JobConfig) -> set[str]:
        """
        Return valid test targets (mapped) to test in for the particular test job.
        """
        targets = set()
        for chroot in self.build_targets_for_test_job_all(test_job_config):
            targets.update(
                self.build_target2test_targets_for_test_job(chroot, test_job_config),
            )
        return targets

    def build_targets_for_test_job(self, test_job_config: JobConfig) -> set[str]:
        """
        Return valid targets/chroots to build in needed to run the particular test job.

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
        helper.tests_targets_override = {"centos-7-x86_64"}

        helper.build_targets_for_test_job(test_job_config)-> {"epel-7-x86_64"}
        """
        configured_targets = self.build_targets_for_test_job_all(test_job_config)
        targets_override = set()

        if self.build_targets_override:
            logger.debug(f"Build targets override: {self.build_targets_override}")
            targets_override.update(
                [
                    target
                    for (target, identifier) in self.build_targets_override
                    if identifier == test_job_config.identifier
                ]
            )

        if self.tests_targets_override:
            logger.debug(f"Test targets override: {self.tests_targets_override}")
            targets_override.update(
                self.test_target2build_target_for_test_job(t, test_job_config)
                for t in [
                    target
                    for (target, identifier) in self.tests_targets_override
                    if identifier == test_job_config.identifier
                ]
            )

        return configured_targets & targets_override if targets_override else configured_targets

    def tests_targets_for_test_job(self, test_job_config: JobConfig) -> set[str]:
        """
        Return valid test targets (mapped) to test in for the particular test job.
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
        helper.tests_targets_override = {"centos-7-x86_64"}

        helper.build_targets_for_test_job(test_job_config)-> {"centos-7-x86_64"}
        """
        configured_targets = self.tests_targets_for_test_job_all(test_job_config)

        targets_override = set()

        if self.build_targets_override:
            logger.debug(f"Build targets override: {self.build_targets_override}")
            for target, identifier in self.build_targets_override:
                if identifier == test_job_config.identifier:
                    targets_override.update(
                        self.build_target2test_targets_for_test_job(target, test_job_config),
                    )

        if self.tests_targets_override:
            logger.debug(f"Test targets override: {self.tests_targets_override}")
            targets_override.update(
                [
                    target
                    for target, identifier in self.tests_targets_override
                    if identifier == test_job_config.identifier
                ]
            )

        return (
            configured_targets & targets_override
            if self.build_targets_override or self.tests_targets_override
            else configured_targets
        )

    def build_target2test_targets_for_test_job(
        self,
        build_target: str,
        test_job_config: JobConfig,
    ) -> set[str]:
        """
        Return all test targets defined for the build target
        (from configuration or from default mapping).
        """
        raise NotImplementedError("Use subclass instead.")

    def test_target2build_target_for_test_job(
        self,
        test_target: str,
        test_job_config: JobConfig,
    ) -> str:
        """
        Return build target to be built for a given test target
        (from configuration or from default mapping).
        """
        raise NotImplementedError("Use subclass instead.")

    @property
    def build_check_names(self) -> list[str]:
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
    def get_check_cls(
        cls,
        job_name: Optional[str] = None,
        chroot: Optional[str] = None,
        project_event_identifier: Optional[str] = None,
        identifier: Optional[str] = None,
        package: Optional[str] = None,
        template: Optional[str] = None,
    ):
        if project_event_identifier:
            project_event_identifier = project_event_identifier.replace(":", "-")

        try:
            if template is not None:
                return template.format(
                    job_name=job_name,
                    chroot=chroot,
                    event=project_event_identifier,
                    identifier=identifier,
                    package=package,
                )
        except Exception as e:
            logger.warning(
                "Failed to use the template for status check, falling back to default: %s",
                e,
            )

        chroot_str = f":{chroot}" if chroot else ""
        # replace ':' in the project event identifier
        trigger_str = f":{project_event_identifier}" if project_event_identifier else ""
        optional_suffix = f":{identifier}" if identifier else ""
        return f"{job_name}{trigger_str}{chroot_str}{optional_suffix}"

    @classmethod
    def get_build_check_cls(
        cls,
        chroot: Optional[str] = None,
        project_event_identifier: Optional[str] = None,
        identifier: Optional[str] = None,
        package: Optional[str] = None,
        template: Optional[str] = None,
    ):
        return cls.get_check_cls(
            cls.status_name_build,
            chroot,
            project_event_identifier,
            identifier,
            package=package,
            template=template,
        )

    @classmethod
    def get_test_check_cls(
        cls,
        chroot: Optional[str] = None,
        project_event_identifier: Optional[str] = None,
        identifier: Optional[str] = None,
        package: Optional[str] = None,
        template: Optional[str] = None,
    ):
        return cls.get_check_cls(
            cls.status_name_test,
            chroot,
            project_event_identifier,
            identifier,
            package=package,
            template=template,
        )

    @property
    def project_event_identifier_for_status(self):
        # for commit and release triggers, we add the identifier to
        # the status name (branch name in case of commit project event,
        # tag name in case of release project event)
        identifier = None

        if isinstance(self._db_project_object, ProjectReleaseModel):
            identifier = self._db_project_object.tag_name
        elif isinstance(self._db_project_object, GitBranchModel):
            identifier = self._db_project_object.name

        return identifier

    def get_build_check(self, chroot: Optional[str] = None) -> str:
        return self.get_build_check_cls(
            chroot,
            self.project_event_identifier_for_status,
            self.job_build_or_job_config.identifier,
            package=self.get_package_name(),
            template=self.job_build_or_job_config.status_name_template,
        )

    def test_check_names_for_test_job(self, test_job_config: JobConfig) -> list[str]:
        """
        List of full names of the commit statuses for a particular test job.

        e.g. ["testing-farm:fedora-rawhide-x86_64"]
        """
        return [
            self.get_test_check_cls(
                target,
                self.project_event_identifier_for_status,
                test_job_config.identifier,
                package=self.get_package_name(),
                template=test_job_config.status_name_template,
            )
            for target in self.tests_targets_for_test_job(test_job_config)
        ]

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
        # We want to get packit logs from the SRPM creation process,
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
            project_event_model=self.db_project_event,
            package_name=self.get_package_name(),
        )
        self._srpm_model.set_start_time(datetime.datetime.utcnow())

        if (
            self.job_config.release_suffix == ""  # TODO remove eventually
            or self.job_config.trigger == JobConfigTriggerType.release
        ):
            update_release = False  # do not modify version/release
        else:
            update_release = None  # take the value from config (defaults to True)

        # use correct git ref to identify most recent tag
        if self.job_config.trigger == JobConfigTriggerType.pull_request:
            merged_ref = self.pull_request_object.target_branch
        elif self.job_config.trigger == JobConfigTriggerType.commit:
            merged_ref = self._db_project_object.commit_sha
        elif self.job_config.trigger == JobConfigTriggerType.release:
            merged_ref = self._db_project_object.tag_name
        else:
            logger.warning(
                f"Unable to determine merged ref for {self.job_config.trigger}",
            )
            merged_ref = None

        try:
            self._srpm_path = Path(
                self.api.create_srpm(
                    srpm_dir=self.api.up.local_project.working_dir,
                    update_release=update_release,
                    release_suffix=self.job_config.release_suffix,
                    merged_ref=merged_ref,
                ),
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
        pg_status = BuildStatus.success if srpm_success else BuildStatus.failure
        self._srpm_model.set_status(pg_status)

        self._srpm_model.set_logs(srpm_logs)
        self._srpm_model.set_end_time(datetime.datetime.utcnow())

        return results

    def report_status_to_all(
        self,
        description: str,
        state: BaseCommitStatus,
        url: str = "",
        markdown_content: Optional[str] = None,
        links_to_external_services: Optional[dict[str, str]] = None,
        update_feedback_time: Optional[Callable] = None,
    ) -> None:
        self.report_status_to_build(
            description=description,
            state=state,
            url=url,
            markdown_content=markdown_content,
            links_to_external_services=links_to_external_services,
            update_feedback_time=update_feedback_time,
        )
        self.report_status_to_all_test_jobs(
            description=description,
            state=state,
            url=url,
            markdown_content=markdown_content,
            links_to_external_services=links_to_external_services,
            update_feedback_time=update_feedback_time,
        )

    def report_status_to_build(
        self,
        description,
        state,
        url: str = "",
        markdown_content: Optional[str] = None,
        links_to_external_services: Optional[dict[str, str]] = None,
        update_feedback_time: Optional[Callable] = None,
    ) -> None:
        if self.job_build:
            self._report(
                description=description,
                state=state,
                url=url,
                check_names=self.build_check_names,
                markdown_content=markdown_content,
                links_to_external_services=links_to_external_services,
                update_feedback_time=update_feedback_time,
            )

    def report_status_to_all_test_jobs(
        self,
        description,
        state,
        url: str = "",
        markdown_content: Optional[str] = None,
        links_to_external_services: Optional[dict[str, str]] = None,
        update_feedback_time: Optional[Callable] = None,
    ) -> None:
        for test_job in self.job_tests_all:
            if test_job.skip_build or test_job.manual_trigger:
                continue
            check_names = self.test_check_names_for_test_job(test_job)
            self._report(
                description=description,
                state=state,
                url=url,
                check_names=check_names,
                markdown_content=markdown_content,
                links_to_external_services=links_to_external_services,
                update_feedback_time=update_feedback_time,
            )

    def report_status_to_build_for_chroot(
        self,
        description,
        state,
        url: str = "",
        chroot: str = "",
        markdown_content: Optional[str] = None,
        links_to_external_services: Optional[dict[str, str]] = None,
        update_feedback_time: Optional[Callable] = None,
    ) -> None:
        if self.job_build and chroot in self.build_targets:
            cs = self.get_build_check(chroot)
            self._report(
                description=description,
                state=state,
                url=url,
                check_names=cs,
                markdown_content=markdown_content,
                links_to_external_services=links_to_external_services,
                update_feedback_time=update_feedback_time,
            )

    def report_status_to_all_test_jobs_for_chroot(
        self,
        description,
        state,
        url: str = "",
        chroot: str = "",
        markdown_content: Optional[str] = None,
        links_to_external_services: Optional[dict[str, str]] = None,
        update_feedback_time: Optional[Callable] = None,
    ) -> None:
        for test_job in self.job_tests_all:
            if (
                not test_job.skip_build
                and not test_job.manual_trigger
                and chroot in self.build_targets_for_test_job(test_job)
            ):
                test_targets = self.build_target2test_targets_for_test_job(
                    chroot,
                    test_job,
                )
                for target in test_targets:
                    self._report(
                        description=description,
                        state=state,
                        url=url,
                        check_names=self.get_test_check_cls(
                            target,
                            self.project_event_identifier_for_status,
                            test_job.identifier,
                        ),
                        markdown_content=markdown_content,
                        links_to_external_services=links_to_external_services,
                        update_feedback_time=update_feedback_time,
                    )

    def report_status_to_all_for_chroot(
        self,
        description,
        state,
        url: str = "",
        chroot: str = "",
        markdown_content: Optional[str] = None,
        links_to_external_services: Optional[dict[str, str]] = None,
        update_feedback_time: Optional[Callable] = None,
    ) -> None:
        self.report_status_to_build_for_chroot(
            description=description,
            state=state,
            url=url,
            chroot=chroot,
            markdown_content=markdown_content,
            update_feedback_time=update_feedback_time,
        )
        self.report_status_to_all_test_jobs_for_chroot(
            description=description,
            state=state,
            url=url,
            chroot=chroot,
            markdown_content=markdown_content,
            links_to_external_services=links_to_external_services,
            update_feedback_time=update_feedback_time,
        )

    def run_build(
        self,
        target: Optional[str] = None,
    ) -> tuple[Optional[int], Optional[str]]:
        """
        Trigger the build and return id and web_url
        :param target: str, run for all if not set
        :return: task_id, task_url
        """
        raise NotImplementedError()

    def report_status_to_configured_job(
        self,
        description: str,
        state: BaseCommitStatus,
        url: str = "",
        markdown_content: Optional[str] = None,
        links_to_external_services: Optional[dict[str, str]] = None,
        update_feedback_time: Optional[Callable] = None,
    ):
        self.report_status_to_build(
            description=description,
            state=state,
            url=url,
            markdown_content=markdown_content,
            links_to_external_services=links_to_external_services,
            update_feedback_time=update_feedback_time,
        )

    def notify_about_failure_if_configured(self, **kwargs):
        """
        If there is a failure_comment_message configured for the job,
        post a comment and include the configured message. Do not post
        the comment if the last comment from the Packit user is identical.
        """
        if not (configured_message := self.job_config.notifications.failure_comment.message):
            return

        all_kwargs = copy.copy(FAILURE_COMMENT_MESSAGE_VARIABLES)
        all_kwargs["commit_sha"] = self.db_project_event.commit_sha
        all_kwargs.update(kwargs)
        formatted_message = configured_message.format(**all_kwargs)

        self.status_reporter.comment(
            formatted_message,
            duplicate_check=DuplicateCheckMode.check_last_comment,
        )

    @abstractmethod
    def get_running_jobs(self) -> Iterable[Any]:
        """Yields the jobs that are already running for the same event and would
        have been triggered by this helper.

        Type of the items of the iterable depends on the representation of the
        external service:
        - Copr - build ID as `int`
        - Testing Farm - request ID as `string` (UUID)

        Returns:
            Iterable over the type that can be used to cancel the jobs.
        """
