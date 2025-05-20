# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
import re
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Optional, Union

from copr.v3 import CoprAuthException, CoprRequestException
from copr.v3.exceptions import CoprTimeoutException
from ogr.abstract import GitProject
from ogr.exceptions import GitForgeInternalError, OgrNetworkError
from ogr.parsing import parse_git_repo
from ogr.services.github import GithubProject
from ogr.services.gitlab import GitlabProject
from packit.config import JobConfig, JobConfigTriggerType, JobType
from packit.config.aliases import get_aliases
from packit.config.common_package_config import Deployment, MockBootstrapSetup
from packit.config.package_config import PackageConfig
from packit.exceptions import (
    PackitCoprException,
    PackitCoprProjectException,
    PackitCoprSettingsException,
)
from packit.utils.source_script import create_source_script

from packit_service import sentry_integration
from packit_service.celerizer import celery_app
from packit_service.config import ServiceConfig
from packit_service.constants import (
    BASE_RETRY_INTERVAL_IN_MINUTES_FOR_OUTAGES,
    BASE_RETRY_INTERVAL_IN_SECONDS_FOR_INTERNAL_ERRORS,
    COPR_CHROOT_CHANGE_MSG,
    CUSTOM_COPR_PROJECT_NOT_ALLOWED_CONTENT,
    CUSTOM_COPR_PROJECT_NOT_ALLOWED_STATUS,
    DASHBOARD_JOBS_TESTING_FARM_PATH,
    DEFAULT_MAPPING_INTERNAL_TF,
    DEFAULT_MAPPING_TF,
    DEFAULT_RETRY_LIMIT_OUTAGE,
    GIT_FORGE_PROJECT_NOT_ALLOWED_TO_BUILD_IN_COPR,
    MISSING_PERMISSIONS_TO_BUILD_IN_COPR,
    MSG_RETRIGGER,
    NOT_ALLOWED_TO_BUILD_IN_COPR,
)
from packit_service.events.event_data import EventData
from packit_service.models import (
    BuildStatus,
    CoprBuildGroupModel,
    CoprBuildTargetModel,
    ProjectEventModel,
    ProjectEventModelType,
    SRPMBuildModel,
    TFTTestRunTargetModel,
)
from packit_service.service.urls import (
    get_copr_build_info_url,
    get_srpm_build_info_url,
)
from packit_service.utils import elapsed_seconds
from packit_service.worker.celery_task import CeleryTask
from packit_service.worker.helpers.build.build_helper import BaseBuildJobHelper
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.reporting import BaseCommitStatus
from packit_service.worker.result import TaskResults

logger = logging.getLogger(__name__)


