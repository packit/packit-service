# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
import argparse
import logging
import re
import shlex
from collections.abc import Iterable
from typing import Any, Callable, Optional, Union

from ogr.abstract import GitProject
from ogr.utils import RequestResponse
from packit.config import JobConfig, PackageConfig
from packit.exceptions import PackitConfigException
from packit.utils import commands, nested_get
from packit.utils.koji_helper import KojiHelper

from packit_service.config import Deployment, ServiceConfig
from packit_service.constants import (
    BASE_RETRY_INTERVAL_IN_MINUTES_FOR_OUTAGES,
    CONTACTS_URL,
    TESTING_FARM_ARTIFACTS_KEY,
    TESTING_FARM_EXTRA_PARAM_MERGED_SUBTREES,
    TESTING_FARM_INSTALLABILITY_TEST_REF,
    TESTING_FARM_INSTALLABILITY_TEST_URL,
)
from packit_service.events import github, gitlab, pagure
from packit_service.events.event_data import EventData
from packit_service.models import (
    BuildStatus,
    CoprBuildTargetModel,
    KojiBuildTargetModel,
    ProjectEventModel,
    PullRequestModel,
    TestingFarmResult,
    TFTTestRunGroupModel,
    TFTTestRunTargetModel,
    filter_most_recent_target_models_by_status,
)
from packit_service.sentry_integration import send_to_sentry
from packit_service.service.urls import get_testing_farm_info_url
from packit_service.utils import get_package_nvrs, get_packit_commands_from_comment
from packit_service.worker.celery_task import CeleryTask
from packit_service.worker.helpers.build import CoprBuildJobHelper
from packit_service.worker.helpers.fedora_ci import FedoraCIHelper
from packit_service.worker.helpers.testing_farm_client import TestingFarmClient
from packit_service.worker.reporting import BaseCommitStatus
from packit_service.worker.result import TaskResults

logger = logging.getLogger(__name__)


class CommentArguments:
    """
    Parse arguments from trigger comment and provide the attributes to Testing Farm helper.
    """

    def __init__(self, command_prefix: str, comment: str):
        self._parser: argparse.ArgumentParser = None
        self.packit_command: str = None
        self.identifier: str = None
        self.labels: list[str] = None
        self.pr_argument: str = None
        self.envs: dict[str, str] = None

        if comment is None:
            return

        # Remove the command prefix from the comment
        logger.debug(f"Parsing comment -> {comment}")
        logger.debug(f"Used command prefix -> {command_prefix}")

        # Match the command prefix and extract the rest of the comment
        match = re.match(r"^" + re.escape(command_prefix) + r"\s+(.*)", comment)
        if match:
            arguments_str = match.group(1)
        else:
            # If the command prefix is not found, nothing to parse
            logger.debug("Command prefix not found in the comment.")
            return

        # Use shlex to split the arguments string into a list
        args_list = shlex.split(arguments_str)
        logger.debug(f"Arguments list after shlex splitting: {args_list}")

        # Parse known arguments
        try:
            args, unknown_args = self.parser.parse_known_args(args_list)
            logger.debug(f"Parsed known args: {args}")
            logger.debug(f"Unknown args: {unknown_args}")
        except argparse.ArgumentError as e:
            logger.error(f"Argument parsing error: {e}")
            return

        self.parse_known_arguments(args)
        self.parse_unknown_arguments(unknown_args)

    @property
    def parser(self) -> argparse.ArgumentParser:
        if self._parser is None:
            # Set up argparse
            self._parser = argparse.ArgumentParser()
            self._parser.add_argument("packit_command")
            self._parser.add_argument("--identifier", "--id", "-i")
            self._parser.add_argument("--labels", type=lambda s: s.split(","))
            # Allows multiple --env arguments
            self._parser.add_argument("--env", action="append")

        return self._parser

    def parse_known_arguments(self, args: argparse.Namespace) -> None:
        # Assign the parsed arguments to the class attributes
        self.packit_command = args.packit_command
        logger.debug(f"Parsed packit_command: {self.packit_command}")

        self.identifier = args.identifier
        logger.debug(f"Parsed identifier: {self.identifier}")

        if args.labels:
            self.labels = args.labels
            logger.debug(f"Parsed labels: {self.labels}")

        if args.env:
            self.envs = {}
            for env in args.env:
                if "=" in env:
                    key, value = env.split("=", 1)
                    self.envs[key] = value
                    logger.debug(f"Parsed env variable: {key}={value}")
                else:
                    logger.error(
                        f"Invalid format for '--env' argument: '{env}'. Expected VAR_NAME=value.",
                    )
                    continue

    def parse_unknown_arguments(self, unknown_args: list[str]) -> None:
        # Process unknown_args to find pr_argument
        pr_argument_pattern = re.compile(r"^[^/\s]+/[^#\s]+#\d+$")
        for arg in unknown_args:
            if pr_argument_pattern.match(arg):
                self.pr_argument = arg
                logger.debug(f"Parsed pr_argument: {self.pr_argument}")
                break


