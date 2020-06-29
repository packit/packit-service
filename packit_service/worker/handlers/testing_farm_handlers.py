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
from typing import Optional, List

from ogr.abstract import CommitStatus
from packit.config import JobType, JobConfig
from packit.config.package_config import PackageConfig

from packit_service.models import TFTTestRunModel, AbstractTriggerDbType
from packit_service.service.events import (
    TestingFarmResult,
    TheJobTriggerType,
    EventData,
    TestResult,
)
from packit_service.worker.handlers import JobHandler
from packit_service.worker.handlers.abstract import use_for
from packit_service.worker.reporting import StatusReporter
from packit_service.worker.result import TaskResults
from packit_service.worker.testing_farm import TestingFarmJobHelper

logger = logging.getLogger(__name__)


@use_for(job_type=JobType.tests)
class TestingFarmResultsHandler(JobHandler):
    type = JobType.report_test_results
    triggers = [TheJobTriggerType.testing_farm_results]
    task_name = "task.run_testing_farm_results_handler"

    def __init__(
        self,
        package_config: PackageConfig,
        job_config: JobConfig,
        data: EventData,
        tests: List[TestResult],
        result: TestingFarmResult,
        pipeline_id: str,
        log_url: str,
        copr_chroot: str,
        message: str,
    ):
        super().__init__(
            package_config=package_config, job_config=job_config, data=data,
        )

        self.tests = tests
        self.result = result
        self.pipeline_id = pipeline_id
        self.log_url = log_url
        self.copr_chroot = copr_chroot
        self.message = message
        self._db_trigger: Optional[AbstractTriggerDbType] = None

    @property
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        if not self._db_trigger:
            run_model = TFTTestRunModel.get_by_pipeline_id(pipeline_id=self.pipeline_id)
            if run_model:
                self._db_trigger = run_model.job_trigger.get_trigger_object()
        return self._db_trigger

    def run(self) -> TaskResults:

        logger.debug(f"Received testing-farm result:\n{self.result}")
        logger.debug(f"Received testing-farm test results:\n{self.tests}")

        test_run_model = TFTTestRunModel.get_by_pipeline_id(
            pipeline_id=self.pipeline_id
        )
        if not test_run_model:
            logger.warning(
                f"Unknown pipeline_id received from the testing-farm: "
                f"{self.pipeline_id}"
            )

        if test_run_model:
            test_run_model.set_status(self.result)

        if self.result == TestingFarmResult.passed:
            status = CommitStatus.success
            passed = True

        else:
            status = CommitStatus.failure
            passed = False

        if len(self.tests) == 1 and self.tests[0].name == "/install/copr-build":
            logger.debug("No-fmf scenario discovered.")
            short_msg = "Installation passed" if passed else "Installation failed"
        else:
            short_msg = self.message

        if test_run_model:
            test_run_model.set_web_url(self.log_url)
        status_reporter = StatusReporter(self.project, self.data.commit_sha)
        status_reporter.report(
            state=status,
            description=short_msg,
            url=self.log_url,
            check_names=TestingFarmJobHelper.get_test_check(self.copr_chroot),
        )

        return TaskResults(success=True, details={})
