# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from typing import Dict, Any, Optional, Tuple

import requests
from ogr.abstract import CommitStatus, GitProject
from ogr.utils import RequestResponse
from packit.config import JobType, JobConfigTriggerType
from packit.config.job_config import JobConfig
from packit.config.package_config import PackageConfig
from packit.exceptions import PackitConfigException
from packit.utils import nested_get

from packit_service.config import ServiceConfig
from packit_service.constants import TESTING_FARM_INSTALLABILITY_TEST_URL
from packit_service.models import CoprBuildModel, TFTTestRunModel, TestingFarmResult
from packit_service.sentry_integration import send_to_sentry
from packit_service.worker.events import EventData
from packit_service.service.urls import get_testing_farm_info_url
from packit_service.worker.build import CoprBuildJobHelper
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
    ):
        super().__init__(
            service_config=service_config,
            package_config=package_config,
            project=project,
            metadata=metadata,
            db_trigger=db_trigger,
            job_config=job_config,
        )

        self.session = requests.session()
        adapter = requests.adapters.HTTPAdapter(max_retries=5)
        self.insecure = False
        self.session.mount("https://", adapter)
        self._tft_api_url: str = ""
        self._tft_token: str = ""

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
    def fmf_url(self):
        return (
            self.job_config.metadata.fmf_url
            or self.project.get_pr(self.metadata.pr_id).source_project.get_web_url()
        )

    @property
    def fmf_ref(self):
        if self.job_config.metadata.fmf_url:
            return self.job_config.metadata.fmf_ref

        return self.metadata.commit_sha

    def _payload(self, build_id: int, chroot: str) -> dict:
        """
        Testing Farm API: https://testing-farm.gitlab.io/api/

        Currently we use the same secret to authenticate both,
        packit service (when sending request to testing farm)
        and testing farm (when sending notification to packit service's webhook).
        We might later use a different secret for those use cases.

        """
        distro, arch = self.chroot2distro_arch(chroot)
        compose = self.distro2compose(distro)
        fmf = {"url": self.fmf_url}
        if self.fmf_ref:
            fmf["ref"] = self.fmf_ref

        return {
            "api_key": self.tft_token,
            "test": {
                "fmf": fmf,
            },
            "environments": [
                {
                    "arch": arch,
                    "os": {"compose": compose},
                    "artifacts": [
                        {
                            "id": f"{build_id}:{chroot}",
                            "type": "fedora-copr-build",
                        }
                    ],
                    "tmt": {
                        "context": {"distro": distro, "arch": arch, "trigger": "commit"}
                    },
                }
            ],
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

    def _payload_install_test(self, build_id: int, chroot: str) -> dict:
        """
        If the project doesn't use fmf, but still wants to run tests in TF.
        TF provides 'installation test', we request it in ['test']['fmf']['url'].
        We don't specify 'artifacts' as in _payload(), but 'variables'.
        """
        copr_build = CoprBuildModel.get_by_build_id(build_id)
        distro, arch = self.chroot2distro_arch(chroot)
        compose = self.distro2compose(distro)
        return {
            "api_key": self.service_config.testing_farm_secret,
            "test": {
                "fmf": {
                    "url": TESTING_FARM_INSTALLABILITY_TEST_URL,
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

    def chroot2distro_arch(self, chroot: str) -> Tuple[str, str]:
        """Get distro and arch from chroot."""
        distro, arch = chroot.rsplit("-", 1)

        if self.job_config.metadata.use_internal_tf:
            epel_mapping = {
                "epel-7": "rhel-7",
                "epel-8": "rhel-8",
            }
        else:
            epel_mapping = {
                "epel-7": "centos-7",
                "epel-8": "centos-stream-8",
            }

        distro = epel_mapping.get(distro, distro)
        return distro, arch

    def distro2compose(self, distro: str) -> str:
        """
        Create a compose string from distro, e.g. fedora-33 -> Fedora-33
        https://api.dev.testing-farm.io/v0.1/composes

        The internal TF has a different set and behaves differently:
        * Fedora-3x -> Fedora-3x-Updated
        * CentOS-x ->  CentOS-x-latest
        * CentOS-Stream-8 -> RHEL-8.5.0-Nightly
        """
        compose = distro.title().replace("Centos", "CentOS").replace("Rhel", "RHEL")
        if compose == "CentOS-Stream":
            compose = "CentOS-Stream-8"

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
            if compose == "RHEL-7":
                return "RHEL-7-LatestReleased"
            if compose == "RHEL-8":
                return "RHEL-8.5.0-Nightly"
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
            state=CommitStatus.error,
            description=f"No build defined for the target '{chroot}'.",
            chroot=chroot,
        )

    def get_latest_copr_build(self, target: str) -> Optional[CoprBuildModel]:
        """
        Search a last build for the given target using Copr owner and project name.
        """
        copr_builds = CoprBuildModel.get_all_by_owner_and_project_and_target(
            owner=self.job_owner, project_name=self.job_project, target=target
        )
        if not copr_builds:
            return None

        return list(copr_builds)[0]

    def run_testing_farm_on_all(self):

        failed = {}
        for chroot in self.tests_targets:

            latest_copr_build = self.get_latest_copr_build(target=chroot)
            if not latest_copr_build:
                failed[chroot] = (
                    f"No copr builds for {self.job_owner}/{self.job_project}"
                    f"with this target: {chroot}"
                )
                continue

            result = self.run_testing_farm(build=latest_copr_build, chroot=chroot)
            if not result["success"]:
                failed[chroot] = result.get("details")

        if not failed:
            return TaskResults(success=True, details={})

        return TaskResults(
            success=False,
            details={
                "msg": f"Failed testing farm targets: '{failed.keys()}'.",
                **failed,
            },
        )

    def run_testing_farm(self, build: "CoprBuildModel", chroot: str) -> TaskResults:
        if chroot not in self.tests_targets:
            # Leaving here just to be sure that we will discover this situation if it occurs.
            # Currently not possible to trigger this situation.
            msg = f"Target '{chroot}' not defined for tests but triggered."
            logger.error(msg)
            send_to_sentry(PackitConfigException(msg))
            return TaskResults(
                success=False,
                details={"msg": msg},
            )

        if chroot not in self.build_targets:
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
            logger.debug(
                f"Internal TF not enabled for"
                f"'{self.project.service.hostname}/{self.project.full_repo_name}'\n"
                f"enabled_projects_for_internal_tf"
                f"={self.service_config.enabled_projects_for_internal_tf}"
            )
            self.report_status_to_test_for_chroot(
                state=CommitStatus.error,
                description="Internal TF not allowed for this project. Let us know.",
                chroot=chroot,
                url="https://packit.dev/#contact",
            )
            return TaskResults(
                success=True,
                details={"msg": "Project not allowed to use internal TF."},
            )

        self.report_status_to_test_for_chroot(
            state=CommitStatus.pending,
            description="Build succeeded. Submitting the tests ...",
            chroot=chroot,
        )

        logger.info("Sending testing farm request...")
        if self.is_fmf_configured():
            payload = self._payload(int(build.build_id), chroot)
        else:
            payload = self._payload_install_test(int(build.build_id), chroot)
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
            self.report_status_to_test_for_chroot(
                state=CommitStatus.error,
                description=msg,
                chroot=chroot,
            )
            return TaskResults(success=False, details={"msg": msg})

        # success set check on pending
        if req.status_code != 200:
            # something went wrong
            if req.json() and "errors" in req.json():
                msg = req.json()["errors"]
                # specific case, unsupported arch
                if nested_get(req.json(), "errors", "environments", "0", "arch"):
                    msg = req.json()["errors"]["environments"][0]["arch"]
            else:
                msg = f"Failed to submit tests: {req.reason}"
            logger.error(msg)
            self.report_status_to_test_for_chroot(
                state=CommitStatus.failure,
                description=msg,
                chroot=chroot,
            )
            return TaskResults(success=False, details={"msg": msg})

        # Response: {"id": "9fa3cbd1-83f2-4326-a118-aad59f5", ...}

        pipeline_id = req.json()["id"]
        logger.debug(
            f"Submitted ({req.status_code}) to testing farm as request {pipeline_id}"
        )

        created_model = TFTTestRunModel.create(
            pipeline_id=pipeline_id,
            commit_sha=self.metadata.commit_sha,
            status=TestingFarmResult.new,
            target=chroot,
            web_url=None,
            run_model=build.runs[-1],
            # In _payload() we ask TF to test commit_sha of fork (PR's source).
            # Store original url. If this proves to work, make it a separate column.
            data={"base_project_url": self.project.get_web_url()},
        )

        self.report_status_to_test_for_chroot(
            state=CommitStatus.pending,
            description="Tests have been submitted ...",
            url=get_testing_farm_info_url(created_model.id),
            chroot=chroot,
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