class TestingFarmJobHelper(CoprBuildJobHelper):
    __test__ = False

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
        celery_task: Optional[CeleryTask] = None,
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
        )
        self.celery_task = celery_task
        self._tft_client: Optional[TestingFarmClient] = None
        self._copr_builds_from_other_pr: Optional[dict[str, CoprBuildTargetModel]] = None
        self._test_check_names: Optional[list[str]] = None
        self._comment_arguments: Optional[CommentArguments] = None

    @property
    def tft_client(self) -> TestingFarmClient:
        if not self._tft_client:
            self._tft_client = TestingFarmClient(
                api_url=self.service_config.testing_farm_api_url,
                # We have two tokens (=TF users), one for upstream and one for internal instance.
                # The URL is same and the instance choice is based on the TF user (=token)
                # we use in the payload.
                # To use internal instance,
                # project needs to be added to the `enabled_projects_for_internal_tf` list
                # in the service config.
                # This is checked in the run_testing_farm method.
                token=(
                    self.service_config.internal_testing_farm_secret
                    if self.job_config.use_internal_tf
                    else self.service_config.testing_farm_secret
                ),
                use_internal_tf=self.job_config.use_internal_tf,
            )
        return self._tft_client

    @property
    def skip_build(self) -> bool:
        return self.job_config.skip_build

    @property
    def custom_fmf(self) -> bool:
        return bool(self.job_config.fmf_url)

    @property
    def fmf_url(self) -> str:
        return (
            self.job_config.fmf_url
            or (
                self.pull_request_object
                and not self.job_config.use_target_repo_for_fmf_url
                and self.pull_request_object.source_project.get_web_url()
            )
            or self.project.get_web_url()
        )

    @property
    def fmf_ref(self) -> str:
        if self.custom_fmf:
            return self.job_config.fmf_ref

        return self.metadata.commit_sha

    @property
    def fmf_path(self) -> str:
        if self.job_config.fmf_path:
            path: str = self.job_config.fmf_path
            # if it is an alias of top level root use current working path
            if path in ("/", "./", "."):
                return "."
            # Otherwise sanitize the path
            path = path.removeprefix("./")
            path = path.removeprefix("/")
            return path.removesuffix("/")
        return "."

    @property
    def tmt_plan(self) -> Optional[str]:
        if self.job_config.tmt_plan:
            return self.job_config.tmt_plan

        return None

    @property
    def tf_post_install_script(self) -> Optional[str]:
        if self.job_config.tf_post_install_script:
            return self.job_config.tf_post_install_script

        return None

    @property
    def source_branch_sha(self) -> Optional[str]:
        return self.pull_request_object.head_commit if self.pull_request_object else None

    @property
    def target_branch_sha(self) -> Optional[str]:
        return (
            self.pull_request_object.target_branch_head_commit if self.pull_request_object else None
        )

    @property
    def target_branch(self) -> Optional[str]:
        return self.pull_request_object.target_branch if self.pull_request_object else None

    @property
    def source_branch(self) -> Optional[str]:
        return self.pull_request_object.source_branch if self.pull_request_object else None

    @property
    def target_project_url(self) -> Optional[str]:
        return (
            self.pull_request_object.target_project.get_web_url()
            if self.pull_request_object
            else None
        )

    @property
    def source_project_url(self) -> Optional[str]:
        return (
            self.pull_request_object.source_project.get_web_url()
            if self.pull_request_object
            else None
        )

    @property
    def comment_arguments(self) -> Optional[CommentArguments]:
        """
        Build CommentArguments class by event comment data.
        """
        if not self._comment_arguments:
            self._comment_arguments = CommentArguments(
                self.service_config.comment_command_prefix,
                self.metadata.event_dict.get("comment"),
            )
        return self._comment_arguments

    def is_comment_event(self) -> bool:
        return self.metadata.event_type in (
            github.pr.Comment.event_type(),
            gitlab.mr.Comment.event_type(),
            pagure.pr.Comment.event_type(),
        )

    def is_copr_build_comment_event(self) -> bool:
        return self.is_comment_event() and self.comment_arguments.packit_command in (
            "build",
            "copr-build",
        )

    def is_test_comment_event(self) -> bool:
        return self.is_comment_event() and self.comment_arguments.packit_command in (
            "test",
            "retest-failed",
        )

    def is_test_comment_pr_argument_present(self):
        return self.is_test_comment_event() and self.comment_arguments.pr_argument

    def is_test_comment_identifier_present(self):
        return self.is_test_comment_event() and self.comment_arguments.identifier

    def is_test_comment_label_present(self):
        return self.is_test_comment_event() and self.comment_arguments.labels

    def build_required(self) -> bool:
        return not self.skip_build and (
            # build is required for push/pull-request events and
            # for comment event requesting copr build
            self.metadata.event_type
            in (
                github.push.Commit.event_type(),
                github.pr.Action.event_type(),
                github.commit.Comment.event_type(),
                gitlab.push.Commit.event_type(),
                gitlab.mr.Action.event_type(),
                gitlab.commit.Comment.event_type(),
            )
            or self.is_copr_build_comment_event()
        )

    @property
    def copr_builds_from_other_pr(
        self,
    ) -> Optional[dict[str, CoprBuildTargetModel]]:
        """
        Dictionary containing copr build target model for each chroot
        if the testing farm was triggered by a comment with PR argument
        and we store any Copr builds for the given PR, otherwise None.
        """
        if not self._copr_builds_from_other_pr and self.is_test_comment_pr_argument_present():
            self._copr_builds_from_other_pr = self.get_copr_builds_from_other_pr()
        return self._copr_builds_from_other_pr

    @staticmethod
    def _artifact(
        chroot: str,
        build_id: Optional[int],
        built_packages: Optional[list[dict]],
    ) -> dict[str, Union[list[str], str]]:
        artifact: dict[str, Union[list[str], str]] = {
            "id": f"{build_id}:{chroot}",
            "type": "fedora-copr-build",
        }

        if built_packages:
            artifact["packages"] = get_package_nvrs(built_packages)

        return artifact

    def _construct_test_payload(self) -> dict:
        tmt = {
            "url": self.fmf_url,
            "path": self.fmf_path,
        }
        if self.fmf_ref:
            tmt["ref"] = self.fmf_ref

            # We assign a commit hash for merging only if:
            # • there are no custom fmf tests set
            # • we merge and have a PR
            if not self.custom_fmf and self.job_config.merge_pr_in_ci and self.target_branch_sha:
                tmt["merge_sha"] = self.target_branch_sha

        if self.tmt_plan:
            tmt["name"] = self.tmt_plan

        return tmt

    @classmethod
    def _merge_payload_with_extra_params(cls, payload: Any, params: Any):
        def is_final(v):
            return not isinstance(v, list) and not isinstance(v, dict)

        if type(payload) is not type(params):
            # Incompatible types, no way to merge this
            return

        if isinstance(params, dict):
            for key, value in params.items():
                if key not in payload or is_final(value):
                    payload[key] = value
                elif not is_final(value):
                    if key == TESTING_FARM_ARTIFACTS_KEY:
                        cls._handle_extra_artifacts(
                            payload,
                            params[TESTING_FARM_ARTIFACTS_KEY],
                        )
                        continue
                    cls._merge_payload_with_extra_params(payload[key], params[key])

        elif isinstance(params, list):
            for payload_el, params_el in zip(payload, params):
                cls._merge_payload_with_extra_params(payload_el, params_el)

    def _inject_extra_params(self, payload: dict) -> dict:
        if not hasattr(self.job_config, "tf_extra_params"):
            return payload

        extra_params = self.job_config.tf_extra_params
        # Merge only some subtrees, we do not want the user to override notification or api_key!
        for subtree in TESTING_FARM_EXTRA_PARAM_MERGED_SUBTREES:
            if subtree not in extra_params:
                continue

            if subtree not in payload:
                payload[subtree] = extra_params[subtree]
            else:
                self._merge_payload_with_extra_params(
                    payload[subtree],
                    extra_params[subtree],
                )

        return payload

    @classmethod
    def _handle_extra_artifacts(cls, payload: Any, extra_params_artifacts: Any):
        """
        We treat `artifacts` specially since we do not want to overwrite
        the artifacts defined by us, but combine them with the one in `tf_extra_params`.
        """
        if isinstance(extra_params_artifacts, list):
            payload[TESTING_FARM_ARTIFACTS_KEY] += extra_params_artifacts
        else:
            logger.info(
                "Type of artifacts in the tf_extra_params is not a list, "
                "not adding them to payload.",
            )

    def _payload(
        self,
        target: str,
        compose: str,
        artifacts: Optional[list[dict[str, Union[list[str], str]]]] = None,
        build: Optional["CoprBuildTargetModel"] = None,
        additional_build: Optional["CoprBuildTargetModel"] = None,
    ) -> dict:
        """Prepare a Testing Farm request payload.

        Testing Farm API: https://testing-farm.gitlab.io/api/

        Currently, we use the same secret to authenticate both,
        packit service (when sending request to testing farm)
        and testing farm (when sending notification to packit service's webhook).
        We might later use a different secret for those use cases.

        Args:
            chroot: Target TF chroot.
            artifact: Optional artifacts, e.g. list of package NEVRAs
            build: The related copr build.
        """
        distro, arch = target.rsplit("-", 1)
        tmt = self._construct_test_payload()

        packit_copr_projects = []

        if build is not None:
            build_log_url = build.build_logs_url
            srpm_build = build.get_srpm_build()
            srpm_url = srpm_build.url
            if build.built_packages:
                nvr_data = build.built_packages[0]
                nvr = f"{nvr_data['name']}-{nvr_data['version']}-{nvr_data['release']}"
            else:
                nvr = None
            packit_copr_projects.append(f"{build.owner}/{build.project_name}")
        else:
            build_log_url = nvr = srpm_url = None

        if additional_build is not None:
            packit_copr_projects.append(
                f"{additional_build.owner}/{additional_build.project_name}",
            )

        packit_copr_rpms = (
            [
                package
                for artifact in artifacts
                if artifact.get("packages")
                for package in artifact["packages"]
            ]
            if artifacts
            else None
        )

        predefined_environment = {
            "PACKIT_FULL_REPO_NAME": self.project.full_repo_name,
            "PACKIT_UPSTREAM_NAME": self.job_config.upstream_package_name,
            "PACKIT_UPSTREAM_URL": self.job_config.upstream_project_url,
            "PACKIT_DOWNSTREAM_NAME": self.job_config.downstream_package_name,
            "PACKIT_DOWNSTREAM_URL": (
                self.job_config.downstream_project_url
                if self.job_config.downstream_package_name
                else None
            ),
            "PACKIT_PACKAGE_NAME": self.job_config.downstream_package_name,
            "PACKIT_PACKAGE_NVR": nvr,
            "PACKIT_BUILD_LOG_URL": build_log_url,
            "PACKIT_SRPM_URL": srpm_url,
            "PACKIT_COMMIT_SHA": self.metadata.commit_sha,
            "PACKIT_TAG_NAME": (self.metadata.tag_name if self.metadata.tag_name else None),
            "PACKIT_SOURCE_SHA": self.source_branch_sha,
            "PACKIT_TARGET_SHA": self.target_branch_sha,
            "PACKIT_SOURCE_BRANCH": self.source_branch,
            "PACKIT_TARGET_BRANCH": self.target_branch,
            "PACKIT_SOURCE_URL": self.source_project_url,
            "PACKIT_TARGET_URL": self.target_project_url,
            "PACKIT_PR_ID": self.pr_id,
            "PACKIT_COPR_PROJECT": (
                " ".join(packit_copr_projects) if packit_copr_projects else None
            ),
            "PACKIT_COPR_RPMS": (" ".join(packit_copr_rpms) if packit_copr_rpms else None),
        }
        predefined_environment = {k: v for k, v in predefined_environment.items() if v is not None}
        # User-defined variables have priority
        env_variables = self.job_config.env if hasattr(self.job_config, "env") else {}
        predefined_environment.update(env_variables)

        # User-defined variables from comments have priority
        if self.is_comment_event and self.comment_arguments.envs is not None:
            for k, v in self.comment_arguments.envs.items():
                # Set env variable
                logger.debug(f"Key: {k} -> Value: '{v}'")
                if v is not None and v != "":
                    predefined_environment[k] = v
                # Unset env variable if it doesn't have value
                else:
                    predefined_environment.pop(k, None)

        environment: dict[str, Any] = {
            "arch": arch,
            "os": {"compose": compose},
            "tmt": {
                "context": {
                    "distro": distro,
                    "arch": arch,
                    "trigger": "commit",
                    "initiator": "packit",
                },
            },
            "variables": predefined_environment,
        }
        if artifacts:
            environment["artifacts"] = artifacts

        if self.tf_post_install_script:
            environment["settings"] = {
                "provisioning": {"post_install_script": self.tf_post_install_script},
            }

        payload = {
            "test": {
                "tmt": tmt,
            },
            "environments": [environment],
            "notification": {
                "webhook": {
                    "url": f"{self.api_url}/testing-farm/results",
                    # Token is checked when accepting the results.
                    # See TestingFarmResults.validate_testing_farm_request
                    # in packit_service/service/api/testing_farm.py
                    # for more details.
                    "token": self.tft_client._token,
                },
            },
        }

        return self._inject_extra_params(payload)

    def _payload_install_test(self, build_id: int, target: str, compose: str) -> dict:
        """
        If the project doesn't use tmt, but still wants to run tests in TF.
        TF provides 'installation test', we request it in ['test']['tmt']['url'].
        We don't specify 'artifacts' as in _payload(), but 'variables'.
        """
        copr_build = CoprBuildTargetModel.get_by_build_id(build_id)
        distro, arch = target.rsplit("-", 1)
        return self._inject_extra_params(
            {
                "test": {
                    "tmt": {
                        "url": TESTING_FARM_INSTALLABILITY_TEST_URL,
                        "ref": TESTING_FARM_INSTALLABILITY_TEST_REF,
                        "name": "/packit/installation",
                    },
                },
                "environments": [
                    {
                        "arch": arch,
                        "os": {"compose": compose},
                        "variables": {
                            "REPOSITORY": f"{copr_build.owner}/{copr_build.project_name}",
                        },
                        "tmt": {
                            "context": {
                                "distro": distro,
                                "arch": arch,
                                "trigger": "commit",
                                "initiator": "packit",
                            },
                        },
                    },
                ],
                "notification": {
                    "webhook": {
                        "url": f"{self.api_url}/testing-farm/results",
                        "token": self.service_config.testing_farm_secret,
                    },
                },
            }
        )

    def check_comment_pr_argument_and_report(self) -> bool:
        """
        Check whether there are successful recent Copr builds for the additional PR given
        in the test comment command argument.
        """
        if not self.copr_builds_from_other_pr:
            self.report_status_to_tests(
                description="We were not able to get any Copr builds for given additional PR. "
                "Please, make sure the comment command is in correct format "
                "`/packit test namespace/repo#pr_id`",
                state=BaseCommitStatus.error,
            )
            return False

        return True

    def is_fmf_configured(self) -> bool:
        """
        Check whether `fmf_url` is configured in the test job
        or `.fmf/version` file exists in the particular ref.
        """
        if self.custom_fmf:
            return True

        try:
            self.project.get_file_content(
                path=f"{self.fmf_path}/.fmf/version",
                ref=self.metadata.commit_sha,
            )
            return True
        except FileNotFoundError:
            return False

    def report_missing_build_chroot(self, chroot: str):
        self.report_status_to_tests_for_chroot(
            state=BaseCommitStatus.error,
            description=f"No build defined for the target '{chroot}'.",
            chroot=chroot,
        )

    def get_latest_copr_build(
        self,
        target: str,
        commit_sha: str,
    ) -> Optional[CoprBuildTargetModel]:
        """
        Search a last build for the given target and commit SHA using Copr owner and project.
        """
        copr_builds = CoprBuildTargetModel.get_all_by(
            project_name=self.job_project,
            commit_sha=commit_sha,
            owner=self.job_owner,
            target=target,
        )
        try:
            return next(iter(copr_builds))
        except StopIteration:
            return None

    def _get_artifacts(
        self,
        chroot: str,
        build: CoprBuildTargetModel,
        additional_build: Optional[CoprBuildTargetModel],
    ) -> list[dict]:
        """
        Get the artifacts list from the build (if the skip_build option is not defined)
        and additional build (from other PR) if present.
        """
        artifacts = []
        if not self.skip_build:
            artifacts.append(
                self._artifact(chroot, int(build.build_id), build.built_packages),
            )

        if additional_build:
            artifacts.append(
                self._artifact(
                    chroot,
                    int(additional_build.build_id),
                    additional_build.built_packages,
                ),
            )

        return artifacts

    def run_testing_farm(
        self,
        test_run: TFTTestRunTargetModel,
        build: Optional["CoprBuildTargetModel"],
    ) -> TaskResults:
        if test_run.target not in self.tests_targets_for_test_job(self.job_config):
            # Leaving here just to be sure that we will discover this situation if it occurs.
            # Currently not possible to trigger this situation.
            msg = f"Target '{test_run.target}' not defined for tests but triggered."
            logger.debug(msg)
            send_to_sentry(PackitConfigException(msg))
            return TaskResults(
                success=False,
                details={"msg": msg},
            )
        chroot = self.test_target2build_target(test_run.target)
        logger.debug(
            f"Running testing farm for target {test_run.target}, chroot={chroot}.",
        )

        if not self.skip_build and chroot not in self.build_targets_all:
            self.report_missing_build_chroot(chroot)
            return TaskResults(
                success=False,
                details={
                    "msg": f"Target '{chroot}' not defined for build. "
                    "Cannot run tests without build.",
                },
            )

        if (
            self.job_config.use_internal_tf
            and f"{self.project.service.hostname}/{self.project.full_repo_name}"
            not in self.service_config.enabled_projects_for_internal_tf
        ):
            self.report_status_to_tests_for_test_target(
                state=BaseCommitStatus.neutral,
                description="Internal TF not allowed for this project. Let us know.",
                target=test_run.target,
                url=CONTACTS_URL,
            )
            return TaskResults(
                success=True,
                details={"msg": "Project not allowed to use internal TF."},
            )

        additional_build = None
        if self.copr_builds_from_other_pr and not (
            additional_build := self.copr_builds_from_other_pr.get(chroot)
        ):
            self.report_status_to_tests_for_test_target(
                state=BaseCommitStatus.failure,
                description="No latest successful Copr build from the other PR found.",
                target=test_run.target,
                url="",
            )
            return TaskResults(
                success=True,
                details={
                    "msg": "No latest successful Copr build from the other PR found.",
                },
            )

        self.report_status_to_tests_for_test_target(
            state=BaseCommitStatus.running,
            description=f"{'Build succeeded. ' if not self.skip_build else ''}"
            f"Submitting the tests ...",
            target=test_run.target,
        )

        return self.prepare_and_send_tf_request(
            test_run=test_run,
            chroot=chroot,
            build=build,
            additional_build=additional_build,
        )

    def prepare_and_send_tf_request(
        self,
        test_run: TFTTestRunTargetModel,
        chroot: str,
        build: Optional[CoprBuildTargetModel],
        additional_build: Optional[CoprBuildTargetModel],
    ) -> TaskResults:
        """
        Prepare the payload that will be sent to Testing Farm, submit it to
        TF API and handle the response (report whether the request was sent
        successfully, store the new TF run in DB or retry if needed).
        """
        logger.info("Preparing testing farm request...")

        distro, arch = test_run.target.rsplit("-", 1)

        def report_error(description, markdown_content):
            kwargs = {
                "state": BaseCommitStatus.error,
                "target": test_run.target,
                "description": description,
            }
            if markdown_content:
                kwargs["markdown_content"] = markdown_content
            self.report_status_to_tests_for_test_target(**kwargs)

        if not self.tft_client.is_supported_architecture(arch, report_error):
            msg = "Not supported architecture."
            return TaskResults(success=True, details={"msg": msg})

        compose = self.tft_client.distro2compose(distro, report_error)

        if not compose:
            msg = "We were not able to map distro to TF compose."
            return TaskResults(success=False, details={"msg": msg})

        if self.is_fmf_configured():
            payload = self._payload(
                target=test_run.target,
                compose=compose,
                artifacts=self._get_artifacts(chroot, build, additional_build),
                build=build,
                additional_build=additional_build,
            )
        elif not self.is_fmf_configured() and not self.skip_build:
            payload = self._payload_install_test(
                build_id=int(build.build_id),
                target=test_run.target,
                compose=compose,
            )
        else:
            self.report_status_to_tests_for_test_target(
                state=BaseCommitStatus.neutral,
                description="No FMF metadata found. Please, initialize the metadata tree "
                "with `fmf init`.",
                target=test_run.target,
            )
            return TaskResults(success=True, details={"msg": "No FMF metadata found."})

        endpoint = "requests"

        response = self.tft_client.send_testing_farm_request(
            endpoint=endpoint,
            method="POST",
            data=payload,
        )

        if response.status_code != 200:
            return self._handle_tf_submit_failure(
                test_run=test_run,
                response=response,
                payload=payload,
            )

        return self._handle_tf_submit_successful(
            test_run=test_run,
            response=response,
            additional_build=additional_build,
        )

    def _handle_tf_submit_successful(
        self,
        test_run: TFTTestRunTargetModel,
        response: RequestResponse,
        additional_build: Optional[CoprBuildTargetModel],
    ):
        """
        Create the model for the TF run in the database and report
        the state to user.
        """
        pipeline_id = response.json()["id"]
        logger.info(f"Request {pipeline_id} submitted to testing farm.")
        test_run.set_pipeline_id(pipeline_id)

        if additional_build:
            test_run.add_copr_build(additional_build)

        self.report_status_to_tests_for_test_target(
            state=BaseCommitStatus.running,
            description="Tests have been submitted ...",
            url=get_testing_farm_info_url(test_run.id),
            target=test_run.target,
        )

        return TaskResults(success=True, details={})

    def _handle_tf_submit_failure(
        self,
        test_run: TFTTestRunTargetModel,
        response: RequestResponse,
        payload: dict,
    ) -> TaskResults:
        """
        Retry the task and report it to user or report the failure state to user.
        """
        # something went wrong
        if response.json() and "errors" in response.json():
            errors = response.json()["errors"]
            # specific case, unsupported arch
            if not (msg := nested_get(errors, "environments", "0", "arch")):
                msg = "There was an error in the API request"
            markdown_content = (
                f"There was an error in the API request: {errors}\n"
                "For the details of the API request parameters, see "
                "[the Testing Farm API definition]"
                "(https://testing-farm.gitlab.io/api/#operation/requestsPost)"
            )
        else:
            msg = response.reason
            markdown_content = None
            if not self.celery_task.is_last_try():
                return self._retry_on_submit_failure(test_run, response.reason)

        test_run.set_status(TestingFarmResult.error)
        logger.error(f"{msg}, {self.tft_client.payload_without_token(payload)}")
        self.report_status_to_tests_for_test_target(
            state=BaseCommitStatus.failure,
            description=f"Failed to submit tests: {msg}.",
            target=test_run.target,
            markdown_content=markdown_content,
        )
        return TaskResults(success=False, details={"msg": msg})

    def _retry_on_submit_failure(
        self,
        test_run: TFTTestRunTargetModel,
        message: str,
    ) -> TaskResults:
        """
        Retry when there was a failure when submitting TF tests.

        Args:
            message: message to report to the user
        """
        test_run.set_status(TestingFarmResult.retry)
        interval = BASE_RETRY_INTERVAL_IN_MINUTES_FOR_OUTAGES * 2**self.celery_task.retries

        self.report_status_to_tests(
            state=BaseCommitStatus.pending,
            description="Failed to submit tests. The task will be"
            f" retried in {interval} {'minute' if interval == 1 else 'minutes'}.",
            markdown_content=message,
        )
        kargs = self.celery_task.task.request.kwargs.copy()
        kargs["testing_farm_target_id"] = test_run.id
        self.celery_task.retry(delay=interval * 60, kargs=kargs)
        return TaskResults(
            success=True,
            details={
                "msg": f"Task will be retried because of failure when submitting tests: {message}",
            },
        )

    def get_copr_builds_from_other_pr(
        self,
    ) -> Optional[dict[str, CoprBuildTargetModel]]:
        """
        Get additional Copr builds if there was a PR argument in the
        test comment command:

        1. parse the PR argument to get the repo, namespace and PR ID
        2. get the PR from the DB
        3. get the copr builds from DB for the given PR model
        4. filter the most recent successful copr build target models
        5. construct a dictionary to map the target names to actual models

        Returns:
            dict
        """
        parsed_pr_argument = self._parse_comment_pr_argument()
        if not parsed_pr_argument:
            return None

        namespace, repo, pr_id = parsed_pr_argument

        # for now let's default to github.com
        project_url = f"https://github.com/{namespace}/{repo}"
        pr_model = PullRequestModel.get(
            pr_id=int(pr_id),
            namespace=namespace,
            repo_name=repo,
            project_url=project_url,
        )

        if not pr_model:
            logger.debug(f"No PR for {project_url} and PR ID {pr_id} found in DB.")
            return None

        copr_builds = pr_model.get_copr_builds()
        if not copr_builds:
            logger.debug(
                f"No copr builds for {project_url} and PR ID {pr_id} found in DB.",
            )
            return None

        successful_most_recent_builds = filter_most_recent_target_models_by_status(
            models=copr_builds,
            statuses_to_filter_with=[BuildStatus.success],
        )

        return self._construct_copr_builds_from_other_pr_dict(
            successful_most_recent_builds,
        )

    def _parse_comment_pr_argument(self) -> Optional[tuple[str, str, str]]:
        """
        Parse the PR argument from test comment command if there is any.

        Returns:
            tuple of strings for namespace, repo and pr_id
        """
        if not self.comment_arguments.pr_argument:
            return None

        # self.comment_arguments.pr_argument should be in format namespace/repo#pr_id
        pr_argument_parts = self.comment_arguments.pr_argument.split("#")
        if len(pr_argument_parts) != 2:
            logger.debug(
                "Unexpected format of the test argument:"
                f" not able to split the test argument "
                f"{self.comment_arguments.pr_argument} with '#'.",
            )
            return None

        pr_id = pr_argument_parts[1]
        namespace_repo = pr_argument_parts[0].split("/")
        if len(namespace_repo) != 2:
            logger.debug(
                "Unexpected format of the test argument: "
                f"not able to split the test argument "
                f"{self.comment_arguments.pr_argument} with '/'.",
            )
            return None
        namespace, repo = namespace_repo

        logger.debug(
            f"Parsed test argument -> namespace: {namespace}, repo: {repo}, PR ID: {pr_id}",
        )

        return namespace, repo, pr_id

    def _construct_copr_builds_from_other_pr_dict(
        self,
        successful_most_recent_builds,
    ) -> Optional[dict[str, CoprBuildTargetModel]]:
        """
        Construct a dictionary that will contain for each build target name
        a build target model from the given models if there is one
        with matching target name.

        Args:
            successful_most_recent_builds: models to get the values from

        Returns:
            dict
        """
        result: dict[str, CoprBuildTargetModel] = {}

        for build_target in self.build_targets_for_tests:
            additional_build = [
                build for build in successful_most_recent_builds if build.target == build_target
            ]
            result[build_target] = additional_build[0] if additional_build else None

        logger.debug(f"Additional builds dictionary: {result}")

        return result

    @property
    def configured_tests_targets(self) -> set[str]:
        """
        Return the configured targets for the job.
        """
        return self.configured_targets_for_tests_job(self.job_config)

    @property
    def tests_targets(self) -> set[str]:
        """
        Return valid test targets (mapped) to test in for the job
        (considering the overrides).
        """
        return self.tests_targets_for_test_job(self.job_config)

    def get_test_check(self, chroot: Optional[str] = None) -> str:
        return self.get_test_check_cls(
            chroot,
            self.project_event_identifier_for_status,
            self.job_config.identifier,
            package=self.get_package_name(),
            template=self.job_config.status_name_template,
        )

    @property
    def test_check_names(self) -> list[str]:
        """
        List of full names of the commit statuses.

        e.g. ["testing-farm:fedora-rawhide-x86_64"]
        """
        if not self._test_check_names:
            self._test_check_names = [self.get_test_check(target) for target in self.tests_targets]
        return self._test_check_names

    def test_target2build_target(self, test_target: str) -> str:
        """
        Return build target to be built for a given test target
        (from configuration or from default mapping).
        """
        return self.test_target2build_target_for_test_job(test_target, self.job_config)

    @property
    def build_targets_for_tests(self) -> set[str]:
        """
        Return valid targets/chroots to build in needed to run the job.
        (considering the overrides).
        """
        return self.build_targets_for_test_job(self.job_config)

    def report_status_to_tests_for_chroot(
        self,
        description,
        state,
        url: str = "",
        chroot: str = "",
        markdown_content: Optional[str] = None,
        links_to_external_services: Optional[dict[str, str]] = None,
        update_feedback_time: Optional[Callable] = None,
    ) -> None:
        if chroot in self.build_targets_for_tests:
            test_targets = self.build_target2test_targets_for_test_job(
                chroot,
                self.job_config,
            )
            for target in test_targets:
                self._report(
                    description=description,
                    state=state,
                    url=url,
                    check_names=self.get_test_check(target),
                    markdown_content=markdown_content,
                    links_to_external_services=links_to_external_services,
                    update_feedback_time=update_feedback_time,
                )

    def report_status_to_tests_for_test_target(
        self,
        description,
        state,
        url: str = "",
        target: str = "",
        markdown_content: Optional[str] = None,
        links_to_external_services: Optional[dict[str, str]] = None,
        update_feedback_time: Optional[Callable] = None,
    ) -> None:
        if target in self.tests_targets:
            self._report(
                description=description,
                state=state,
                url=url,
                check_names=self.get_test_check(target),
                markdown_content=markdown_content,
                links_to_external_services=links_to_external_services,
                update_feedback_time=update_feedback_time,
            )

    def report_status_to_tests(
        self,
        description,
        state,
        url: str = "",
        markdown_content: Optional[str] = None,
        links_to_external_services: Optional[dict[str, str]] = None,
        update_feedback_time: Optional[Callable] = None,
    ) -> None:
        self._report(
            description=description,
            state=state,
            url=url,
            check_names=self.test_check_names,
            markdown_content=markdown_content,
            links_to_external_services=links_to_external_services,
            update_feedback_time=update_feedback_time,
        )

    def report_status_to_configured_job(
        self,
        description: str,
        state: BaseCommitStatus,
        url: str = "",
        markdown_content: Optional[str] = None,
        links_to_external_services: Optional[dict[str, str]] = None,
        update_feedback_time: Optional[Callable] = None,
    ):
        if self.job_config.manual_trigger and self.build_required():
            logger.debug("Skipping the reporting.")
            return

        self.report_status_to_tests(
            description=description,
            state=state,
            url=url,
            markdown_content=markdown_content,
            links_to_external_services=links_to_external_services,
            update_feedback_time=update_feedback_time,
        )

    def get_running_jobs(self) -> Iterable[tuple["TFTTestRunTargetModel"]]:
        if sha := self.metadata.commit_sha_before:
            yield from TFTTestRunGroupModel.get_running(
                commit_sha=sha, ranch=self.tft_client.default_ranch
            )

        # [SAFETY] When there's no previous commit hash, yields nothing

    def cancel_running_tests(self):
        running_tests = list(self.get_running_jobs())
        if not running_tests:
            logger.info("No running TF tests to cancel.")
            return

        for (test_run,) in running_tests:
            self.tft_client.cancel(test_run.pipeline_id)
            test_run.set_status(TestingFarmResult.cancel_requested)


