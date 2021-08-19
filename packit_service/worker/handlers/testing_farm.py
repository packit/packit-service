# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
This file defines classes for job handlers specific for Testing farm
"""
import logging
from datetime import datetime
from typing import Optional

from celery import signature
from packit.config import JobConfig, JobType
from packit.config.package_config import PackageConfig

from packit_service.models import (
    AbstractTriggerDbType,
    TFTTestRunModel,
    CoprBuildModel,
    TestingFarmResult,
)
from packit_service.worker.events import (
    TestingFarmResultsEvent,
    PullRequestCommentGithubEvent,
    MergeRequestCommentGitlabEvent,
    PullRequestCommentPagureEvent,
)
from packit_service.service.urls import (
    get_testing_farm_info_url,
    get_copr_build_info_url,
)
from packit_service.worker.handlers import JobHandler
from packit_service.worker.handlers.abstract import (
    TaskName,
    configured_as,
    reacts_to,
    run_for_comment,
)
from packit_service.worker.reporting import StatusReporter, BaseCommitStatus
from packit_service.worker.result import TaskResults
from packit_service.worker.testing_farm import TestingFarmJobHelper
from packit_service.constants import PG_COPR_BUILD_STATUS_SUCCESS
from packit_service.utils import dump_job_config, dump_package_config

logger = logging.getLogger(__name__)


@run_for_comment(command="test")
@reacts_to(PullRequestCommentGithubEvent)
@reacts_to(MergeRequestCommentGitlabEvent)
@reacts_to(PullRequestCommentPagureEvent)
@configured_as(job_type=JobType.tests)
class TestingFarmHandler(JobHandler):
    """
    The automatic matching is now used only for /packit test
    TODO: We can react directly to the finished Copr build.
    """

    task_name = TaskName.testing_farm

    def __init__(
        self,
        package_config: PackageConfig,
        job_config: JobConfig,
        event: dict,
        chroot: Optional[str] = None,
        build_id: Optional[int] = None,
    ):
        super().__init__(
            package_config=package_config,
            job_config=job_config,
            event=event,
        )
        self.chroot = chroot
        self.build_id = build_id
        self._db_trigger: Optional[AbstractTriggerDbType] = None

    @property
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        if not self._db_trigger:
            # copr build end
            if self.build_id:
                build = CoprBuildModel.get_by_id(self.build_id)
                self._db_trigger = build.get_trigger_object()
            # '/packit test' comment
            else:
                self._db_trigger = self.data.db_trigger
        return self._db_trigger

    def run(self) -> TaskResults:
        # TODO: once we turn handlers into respective celery tasks, we should iterate
        #       here over *all* matching jobs and do them all, not just the first one
        testing_farm_helper = TestingFarmJobHelper(
            service_config=self.service_config,
            package_config=self.package_config,
            project=self.project,
            metadata=self.data,
            db_trigger=self.db_trigger,
            job_config=self.job_config,
        )

        if self.data.event_type in (
            PullRequestCommentGithubEvent.__name__,
            MergeRequestCommentGitlabEvent.__name__,
            PullRequestCommentPagureEvent.__name__,
        ):
            logger.debug(f"Test job config: {testing_farm_helper.job_tests}")
            targets = list(testing_farm_helper.tests_targets)
        else:
            targets = [self.chroot]

        logger.debug(f"Targets to run the tests: {targets}")
        targets_with_builds = {}
        for target in targets:
            if self.build_id:
                copr_build = CoprBuildModel.get_by_id(self.build_id)
            else:
                copr_build = testing_farm_helper.get_latest_copr_build(
                    target=target, commit_sha=self.data.commit_sha
                )
            if copr_build:
                targets_with_builds[target] = copr_build

        # If there is missing build for some target, trigger copr build for everything
        if len(targets) != len(targets_with_builds):
            logger.info(
                f"Missing Copr build for some target in {testing_farm_helper.job_owner}/"
                f"{testing_farm_helper.job_project}"
                f" and commit:{self.data.commit_sha}, running a new Copr build."
            )
            testing_farm_helper.report_status_to_tests(
                state=BaseCommitStatus.pending,
                description="Missing Copr build for some target, running a new Copr build.",
                url="",
            )

            # monitor queued builds
            for _ in range(len(targets)):
                self.pushgateway.copr_builds_queued.inc()

            signature(
                TaskName.copr_build.value,
                kwargs={
                    "package_config": dump_package_config(self.package_config),
                    "job_config": dump_job_config(self.job_config),
                    "event": self.data.get_dict(),
                },
            ).apply_async()

            return TaskResults(
                success=True,
                details={"msg": "Build required, triggering copr build"},
            )

        failed = {}
        for target, copr_build in targets_with_builds.items():
            if copr_build.status != PG_COPR_BUILD_STATUS_SUCCESS:
                logger.info(
                    "The latest build was not successful, not running tests for it."
                )
                testing_farm_helper.report_status_to_test_for_chroot(
                    state=BaseCommitStatus.failure,
                    description="The latest build was not successful, not running tests for it.",
                    chroot=target,
                    url=get_copr_build_info_url(copr_build.id),
                )
                continue

            logger.info(f"Running testing farm for {copr_build}:{target}.")
            self.pushgateway.test_runs_queued.inc()
            result = testing_farm_helper.run_testing_farm(
                build=copr_build, chroot=target
            )
            if not result["success"]:
                failed[target] = result.get("details")

        if not failed:
            return TaskResults(success=True, details={})

        return TaskResults(
            success=False,
            details={
                "msg": f"Failed testing farm targets: '{failed.keys()}'.",
                **failed,
            },
        )


@configured_as(job_type=JobType.tests)
@reacts_to(event=TestingFarmResultsEvent)
class TestingFarmResultsHandler(JobHandler):
    task_name = TaskName.testing_farm_results

    def __init__(
        self,
        package_config: PackageConfig,
        job_config: JobConfig,
        event: dict,
    ):
        super().__init__(
            package_config=package_config,
            job_config=job_config,
            event=event,
        )
        self.result = (
            TestingFarmResult(event.get("result")) if event.get("result") else None
        )
        self.pipeline_id = event.get("pipeline_id")
        self.log_url = event.get("log_url")
        self.copr_chroot = event.get("copr_chroot")
        self.summary = event.get("summary")
        self._db_trigger: Optional[AbstractTriggerDbType] = None

    @property
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        if not self._db_trigger:
            run_model = TFTTestRunModel.get_by_pipeline_id(pipeline_id=self.pipeline_id)
            if run_model:
                self._db_trigger = run_model.get_trigger_object()
        return self._db_trigger

    def run(self) -> TaskResults:
        logger.debug(f"Testing farm {self.pipeline_id} result:\n{self.result}")

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
            status = BaseCommitStatus.running
            summary = self.summary or "Tests are running ..."
        elif self.result == TestingFarmResult.passed:
            status = BaseCommitStatus.success
            summary = self.summary or "Tests passed ..."
        elif self.result == TestingFarmResult.error:
            status = BaseCommitStatus.error
            summary = self.summary or "Error ..."
        else:
            status = BaseCommitStatus.failure
            summary = self.summary or "Tests failed ..."

        if self.result == TestingFarmResult.running:
            self.pushgateway.test_runs_started.inc()
        else:
            self.pushgateway.test_runs_finished.inc()
            test_run_time = (
                datetime.now() - test_run_model.submitted_time
            ).total_seconds()
            self.pushgateway.test_run_finished_time.observe(test_run_time)

        if test_run_model:
            test_run_model.set_web_url(self.log_url)
        status_reporter = StatusReporter.get_instance(
            project=self.project, commit_sha=self.data.commit_sha, pr_id=self.data.pr_id
        )
        status_reporter.report(
            state=status,
            description=summary,
            url=get_testing_farm_info_url(test_run_model.id)
            if test_run_model
            else self.log_url,
            check_names=TestingFarmJobHelper.get_test_check(self.copr_chroot),
        )

        return TaskResults(success=True, details={})
