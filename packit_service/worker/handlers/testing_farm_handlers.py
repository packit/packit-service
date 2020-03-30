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

"""
This file defines classes for job handlers specific for Testing farm
"""
import logging
from typing import Optional

from ogr.abstract import GitProject, CommitStatus
from packit.config import (
    JobType,
    JobConfig,
    get_package_config_from_repo,
)

from packit_service.config import ServiceConfig
from packit_service.models import TFTTestRunModel
from packit_service.service.events import (
    TestingFarmResultsEvent,
    TestingFarmResult,
    TheJobTriggerType,
)
from packit_service.worker.handlers import AbstractGithubJobHandler
from packit_service.worker.handlers.abstract import use_for
from packit_service.worker.reporting import StatusReporter
from packit_service.worker.result import HandlerResults
from packit_service.worker.testing_farm import TestingFarmJobHelper

logger = logging.getLogger(__name__)


@use_for(job_type=JobType.tests)
class TestingFarmResultsHandler(AbstractGithubJobHandler):
    type = JobType.report_test_results
    triggers = [TheJobTriggerType.testing_farm_results]
    event: TestingFarmResultsEvent

    def __init__(
        self,
        config: ServiceConfig,
        job_config: Optional[JobConfig],
        event: TestingFarmResultsEvent,
    ):
        super().__init__(config=config, job_config=job_config, event=event)
        self.project: GitProject = event.get_project()
        self.package_config = self.get_package_config_from_repo(
            project=self.project, reference=self.event.git_ref
        )
        if not self.package_config:
            raise ValueError(f"No config file found in {self.project.full_repo_name}")

        self.package_config.upstream_project_url = event.project_url

    def get_package_config_from_repo(
        self,
        project: GitProject,
        reference: str,
        pr_id: int = None,
        fail_when_missing: bool = False,
    ):
        return get_package_config_from_repo(self.project, self.event.git_ref)

    def run(self) -> HandlerResults:

        logger.debug(f"Received testing-farm result:\n{self.event.result}")
        logger.debug(f"Received testing-farm test results:\n{self.event.tests}")

        test_run_model = TFTTestRunModel.get_by_pipeline_id(
            pipeline_id=self.event.pipeline_id
        )
        if not test_run_model:
            logger.warning(
                f"Unknown pipeline_id received from the testing-farm: "
                f"{self.event.pipeline_id}"
            )

        if test_run_model:
            test_run_model.set_status(self.event.result)

        if self.event.result == TestingFarmResult.passed:
            status = CommitStatus.success
            passed = True

        else:
            status = CommitStatus.failure
            passed = False

        if (
            len(self.event.tests) == 1
            and self.event.tests[0].name == "/install/copr-build"
        ):
            logger.debug("No-fmf scenario discovered.")
            short_msg = "Installation passed" if passed else "Installation failed"
        else:
            short_msg = self.event.message

        if test_run_model:
            test_run_model.set_web_url(self.event.log_url)
        status_reporter = StatusReporter(self.project, self.event.commit_sha)
        status_reporter.report(
            state=status,
            description=short_msg,
            url=self.event.log_url,
            check_names=TestingFarmJobHelper.get_test_check(self.event.copr_chroot),
        )

        return HandlerResults(success=True, details={})