FEDORA_CI_TESTS = {}


def implements_fedora_ci_test(test_name: str, skipif: Optional[Callable] = None) -> Callable:
    def _update_mapping(function: Callable) -> Callable:
        FEDORA_CI_TESTS[test_name] = (function, skipif)
        return function

    return _update_mapping


class DownstreamTestingFarmJobHelper:
    _koji_helper: Optional[KojiHelper] = None

    def __init__(
        self,
        service_config: ServiceConfig,
        project: GitProject,
        metadata: EventData,
        koji_build: KojiBuildTargetModel,
        celery_task: Optional[CeleryTask] = None,
    ):
        self.service_config = service_config
        self.project = project
        self.metadata = metadata
        self.koji_build = koji_build
        self.celery_task = celery_task
        self._tft_client: Optional[TestingFarmClient] = None
        self._ci_helper: Optional[FedoraCIHelper] = None

    @property
    def koji_helper(self):
        if not self._koji_helper:
            self._koji_helper = KojiHelper()
        return self._koji_helper

    @staticmethod
    def get_fedora_ci_tests(
        service_config: ServiceConfig, project: GitProject, metadata: EventData
    ) -> list[str]:
        """
        Gets relevant Fedora CI tests registered using the `@implements_fedora_ci_test()` decorator.
        In case of valid comment command, if a test name is specified as an argument to the command
        and such a test is registered, only that test is returned, otherwise all registered tests
        are returned. An empty list is returned in case of invalid command.

        Args:
            service_config: Service config.
            project: Git project.
            metadata: Event metadata.

        Returns:
            List of registered Fedora CI test names.
        """
        all_tests = [
            name
            for name, (_, skipif) in FEDORA_CI_TESTS.items()
            if not skipif or not skipif(service_config, project, metadata)
        ]
        if metadata.event_type != pagure.pr.Comment.event_type():
            return all_tests
        # TODO: remove this once Fedora CI has its own instances and comment_command_prefixes
        # comment_command_prefixes for Fedora CI are /packit-ci and /packit-ci-stg
        comment_command_prefix = (
            "/packit-ci-stg"
            if service_config.comment_command_prefix.endswith("-stg")
            else "/packit-ci"
        )
        commands = get_packit_commands_from_comment(
            metadata.event_dict.get("comment"), comment_command_prefix
        )
        if not commands:
            return []
        if len(commands) > 1 and commands[1] in all_tests:
            return [commands[1]]
        return all_tests

    @property
    def api_url(self) -> str:
        return (
            "https://prod.packit.dev/api"
            if self.service_config.deployment == Deployment.prod
            else "https://stg.packit.dev/api"
        )

    @property
    def tft_client(self) -> TestingFarmClient:
        if not self._tft_client:
            self._tft_client = TestingFarmClient(
                api_url=self.service_config.testing_farm_api_url,
                token=self.service_config.testing_farm_secret,
            )
        return self._tft_client

    @property
    def ci_helper(self) -> FedoraCIHelper:
        if not self._ci_helper:
            self._ci_helper = FedoraCIHelper(
                project=self.project,
                metadata=self.metadata,
                target_branch=self.koji_build.target,
            )
        return self._ci_helper

    @staticmethod
    def get_check_name(test_name: str) -> str:
        return f"Packit - {test_name} test(s)"

    def report(
        self,
        test_run: TFTTestRunTargetModel,
        state: BaseCommitStatus,
        description: str,
        url: Optional[str] = None,
    ):
        self.ci_helper.report(
            state=state,
            description=description,
            url=url if url else "",
            check_name=self.get_check_name(test_run.data["fedora_ci_test"]),
        )

    def run_testing_farm(
        self,
        test_run: TFTTestRunTargetModel,
    ) -> TaskResults:
        logger.debug(
            f"Running testing farm for test {test_run.data['fedora_ci_test']}.",
        )

        self.report(
            test_run=test_run,
            state=BaseCommitStatus.running,
            description="Submitting the tests ...",
        )

        return self.prepare_and_send_tf_request(test_run)

    def prepare_and_send_tf_request(
        self,
        test_run: TFTTestRunTargetModel,
    ) -> TaskResults:
        """
        Prepare the payload that will be sent to Testing Farm, submit it to
        TF API and handle the response (report whether the request was sent
        successfully, store the new TF run in DB or retry if needed).
        """
        logger.info("Preparing testing farm request...")

        compose = self.tft_client.distro2compose(test_run.target)

        if not compose:
            msg = "We were not able to map distro to TF compose."
            return TaskResults(success=False, details={"msg": msg})

        payload = FEDORA_CI_TESTS[test_run.data["fedora_ci_test"]][0](
            self, distro=test_run.target, compose=compose
        )

        endpoint = "requests"

        response = self._tft_client.send_testing_farm_request(
            endpoint=endpoint,
            method="POST",
            data=payload,
        )

        if response.status_code != 200:
            return self._handle_tf_submit_failure(
                test_run=test_run,
                response=response,
                payload=payload,
            )

        return self._handle_tf_submit_successful(
            test_run=test_run,
            response=response,
        )

    @implements_fedora_ci_test("installability")
    def _payload_installability(self, distro: str, compose: str) -> dict:
        git_repo = "https://github.com/fedora-ci/installability-pipeline.git"
        git_ref = (
            commands.run_command(["git", "ls-remote", git_repo, "HEAD"], output=True)
            .stdout.strip()
            .split()[0]
        )

        if distro == "fedora-rawhide":
            # profile names are in "fedora-N" format
            # extract current rawhide version number from its candidate tag
            candidate_tag = self.koji_helper.get_candidate_tag("rawhide")
            profile = re.sub(r"f(\d+)(-.*)?", r"fedora-\1", candidate_tag)
        else:
            profile = distro

        return {
            "test": {
                "tmt": {
                    "url": git_repo,
                    "ref": git_ref,
                },
            },
            "environments": [
                {
                    "arch": "x86_64",
                    "os": {"compose": compose},
                    "variables": {
                        "PROFILE_NAME": profile,
                        "TASK_ID": self.koji_build.task_id,
                    },
                },
            ],
            "notification": {
                "webhook": {
                    "url": f"{self.api_url}/testing-farm/results",
                    "token": self.service_config.testing_farm_secret,
                },
            },
        }

    @staticmethod
    def is_fmf_configured(project: GitProject, metadata: EventData) -> bool:
        try:
            project.get_file_content(
                path=".fmf/version",
                ref=metadata.commit_sha,
            )
        except FileNotFoundError:
            return False
        return True

    @implements_fedora_ci_test(
        "custom",
        skipif=lambda _, project, metadata: not DownstreamTestingFarmJobHelper.is_fmf_configured(
            project, metadata
        ),
    )
    def _payload_custom(self, distro: str, compose: str) -> dict:
        return {
            "test": {
                "tmt": {
                    "url": self.project.get_pr(self.metadata.pr_id)
                    .source_project.get_git_urls()
                    .get("git"),
                    "ref": self.metadata.commit_sha,
                },
            },
            "environments": [
                {
                    "arch": "x86_64",
                    "os": {"compose": compose},
                    "variables": {
                        "KOJI_TASK_ID": self.koji_build.task_id,
                    },
                    "artifacts": [
                        {"id": self.koji_build.task_id, "type": "fedora-koji-build"},
                    ],
                    "tmt": {
                        "context": {
                            "distro": distro,
                            "arch": "x86_64",
                            "trigger": "commit",
                            "initiator": "fedora-ci",
                        }
                    },
                },
            ],
            "notification": {
                "webhook": {
                    "url": f"{self.api_url}/testing-farm/results",
                    "token": self.service_config.testing_farm_secret,
                },
            },
        }

    def _handle_tf_submit_successful(
        self,
        test_run: TFTTestRunTargetModel,
        response: RequestResponse,
    ):
        """
        Create the model for the TF run in the database and report
        the state to user.
        """
        pipeline_id = response.json()["id"]
        logger.info(f"Request {pipeline_id} submitted to testing farm.")
        test_run.set_pipeline_id(pipeline_id)

        self.report(
            test_run=test_run,
            state=BaseCommitStatus.running,
            description="Tests have been submitted ...",
            url=get_testing_farm_info_url(test_run.id),
        )

        return TaskResults(success=True, details={})

    def _handle_tf_submit_failure(
        self,
        test_run: TFTTestRunTargetModel,
        response: RequestResponse,
        payload: dict,
    ) -> TaskResults:
        """
        Retry the task and report it to user or report the failure state to user.
        """
        # something went wrong
        if response.json() and "errors" in response.json():
            errors = response.json()["errors"]
            # specific case, unsupported arch
            if not (msg := nested_get(errors, "environments", "0", "arch")):
                msg = "There was an error in the API request"
        else:
            msg = response.reason
            if not self.celery_task.is_last_try():
                return self._retry_on_submit_failure(test_run, response.reason)

        test_run.set_status(TestingFarmResult.error)
        logger.error(f"{msg}, {self.tft_client.payload_without_token(payload)}")
        self.report(
            test_run=test_run,
            state=BaseCommitStatus.failure,
            description=f"Failed to submit tests: {msg}.",
        )
        return TaskResults(success=False, details={"msg": msg})

    def _retry_on_submit_failure(
        self,
        test_run: TFTTestRunTargetModel,
        message: str,
    ) -> TaskResults:
        """
        Retry when there was a failure when submitting TF tests.

        Args:
            message: message to report to the user
        """
        test_run.set_status(TestingFarmResult.retry)
        interval = BASE_RETRY_INTERVAL_IN_MINUTES_FOR_OUTAGES * 2**self.celery_task.retries

        self.report(
            test_run=test_run,
            state=BaseCommitStatus.pending,
            description="Failed to submit tests. The task will be"
            f" retried in {interval} {'minute' if interval == 1 else 'minutes'}.",
        )
        kargs = self.celery_task.task.request.kwargs.copy()
        kargs["testing_farm_target_id"] = test_run.id
        self.celery_task.retry(delay=interval * 60, kargs=kargs)
        return TaskResults(
            success=True,
            details={
                "msg": f"Task will be retried because of failure when submitting tests: {message}",
            },
        )

    def get_running_jobs(self) -> Iterable[str]:
        # [TODO] Do not cancel TF runs on the downstream yet, to be decided later on
        pass
