# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
This file defines classes for job handlers specific for Testing farm
"""
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, List, Tuple, Type

from celery import Task

from packit.config import JobConfig, JobType
from packit.config.package_config import PackageConfig
from packit_service.models import (
    ProjectEventModel,
    TFTTestRunTargetModel,
    CoprBuildTargetModel,
    BuildStatus,
    TestingFarmResult,
    PipelineModel,
    TFTTestRunGroupModel,
)
from packit_service.service.urls import (
    get_testing_farm_info_url,
    get_copr_build_info_url,
)
from packit_service.utils import elapsed_seconds
from packit_service.worker.checker.abstract import Checker
from packit_service.worker.checker.testing_farm import (
    CanActorRunJob,
    IsEventForJob,
    IsEventOk,
    IsJobConfigTriggerMatching,
    IsCoprBuildDefined,
    IsIdentifierFromCommentMatching,
    IsLabelFromCommentMatching,
)
from packit_service.worker.events import (
    TestingFarmResultsEvent,
    CheckRerunCommitEvent,
    CheckRerunPullRequestEvent,
    PullRequestGithubEvent,
    PushGitHubEvent,
    PushGitlabEvent,
    MergeRequestGitlabEvent,
    AbstractPRCommentEvent,
)
from packit_service.worker.handlers import JobHandler
from packit_service.worker.handlers.abstract import (
    TaskName,
    configured_as,
    reacts_to,
    run_for_comment,
    run_for_check_rerun,
    RetriableJobHandler,
)
from packit_service.worker.handlers.mixin import (
    GetCoprBuildMixin,
    GetTestingFarmJobHelperMixin,
)
from packit_service.worker.handlers.mixin import GetGithubCommentEventMixin
from packit_service.worker.helpers.testing_farm import TestingFarmJobHelper
from packit_service.worker.mixin import PackitAPIWithDownstreamMixin
from packit_service.worker.reporting import BaseCommitStatus
from packit_service.worker.result import TaskResults

logger = logging.getLogger(__name__)


@run_for_comment(command="test")
@run_for_comment(command="build")
@run_for_comment(command="copr-build")
@run_for_comment(command="retest-failed")
@run_for_check_rerun(prefix="testing-farm")
@reacts_to(PullRequestGithubEvent)
@reacts_to(PushGitHubEvent)
@reacts_to(PushGitlabEvent)
@reacts_to(MergeRequestGitlabEvent)
@reacts_to(AbstractPRCommentEvent)
@reacts_to(CheckRerunPullRequestEvent)
@reacts_to(CheckRerunCommitEvent)
@configured_as(job_type=JobType.tests)
class TestingFarmHandler(
    RetriableJobHandler,
    PackitAPIWithDownstreamMixin,
    GetTestingFarmJobHelperMixin,
    GetCoprBuildMixin,
    GetGithubCommentEventMixin,
):
    """
    The automatic matching is now used only for /packit test
    TODO: We can react directly to the finished Copr build.
    """

    __test__ = False

    task_name = TaskName.testing_farm

    def __init__(
        self,
        package_config: PackageConfig,
        job_config: JobConfig,
        event: dict,
        celery_task: Task,
        testing_farm_target_id: Optional[int] = None,
        build_id: Optional[int] = None,
    ):
        super().__init__(
            package_config=package_config,
            job_config=job_config,
            event=event,
            celery_task=celery_task,
        )
        self.build_id = build_id
        self._testing_farm_target_id = testing_farm_target_id
        self._testing_farm_job_helper: Optional[TestingFarmJobHelper] = None
        self._targets_with_builds: dict = None

    @property
    def targets_with_builds(self):
        if self._targets_with_builds is None:
            (
                self._targets_with_builds,
                _,
            ) = self.testing_farm_job_helper.get_targets_with_builds(self.build_id)
        return self._targets_with_builds

    @staticmethod
    def get_checkers() -> Tuple[Type[Checker], ...]:
        return (
            IsJobConfigTriggerMatching,
            IsEventOk,
            IsCoprBuildDefined,
            CanActorRunJob,
            IsIdentifierFromCommentMatching,
            IsLabelFromCommentMatching,
        )

    def _get_or_create_group(
        self, builds: Dict[str, CoprBuildTargetModel]
    ) -> Tuple[TFTTestRunGroupModel, List[TFTTestRunTargetModel]]:
        """Creates a TFTTestRunGroup.

        If a group is already attached to this handler, it returns the
        existing group instead.

        Args:
            builds: Dict mapping Testing Farm target (e.g. f37) to the
                corresponding copr build.

        Returns:
            The existing or created test run group and the test targets
            to run the tests for.

        """
        if self._testing_farm_target_id is not None:
            target_model = TFTTestRunTargetModel.get_by_id(self._testing_farm_target_id)
            return target_model.group_of_targets, [target_model]

        run_model = (
            PipelineModel.create(
                project_event=self.db_project_event,
                package_name=self.get_package_name(),
            )
            if self.testing_farm_job_helper.skip_build or not builds
            # All the builds should be in the same copr build group, therefore
            # connected to the same pipeline, just take the first one
            else next(iter(builds.values())).group_of_targets.runs[-1]
        )
        group = (
            TFTTestRunGroupModel.create([run_model])
            if not run_model.test_run_group
            else run_model.test_run_group
        )
        runs = []
        for target, build in builds.items():
            test_builds = [build] if build else []
            runs.append(
                TFTTestRunTargetModel.create(
                    pipeline_id=None,
                    identifier=self.job_config.identifier,
                    status=TestingFarmResult.new,
                    target=target,
                    web_url=None,
                    test_run_group=group,
                    copr_build_targets=test_builds,
                    # In _payload() we ask TF to test commit_sha of fork (PR's source).
                    # Store original url. If this proves to work, make it a separate column.
                    data={"base_project_url": self.project.get_web_url()},
                )
            )

        return group, runs

    def run_with_copr_builds(self, targets: List[str], failed: Dict):
        if not self.targets_with_builds:
            return

        group, test_runs = self._get_or_create_group(self.targets_with_builds)
        for test_run in test_runs:
            copr_build = test_run.copr_builds[0]
            if copr_build.status in (
                BuildStatus.pending,
                BuildStatus.waiting_for_srpm,
            ):
                logger.info("The latest build has not finished yet.")
                if self.job_config.manual_trigger:
                    state = BaseCommitStatus.neutral
                    description = (
                        "The latest build has not finished yet. "
                        "Please retrigger the tests once it has finished."
                    )
                else:
                    state = BaseCommitStatus.pending
                    description = (
                        "The latest build has not finished yet, "
                        "waiting until it finishes before running tests for it."
                    )
                self.testing_farm_job_helper.report_status_to_tests_for_test_target(
                    state=state,
                    description=description,
                    target=test_run.target,
                    url=get_copr_build_info_url(copr_build.id),
                )
                continue

            # Only retry what's needed
            if test_run.status not in [TestingFarmResult.new, TestingFarmResult.retry]:
                continue
            logger.info(f"Running testing farm for {copr_build}:{test_run.target}.")
            self.run_for_target(test_run=test_run, build=copr_build, failed=failed)

    def run_for_target(
        self,
        test_run: "TFTTestRunTargetModel",
        failed: Dict,
        build: Optional[CoprBuildTargetModel] = None,
    ):
        if self.celery_task.retries == 0:
            self.pushgateway.test_runs_queued.inc()
        result = self.testing_farm_job_helper.run_testing_farm(
            test_run=test_run, build=build
        )
        if not result["success"]:
            failed[test_run.target] = result.get("details")

    def run(self) -> TaskResults:
        # TODO: once we turn handlers into respective celery tasks, we should iterate
        #       here over *all* matching jobs and do them all, not just the first one
        logger.debug(f"Test job config: {self.job_config}")
        targets = list(self.testing_farm_job_helper.tests_targets)
        logger.debug(f"Targets to run the tests: {targets}")

        if self.testing_farm_job_helper.build_required():
            if self.testing_farm_job_helper.job_build:
                msg = "Build required, already handled by build job."
            else:
                # this should not happen as there is the IsCoprBuildDefined pre-check
                msg = "Build required, no build job defined in config."
            logger.info(msg)
            return TaskResults(
                success=True,
                details={"msg": msg},
            )

        failed: Dict[str, str] = {}

        if self.testing_farm_job_helper.skip_build:
            group, test_runs = self._get_or_create_group(
                {target: None for target in targets}
            )
            for test_run in test_runs:
                # Only retry what's needed
                if test_run.status not in [
                    TestingFarmResult.new,
                    TestingFarmResult.retry,
                ]:
                    continue
                self.run_for_target(test_run=test_run, failed=failed)

        else:
            self.run_with_copr_builds(targets=targets, failed=failed)

        if not failed:
            return TaskResults(success=True, details={})

        result_details = {"msg": f"Failed testing farm targets: '{failed.keys()}'."}
        result_details.update(failed)

        return TaskResults(success=False, details=result_details)


@configured_as(job_type=JobType.tests)
@reacts_to(event=TestingFarmResultsEvent)
class TestingFarmResultsHandler(
    JobHandler,
    PackitAPIWithDownstreamMixin,
    GetTestingFarmJobHelperMixin,
):
    __test__ = False
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
        self.summary = event.get("summary")
        self.created = event.get("created")

    @staticmethod
    def get_checkers() -> Tuple[Type[Checker], ...]:
        return (IsEventForJob,)

    @property
    def db_project_event(self) -> Optional[ProjectEventModel]:
        if not self._db_project_event:
            run_model = TFTTestRunTargetModel.get_by_pipeline_id(
                pipeline_id=self.pipeline_id
            )
            if run_model:
                self._db_project_event = run_model.get_project_event_model()
        return self._db_project_event

    def run(self) -> TaskResults:
        logger.debug(f"Testing farm {self.pipeline_id} result:\n{self.result}")

        test_run_model = TFTTestRunTargetModel.get_by_pipeline_id(
            pipeline_id=self.pipeline_id
        )
        if not test_run_model:
            msg = f"Unknown pipeline_id received from the testing-farm: {self.pipeline_id}"
            logger.warning(msg)
            return TaskResults(success=False, details={"msg": msg})

        if test_run_model.status == self.result:
            logger.debug(
                "Testing farm results already processed "
                "(state in the DB is the same as the one about to report)."
            )
            return TaskResults(
                success=True, details={"msg": "Testing farm results already processed"}
            )

        failure = False
        if self.result == TestingFarmResult.running:
            status = BaseCommitStatus.running
            summary = self.summary or "Tests are running ..."
        elif self.result == TestingFarmResult.passed:
            status = BaseCommitStatus.success
            summary = self.summary or "Tests passed ..."
        elif self.result == TestingFarmResult.failed:
            status = BaseCommitStatus.failure
            summary = self.summary or "Tests failed ..."
            failure = True
        elif self.result == TestingFarmResult.canceled:
            status = BaseCommitStatus.neutral
            summary = self.summary or "Tests canceled ..."
        else:
            status = BaseCommitStatus.error
            summary = self.summary or "Error ..."

        if self.result == TestingFarmResult.running:
            self.pushgateway.test_runs_started.inc()
        else:
            self.pushgateway.test_runs_finished.inc()
            test_run_time = elapsed_seconds(
                begin=test_run_model.submitted_time, end=datetime.now(timezone.utc)
            )
            self.pushgateway.test_run_finished_time.observe(test_run_time)

        test_run_model.set_web_url(self.log_url)
        url = get_testing_farm_info_url(test_run_model.id) if test_run_model else None
        self.testing_farm_job_helper.report_status_to_tests_for_test_target(
            state=status,
            description=summary,
            target=test_run_model.target,
            url=url if url else self.log_url,
            links_to_external_services={"Testing Farm": self.log_url},
        )
        if failure:
            self.testing_farm_job_helper.notify_about_failure_if_configured(
                packit_dashboard_url=url,
                logs_url=self.log_url,
            )

        test_run_model.set_status(self.result, created=self.created)

        return TaskResults(success=True, details={})
