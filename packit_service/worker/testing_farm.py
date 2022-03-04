# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from typing import Dict, Any, Optional, Set, List, Union

import requests
from ogr.abstract import GitProject, PullRequest
from ogr.utils import RequestResponse
from packit.config import JobType, JobConfigTriggerType
from packit.config.job_config import JobConfig
from packit.config.package_config import PackageConfig
from packit.exceptions import PackitConfigException
from packit.utils import nested_get

from packit_service.config import ServiceConfig
from packit_service.constants import (
    TESTING_FARM_INSTALLABILITY_TEST_URL,
    TESTING_FARM_INSTALLABILITY_TEST_REF,
)
from packit_service.models import (
    CoprBuildTargetModel,
    TFTTestRunTargetModel,
    TestingFarmResult,
    PipelineModel,
)
from packit_service.sentry_integration import send_to_sentry
from packit_service.utils import get_package_nvrs
from packit_service.worker.events import EventData
from packit_service.service.urls import get_testing_farm_info_url
from packit_service.worker.build import CoprBuildJobHelper
from packit_service.worker.reporting import BaseCommitStatus
from packit_service.worker.result import TaskResults

logger = logging.getLogger(__name__)


class TestingFarmJobHelper(CoprBuildJobHelper):
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
        )

        self.session = requests.session()
        adapter = requests.adapters.HTTPAdapter(max_retries=5)
        self.insecure = False
        self.session.mount("https://", adapter)
        self._tft_api_url: str = ""
        self._tft_token: str = ""
        self.__pr = None

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
                if self.job_config.metadata.use_internal_tf
                else self.service_config.testing_farm_secret
            )
        return self._tft_token

    @property
    def skip_build(self) -> bool:
        return self.job_config.metadata.skip_build

    @property
    def fmf_url(self) -> str:
        return (
            self.job_config.metadata.fmf_url
            or self.project.get_pr(self.metadata.pr_id).source_project.get_web_url()
        )

    @property
    def fmf_ref(self) -> str:
        if self.job_config.metadata.fmf_url:
            return self.job_config.metadata.fmf_ref

        return self.metadata.commit_sha

    @property
    def source_branch_sha(self) -> Optional[str]:
        return self._pr.head_commit if self._pr else None

    @property
    def target_branch_sha(self) -> Optional[str]:
        return self._pr.target_branch_head_commit if self._pr else None

    @property
    def target_branch(self) -> Optional[str]:
        return self._pr.target_branch if self._pr else None

    @property
    def source_branch(self) -> Optional[str]:
        return self._pr.source_branch if self._pr else None

    @property
    def target_project_url(self) -> Optional[str]:
        return self._pr.target_project.get_web_url() if self._pr else None

    @property
    def source_project_url(self) -> Optional[str]:
        return self._pr.source_project.get_web_url() if self._pr else None

    @property
    def _pr(self) -> Optional[PullRequest]:
        if not self.metadata.pr_id:
            return None
        if not self.__pr:
            self.__pr = self.project.get_pr(int(self.metadata.pr_id))
        return self.__pr

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

    def _payload(
        self,
        target: str,
        artifact: Optional[Dict[str, Union[List[str], str]]] = None,
        build: Optional["CoprBuildTargetModel"] = None,
    ) -> dict:
        """Prepare a Testing Farm request payload.

        Testing Farm API: https://testing-farm.gitlab.io/api/

        Currently we use the same secret to authenticate both,
        packit service (when sending request to testing farm)
        and testing farm (when sending notification to packit service's webhook).
        We might later use a different secret for those use cases.

        Args:
            chroot: Target TF chroot.
            artifact: Optional artifacts, e.g. list of package NEVRAs
            build: The related copr build.
        """
        distro, arch = target.rsplit("-", 1)
        compose = self.distro2compose(distro, arch)
        fmf = {"url": self.fmf_url}
        if self.fmf_ref:
            fmf["ref"] = self.fmf_ref

        if build is not None:
            build_log_url = build.build_logs_url
            srpm_build = build.get_srpm_build()
            srpm_url = srpm_build.url
            if build.built_packages:
                nvr_data = build.built_packages[0]
                nvr = f"{nvr_data['name']}-{nvr_data['version']}-{nvr_data['release']}"
            else:
                nvr = None
        else:
            build_log_url = nvr = srpm_url = None

        predefined_environment = {
            "PACKIT_FULL_REPO_NAME": self.project.full_repo_name,
            "PACKIT_UPSTREAM_NAME": self.job_config.upstream_package_name,
            "PACKIT_UPSTREAM_URL": self.job_config.upstream_project_url,
            "PACKIT_DOWNSTREAM_NAME": self.job_config.downstream_package_name,
            "PACKIT_DOWNSTREAM_URL": self.job_config.downstream_project_url
            if self.job_config.downstream_package_name
            else None,
            "PACKIT_PACKAGE_NAME": self.job_config.downstream_package_name,
            "PACKIT_PACKAGE_NVR": nvr,
            "PACKIT_BUILD_LOG_URL": build_log_url,
            "PACKIT_SRPM_URL": srpm_url,
            "PACKIT_COMMIT_SHA": self.metadata.commit_sha,
            "PACKIT_SOURCE_SHA": self.source_branch_sha,
            "PACKIT_TARGET_SHA": self.target_branch_sha,
            "PACKIT_SOURCE_BRANCH": self.source_branch,
            "PACKIT_TARGET_BRANCH": self.target_branch,
            "PACKIT_SOURCE_URL": self.source_project_url,
            "PACKIT_TARGET_URL": self.target_project_url,
        }
        predefined_environment = {
            k: v for k, v in predefined_environment.items() if v is not None
        }
        # User-defined variables have priority
        metadata = self.job_config.metadata
        env_variables = metadata.env if hasattr(metadata, "env") else {}
        predefined_environment.update(env_variables)

        environment: Dict[str, Any] = {
            "arch": arch,
            "os": {"compose": compose},
            "tmt": {"context": {"distro": distro, "arch": arch, "trigger": "commit"}},
            "variables": predefined_environment,
        }
        if artifact:
            environment["artifacts"] = [artifact]

        return {
            "api_key": self.tft_token,
            "test": {
                "fmf": fmf,
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

    def _payload_install_test(self, build_id: int, target: str) -> dict:
        """
        If the project doesn't use fmf, but still wants to run tests in TF.
        TF provides 'installation test', we request it in ['test']['fmf']['url'].
        We don't specify 'artifacts' as in _payload(), but 'variables'.
        """
        copr_build = CoprBuildTargetModel.get_by_build_id(build_id)
        distro, arch = target.rsplit("-", 1)
        compose = self.distro2compose(distro, arch)
        return {
            "api_key": self.service_config.testing_farm_secret,
            "test": {
                "fmf": {
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

    def is_fmf_configured(self) -> bool:

        if self.job_config.metadata.fmf_url is not None:
            return True

        try:
            self.project.get_file_content(
                path=".fmf/version", ref=self.metadata.commit_sha
            )
            return True
        except FileNotFoundError:
            return False

    def distro2compose(self, distro: str, arch: str) -> str:
        """
        Create a compose string from distro, e.g. fedora-33 -> Fedora-33
        https://api.dev.testing-farm.io/v0.1/composes

        The internal TF has a different set and behaves differently:
        * Fedora-3x -> Fedora-3x-Updated
        * CentOS-x ->  CentOS-x-latest
        * CentOS-Stream-8 -> RHEL-8.5.0-Nightly
        """
        compose = (
            distro.title()
            .replace("Centos", "CentOS")
            .replace("Rhel", "RHEL")
            .replace("Oraclelinux", "Oracle-Linux")
        )
        if compose == "CentOS-Stream":
            compose = "CentOS-Stream-8"

        if arch == "aarch64":
            # TF has separate composes for aarch64 architecture
            compose += "-aarch64"

        if self.job_config.metadata.use_internal_tf:
            # Internal TF does not have own endpoint for composes
            # This should be solved on the TF side.
            if compose == "Fedora-Rawhide":
                return "Fedora-Rawhide-Nightly"
            if compose.startswith("Fedora-"):
                return f"{compose}-Updated"
            if compose == "CentOS-Stream-8":
                return "RHEL-8.5.0-Nightly"
            if compose.startswith("CentOS"):
                return f"{compose}-latest"
            if compose == "RHEL-6":
                return "RHEL-6-LatestReleased"
            if compose == "RHEL-7":
                return "RHEL-7-LatestReleased"
            if compose == "RHEL-8":
                return "RHEL-8.5.0-Nightly"
            if compose == "Oracle-Linux-7":
                return "Oracle-Linux-7.9"
            if compose == "Oracle-Linux-8":
                return "Oracle-Linux-8.5"
        else:
            response = self.send_testing_farm_request(endpoint="composes")
            if response.status_code == 200:
                # {'composes': [{'name': 'CentOS-Stream-8'}, {'name': 'Fedora-Rawhide'}]}
                composes = [c["name"] for c in response.json()["composes"]]
                if compose not in composes:
                    logger.error(f"Can't map {compose} (from {distro}) to {composes}")

        return compose

    def report_missing_build_chroot(self, chroot: str):
        self.report_status_to_test_for_chroot(
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
        if not copr_builds:
            return None

        return list(copr_builds)[0]

    def run_testing_farm(
        self, target: str, build: Optional["CoprBuildTargetModel"]
    ) -> TaskResults:
        if target not in self.tests_targets:
            # Leaving here just to be sure that we will discover this situation if it occurs.
            # Currently not possible to trigger this situation.
            msg = f"Target '{target}' not defined for tests but triggered."
            logger.error(msg)
            send_to_sentry(PackitConfigException(msg))
            return TaskResults(
                success=False,
                details={"msg": msg},
            )
        chroot = self.test_target2build_target(target)

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
            self.job_config.metadata.use_internal_tf
            and f"{self.project.service.hostname}/{self.project.full_repo_name}"
            not in self.service_config.enabled_projects_for_internal_tf
        ):
            self.report_status_to_test_for_test_target(
                state=BaseCommitStatus.neutral,
                description="Internal TF not allowed for this project. Let us know.",
                target=target,
                url="https://packit.dev/#contact",
            )
            return TaskResults(
                success=True,
                details={"msg": "Project not allowed to use internal TF."},
            )

        self.report_status_to_test_for_test_target(
            state=BaseCommitStatus.running,
            description=f"{'Build succeeded. ' if not self.skip_build else ''}"
            f"Submitting the tests ...",
            target=target,
        )

        logger.info("Sending testing farm request...")

        if self.is_fmf_configured():
            artifact = (
                self._artifact(chroot, int(build.build_id), build.built_packages)
                if not self.skip_build
                else None
            )
            payload = self._payload(target, artifact, build)
        elif not self.is_fmf_configured() and not self.skip_build:
            payload = self._payload_install_test(int(build.build_id), target)
        else:
            return TaskResults(
                success=True, details={"msg": "No actions for TestingFarmHandler."}
            )
        endpoint = "requests"
        logger.debug(f"POSTing {payload} to {self.tft_api_url}{endpoint}")
        req = self.send_testing_farm_request(
            endpoint=endpoint,
            method="POST",
            data=payload,
        )
        logger.debug(f"Request sent: {req}")

        if not req:
            msg = "Failed to post request to testing farm API."
            logger.debug("Failed to post request to testing farm API.")
            self.report_status_to_test_for_test_target(
                state=BaseCommitStatus.error,
                description=msg,
                target=target,
            )
            return TaskResults(success=False, details={"msg": msg})

        # success set check on pending
        if req.status_code != 200:
            # something went wrong
            if req.json() and "errors" in req.json():
                msg = req.json()["errors"]
                # specific case, unsupported arch
                if nested_get(req.json(), "errors", "environments", "0", "arch"):
                    msg = req.json()["errors"]["environments"]["0"]["arch"]
            else:
                msg = f"Failed to submit tests: {req.reason}"
            logger.error(msg)
            self.report_status_to_test_for_test_target(
                state=BaseCommitStatus.failure,
                description=msg,
                target=target,
            )
            return TaskResults(success=False, details={"msg": msg})

        # Response: {"id": "9fa3cbd1-83f2-4326-a118-aad59f5", ...}

        pipeline_id = req.json()["id"]
        logger.debug(
            f"Submitted ({req.status_code}) to testing farm as request {pipeline_id}"
        )

        run_model = (
            PipelineModel.create(
                type=self.db_trigger.job_trigger_model_type,
                trigger_id=self.db_trigger.id,
            )
            if self.skip_build
            else build.runs[-1]
        )

        created_model = TFTTestRunTargetModel.create(
            pipeline_id=pipeline_id,
            commit_sha=self.metadata.commit_sha,
            status=TestingFarmResult.new,
            target=target,
            web_url=None,
            run_model=run_model,
            # In _payload() we ask TF to test commit_sha of fork (PR's source).
            # Store original url. If this proves to work, make it a separate column.
            data={"base_project_url": self.project.get_web_url()},
        )

        self.report_status_to_test_for_test_target(
            state=BaseCommitStatus.running,
            description="Tests have been submitted ...",
            url=get_testing_farm_info_url(created_model.id),
            target=target,
        )

        return TaskResults(success=True, details={})

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
            raise Exception(f"Cannot connect to url: `{url}`.", er)
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
            package_config=PackageConfig(),
            project=None,
            metadata=None,
            db_trigger=None,
            job_config=JobConfig(
                # dummy values to be able to construct the object
                type=JobType.tests,
                trigger=JobConfigTriggerType.pull_request,
            ),
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
