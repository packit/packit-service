# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
import re
from re import Pattern
from typing import Dict, Any, Optional, Set, List, Union, Tuple, Callable

import requests

from ogr.abstract import GitProject
from ogr.utils import RequestResponse
from packit.config import JobConfig, PackageConfig
from packit.exceptions import PackitConfigException, PackitException
from packit.utils import nested_get
from packit_service.config import ServiceConfig
from packit_service.constants import (
    CONTACTS_URL,
    TESTING_FARM_INSTALLABILITY_TEST_URL,
    TESTING_FARM_INSTALLABILITY_TEST_REF,
    TESTING_FARM_EXTRA_PARAM_MERGED_SUBTREES,
    BASE_RETRY_INTERVAL_IN_MINUTES_FOR_OUTAGES,
    PUBLIC_TF_ARCHITECTURE_LIST,
    INTERNAL_TF_ARCHITECTURE_LIST,
    TESTING_FARM_ARTIFACTS_KEY,
)
from packit_service.models import (
    CoprBuildTargetModel,
    TFTTestRunTargetModel,
    TestingFarmResult,
    PullRequestModel,
    filter_most_recent_target_models_by_status,
    BuildStatus,
    ProjectEventModel,
)
from packit_service.sentry_integration import send_to_sentry
from packit_service.service.urls import get_testing_farm_info_url
from packit_service.utils import get_package_nvrs
from packit_service.worker.celery_task import CeleryTask
from packit_service.worker.events import (
    EventData,
    PullRequestCommentGithubEvent,
    MergeRequestCommentGitlabEvent,
    PullRequestCommentPagureEvent,
    PushGitHubEvent,
    PushGitlabEvent,
    PullRequestGithubEvent,
    MergeRequestGitlabEvent,
)
from packit_service.worker.helpers.build import CoprBuildJobHelper
from packit_service.worker.reporting import BaseCommitStatus
from packit_service.worker.result import TaskResults

logger = logging.getLogger(__name__)


