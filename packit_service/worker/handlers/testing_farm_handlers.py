# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
This file defines classes for job handlers specific for Testing farm
"""
import logging
from typing import List, Optional

from ogr import GitlabService
from ogr.abstract import CommitStatus
from packit.config import JobConfig, JobType
from packit.config.package_config import PackageConfig

from packit_service.models import AbstractTriggerDbType, TFTTestRunModel
from packit_service.service.events import (
    EventData,
    TestResult,
    TestingFarmResult,
    TestingFarmResultsEvent,
)
from packit_service.worker.handlers import JobHandler
from packit_service.worker.handlers.abstract import TaskName, configured_as, reacts_to
from packit_service.worker.reporting import StatusReporter
from packit_service.worker.result import TaskResults
from packit_service.worker.testing_farm import TestingFarmJobHelper

logger = logging.getLogger(__name__)


@configured_as(job_type=JobType.tests)
@reacts_to(event=TestingFarmResultsEvent)
class TestingFarmResultsHandler(JobHandler):
    task_name = TaskName.testing_farm_results

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
        summary: str,
    ):
        super().__init__(
            package_config=package_config,
            job_config=job_config,
            data=data,
        )

        self.tests = tests
        self.result = result
        self.pipeline_id = pipeline_id
        self.log_url = log_url
        self.copr_chroot = copr_chroot
        self.summary = summary
        self._db_trigger: Optional[AbstractTriggerDbType] = None

    @property
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        if not self._db_trigger:
            run_model = TFTTestRunModel.get_by_pipeline_id(pipeline_id=self.pipeline_id)
            if run_model:
                self._db_trigger = run_model.job_trigger.get_trigger_object()
        return self._db_trigger

    def run(self) -> TaskResults:
        logger.debug(f"Testing farm {self.pipeline_id} result:\n{self.result}")
        logger.debug(f"Testing farm test results:\n{self.tests}")

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

        if self.result == TestingFarmResult.running:
            status = CommitStatus.pending
            if isinstance(self.project.service, GitlabService):
                # only Gitlab has 'running' state
                status = CommitStatus.running
            summary = self.summary or "Tests are running ..."
        elif self.result == TestingFarmResult.passed:
            status = CommitStatus.success
            summary = self.summary or "Tests passed ..."
        elif self.result == TestingFarmResult.error:
            status = CommitStatus.error
            if isinstance(self.project.service, GitlabService):
                # Gitlab has no 'error' state
                status = CommitStatus.failure
            summary = self.summary or "Error ..."
        else:
            status = CommitStatus.failure
            summary = self.summary or "Tests failed ..."

        if test_run_model:
            test_run_model.set_web_url(self.log_url)
        status_reporter = StatusReporter(
            project=self.project, commit_sha=self.data.commit_sha, pr_id=self.data.pr_id
        )
        status_reporter.report(
            state=status,
            description=summary,
            url=self.log_url,
            check_names=TestingFarmJobHelper.get_test_check(self.copr_chroot),
        )

        return TaskResults(success=True, details={})
