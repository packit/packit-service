# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from datetime import datetime, timezone
from typing import Iterable, List, Optional, Set, Tuple

from copr.v3 import CoprAuthException, CoprRequestException

from ogr.abstract import GitProject
from ogr.parsing import parse_git_repo
from ogr.services.github import GithubProject
from packit.config import JobConfig, JobType, JobConfigTriggerType
from packit.config.aliases import get_aliases, get_valid_build_targets
from packit.config.common_package_config import Deployment
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
    COPR_CHROOT_CHANGE_MSG,
    DEFAULT_MAPPING_INTERNAL_TF,
    DEFAULT_MAPPING_TF,
    MSG_RETRIGGER,
    PG_BUILD_STATUS_SUCCESS,
)
from packit_service.models import (
    AbstractTriggerDbType,
    CoprBuildTargetModel,
    SRPMBuildModel,
    JobTriggerModelType,
)
from packit_service.service.urls import (
    get_copr_build_info_url,
    get_srpm_build_info_url,
)
from packit_service.utils import get_package_nvrs
from packit_service.worker.helpers.build.build_helper import BaseBuildJobHelper
from packit_service.worker.events import EventData
from packit_service.worker.monitoring import Pushgateway, measure_time
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
        db_trigger: AbstractTriggerDbType,
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
            build_targets_override=build_targets_override,
            tests_targets_override=tests_targets_override,
            pushgateway=pushgateway,
        )

        self.msg_retrigger: str = MSG_RETRIGGER.format(
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
        service_prefix = (
            "" if isinstance(self.project, GithubProject) else f"{service_hostname}-"
        )

        namespace = self.project.namespace.replace("/", "-")
        # We want to share project between all releases.
        # More details: https://github.com/packit/packit-service/issues/1044
        ref_identifier = (
            "releases"
            if self.db_trigger.job_trigger_model_type == JobTriggerModelType.release
            else self.metadata.identifier
        )
        configured_identifier = (
            f"-{self.job_config.identifier}" if self.job_config.identifier else ""
        )

        return (
            f"{service_prefix}{namespace}-{self.project.repo}-{ref_identifier}"
            f"{configured_identifier}"
        )

    @property
    def job_project(self) -> Optional[str]:
        """
        The job definition from the config file.
        """
        if self.job_build and self.job_build.project:
            return self.job_build.project

        if self.job_tests and self.job_tests.project:
            return self.job_tests.project

        return self.default_project_name

    @property
    def job_owner(self) -> Optional[str]:
        """
        Owner used for the copr build -- search the config or use the copr's config.
        """
        if self.job_build and self.job_build.owner:
            return self.job_build.owner

        if self.job_tests and self.job_tests.owner:
            return self.job_tests.owner

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
    def additional_repos(self) -> Optional[List[str]]:
        """
        Additional repos that will be enable for copr build.
        """
        return self.job_build.additional_repos if self.job_build else None

    @property
    def build_targets_all(self) -> Set[str]:
        """
        Return all valid Copr build targets/chroots from config.
        """
        return get_valid_build_targets(*self.configured_build_targets, default=None)

    @property
    def tests_targets_all(self) -> Set[str]:
        """
        Return all valid test targets/chroots from config.
        """
        return get_valid_build_targets(*self.configured_tests_targets, default=None)

    @property
    def tests_targets_all_mapped(self) -> Set[str]:
        """
        Return all valid mapped test targets from config.

        Examples:
        test job configuration:
          - job: tests
            trigger: pull_request
            metadata:
                targets:
                      epel-7-x86_64:
                        distros: [centos-7, rhel-7]
                      fedora-35-x86_64: {}

        helper.tests_targets_all_mapped -> {"centos-7", "rhel-7", "fedora-35-x86_64"}
        """
        targets = set()
        for chroot in self.tests_targets_all:
            targets.update(self.build_target2test_targets(chroot))
        return targets

    def build_target2test_targets(self, build_target: str) -> Set[str]:
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

        helper.build_target2test_targets("epel-7-x86_64") -> {"centos-7-x86_64", "rhel-7-x86_64"}

        test job configuration:
          - job: tests
            trigger: pull_request
            metadata:
                targets:
                      fedora-35-x86_64

        helper.build_target2test_targets("fedora-35-x86_64") -> {"fedora-35-x86_64"}
        """
        if not self.job_tests or build_target not in self.tests_targets_all:
            return set()

        distro, arch = build_target.rsplit("-", 1)
        configured_distros = self.job_tests.targets_dict.get(build_target, {}).get(
            "distros"
        )

        if configured_distros:
            distro_arch_list = [(distro, arch) for distro in configured_distros]
        else:
            mapping = (
                DEFAULT_MAPPING_INTERNAL_TF
                if self.job_config.use_internal_tf
                else DEFAULT_MAPPING_TF
            )
            distro = mapping.get(distro, distro)
            distro_arch_list = [(distro, arch)]

        return {f"{distro}-{arch}" for (distro, arch) in distro_arch_list}

    def test_target2build_target(self, test_target: str) -> str:
        """
        Return build target to build in needed for testing in test target.
        Go through the not mapped test targets (self.test_targets_all)
        and for each check the mapped test targets, if test_target is
        in mapped test targets, return the target that was mapped.

        Examples:
        configuration:
          - job: tests
            trigger: pull_request
            metadata:
                targets:
                      epel-7-x86_64:
                        distros: [centos-7, rhel-7]

        helper.test_target2build_target("centos-7") -> "epel-7-x86_64"

        configuration:
          - job: tests
            trigger: pull_request
            metadata:
                targets:
                      fedora-35-x86_64


        helper.test_target2build_target("fedora-35-x86_64") -> "fedora-35-x86_64"

        configuration:
          - job: tests
            trigger: pull_request
            metadata:
                targets:
                      centos-stream-8-x86_64


        helper.test_target2build_target("centos-stream-8-x86_64") -> "centos-stream-8-x86_64"
        """
        for target in self.tests_targets_all:
            if test_target in self.build_target2test_targets(target):
                logger.debug(f"Build target corresponding to {test_target}: {target}")
                return target

        return test_target

    @property
    def available_chroots(self) -> Set[str]:
        """
        Returns set of available COPR targets.
        """
        return {
            *filter(
                lambda chroot: not chroot.startswith("_"),
                self.api.copr_helper.get_copr_client()
                .mock_chroot_proxy.get_list()
                .keys(),
            )
        }

    def get_built_packages(self, build_id: int, chroot: str) -> List:
        return self.api.copr_helper.copr_client.build_chroot_proxy.get_built_packages(
            build_id, chroot
        ).packages

    def get_build(self, build_id: int):
        return self.api.copr_helper.copr_client.build_proxy.get(build_id)

    def monitor_not_submitted_copr_builds(self, number_of_builds: int, reason: str):
        """
        Measure the time it took to set the failed status in case of event (e.g. failed SRPM)
        that prevents Copr build to be submitted.
        """
        time = measure_time(
            end=datetime.now(timezone.utc), begin=self.metadata.task_accepted_time
        )
        for _ in range(number_of_builds):
            self.pushgateway.copr_build_not_submitted_time.labels(
                reason=reason
            ).observe(time)

    def get_packit_copr_download_urls(self) -> List[str]:
        """
        Get packit package download urls for latest succeeded build in Copr project for the given
        environment (for production packit/packit-stable, else packit/packit-dev,
        e.g https://download.copr.fedorainfracloud.org/results/packit/
        packit-stable/fedora-35-x86_64/03247833-packit/
        packit-0.44.1.dev4+g5ec2bd1-1.20220124144110935127.stable.4.g5ec2bd1.fc35.noarch.rpm)

        Returns: list of urls
        """
        try:
            latest_successful_build_id = (
                self.api.copr_helper.copr_client.package_proxy.get(
                    ownername="packit",
                    projectname="packit-stable"
                    if self.service_config.deployment == Deployment.prod
                    else "packit-dev",
                    packagename="packit",
                    with_latest_succeeded_build=True,
                ).builds["latest_succeeded"]["id"]
            )
            result_url = self.api.copr_helper.copr_client.build_chroot_proxy.get(
                latest_successful_build_id, self.get_latest_fedora_stable_chroot()
            ).result_url
            package_nvrs = self.get_built_packages(
                latest_successful_build_id, self.get_latest_fedora_stable_chroot()
            )
            built_packages = get_package_nvrs(package_nvrs)
            return [f"{result_url}{package}.rpm" for package in built_packages]
        except Exception as ex:
            logger.debug(
                f"Getting download urls for latest packit/packit-stable "
                f"build was not successful: {ex}"
            )
            raise ex

    def get_job_config_index(self) -> int:
        """
        Get index of the job config in the package config.
        (Index is being submitted to Copr via source script.)
        """
        return self.package_config.jobs.index(self.job_config)

    def run_copr_build_from_source_script(self) -> TaskResults:
        """
        Run copr build using custom source method.
        """
        try:
            pr_id = self.metadata.pr_id
            script = create_source_script(
                url=self.metadata.project_url,
                ref=self.metadata.git_ref,
                pr_id=str(pr_id) if pr_id else None,
                merge_pr=self.package_config.merge_pr_in_ci,
                target_branch=self.project.get_pr(pr_id).target_branch
                if pr_id
                else None,
                job_config_index=self.get_job_config_index(),
                bump_version=self.job_config.trigger != JobConfigTriggerType.release,
                release_suffix=self.job_config.release_suffix,
            )
            build_id, web_url = self.submit_copr_build(script=script)
        except Exception as ex:
            self.handle_build_submit_error(ex)
            return TaskResults(
                success=False,
                details={"msg": "Submit of the Copr build failed.", "error": str(ex)},
            )

        self._srpm_model, self.run_model = SRPMBuildModel.create_with_new_run(
            copr_build_id=str(build_id),
            commit_sha=self.metadata.commit_sha,
            trigger_model=self.db_trigger,
            copr_web_url=web_url,
        )

        self.report_status_to_all(
            description="SRPM build in Copr was submitted...",
            state=BaseCommitStatus.pending,
            url=get_srpm_build_info_url(self.srpm_model.id),
        )

        self.handle_rpm_build_start(build_id, web_url, waiting_for_srpm=True)

        return TaskResults(success=True, details={})

    @staticmethod
    def get_latest_fedora_stable_chroot() -> str:
        """
        Get the latest stable Fedora chroot.

        This is used as a chroot where the Copr source script will be run.
        """
        latest_fedora_stable_chroot = get_aliases().get("fedora-stable")[-1]
        return list(get_valid_build_targets(latest_fedora_stable_chroot))[0]

    def submit_copr_build(self, script: Optional[str] = None) -> Tuple[int, str]:
        """
        Create the project in Copr if not exists and submit a new build using
        source script method
        Return:
            tuple of build ID and web url
        """
        owner = self.create_copr_project_if_not_exists()
        try:
            if script:
                build = self.api.copr_helper.copr_client.build_proxy.create_from_custom(
                    ownername=owner,
                    projectname=self.job_project,
                    script=script,
                    # use the latest stable chroot
                    script_chroot=self.get_latest_fedora_stable_chroot(),
                    script_builddeps=self.get_packit_copr_download_urls()
                    + self.package_config.srpm_build_deps,
                    buildopts={
                        "chroots": list(self.build_targets),
                        "enable_net": self.job_config.enable_net,
                    },
                )
            else:
                build = self.api.copr_helper.copr_client.build_proxy.create_from_file(
                    ownername=owner,
                    projectname=self.job_project,
                    path=self.srpm_path,
                    buildopts={
                        "chroots": list(self.build_targets),
                        "enable_net": self.job_config.enable_net,
                    },
                )

        except (CoprRequestException, CoprAuthException) as ex:
            if "You don't have permissions to build in this copr." in str(
                ex
            ) or "is not allowed to build in the copr" in str(ex):
                self.api.copr_helper.copr_client.project_proxy.request_permissions(
                    ownername=owner,
                    projectname=self.job_project,
                    permissions={"builder": True},
                )

                # notify user, PR if exists, commit comment otherwise
                permissions_url = self.api.copr_helper.get_copr_settings_url(
                    owner, self.job_project, section="permissions"
                )
                self.status_reporter.comment(
                    body="We have requested the `builder` permissions "
                    f"for the {owner}/{self.job_project} Copr project.\n"
                    "\n"
                    "Please confirm the request on the "
                    f"[{owner}/{self.job_project} Copr project permissions page]"
                    f"({permissions_url})"
                    " and retrigger the build.",
                )
            raise ex

        return build.id, self.api.copr_helper.copr_web_build_url(build)

    def run_copr_build(self) -> TaskResults:
        """
        Run copr build using SRPM built by us.
        """
        self.report_status_to_all(
            description="Building SRPM ...",
            state=BaseCommitStatus.running,
            # pagure requires "valid url"
            url="",
        )
        if results := self.create_srpm_if_needed():
            return results

        if self.srpm_model.status != PG_BUILD_STATUS_SUCCESS:
            msg = "SRPM build failed, check the logs for details."
            self.report_status_to_all(
                state=BaseCommitStatus.failure,
                description=msg,
                url=get_srpm_build_info_url(self.srpm_model.id),
            )
            self.monitor_not_submitted_copr_builds(
                len(self.build_targets), "srpm_failure"
            )
            return TaskResults(success=False, details={"msg": msg})

        try:
            build_id, web_url = self.submit_copr_build()
        except Exception as ex:
            self.handle_build_submit_error(ex)
            return TaskResults(
                success=False,
                details={"msg": "Submit of the Copr build failed.", "error": str(ex)},
            )

        self.handle_rpm_build_start(build_id, web_url)
        return TaskResults(success=True, details={})

    def handle_build_submit_error(self, ex):
        """
        Handle errors when submitting Copr build.
        """
        sentry_integration.send_to_sentry(ex)
        # TODO: Where can we show more info about failure?
        # TODO: Retry
        self.report_status_to_all(
            state=BaseCommitStatus.error,
            description=f"Submit of the build failed: {ex}",
        )
        self.monitor_not_submitted_copr_builds(
            len(self.build_targets), "submit_failure"
        )

    def handle_rpm_build_start(
        self, build_id: int, web_url: str, waiting_for_srpm: bool = False
    ):
        """
        Create models for Copr build chroots and report start of RPM build
        if the SRPM is already built.
        """
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

            copr_build = CoprBuildTargetModel.create(
                build_id=str(build_id),
                commit_sha=self.metadata.commit_sha,
                project_name=self.job_project,
                owner=self.job_owner,
                web_url=web_url,
                target=chroot,
                status="waiting_for_srpm" if waiting_for_srpm else "pending",
                run_model=self.run_model,
                task_accepted_time=self.metadata.task_accepted_time,
            )
            if not waiting_for_srpm:
                url = get_copr_build_info_url(id_=copr_build.id)
                self.report_status_to_all_for_chroot(
                    state=BaseCommitStatus.running,
                    description="Starting RPM build...",
                    url=url,
                    chroot=chroot,
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

        # release the hounds!
        celery_app.send_task(
            "task.babysit_copr_build",
            args=(build_id,),
            countdown=120,  # do the first check in 120s
        )

    def _visualize_chroots_diff(
        self, old_chroots: Iterable[str], new_chroots: Iterable[str]
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
        self, owner: str, chroots_diff: str, table: str
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

    def create_copr_project_if_not_exists(self) -> str:
        """
        Create project in Copr.

        Returns:
            str owner
        """
        owner = self.job_owner or self.api.copr_helper.configured_owner
        if not owner:
            raise PackitCoprException(
                "Copr owner not set. Use Copr config file or `--owner` when calling packit CLI."
            )

        try:
            overwrite_booleans = owner == self.service_config.fas_user
            self.api.copr_helper.create_copr_project_if_not_exists(
                project=self.job_project,
                chroots=list(self.build_targets_all),
                owner=owner,
                description=None,
                instructions=None,
                list_on_homepage=self.list_on_homepage if overwrite_booleans else None,
                preserve_project=self.preserve_project if overwrite_booleans else None,
                additional_repos=self.additional_repos,
                request_admin_if_needed=True,
            )
        except PackitCoprSettingsException as ex:
            # notify user first, PR if exists, commit comment otherwise
            table = (
                "| field | old value | new value |\n"
                "| ----- | --------- | --------- |\n"
            )
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
                owner, self.job_project, section="permissions"
            )
            settings_url = self.api.copr_helper.get_copr_settings_url(
                owner, self.job_project
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
                "We were not able to find Copr project"
                f"({owner}/{self.job_project}) "
                "specified in the config with the following error:\n"
                f"```\n{str(ex.__cause__)}\n```\n---\n"
                "Please check your configuration for:\n\n"
                "1. typos in owner and project name (groups need to be prefixed with `@`)\n"
                "2. whether the project itself exists (Packit creates projects"
                " only in its own namespace)\n"
                "3. whether Packit is allowed to build in your Copr project\n"
                "4. whether your Copr project/group is not private"
            )
            self.status_reporter.comment(body=msg)
            raise ex

        return owner