class CoprBuildJobHelper(BaseBuildJobHelper):
    job_type_build = JobType.copr_build
    job_type_test = JobType.tests
    status_name_build: str = "rpm-build"
    status_name_test: str = "testing-farm"

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
        celery_task: Optional[CeleryTask] = None,
        copr_build_group_id: Optional[int] = None,
    ):
        super().__init__(
            service_config=service_config,
            package_config=package_config,
            project=project,
            metadata=metadata,
            db_project_event=db_project_event,
            job_config=job_config,
            build_targets_override=build_targets_override,
            tests_targets_override=tests_targets_override,
            pushgateway=pushgateway,
        )
        self.celery_task = celery_task
        self._copr_build_group_id = copr_build_group_id

    @property
    def msg_retrigger(self) -> str:
        return MSG_RETRIGGER.format(
            job="build",
            command="copr-build" if self.job_build else "build",
            place="pull request",
            packit_comment_command_prefix=self.service_config.comment_command_prefix,
        )

    @property
    def default_project_name(self) -> str:
        """
        Project name for copr.

        * use hostname prefix for non-github service
        * replace slash in namespace with dash
        """

        service_hostname = parse_git_repo(self.project.service.instance_url).hostname
        service_prefix = "" if isinstance(self.project, GithubProject) else f"{service_hostname}-"

        namespace = self.project.namespace.replace("/", "-")
        # We want to share project between all releases.
        # More details: https://github.com/packit/packit-service/issues/1044
        ref_identifier = (
            "releases"
            if self._db_project_object.project_event_model_type == ProjectEventModelType.release
            else self.metadata.identifier
        )

        # if we call default_project_name when running TF, we want to get the default name
        # for the build job
        configured_identifier = (
            f"-{self.job_build_or_job_config.identifier}"
            if self.job_build_or_job_config.identifier
            else ""
        )

        # do not add the package identifier if handling monorepo job
        if self.job_config.package:
            configured_identifier = ""

        copr_project_name = (
            f"{service_prefix}{namespace}-{self.project.repo}-{ref_identifier}"
            f"{configured_identifier}"
        )

        return self.normalise_copr_project_name(copr_project_name)

    @staticmethod
    def normalise_copr_project_name(copr_project_name: str) -> str:
        """
        Transform not allowed characters for Copr project name to '-'
        (name must contain only letters, digits, underscores, dashes and dots).
        """
        return re.sub(r"[^\w.-]", "-", copr_project_name)

    @property
    def job_project(self) -> Optional[str]:
        """
        The project definition from the config file.
        """
        if self.job_build and self.job_build.project:
            return self.job_build.project

        for test_job in self.job_tests_all:
            # return the project from first test job where present
            if test_job and test_job.project:
                return test_job.project

        return self.default_project_name

    def job_project_for_commit_job_config(self, job_config) -> Optional[str]:
        """
        Get the Copr project name for the specified job config with commit trigger.
        """
        if job_config.project:
            return job_config.project

        service_hostname = parse_git_repo(self.project.service.instance_url).hostname
        service_prefix = "" if isinstance(self.project, GithubProject) else f"{service_hostname}-"

        namespace = self.project.namespace.replace("/", "-")

        ref_identifier = job_config.branch or self.project.default_branch

        configured_identifier = (
            f"-{job_config.identifier}"
            if job_config.identifier and not self.job_config.package
            else ""
        )

        copr_project_name = (
            f"{service_prefix}{namespace}-{self.project.repo}-{ref_identifier}"
            f"{configured_identifier}"
        )

        return self.normalise_copr_project_name(copr_project_name)

    @property
    def job_owner(self) -> Optional[str]:
        """
        Owner used for the copr build -- search the config or use the copr's config.
        """
        if self.job_build and self.job_build.owner:
            return self.job_build.owner

        for test_job in self.job_tests_all:
            # return the owner from first test job where present
            if test_job and test_job.owner:
                return test_job.owner
        return self.api.copr_helper.copr_client.config.get("username")

    def job_owner_for_job_config(self, job_config: JobConfig) -> Optional[str]:
        """
        Owner used for the copr build for the specified config
         -- search the config or use the copr's config.
        """
        if job_config.owner:
            return job_config.owner

        return self.api.copr_helper.copr_client.config.get("username")

    @property
    def preserve_project(self) -> Optional[bool]:
        """
        If the project will be preserved or can be removed after 60 days.
        """
        return self.job_build.preserve_project if self.job_build else None

    @property
    def list_on_homepage(self) -> Optional[bool]:
        """
        If the project will be shown on the copr home page.
        """
        return self.job_build.list_on_homepage if self.job_build else None

    @property
    def module_hotfixes(self) -> Optional[bool]:
        """
        If the project will generate repo files with module_hotfixes=1.
        """
        return self.job_build.module_hotfixes if self.job_build else None

    @property
    def additional_repos(self) -> Optional[list[str]]:
        """
        Additional repos that will be enabled for copr build.
        """
        return self.job_build.additional_repos if self.job_build else None

    @property
    def bootstrap(self) -> Optional[MockBootstrapSetup]:
        """
        mock bootstrap feature setup.
        """
        return self.job_build.bootstrap if self.job_build else None

    @property
    def build_targets_all(self) -> set[str]:
        """
        Return all valid Copr build targets/chroots from config.
        """
        return self.api.copr_helper.get_valid_build_targets(
            *self.configured_build_targets,
            default=None,
        )

    def build_targets_for_test_job_all(self, job: JobConfig):
        return self.api.copr_helper.get_valid_build_targets(
            *self.configured_targets_for_tests_job(job),
            default=None,
        )

    @property
    def configured_copr_project(self):
        return f"{self.job_owner}/{self.job_project}"

    @property
    def forge_project(self):
        return f"{self.project.service.hostname}/{self.project.namespace}/{self.project.repo}"

    @property
    def copr_settings_url(self):
        return self.api.copr_helper.get_copr_settings_url(
            self.job_owner,
            self.job_project,
        )

    def build_target2test_targets_for_test_job(
        self,
        build_target: str,
        test_job_config: JobConfig,
    ) -> set[str]:
        """
        Return all test targets defined for the build target
        (from configuration or from default mapping).

        Examples:
        test job configuration:
          - job: tests
            trigger: pull_request
            metadata:
                targets:
                      epel-7-x86_64:
                        distros: [centos-7, rhel-7]

        helper.build_target2test_targets_for_test_job("epel-7-x86_64") ->
        {"centos-7-x86_64", "rhel-7-x86_64"}

        test job configuration:
          - job: tests
            trigger: pull_request
            metadata:
                targets:
                      fedora-35-x86_64

        helper.build_target2test_targets_for_test_job("fedora-35-x86_64") -> {"fedora-35-x86_64"}
        """
        if not test_job_config or build_target not in self.build_targets_for_test_job_all(
            test_job_config
        ):
            return set()

        distro, arch = build_target.rsplit("-", 1)
        configured_distros = test_job_config.targets_dict.get(build_target, {}).get(
            "distros",
        )

        if configured_distros:
            distro_arch_list = [(distro, arch) for distro in configured_distros]
        else:
            mapping = (
                DEFAULT_MAPPING_INTERNAL_TF
                if test_job_config.use_internal_tf
                else DEFAULT_MAPPING_TF
            )
            distro = mapping.get(distro, distro)
            distro_arch_list = [(distro, arch)]

        return {f"{distro}-{arch}" for (distro, arch) in distro_arch_list}

    def test_target2build_target_for_test_job(
        self,
        test_target: str,
        test_job_config: JobConfig,
    ) -> str:
        """
        Return build target to be built for a given test target
        (from configuration or from default mapping).
        """
        for target in self.build_targets_for_test_job_all(test_job_config):
            if test_target in self.build_target2test_targets_for_test_job(
                target,
                test_job_config,
            ):
                logger.debug(f"Build target corresponding to {test_target}: {target}")
                return target

        return test_target

    @property
    def available_chroots(self) -> set[str]:
        """
        Returns set of available COPR targets.
        """
        return {
            *filter(
                lambda chroot: not chroot.startswith("_"),
                self.api.copr_helper.get_copr_client().mock_chroot_proxy.get_list().keys(),
            ),
        }

    def is_custom_copr_project_defined(self) -> bool:
        return (
            self.job_owner != self.api.copr_helper.copr_client.config.get("username")
            or self.job_project != self.default_project_name
        )

    def is_forge_project_allowed_to_build_in_copr(self) -> bool:
        """Is this forge project allowed to build in COPR project?
        Ask to COPR server if the forge project has been granted permissions.

        Returns:
            bool: True if the forge project is allowed to build in COPR project.
        """
        copr_project = self.api.copr_helper.copr_client.project_proxy.get(
            self.job_owner,
            self.job_project,
        )
        allowed_projects = copr_project["packit_forge_projects_allowed"]
        allowed = self.forge_project in allowed_projects
        if not allowed:
            logger.warning(
                f"git-forge project {self.forge_project} "
                f"can't use {self.configured_copr_project} Copr project "
                f"(Only {allowed_projects} are allowed.)",
            )
        return allowed

    def check_if_custom_copr_can_be_used_and_report(self) -> bool:
        """
        Check if the git-forge project can build in the configured Copr project.

        The mapping is hardcoded for now in the service config.
        There will be a better integration in form of
        a new config field in Copr settings that Packit can use.

        :return: True if the matching is configured.
        """
        if self.is_forge_project_allowed_to_build_in_copr():
            return True

        self.report_status_to_build(
            description=CUSTOM_COPR_PROJECT_NOT_ALLOWED_STATUS.format(
                copr_project=self.configured_copr_project,
            ),
            state=BaseCommitStatus.neutral,
            markdown_content=CUSTOM_COPR_PROJECT_NOT_ALLOWED_CONTENT.format(
                copr_project=self.configured_copr_project,
                forge_project=self.forge_project,
                copr_settings_url=self.copr_settings_url,
            ),
        )
        return False

    def get_built_packages(self, build_id: int, chroot: str) -> list:
        return self.api.copr_helper.copr_client.build_chroot_proxy.get_built_packages(
            build_id,
            chroot,
        ).packages

    def get_build(self, build_id: int):
        return self.api.copr_helper.copr_client.build_proxy.get(build_id)

    def get_build_chroot(self, build_id: int, chroot: str):
        return self.api.copr_helper.copr_client.build_chroot_proxy.get(build_id, chroot)

    def monitor_not_submitted_copr_builds(self, number_of_builds: int, reason: str):
        """
        Measure the time it took to set the failed status in case of event (e.g. failed SRPM)
        that prevents Copr build to be submitted.
        """

        # NOTE: When there is no ‹task_accepted_time›, we skip the submission to
        # the metrics, since there is no delay between the submission and
        # failure. We could probably track those by a separate metric as
        # suggested by Maja in the PR.
        if self.metadata.task_accepted_time is None:
            logger.warning(
                "No task_accepted_time for failed Copr build with reason: %s",
                reason,
            )
            return

        time = elapsed_seconds(
            begin=self.metadata.task_accepted_time,
            end=datetime.now(timezone.utc),
        )
        for _ in range(number_of_builds):
            self.pushgateway.copr_build_not_submitted_time.labels(
                reason=reason,
            ).observe(time)

    def get_packit_copr(self) -> str:
        """
        Get a Copr repo with dependencies for custom SRPM method in Copr.

        Returns:
            Repo that contains appropriate version of Packit.
        """
        return "copr://packit/{project}".format(
            project=(
                "packit-stable"
                if self.service_config.deployment == Deployment.prod
                else "packit-dev"
            ),
        )

    def get_job_config_index(self) -> int:
        """
        Get index of the job config in the package config.
        (Index is being submitted to Copr via source script.)
        """
        return self.package_config.jobs.index(self.job_config)

    def report_running_build_and_test_on_build_submission(self, web_url: str):
        """
        Report the first pending status for build/test job considering the
        issue in GitLab where we cannot overwrite the pending status
        (https://github.com/packit/packit-service/issues/1914),
        therefore differentiate the description and URL provided there.
        Args:
            web_url: URL for the particular Copr build
        """
        if isinstance(self.project, GitlabProject):
            build_description = test_description = "Job is in progress..."
            url_for_build = web_url
            url_for_tests = f"{self.service_config.dashboard_url}{DASHBOARD_JOBS_TESTING_FARM_PATH}"
        else:
            build_description = "SRPM build in Copr was submitted..."
            test_description = "Waiting for RPMs to be built..."
            url_for_build = url_for_tests = get_srpm_build_info_url(self.srpm_model.id)

        self.report_status_to_build(
            description=build_description,
            state=BaseCommitStatus.running,
            url=url_for_build,
        )
        self.report_status_to_all_test_jobs(
            description=(
                build_description
                if self.job_config.sync_test_job_statuses_with_builds
                else test_description
            ),
            state=(
                BaseCommitStatus.running
                if self.job_config.sync_test_job_statuses_with_builds
                else BaseCommitStatus.pending
            ),
            url=url_for_tests,
        )

    def run_copr_build_from_source_script(self) -> TaskResults:
        """
        Run copr build using custom source method.
        """
        self._srpm_model, self.run_model = SRPMBuildModel.create_with_new_run(
            project_event_model=self.db_project_event,
            package_name=self.get_package_name(),
        )
        group = self._get_or_create_build_group()
        try:
            pr_id = self.metadata.pr_id

            # use correct git ref to identify most recent tag
            if self.job_config.trigger == JobConfigTriggerType.pull_request:
                merged_ref = self.pull_request_object.target_branch
            elif self.job_config.trigger == JobConfigTriggerType.commit:
                merged_ref = self.metadata.commit_sha
            elif self.job_config.trigger == JobConfigTriggerType.release:
                merged_ref = self.metadata.tag_name
            else:
                logger.warning(
                    f"Unable to determine merged ref for {self.job_config.trigger}",
                )
                merged_ref = None

            script = create_source_script(
                url=self.metadata.project_url,
                ref=self.metadata.git_ref,
                pr_id=str(pr_id) if pr_id else None,
                merge_pr=self.package_config.merge_pr_in_ci,
                target_branch=(
                    self.pull_request_object.target_branch if self.pull_request_object else None
                ),
                job_config_index=self.get_job_config_index(),
                update_release=self.job_config.trigger != JobConfigTriggerType.release,
                release_suffix=self.job_config.release_suffix,
                package=self.job_config.package,
                merged_ref=merged_ref,
            )
            build_id, web_url = self.submit_copr_build(script=script)
        except Exception as ex:
            return self.handle_build_submit_error(group, ex)
        else:
            self._srpm_model.set_copr_build_id(str(build_id))
            self._srpm_model.set_copr_web_url(web_url)

        self.report_running_build_and_test_on_build_submission(web_url)
        self.handle_rpm_build_start(group, build_id, web_url)

        return TaskResults(success=True, details={})

    def _get_or_create_build_group(self) -> CoprBuildGroupModel:
        if self._copr_build_group_id is not None:
            group = CoprBuildGroupModel.get_by_id(self._copr_build_group_id)
            # Update the status, we are retrying
            for target in group.grouped_targets:
                target.set_status(BuildStatus.waiting_for_srpm)
            return group

        group = CoprBuildGroupModel.create(self.run_model)
        unprocessed_chroots = []
        for chroot in self.build_targets:
            if chroot not in self.available_chroots:
                self.report_status_to_all_for_chroot(
                    state=BaseCommitStatus.error,
                    description=f"Not supported target: {chroot}",
                    url=get_srpm_build_info_url(self.srpm_model.id),
                    chroot=chroot,
                )
                self.monitor_not_submitted_copr_builds(1, "not_supported_target")
                unprocessed_chroots.append(chroot)
                continue

            CoprBuildTargetModel.create(
                build_id=None,
                project_name=self.job_project,
                owner=self.job_owner,
                web_url=None,
                target=chroot,
                status=BuildStatus.waiting_for_srpm,
                copr_build_group=group,
                task_accepted_time=self.metadata.task_accepted_time,
                identifier=self.job_config.identifier,
            )

        if unprocessed_chroots:
            unprocessed = "\n".join(sorted(unprocessed_chroots))
            available = "\n".join(sorted(self.available_chroots))
            self.status_reporter.comment(
                body="There are build targets that are not supported by COPR.\n"
                "<details>\n<summary>Unprocessed build targets</summary>\n\n"
                f"```\n{unprocessed}\n```\n</details>\n"
                "<details>\n<summary>Available build targets</summary>\n\n"
                f"```\n{available}\n```\n</details>",
            )

        return group

    def get_latest_fedora_stable_chroot(self) -> str:
        """
        Get the latest stable Fedora chroot.

        This is used as a chroot where the Copr source script will be run.
        """
        latest_fedora_stable_chroot = get_aliases().get("fedora-stable")[-1]
        [build_target] = self.api.copr_helper.get_valid_build_targets(
            latest_fedora_stable_chroot.namever
        )
        return build_target

    def submit_copr_build(self, script: Optional[str] = None) -> tuple[int, str]:
        """
        Create the project in Copr if not exists and submit a new build using
        source script method
        Return:
            tuple of build ID and web url
        """
        owner = self.create_or_update_copr_project()
        try:
            buildopts = (
                {
                    "packit_forge_project": self.forge_project,
                }
                if self.is_custom_copr_project_defined()
                else {}
            )
            buildopts.update(
                {
                    "chroots": list(self.build_targets),
                    "enable_net": self.job_config.enable_net,
                },
            )

            if script:
                build = self.api.copr_helper.copr_client.build_proxy.create_from_custom(
                    ownername=owner,
                    projectname=self.job_project,
                    script=script,
                    # use the latest stable chroot
                    script_repos=self.get_packit_copr(),
                    script_chroot=self.get_latest_fedora_stable_chroot(),
                    script_builddeps=["packit"] + (self.job_config.srpm_build_deps or []),
                    buildopts=buildopts,
                )
            else:
                build = self.api.copr_helper.copr_client.build_proxy.create_from_file(
                    ownername=owner,
                    projectname=self.job_project,
                    path=self.srpm_path,
                    buildopts=buildopts,
                )

        except (CoprRequestException, CoprAuthException) as ex:
            if MISSING_PERMISSIONS_TO_BUILD_IN_COPR in str(
                ex,
            ) or NOT_ALLOWED_TO_BUILD_IN_COPR in str(ex):
                self.api.copr_helper.copr_client.project_proxy.request_permissions(
                    ownername=owner,
                    projectname=self.job_project,
                    permissions={"builder": True},
                )

                # notify user, PR if exists, commit comment otherwise
                permissions_url = self.api.copr_helper.get_copr_settings_url(
                    owner,
                    self.job_project,
                    section="permissions",
                )

                self.status_reporter.comment(
                    body="We have requested the `builder` permissions "
                    f"for the {owner}/{self.job_project} Copr project.\n"
                    "\n"
                    "Please confirm the request on the "
                    f"[{owner}/{self.job_project} Copr project permissions page]"
                    f"({permissions_url})"
                    " and retrigger the build by clicking on a `Re-run` button"
                    " or with a `/packit build` pull-request comment "
                    "(for builds in pull-requests).",
                )
            elif GIT_FORGE_PROJECT_NOT_ALLOWED_TO_BUILD_IN_COPR in str(ex):
                markdown_content = CUSTOM_COPR_PROJECT_NOT_ALLOWED_CONTENT.format(
                    copr_project=self.configured_copr_project,
                    forge_project=self.forge_project,
                    copr_settings_url=self.copr_settings_url,
                )
                self.status_reporter.comment(body=markdown_content)

            raise ex

        return build.id, self.api.copr_helper.copr_web_build_url(build)

    def handle_build_submit_error(
        self,
        group: CoprBuildGroupModel,
        ex: Exception,
    ) -> TaskResults:
        """
        Handle errors when submitting Copr build.

        Args:
            group: The copr build group currently in use.
            ex: The exception that caused the build submit error.

        Returns:
            result of the task saying whether the task was retried
        """
        possible_copr_outage_exc = (
            isinstance(ex, CoprRequestException) and "Unable to connect" in str(ex)
        ) or isinstance(ex, CoprTimeoutException)
        forge_outage_exc = isinstance(ex, OgrNetworkError)
        forge_internal_error = isinstance(ex, GitForgeInternalError)

        if not self.celery_task.is_last_try() and (
            possible_copr_outage_exc or forge_outage_exc or forge_internal_error
        ):
            what_failed = "Copr" if possible_copr_outage_exc else "Git forge"
            max_retries = None
            if forge_internal_error:
                # Internal error is delayed in seconds
                delay = (
                    BASE_RETRY_INTERVAL_IN_SECONDS_FOR_INTERNAL_ERRORS * 2**self.celery_task.retries
                )
                retry_in = f"{delay} seconds"
            else:
                # Outages are delayed in minutes
                interval = BASE_RETRY_INTERVAL_IN_MINUTES_FOR_OUTAGES * 2**self.celery_task.retries
                retry_in = f"{interval} {'minute' if interval == 1 else 'minutes'}"
                delay = 60 * interval
                max_retries = DEFAULT_RETRY_LIMIT_OUTAGE

            report_status = (
                self.report_status_to_all
                if self.job_config.sync_test_job_statuses_with_builds
                else self.report_status_to_build
            )
            report_status(
                state=BaseCommitStatus.pending,
                description=f"Submit of the build failed due to a {what_failed} error, the task "
                f"will be retried in {retry_in}.",
            )
            # Set status
            for chroot in group.grouped_targets:
                chroot.set_status(BuildStatus.retry)
            kargs = self.celery_task.task.request.kwargs.copy()
            kargs["copr_build_group_id"] = group.id
            self.celery_task.retry(
                delay=delay,
                ex=ex,
                max_retries=max_retries,
            )
            return TaskResults(
                success=True,
                details={
                    "msg": f"There was a {what_failed} error: {ex}. Task will be retried.",
                },
            )
        # Set DB statuses
        self._srpm_model.set_status(BuildStatus.error)
        for chroot in group.grouped_targets:
            chroot.set_status(BuildStatus.error)
        sentry_integration.send_to_sentry(ex)
        # TODO: Where can we show more info about failure?
        # TODO: Retry
        self.report_status_to_all(
            state=BaseCommitStatus.error,
            description=f"Submit of the build failed: {ex}",
        )
        self.monitor_not_submitted_copr_builds(
            len(self.build_targets),
            "submit_failure",
        )
        return TaskResults(
            success=False,
            details={
                "msg": "Submit of the Copr build failed.",
                "error": str(ex),
            },
        )

    def handle_rpm_build_start(
        self,
        group: CoprBuildGroupModel,
        build_id: int,
        web_url: str,
    ):
        """
        Update models for Copr build chroots and report start of RPM build
        if the SRPM is already built.
        """
        for target in group.grouped_targets:
            # Add missing data
            target.set_build_id(str(build_id))
            target.set_web_url(web_url)
            if target.status != BuildStatus.waiting_for_srpm:
                url = get_copr_build_info_url(id_=target.id)
                report_status_for_chroot = (
                    self.report_status_to_all_for_chroot
                    if self.job_config.sync_test_job_statuses_with_builds
                    else self.report_status_to_build_for_chroot
                )
                report_status_for_chroot(
                    state=BaseCommitStatus.running,
                    description="Starting RPM build...",
                    url=url,
                    chroot=target.target,
                )

        # release the hounds!
        celery_app.send_task(
            "task.babysit_copr_build",
            args=(build_id,),
            countdown=120,  # do the first check in 120s
        )

    def _visualize_chroots_diff(
        self,
        old_chroots: Iterable[str],
        new_chroots: Iterable[str],
    ):
        """
        Visualize in markdown via code diff the difference in 2 sets of chroots

        Args:
            old_chroots: previous set of chroots
            new_chroots: current set of chroots

        Returns:
            the diff of the two set of chroots rendered as markdown code diff
        """
        chroots_diff = "Diff of chroots:\n```diff\n"
        extra_chroots = set(old_chroots).difference(new_chroots)
        missing_chroots = set(new_chroots).difference(old_chroots)
        for e in extra_chroots:
            chroots_diff += f"-{e}\n"
        for m in missing_chroots:
            chroots_diff += f"+{m}\n"
        chroots_diff += "```\n"
        return chroots_diff

    def _report_copr_chroot_change_problem(
        self,
        owner: str,
        chroots_diff: str,
        table: str,
    ):
        """
        When we fail to update the list of chroots of a project,
        we need to inform user this has happened

        Args:
            owner: Copr project owner (namespace)
            chroots_diff: markdown code diff of Copr project chroots
            table: markdown table which shows the change we intend to do
        """
        msg = COPR_CHROOT_CHANGE_MSG.format(
            owner=owner,
            project=self.job_project,
            table=table,
            packit_comment_command_prefix=self.service_config.comment_command_prefix,
        )
        if chroots_diff:
            msg += chroots_diff
        self.status_reporter.comment(body=msg)

    def create_or_update_copr_project(self) -> str:
        """
        Create project in Copr.

        Returns:
            str owner
        """
        owner = self.job_owner or self.api.copr_helper.configured_owner
        if not owner:
            raise PackitCoprException(
                "Copr owner not set. Use Copr config file or `--owner` when calling packit CLI.",
            )

        try:
            overwrite_booleans = owner == self.service_config.fas_user
            self.api.copr_helper.create_or_update_copr_project(
                project=self.job_project,
                chroots=list(self.build_targets_all),
                owner=owner,
                description=None,
                instructions=None,
                list_on_homepage=self.list_on_homepage if overwrite_booleans else None,
                preserve_project=self.preserve_project if overwrite_booleans else None,
                additional_repos=self.additional_repos,
                bootstrap=self.bootstrap,
                request_admin_if_needed=True,
                targets_dict=self.job_config.targets_dict,
                module_hotfixes=self.module_hotfixes if overwrite_booleans else None,
            )
        except PackitCoprSettingsException as ex:
            # notify user first, PR if exists, commit comment otherwise
            table = "| field | old value | new value |\n| ----- | --------- | --------- |\n"
            for field, (old, new) in ex.fields_to_change.items():
                table += f"| {field} | {old} | {new} |\n"

            chroots_diff = ""
            if "chroots" in ex.fields_to_change:
                old_chroots, new_chroots = ex.fields_to_change["chroots"]
                chroots_diff = self._visualize_chroots_diff(old_chroots, new_chroots)

            if owner == self.service_config.fas_user:
                # the problem is on our side and user cannot fix it
                self._report_copr_chroot_change_problem(owner, chroots_diff, table)
                raise ex

            boolean_note = ""
            if "unlisted_on_hp" in ex.fields_to_change:
                boolean_note += (
                    "The `unlisted_on_hp` field is represented as `list_on_homepage`"
                    " in the packit config."
                    "By default we create projects with `list_on_homepage: False`.\n"
                )

            if "delete_after_days" in ex.fields_to_change:
                boolean_note += (
                    "The `delete_after_days` field is represented as `preserve_project`"
                    " in the packit config (`True` is `-1` and `False` is `60`)."
                    "By default we create projects with `preserve: True` "
                    "which means `delete_after_days=60`.\n"
                )

            permissions_url = self.api.copr_helper.get_copr_settings_url(
                owner,
                self.job_project,
                section="permissions",
            )
            settings_url = self.api.copr_helper.get_copr_settings_url(
                owner,
                self.job_project,
            )

            msg = (
                "Based on your Packit configuration the settings "
                f"of the {owner}/{self.job_project} "
                "Copr project would need to be updated as follows:\n"
                "\n"
                f"{table}"
                "\n"
                f"{chroots_diff}"
                f"{boolean_note}"
                "\n"
                "Packit was unable to update the settings above as it is missing `admin` "
                f"permissions on the {owner}/{self.job_project} Copr project.\n"
                "\n"
                "To fix this you can do one of the following:\n"
                "\n"
                f"- Grant Packit `admin` permissions on the {owner}/{self.job_project} "
                f"Copr project on the [permissions page]({permissions_url}).\n"
                "- Change the above Copr project settings manually "
                f"on the [settings page]({settings_url}) "
                "to match the Packit configuration.\n"
                "- Update the Packit configuration to match the Copr project settings.\n"
                "\n"
                "Please retrigger the build, once the issue above is fixed.\n"
            )
            self.status_reporter.comment(body=msg)
            raise ex
        except PackitCoprProjectException as ex:
            msg = (
                "We were not able to find or create Copr project"
                f" `{owner}/{self.job_project}` "
                "specified in the config with the following error:\n"
                f"```\n{ex!s}\n```\n---\n"
                "Unless the HTTP status code above is >= 500, "
                " please check your configuration for:\n\n"
                "1. typos in owner and project name (groups need to be prefixed with `@`)\n"
                "2. whether the project name doesn't contain not allowed characters (only letters, "
                "digits, underscores, dashes and dots must be used)\n"
                "3. whether the project itself exists (Packit creates projects"
                " only in its own namespace)\n"
                "4. whether Packit is allowed to build in your Copr project\n"
                "5. whether your Copr project/group is not private"
            )
            self.status_reporter.comment(body=msg)
            raise ex

        return owner

    def get_configured_targets(self) -> set[str]:
        """
        Get configured targets of the custom Copr project.

        Returns:
            Set of Copr targets configured in the custom Copr project.
        """
        owner, project = self.job_owner, self.job_project
        return self.api.copr_helper.get_chroots(owner=owner, project=project)

    # [NOTE] Needs to return a union, because TF helper inherits from this and
    # it clashes the type checking…
    def get_running_jobs(
        self,
    ) -> Union[Iterable[tuple["CoprBuildTargetModel"]], Iterable[tuple["TFTTestRunTargetModel"]]]:
        if sha := self.metadata.commit_sha_before:
            yield from CoprBuildGroupModel.get_running(commit_sha=sha)

        # [SAFETY] When there's no previous commit hash, yields nothing

    def cancel_running_builds(self):
        running_builds = list(self.get_running_jobs())
        if not running_builds:
            logger.info("No running Copr builds to cancel.")
            return

        # Cancel unique builds
        unique_builds = {
            int(build.build_id) for (build,) in running_builds if build.build_id is not None
        }
        for build_id in unique_builds:
            logger.debug("Cancelling Copr build #%s", build_id)
            self.api.copr_helper.cancel_build(build_id)

        # Mark them as canceled
        for (target,) in running_builds:
            target.set_status(BuildStatus.canceled)
