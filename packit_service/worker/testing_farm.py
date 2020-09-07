# MIT License
#
# Copyright (c) 2018-2019 Red Hat, Inc.

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


import json
import logging
import uuid

import requests
from ogr.abstract import GitProject, CommitStatus
from ogr.utils import RequestResponse
from packit.config.job_config import JobConfig
from packit.config.package_config import PackageConfig
from packit.exceptions import PackitConfigException

from packit_service.config import ServiceConfig
from packit_service.constants import TESTING_FARM_TRIGGER_URL
from packit_service.models import TFTTestRunModel, TestingFarmResult
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
        self.header: dict = {"Content-Type": "application/json"}

    def _trigger_payload(self, pipeline_id: str, chroot: str) -> dict:
        """Produce payload that can be used to trigger tests in Testing
        Farm using the Copr chroot given.
        """
        git_url = self.metadata.project_url
        if not git_url.endswith(".git"):
            git_url = f"{git_url}.git"

        return {
            "pipeline": {"id": pipeline_id},
            "api": {"token": self.service_config.testing_farm_secret},
            "response-url": f"{self.api_url}/testing-farm/results",
            "artifact": {
                "repo-name": self.project.repo,
                "repo-namespace": self.project.namespace,
                "copr-repo-name": f"{self.job_owner}/{self.job_project}",
                "copr-chroot": chroot,
                "commit-sha": self.metadata.commit_sha,
                "git-url": git_url,
                "git-ref": self.metadata.git_ref
                if self.metadata.git_ref
                else self.metadata.commit_sha,
            },
        }

    def report_missing_build_chroot(self, chroot: str):
        self.report_status_to_test_for_chroot(
            state=CommitStatus.error,
            description=f"No build defined for the target '{chroot}'.",
            chroot=chroot,
        )

    def run_testing_farm_on_all(self):
        failed = {}
        for chroot in self.tests_targets:
            result = self.run_testing_farm(chroot)
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

    def run_testing_farm(self, chroot: str) -> TaskResults:
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
                    f"Cannot run tests without build."
                },
            )

        self.report_status_to_test_for_chroot(
            state=CommitStatus.pending,
            description="Build succeeded. Submitting the tests ...",
            chroot=chroot,
        )

        pipeline_id = str(uuid.uuid4())
        logger.debug(f"Pipeline id: {pipeline_id}")

        test_run_model = TFTTestRunModel.create(
            pipeline_id=pipeline_id,
            commit_sha=self.metadata.commit_sha,
            status=TestingFarmResult.new,
            target=chroot,
            web_url=None,
            trigger_model=self.db_trigger,
        )

        logger.debug("Sending testing farm request...")
        payload = self._trigger_payload(pipeline_id, chroot)
        logger.debug(f"Payload: {payload}")

        req = self.send_testing_farm_request(
            TESTING_FARM_TRIGGER_URL, "POST", {}, json.dumps(payload)
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
            test_run_model.set_status(TestingFarmResult.error)
            return TaskResults(success=False, details={"msg": msg})
        else:
            logger.debug(
                f"Submitted to testing farm with return code: {req.status_code}"
            )

            """
            Response:
            {
                "id": "9fa3cbd1-83f2-4326-a118-aad59f5",
                "success": true,
                "url": "https://console-testing-farm.apps.ci.centos.org/pipeline/<id>"
            }
            """

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
                test_run_model.set_status(TestingFarmResult.error)
                return TaskResults(success=False, details={"msg": msg})

            test_run_model.set_status(TestingFarmResult.running)
            self.report_status_to_test_for_chroot(
                state=CommitStatus.pending,
                description="Tests are running ...",
                url=req.json()["url"],
                chroot=chroot,
            )

        return TaskResults(success=True, details={})

    def send_testing_farm_request(
        self, url: str, method: str = None, params: dict = None, data=None
    ):
        method = method or "GET"
        try:
            response = self.get_raw_request(
                method=method, url=url, params=params, data=data
            )
        except requests.exceptions.ConnectionError as er:
            logger.error(er)
            raise Exception(f"Cannot connect to url: `{url}`.", er)
        return response

    def get_raw_request(
        self, url, method="GET", params=None, data=None, header=None
    ) -> RequestResponse:

        response = self.session.request(
            method=method,
            url=url,
            params=params,
            headers=header or self.header,
            data=data,
            verify=not self.insecure,
        )

        json_output = None
        try:
            json_output = response.json()
        except ValueError:
            logger.debug(response.text)

        return RequestResponse(
            status_code=response.status_code,
            ok=response.ok,
            content=response.content,
            json=json_output,
            reason=response.reason,
        )
