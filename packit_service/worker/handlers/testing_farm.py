# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
This file defines classes for job handlers specific for Testing farm
"""
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, List, Tuple, Type

from celery import Task
from celery import signature

from packit.config import JobConfig, JobType
from packit.config.package_config import PackageConfig
from packit_service.models import (
    AbstractTriggerDbType,
    TFTTestRunTargetModel,
    CoprBuildTargetModel,
    BuildStatus,
    TestingFarmResult,
    JobTriggerModel,
    PipelineModel,
    TFTTestRunGroupModel,
)
from packit_service.service.urls import (
    get_testing_farm_info_url,
    get_copr_build_info_url,
)
from packit_service.worker.mixin import ConfigFromEventMixin
from packit_service.utils import dump_job_config, dump_package_config, elapsed_seconds
from packit_service.worker.checker.abstract import Checker
from packit_service.worker.checker.testing_farm import (
    CanActorRunJob,
    IsEventForJob,
    IsEventOk,
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
from packit_service.worker.helpers.testing_farm import TestingFarmJobHelper
from packit_service.worker.reporting import StatusReporter, BaseCommitStatus
from packit_service.worker.result import TaskResults
from packit_service.worker.handlers.mixin import (
    GetCoprBuildMixin,
    GetTestingFarmJobHelperMixin,
)
from packit_service.worker.mixin import PackitAPIWithDownstreamMixin
from packit_service.worker.handlers.mixin import GetGithubCommentEventMixin

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
        testing_farm_group_id: Optional[int] = None,
        build_id: Optional[int] = None,
    ):
        super().__init__(
            package_config=package_config,
            job_config=job_config,
            event=event,
            celery_task=celery_task,
        )
        self.build_id = build_id
        self._testing_farm_group_id = testing_farm_group_id
        self._testing_farm_job_helper: Optional[TestingFarmJobHelper] = None

    @staticmethod
    def get_checkers() -> Tuple[Type[Checker], ...]:
        return (
            IsEventOk,
            CanActorRunJob,
        )

    def _get_or_create_group(
        self, builds: Dict[str, CoprBuildTargetModel]
    ) -> TFTTestRunGroupModel:
        """Creates a TFTTestRunGroup.

        If a group is already attached to this handler, it returns the
        existing group instead.

        Args:
            builds: Dict mapping Testing Farm target (e.g. f37) to the
                corresponding copr build.

        Returns:
            The existing or created test run group.

        """
        if self._testing_farm_group_id is not None:
            return TFTTestRunGroupModel.get_by_id(self._testing_farm_group_id)

        run_model = (
            PipelineModel.create(
                type=self.db_trigger.job_trigger_model_type,
                trigger_id=self.db_trigger.id,
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
        for target, build in builds.items():
            test_builds = [build] if build else []
            TFTTestRunTargetModel.create(
                pipeline_id=None,
                identifier=self.job_config.identifier,
                commit_sha=self.data.commit_sha,
                status=TestingFarmResult.new,
                target=target,
                web_url=None,
                test_run_group=group,
                copr_build_targets=test_builds,
                # In _payload() we ask TF to test commit_sha of fork (PR's source).
                # Store original url. If this proves to work, make it a separate column.
                data={"base_project_url": self.project.get_web_url()},
            )

        return group

    def run_copr_build_handler(self, event_data: dict, number_of_builds: int):
        for _ in range(number_of_builds):
            self.pushgateway.copr_builds_queued.inc()

        signature(
            TaskName.copr_build.value,
            kwargs={
                "package_config": dump_package_config(self.package_config),
                "job_config": dump_job_config(
                    job_config=self.testing_farm_job_helper.job_build_or_job_config
                ),
                "event": event_data,
            },
        ).apply_async()

    def run_with_copr_builds(self, targets: List[str], failed: Dict):
        targets_without_builds = set()
        targets_with_builds = {}

        copr_build = None
        for target in targets:
            chroot = self.testing_farm_job_helper.test_target2build_target(target)
            if self.build_id:
                copr_build = CoprBuildTargetModel.get_by_id(self.build_id)
            else:
                copr_build = self.testing_farm_job_helper.get_latest_copr_build(
                    target=chroot, commit_sha=self.data.commit_sha
                )

            if copr_build:
                targets_with_builds[target] = copr_build
            else:
                targets_without_builds.add(chroot)

        # Trigger copr build for targets missing build
        if targets_without_builds:
            logger.info(
                f"Missing Copr build for targets {targets_without_builds} in "
                f"{self.testing_farm_job_helper.job_owner}/"
                f"{self.testing_farm_job_helper.job_project}"
                f" and commit:{self.data.commit_sha}, running a new Copr build."
            )

            for missing_target in targets_without_builds:
                self.testing_farm_job_helper.report_status_to_tests_for_chroot(
                    state=BaseCommitStatus.pending,
                    description="Missing Copr build for this target, "
                    "running a new Copr build.",
                    url="",
                    chroot=missing_target,
                )

            event_data = self.data.get_dict()
            event_data["build_targets_override"] = list(targets_without_builds)
            self.run_copr_build_handler(event_data, len(targets_without_builds))

        if not targets_with_builds:
            return

        group = self._get_or_create_group(targets_with_builds)
        for test_run in group.grouped_targets:
            copr_build = test_run.copr_builds[0]
            if copr_build.status in (BuildStatus.failure, BuildStatus.error):
                logger.info(
                    "The latest build was not successful, not running tests for it."
                )
                self.testing_farm_job_helper.report_status_to_tests_for_test_target(
                    state=BaseCommitStatus.failure,
                    description="The latest build was not successful, "
                    "not running tests for it.",
                    target=test_run.target,
                    url=get_copr_build_info_url(copr_build.id),
                )
                continue
            elif copr_build.status in (
                BuildStatus.pending,
                BuildStatus.waiting_for_srpm,
            ):
                logger.info(
                    "The latest build has not finished yet, "
                    "waiting until it finishes before running tests for it."
                )
                self.testing_farm_job_helper.report_status_to_tests_for_test_target(
                    state=BaseCommitStatus.pending,
                    description="The latest build has not finished yet, "
                    "waiting until it finishes before running tests for it.",
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
                msg = "Build required, CoprBuildHandler task sent."
                self.run_copr_build_handler(
                    self.data.get_dict(),
                    len(self.testing_farm_job_helper.build_targets),
                )
            logger.info(msg)
            return TaskResults(
                success=True,
                details={"msg": msg},
            )

        failed: Dict[str, str] = {}

        if self.testing_farm_job_helper.skip_build:
            group = self._get_or_create_group({target: None for target in targets})
            for test_run in group.grouped_targets:
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
    JobHandler, ConfigFromEventMixin, PackitAPIWithDownstreamMixin
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
        self._db_trigger: Optional[AbstractTriggerDbType] = None
        self.created = event.get("created")

    @staticmethod
    def get_checkers() -> Tuple[Type[Checker], ...]:
        return (IsEventForJob,)

    @property
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        if not self._db_trigger:
            run_model = TFTTestRunTargetModel.get_by_pipeline_id(
                pipeline_id=self.pipeline_id
            )
            if run_model:
                self._db_trigger = run_model.get_trigger_object()
        return self._db_trigger

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
            test_run_time = elapsed_seconds(
                begin=test_run_model.submitted_time, end=datetime.now(timezone.utc)
            )
            self.pushgateway.test_run_finished_time.observe(test_run_time)

        test_run_model.set_web_url(self.log_url)

        trigger = JobTriggerModel.get_or_create(
            type=self.db_trigger.job_trigger_model_type,
            trigger_id=self.db_trigger.id,
        )
        status_reporter = StatusReporter.get_instance(
            project=self.project,
            commit_sha=self.data.commit_sha,
            packit_user=self.service_config.get_github_account_name(),
            trigger_id=trigger.id if trigger else None,
            pr_id=self.data.pr_id,
        )
        status_reporter.report(
            state=status,
            description=summary,
            url=get_testing_farm_info_url(test_run_model.id)
            if test_run_model
            else self.log_url,
            links_to_external_services={"Testing Farm": self.log_url},
            check_names=TestingFarmJobHelper.get_test_check_cls(
                test_run_model.target, identifier=self.job_config.identifier
            ),
        )

        test_run_model.set_status(self.result, created=self.created)

        return TaskResults(success=True, details={})
