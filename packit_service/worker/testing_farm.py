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
from typing import Union

import requests
from ogr.abstract import GitProject
from ogr.utils import RequestResponse
from packit.config import PackageConfig

from packit_service.config import Deployment, ServiceConfig
from packit_service.constants import TESTING_FARM_TRIGGER_URL
from packit_service.service.events import (
    PullRequestEvent,
    PullRequestCommentEvent,
    CoprBuildEvent,
)
from packit_service.worker.copr_build import JobHelper
from packit_service.worker.handler import (
    HandlerResults,
    PRCheckName,
)

logger = logging.getLogger(__name__)


class TestingFarmJobHelper(JobHelper):
    def __init__(
        self,
        config: ServiceConfig,
        package_config: PackageConfig,
        project: GitProject,
        event: Union[
            PullRequestEvent,
            PullRequestCommentEvent,
            CoprBuildEvent,
            PullRequestCommentEvent,
        ],
    ):
        super().__init__(config, package_config, project, event)
        self.session = requests.session()
        adapter = requests.adapters.HTTPAdapter(max_retries=5)
        self.insecure = False
        self.session.mount("https://", adapter)
        self.header: dict = {"Content-Type": "application/json"}

    def run_testing_farm(self):
        for chroot in self.tests_chroots:
            pipeline_id = str(uuid.uuid4())
            logger.debug(f"Pipeline id: {pipeline_id}")
            payload: dict = {
                "pipeline": {"id": pipeline_id},
                "api": {"token": self.config.testing_farm_secret},
            }

            logger.debug(f"Payload: {payload}")

            stg = "-stg" if self.config.deployment == Deployment.stg else ""
            copr_repo_name = (
                f"packit/{self.project.namespace}-{self.project.repo}-"
                f"{self.event.pr_id}{stg}"
            )

            payload["artifact"] = {
                "repo-name": self.event.base_repo_name,
                "repo-namespace": self.event.base_repo_namespace,
                "copr-repo-name": copr_repo_name,
                "copr-chroot": chroot,
                "commit-sha": self.event.commit_sha,
                "git-url": self.event.project_url,
                "git-ref": self.base_ref,
            }

            logger.debug("Sending testing farm request...")
            logger.debug(payload)

            req = self.send_testing_farm_request(
                TESTING_FARM_TRIGGER_URL, "POST", {}, json.dumps(payload)
            )
            logger.debug(f"Request sent: {req}")
            if not req:
                msg = "Failed to post request to testing farm API."
                logger.debug("Failed to post request to testing farm API.")
                self.status_reporter.report(
                    "failure",
                    msg,
                    None,
                    "",
                    check_names=PRCheckName.get_testing_farm_check(chroot),
                )
                return HandlerResults(success=False, details={"msg": msg})
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
                    msg = req.json()["message"]
                    self.status_reporter.report(
                        "failure",
                        msg,
                        None,
                        check_names=PRCheckName.get_testing_farm_check(chroot),
                    )
                    return HandlerResults(success=False, details={"msg": msg})

                self.status_reporter.report(
                    "pending",
                    "Tests are running ...",
                    None,
                    req.json()["url"],
                    check_names=PRCheckName.get_testing_farm_check(chroot),
                )

        return HandlerResults(success=True, details={})

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
