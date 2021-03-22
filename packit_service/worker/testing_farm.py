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

from packit_service.config import ServiceConfig
from packit_service.constants import TESTING_FARM_INSTALLABILITY_TEST_URL
from packit_service.models import CoprBuildModel, TFTTestRunModel, TestingFarmResult
from packit_service.sentry_integration import send_to_sentry
from packit_service.service.events import EventData
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

    @property
    def tft_api_url(self) -> str:
        if not self._tft_api_url:
            self._tft_api_url = self.service_config.testing_farm_api_url
            if not self._tft_api_url.endswith("/"):
                self._tft_api_url += "/"
        return self._tft_api_url

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
            "api_key": self.service_config.testing_farm_secret,
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
                    "token": self.service_config.testing_farm_secret,
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
                    "name": "/packit/install-and-verify",
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

    @staticmethod
    def chroot2distro_arch(chroot: str) -> Tuple[str, str]:
        """ Get distro and arch from chroot. """
        distro, arch = chroot.rsplit("-", 1)
        # https://github.com/packit/packit-service/issues/939#issuecomment-769896841
        # https://github.com/packit/packit-service/pull/1008#issuecomment-789574614
        distro = distro.replace("epel", "centos")
        return distro, arch

    def distro2compose(self, distro: str) -> str:
        """Create a compose string from distro, e.g. fedora-33 -> Fedora-33
        https://api.dev.testing-farm.io/v0.1/composes"""
        compose = distro.title().replace("Centos", "CentOS")
        if compose == "CentOS-Stream":
            compose = "CentOS-Stream-8"

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

    @property
    def latest_copr_build(self) -> Optional[CoprBuildModel]:
        copr_builds = CoprBuildModel.get_all_by_owner_and_project(
            owner=self.job_owner, project_name=self.job_project
        )
        if not copr_builds:
            return None

        return list(copr_builds)[0]

    def run_testing_farm_on_all(self):
        latest_copr_build = self.latest_copr_build
        if not latest_copr_build:
            return TaskResults(
                success=False,
                details={
                    "msg": f"No copr builds for {self.job_owner}/{self.job_project}"
                },
            )

        failed = {}
        for chroot in self.tests_targets:
            result = self.run_testing_farm(
                build_id=int(latest_copr_build.build_id), chroot=chroot
            )
            if not result["success"]:
                failed[chroot] = result.get("details")

        if not failed:
            return TaskResults(success=True, details={})

        return TaskResults(
            success=False,
            details={"msg": f"Failed testing farm targets: '{failed.keys()}'."}.update(
                failed
            ),
        )

    def run_testing_farm(self, build_id: int, chroot: str) -> TaskResults:
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

        self.report_status_to_test_for_chroot(
            state=CommitStatus.pending,
            description="Build succeeded. Submitting the tests ...",
            chroot=chroot,
        )

        logger.info("Sending testing farm request...")
        if self.is_fmf_configured():
            payload = self._payload(build_id, chroot)
        else:
            payload = self._payload_install_test(build_id, chroot)
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
            if req.json() and "message" in req.json():
                msg = req.json()["message"]
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

        TFTTestRunModel.create(
            pipeline_id=pipeline_id,
            commit_sha=self.metadata.commit_sha,
            status=TestingFarmResult.new,
            target=chroot,
            web_url=None,
            trigger_model=self.db_trigger,
            # In _payload() we ask TF to test commit_sha of fork (PR's source).
            # Store original url. If this proves to work, make it a separate column.
            data={"base_project_url": self.project.get_web_url()},
        )

        self.report_status_to_test_for_chroot(
            state=CommitStatus.pending,
            description="Tests have been submitted ...",
            url=f"{self.tft_api_url}requests/{pipeline_id}",
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