class CommentArguments:
    """
    Parse arguments from trigger comment and provide the attributes to Testing Farm helper.
    """

    packit_command: str = None
    identifier: str = None
    labels: List[str] = None
    pr_argument: str = None

    def __init__(self, command_prefix: str, comment: str):
        if comment is None:
            return

        # Try to parse identifier argument from comment
        logger.debug(f"Parsing comment -> {comment}")
        logger.debug(f"Used command prefix -> {command_prefix}")

        match = re.search(
            r"^" + re.escape(command_prefix) + r"\s(?P<packit_command>\S+)", comment
        )
        if match:
            self.packit_command = match.group("packit_command")
            logger.debug(f"Parsed packit_command: {self.packit_command}")

        match = re.search(r"(--identifier|--id|-i)[\s=](?P<identifier>\S+)", comment)
        if match:
            self.identifier = match.group("identifier")
            logger.debug(f"Parsed test argument -> identifier: {self.identifier}")

        match = re.search(r"--labels[\s=](?P<labels>\S+)", comment)
        if match:
            self.labels = match.group("labels").split(",")
            logger.debug(f"Parsed test argument -> labels: {self.labels}")

        match = re.search(r"(?P<pr_arg>[^/\s]+/[^#]+#\d+)", comment)
        if match:
            self.pr_argument = match.group("pr_arg")
            logger.debug(f"Parsed test argument -> pr_argument: {self.pr_argument}")


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
        build_targets_override: Optional[Set[str]] = None,
        tests_targets_override: Optional[Set[str]] = None,
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
        self.session = requests.session()
        adapter = requests.adapters.HTTPAdapter(max_retries=5)
        self.insecure = False
        self.session.mount("https://", adapter)
        self._tft_api_url: str = ""
        self._tft_token: str = ""
        self.__pr = None
        self._copr_builds_from_other_pr: Optional[Dict[str, CoprBuildTargetModel]] = (
            None
        )
        self._test_check_names: Optional[List[str]] = None
        self._comment_arguments: Optional[CommentArguments] = None

    @property
    def tft_api_url(self) -> str:
        if not self._tft_api_url:
            self._tft_api_url = self.service_config.testing_farm_api_url
            if not self._tft_api_url.endswith("/"):
                self._tft_api_url += "/"
        return self._tft_api_url

    @property
    def tft_token(self) -> str:
        if not self._tft_token:
            # We have two tokens (=TF users), one for upstream and one for internal instance.
            # The URL is same and the instance choice is based on the TF user (=token)
            # we use in the payload.
            # To use internal instance,
            # project needs to be added to the `enabled_projects_for_internal_tf` list
            # in the service config.
            # This is checked in the run_testing_farm method.
            self._tft_token = (
                self.service_config.internal_testing_farm_secret
                if self.job_config.use_internal_tf
                else self.service_config.testing_farm_secret
            )
        return self._tft_token

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
            path = path.removesuffix("/")
            return path
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
        return (
            self.pull_request_object.head_commit if self.pull_request_object else None
        )

    @property
    def target_branch_sha(self) -> Optional[str]:
        return (
            self.pull_request_object.target_branch_head_commit
            if self.pull_request_object
            else None
        )

    @property
    def target_branch(self) -> Optional[str]:
        return (
            self.pull_request_object.target_branch if self.pull_request_object else None
        )

    @property
    def source_branch(self) -> Optional[str]:
        return (
            self.pull_request_object.source_branch if self.pull_request_object else None
        )

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
            PullRequestCommentGithubEvent.__name__,
            MergeRequestCommentGitlabEvent.__name__,
            PullRequestCommentPagureEvent.__name__,
        )

    def is_copr_build_comment_event(self) -> bool:
        return self.is_comment_event() and self.comment_arguments.packit_command in (
            "build",
            "copr-build",
        )

    def is_test_comment_event(self) -> bool:
        return (
            self.is_comment_event() and self.comment_arguments.packit_command == "test"
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
                PushGitHubEvent.__name__,
                PushGitlabEvent.__name__,
                PullRequestGithubEvent.__name__,
                MergeRequestGitlabEvent.__name__,
            )
            or self.is_copr_build_comment_event()
        )

    @property
    def copr_builds_from_other_pr(
        self,
    ) -> Optional[Dict[str, CoprBuildTargetModel]]:
        """
        Dictionary containing copr build target model for each chroot
        if the testing farm was triggered by a comment with PR argument
        and we store any Copr builds for the given PR, otherwise None.
        """
        if (
            not self._copr_builds_from_other_pr
            and self.is_test_comment_pr_argument_present()
        ):
            self._copr_builds_from_other_pr = self.get_copr_builds_from_other_pr()
        return self._copr_builds_from_other_pr

    @property
    def available_composes(self) -> Optional[Set[str]]:
        """
        Fetches available composes from the Testing Farm endpoint.

        Returns:
            Set of all available composes or `None` if error occurs.
        """
        endpoint = (
            f"composes/{'redhat' if self.job_config.use_internal_tf else 'public'}"
        )

        response = self.send_testing_farm_request(endpoint=endpoint)
        if response.status_code != 200:
            return None

        # {'composes': [{'name': 'CentOS-Stream-8'}, {'name': 'Fedora-Rawhide'}]}
        return {c["name"] for c in response.json()["composes"]}

    @staticmethod
    def _artifact(
        chroot: str, build_id: Optional[int], built_packages: Optional[List[Dict]]
    ) -> Dict[str, Union[List[str], str]]:
        artifact: Dict[str, Union[List[str], str]] = {
            "id": f"{build_id}:{chroot}",
            "type": "fedora-copr-build",
        }

        if built_packages:
            artifact["packages"] = get_package_nvrs(built_packages)

        return artifact

    @staticmethod
    def _payload_without_token(payload: Dict) -> Dict:
        """Return a copy of the payload with token/api_key removed."""
        payload_ = payload.copy()
        payload_.pop("api_key")
        payload_["notification"]["webhook"].pop("token")
        return payload_

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
            if (
                not self.custom_fmf
                and self.job_config.merge_pr_in_ci
                and self.target_branch_sha
            ):
                tmt["merge_sha"] = self.target_branch_sha

        if self.tmt_plan:
            tmt["name"] = self.tmt_plan

        return tmt

    @classmethod
    def _merge_payload_with_extra_params(cls, payload: Any, params: Any):
        def is_final(v):
            return not isinstance(v, list) and not isinstance(v, dict)

        if type(payload) != type(params):  # noqa: E721
            # Incompatible types, no way to merge this
            return

        if isinstance(params, dict):
            for key, value in params.items():
                if key not in payload or is_final(value):
                    payload[key] = value
                elif not is_final(value):
                    if key == TESTING_FARM_ARTIFACTS_KEY:
                        cls._handle_extra_artifacts(
                            payload, params[TESTING_FARM_ARTIFACTS_KEY]
                        )
                        continue
                    cls._merge_payload_with_extra_params(payload[key], params[key])

        elif isinstance(params, list):
            for payload_el, params_el in zip(payload, params):
                cls._merge_payload_with_extra_params(payload_el, params_el)

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
                "not adding them to payload."
            )

    def _payload(
        self,
        target: str,
        compose: str,
        artifacts: Optional[List[Dict[str, Union[List[str], str]]]] = None,
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
                f"{additional_build.owner}/{additional_build.project_name}"
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
            "PACKIT_TAG_NAME": (
                self.metadata.tag_name if self.metadata.tag_name else None
            ),
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
            "PACKIT_COPR_RPMS": (
                " ".join(packit_copr_rpms) if packit_copr_rpms else None
            ),
        }
        predefined_environment = {
            k: v for k, v in predefined_environment.items() if v is not None
        }
        # User-defined variables have priority
        env_variables = self.job_config.env if hasattr(self.job_config, "env") else {}
        predefined_environment.update(env_variables)

        environment: Dict[str, Any] = {
            "arch": arch,
            "os": {"compose": compose},
            "tmt": {
                "context": {
                    "distro": distro,
                    "arch": arch,
                    "trigger": "commit",
                    "initiator": "packit",
                }
            },
            "variables": predefined_environment,
        }
        if artifacts:
            environment["artifacts"] = artifacts

        if self.tf_post_install_script:
            environment["settings"] = {
                "provisioning": {"post_install_script": self.tf_post_install_script}
            }

        payload = {
            "api_key": self.tft_token,
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
                    "token": self.tft_token,
                },
            },
        }

        if hasattr(self.job_config, "tf_extra_params"):
            extra_params = self.job_config.tf_extra_params
        else:
            extra_params = {}
        # Merge only some subtrees, we do not want the user to override notification or api_key!
        for subtree in TESTING_FARM_EXTRA_PARAM_MERGED_SUBTREES:
            if subtree not in extra_params:
                continue
            if subtree not in payload:
                payload[subtree] = extra_params[subtree]
            else:
                self._merge_payload_with_extra_params(
                    payload[subtree], extra_params[subtree]
                )

        return payload

    def _payload_install_test(self, build_id: int, target: str, compose: str) -> dict:
        """
        If the project doesn't use tmt, but still wants to run tests in TF.
        TF provides 'installation test', we request it in ['test']['tmt']['url'].
        We don't specify 'artifacts' as in _payload(), but 'variables'.
        """
        copr_build = CoprBuildTargetModel.get_by_build_id(build_id)
        distro, arch = target.rsplit("-", 1)
        return {
            "api_key": self.service_config.testing_farm_secret,
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
                }
            ],
            "notification": {
                "webhook": {
                    "url": f"{self.api_url}/testing-farm/results",
                    "token": self.service_config.testing_farm_secret,
                },
            },
        }

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
                path=f"{self.fmf_path}/.fmf/version", ref=self.metadata.commit_sha
            )
            return True
        except FileNotFoundError:
            return False

    @staticmethod
    def is_compose_matching(compose_to_check: str, composes: Set[Pattern]) -> bool:
        """
        Check whether the compose matches any compose in the list of re-compiled
        composes.
        """
        return any(compose.fullmatch(compose_to_check) for compose in composes)

    def distro2compose(self, target: str) -> Optional[str]:
        """
        Create a compose string from distro, e.g. fedora-33 -> Fedora-33
        https://api.dev.testing-farm.io/v0.1/composes

        The internal TF has a different set and behaves differently:
        * Fedora-3x -> Fedora-3x-Updated
        * CentOS-x ->  CentOS-x-latest

        Returns:
            compose if we were able to map the distro to compose present
            in the list of available composes, otherwise None
        """
        composes = self.available_composes
        if composes is None:
            msg = "We were not able to get the available TF composes."
            logger.error(msg)
            self.report_status_to_tests_for_test_target(
                state=BaseCommitStatus.error,
                description=msg,
                target=target,
            )
            return None

        compiled_composes = {re.compile(compose) for compose in composes}
        distro, arch = target.rsplit("-", 1)

        # if the user precisely specified the compose via target
        # we should just use it instead of continuing below with our logic
        # some of those changes can change the target and result in a failure
        if self.is_compose_matching(distro, compiled_composes):
            logger.debug(
                f"Distro {distro} directly matches a compose in the compose list."
            )
            return distro

        compose = (
            distro.title()
            .replace("Centos", "CentOS")
            .replace("Rhel", "RHEL")
            .replace("Oraclelinux", "Oracle-Linux")
            .replace("Latest", "latest")
        )
        if compose == "CentOS-Stream":
            compose = "CentOS-Stream-8"

        if self.job_config.use_internal_tf:
            if self.is_compose_matching(compose, compiled_composes):
                return compose

            if compose == "Fedora-Rawhide":
                compose = "Fedora-Rawhide-Nightly"
            elif compose.startswith("Fedora-"):
                compose = f"{compose}-Updated"
            elif compose.startswith("CentOS") and len(compose) == len("CentOS-7"):
                # Attach latest suffix only to major versions:
                # CentOS-7 -> CentOS-7-latest
                # CentOS-8 -> CentOS-8-latest
                # CentOS-8.4 -> CentOS-8.4
                # CentOS-8-latest -> CentOS-8-latest
                # CentOS-Stream-8 -> CentOS-Stream-8
                compose = f"{compose}-latest"
            elif compose == "RHEL-6":
                compose = "RHEL-6-LatestReleased"
            elif compose == "RHEL-7":
                compose = "RHEL-7-LatestReleased"
            elif compose == "RHEL-8":
                compose = "RHEL-8.5.0-Nightly"
            elif compose == "Oracle-Linux-7":
                compose = "Oracle-Linux-7.9"
            elif compose == "Oracle-Linux-8":
                compose = "Oracle-Linux-8.6"

        if not self.is_compose_matching(compose, compiled_composes):
            msg = (
                f"The compose {compose} (from target {distro}) does not match any compose"
                f" in the list of available composes:\n{composes}. "
            )
            logger.error(msg)
            msg += (
                "Please, check the targets defined in your test job configuration. If you think"
                f" your configuration is correct, get in touch with [us]({CONTACTS_URL})."
            )
            description = (
                f"The compose {compose} is not available in the "
                f"{'internal' if self.job_config.use_internal_tf else 'public'} "
                f"Testing Farm infrastructure."
            )
            self.report_status_to_tests_for_test_target(
                state=BaseCommitStatus.error,
                description=description,
                target=target,
                markdown_content=msg,
            )
            return None

        return compose

    def _is_supported_architecture(self, target: str):
        distro, arch = target.rsplit("-", 1)
        supported_architectures = (
            INTERNAL_TF_ARCHITECTURE_LIST
            if self.job_config.use_internal_tf
            else PUBLIC_TF_ARCHITECTURE_LIST
        )
        if arch not in supported_architectures:
            msg = (
                f"The architecture {arch} is not in the list of "
                f"available architectures:\n{supported_architectures}. "
            )
            logger.error(msg)
            msg += (
                "Please, check the targets defined in your test job configuration. If you think"
                f" your configuration is correct, get in touch with [us]({CONTACTS_URL})."
            )
            description = (
                f"The architecture {arch} is not available in the "
                f"{'internal' if self.job_config.use_internal_tf else 'public'} "
                f"Testing Farm infrastructure."
            )
            self.report_status_to_tests_for_test_target(
                state=BaseCommitStatus.error,
                description=description,
                target=target,
                markdown_content=msg,
            )
            return False
        return True

    def report_missing_build_chroot(self, chroot: str):
        self.report_status_to_tests_for_chroot(
            state=BaseCommitStatus.error,
            description=f"No build defined for the target '{chroot}'.",
            chroot=chroot,
        )

    def get_latest_copr_build(
        self, target: str, commit_sha: str
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
    ) -> List[Dict]:
        """
        Get the artifacts list from the build (if the skip_build option is not defined)
        and additional build (from other PR) if present.
        """
        artifacts = []
        if not self.skip_build:
            artifacts.append(
                self._artifact(chroot, int(build.build_id), build.built_packages)
            )

        if additional_build:
            artifacts.append(
                self._artifact(
                    chroot,
                    int(additional_build.build_id),
                    additional_build.built_packages,
                )
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
            logger.error(msg)
            send_to_sentry(PackitConfigException(msg))
            return TaskResults(
                success=False,
                details={"msg": msg},
            )
        chroot = self.test_target2build_target(test_run.target)
        logger.debug(
            f"Running testing farm for target {test_run.target}, chroot={chroot}."
        )

        if not self.skip_build and chroot not in self.build_targets:
            self.report_missing_build_chroot(chroot)
            return TaskResults(
                success=False,
                details={
                    "msg": f"Target '{chroot}' not defined for build. "
                    "Cannot run tests without build."
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
                    "msg": "No latest successful Copr build from the other PR found."
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

        if not self._is_supported_architecture(test_run.target):
            msg = "Not supported architecture."
            return TaskResults(success=True, details={"msg": msg})

        compose = self.distro2compose(test_run.target)

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
                build_id=int(build.build_id), target=test_run.target, compose=compose
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

        response = self.send_testing_farm_request(
            endpoint=endpoint,
            method="POST",
            data=payload,
        )

        if not response:
            return self._handle_tf_submit_no_response(
                test_run=test_run, target=test_run.target, payload=payload
            )

        if response.status_code != 200:
            return self._handle_tf_submit_failure(
                test_run=test_run, response=response, payload=payload
            )

        return self._handle_tf_submit_successful(
            test_run=test_run,
            response=response,
            additional_build=additional_build,
        )

    def send_testing_farm_request(
        self, endpoint: str, method: str = None, params: dict = None, data=None
    ) -> RequestResponse:
        method = method or "GET"
        url = f"{self.tft_api_url}{endpoint}"
        try:
            response = self.get_raw_request(
                method=method, url=url, params=params, data=data
            )
        except requests.exceptions.ConnectionError as er:
            logger.error(er)
            raise PackitException(f"Cannot connect to url: `{url}`") from er
        return response

    def get_raw_request(
        self,
        url,
        method="GET",
        params=None,
        data=None,
    ) -> RequestResponse:
        response = self.session.request(
            method=method,
            url=url,
            params=params,
            json=data,
            verify=not self.insecure,
        )

        try:
            json_output = response.json()
        except ValueError:
            logger.debug(response.text)
            json_output = None

        return RequestResponse(
            status_code=response.status_code,
            ok=response.ok,
            content=response.content,
            json=json_output,
            reason=response.reason,
        )

    @classmethod
    def get_request_details(cls, request_id: str) -> Dict[str, Any]:
        """Testing Farm sends only request/pipeline id in a notification.
        We need to get more details ourselves."""
        self = cls(
            service_config=ServiceConfig.get_service_config(),
            package_config=None,
            project=None,
            metadata=None,
            db_project_event=None,
            job_config=None,
        )

        response = self.send_testing_farm_request(
            endpoint=f"requests/{request_id}", method="GET"
        )
        if not response or response.status_code != 200:
            msg = f"Failed to get request/pipeline {request_id} details from TF. {response.reason}"
            logger.error(msg)
            return {}

        details = response.json()
        # logger.debug(f"Request/pipeline {request_id} details: {details}")

        return details

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

    def _handle_tf_submit_no_response(
        self, test_run: TFTTestRunTargetModel, target: str, payload: dict
    ):
        """
        Retry the task and report it to user or report the error state to user.
        """
        msg = "Failed to post request to testing farm API."
        if not self.celery_task.is_last_try():
            return self._retry_on_submit_failure(test_run, msg)

        logger.error(f"{msg} {self._payload_without_token(payload)}")
        self.report_status_to_tests_for_test_target(
            state=BaseCommitStatus.error,
            description=msg,
            target=target,
        )
        return TaskResults(success=False, details={"msg": msg})

    def _handle_tf_submit_failure(
        self, test_run: TFTTestRunTargetModel, response: RequestResponse, payload: dict
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
        logger.error(f"{msg}, {self._payload_without_token(payload)}")
        self.report_status_to_tests_for_test_target(
            state=BaseCommitStatus.failure,
            description=f"Failed to submit tests: {msg}.",
            target=test_run.target,
            markdown_content=markdown_content,
        )
        return TaskResults(success=False, details={"msg": msg})

    def _retry_on_submit_failure(
        self, test_run: TFTTestRunTargetModel, message: str
    ) -> TaskResults:
        """
        Retry when there was a failure when submitting TF tests.

        Args:
            message: message to report to the user
        """
        test_run.set_status(TestingFarmResult.retry)
        interval = (
            BASE_RETRY_INTERVAL_IN_MINUTES_FOR_OUTAGES * 2**self.celery_task.retries
        )

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
                "msg": f"Task will be retried because of failure when submitting tests: {message}"
            },
        )

    def get_copr_builds_from_other_pr(
        self,
    ) -> Optional[Dict[str, CoprBuildTargetModel]]:
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
        else:
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
                f"No copr builds for {project_url} and PR ID {pr_id} found in DB."
            )
            return None

        successful_most_recent_builds = filter_most_recent_target_models_by_status(
            models=copr_builds, statuses_to_filter_with=[BuildStatus.success]
        )

        return self._construct_copr_builds_from_other_pr_dict(
            successful_most_recent_builds
        )

    def _parse_comment_pr_argument(self) -> Optional[Tuple[str, str, str]]:
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
                f"{self.comment_arguments.pr_argument} with '#'."
            )
            return None

        pr_id = pr_argument_parts[1]
        namespace_repo = pr_argument_parts[0].split("/")
        if len(namespace_repo) != 2:
            logger.debug(
                "Unexpected format of the test argument: "
                f"not able to split the test argument "
                f"{self.comment_arguments.pr_argument} with '/'."
            )
            return None
        namespace, repo = namespace_repo

        logger.debug(
            f"Parsed test argument -> namespace: {namespace}, repo: {repo}, PR ID: {pr_id}"
        )

        return namespace, repo, pr_id

    def _construct_copr_builds_from_other_pr_dict(
        self, successful_most_recent_builds
    ) -> Optional[Dict[str, CoprBuildTargetModel]]:
        """
        Construct a dictionary that will contain for each build target name
        a build target model from the given models if there is one
        with matching target name.

        Args:
            successful_most_recent_builds: models to get the values from

        Returns:
            dict
        """
        result: Dict[str, CoprBuildTargetModel] = {}

        for build_target in self.build_targets_for_tests:
            additional_build = [
                build
                for build in successful_most_recent_builds
                if build.target == build_target
            ]
            result[build_target] = additional_build[0] if additional_build else None

        logger.debug(f"Additional builds dictionary: {result}")

        return result

    @property
    def configured_tests_targets(self) -> Set[str]:
        """
        Return the configured targets for the job.
        """
        return self.configured_targets_for_tests_job(self.job_config)

    @property
    def tests_targets(self) -> Set[str]:
        """
        Return valid test targets (mapped) to test in for the job
        (considering the overrides).
        """
        return self.tests_targets_for_test_job(self.job_config)

    def get_test_check(self, chroot: str = None) -> str:
        return self.get_test_check_cls(
            chroot,
            self.project_event_identifier_for_status,
            self.job_config.identifier,
            package=self.get_package_name(),
            template=self.job_config.status_name_template,
        )

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

    def test_target2build_target(self, test_target: str) -> str:
        """
        Return build target to be built for a given test target
        (from configuration or from default mapping).
        """
        return self.test_target2build_target_for_test_job(test_target, self.job_config)

    @property
    def build_targets_for_tests(self) -> Set[str]:
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
        markdown_content: str = None,
        links_to_external_services: Optional[Dict[str, str]] = None,
        update_feedback_time: Callable = None,
    ) -> None:
        if chroot in self.build_targets_for_tests:
            test_targets = self.build_target2test_targets_for_test_job(
                chroot, self.job_config
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
        markdown_content: str = None,
        links_to_external_services: Optional[Dict[str, str]] = None,
        update_feedback_time: Callable = None,
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
        markdown_content: str = None,
        links_to_external_services: Optional[Dict[str, str]] = None,
        update_feedback_time: Callable = None,
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
        markdown_content: str = None,
        links_to_external_services: Optional[Dict[str, str]] = None,
        update_feedback_time: Callable = None,
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
