# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
This file defines classes for job handlers specific for Testing farm
"""
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, List

from celery import signature
from packit.config import JobConfig, JobType
from packit.config.package_config import PackageConfig

from packit_service.models import (
    AbstractTriggerDbType,
    TFTTestRunTargetModel,
    CoprBuildTargetModel,
    TestingFarmResult,
    JobTriggerModel,
)
from packit_service.worker.events import (
    TestingFarmResultsEvent,
    PullRequestCommentGithubEvent,
    MergeRequestCommentGitlabEvent,
    PullRequestCommentPagureEvent,
    CheckRerunCommitEvent,
    CheckRerunPullRequestEvent,
    PullRequestGithubEvent,
    PushGitHubEvent,
    PushGitlabEvent,
    MergeRequestGitlabEvent,
    AbstractPRCommentEvent,
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
    run_for_check_rerun,
    get_packit_commands_from_comment,
)
from packit_service.worker.monitoring import measure_time
from packit_service.worker.reporting import StatusReporter, BaseCommitStatus
from packit_service.worker.result import TaskResults
from packit_service.worker.testing_farm import TestingFarmJobHelper
from packit_service.constants import (
    PG_BUILD_STATUS_SUCCESS,
    INTERNAL_TF_TESTS_NOT_ALLOWED,
    INTERNAL_TF_BUILDS_AND_TESTS_NOT_ALLOWED,
)
from packit_service.utils import dump_job_config, dump_package_config

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
        build_id: Optional[int] = None,
    ):
        super().__init__(
            package_config=package_config,
            job_config=job_config,
            event=event,
        )
        self.build_id = build_id
        self._db_trigger: Optional[AbstractTriggerDbType] = None
        self._testing_farm_job_helper: Optional[TestingFarmJobHelper] = None

    def check_if_actor_can_run_job_and_report(self, actor: str) -> bool:
        """
        The job is not allowed for external contributors when using internal TF.
        """
        if self.job_config.metadata.use_internal_tf and not self.project.can_merge_pr(
            actor
        ):
            message = (
                INTERNAL_TF_BUILDS_AND_TESTS_NOT_ALLOWED
                if self.testing_farm_job_helper.job_build
                else INTERNAL_TF_TESTS_NOT_ALLOWED
            )
            self.testing_farm_job_helper.report_status_to_tests(
                description=message[0].format(actor=actor),
                state=BaseCommitStatus.neutral,
                markdown_content=message[1],
            )
            return False
        return True

    def pre_check(self) -> bool:
        return not (
            self.testing_farm_job_helper.skip_build
            and self.is_copr_build_comment_event()
        )

    @property
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        if not self._db_trigger:
            # copr build end
            if self.build_id:
                build = CoprBuildTargetModel.get_by_id(self.build_id)
                self._db_trigger = build.get_trigger_object()
            # other events
            else:
                self._db_trigger = self.data.db_trigger
        return self._db_trigger

    @property
    def testing_farm_job_helper(self) -> TestingFarmJobHelper:
        if not self._testing_farm_job_helper:
            self._testing_farm_job_helper = TestingFarmJobHelper(
                service_config=self.service_config,
                package_config=self.package_config,
                project=self.project,
                metadata=self.data,
                db_trigger=self.db_trigger,
                job_config=self.job_config,
                build_targets_override=self.data.build_targets_override,
                tests_targets_override=self.data.tests_targets_override,
            )
        return self._testing_farm_job_helper

    def build_required(self) -> bool:
        return not self.testing_farm_job_helper.skip_build and (
            self.data.event_type
            in (
                PushGitHubEvent.__name__,
                PushGitlabEvent.__name__,
                PullRequestGithubEvent.__name__,
                MergeRequestGitlabEvent.__name__,
            )
            or self.is_copr_build_comment_event()
        )

    def is_comment_event(self) -> bool:
        return self.data.event_type in (
            PullRequestCommentGithubEvent.__name__,
            MergeRequestCommentGitlabEvent.__name__,
            PullRequestCommentPagureEvent.__name__,
        )

    def is_copr_build_comment_event(self) -> bool:
        return self.is_comment_event() and get_packit_commands_from_comment(
            self.data.event_dict.get("comment")
        )[0] in ("build", "copr-build")

    def run_copr_build_handler(self, event_data: dict, number_of_builds: int):
        for _ in range(number_of_builds):
            self.pushgateway.copr_builds_queued.inc()

        signature(
            TaskName.copr_build.value,
            kwargs={
                "package_config": dump_package_config(self.package_config),
                "job_config": dump_job_config(self.job_config),
                "event": event_data,
            },
        ).apply_async()

    def run_with_copr_builds(self, targets: List[str], failed: Dict):
        targets_without_builds = set()
        targets_with_builds = {}

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
                self.testing_farm_job_helper.report_status_to_test_for_chroot(
                    state=BaseCommitStatus.pending,
                    description="Missing Copr build for this target, "
                    "running a new Copr build.",
                    url="",
                    chroot=missing_target,
                )

            event_data = self.data.get_dict()
            event_data["build_targets_override"] = list(targets_without_builds)
            self.run_copr_build_handler(event_data, len(targets_without_builds))

        for target, copr_build in targets_with_builds.items():
            if copr_build.status != PG_BUILD_STATUS_SUCCESS:
                logger.info(
                    "The latest build was not successful, not running tests for it."
                )
                self.testing_farm_job_helper.report_status_to_test_for_test_target(
                    state=BaseCommitStatus.failure,
                    description="The latest build was not successful, "
                    "not running tests for it.",
                    target=target,
                    url=get_copr_build_info_url(copr_build.id),
                )
                continue

            logger.info(f"Running testing farm for {copr_build}:{target}.")
            self.run_for_target(target=target, build=copr_build, failed=failed)

    def run_for_target(
        self,
        target: str,
        failed: Dict,
        build: Optional[CoprBuildTargetModel] = None,
    ):
        self.pushgateway.test_runs_queued.inc()
        result = self.testing_farm_job_helper.run_testing_farm(
            build=build, target=target
        )
        if not result["success"]:
            failed[target] = result.get("details")

    def run(self) -> TaskResults:

        # TODO: once we turn handlers into respective celery tasks, we should iterate
        #       here over *all* matching jobs and do them all, not just the first one
        logger.debug(f"Test job config: {self.testing_farm_job_helper.job_tests}")
        targets = list(self.testing_farm_job_helper.tests_targets)
        logger.debug(f"Targets to run the tests: {targets}")

        if self.build_required():
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
            for target in targets:
                self.run_for_target(target=target, failed=failed)

        else:
            self.run_with_copr_builds(targets=targets, failed=failed)

        if not failed:
            return TaskResults(success=True, details={})

        result_details = {"msg": f"Failed testing farm targets: '{failed.keys()}'."}
        result_details.update(failed)

        return TaskResults(success=False, details=result_details)


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
        self.summary = event.get("summary")
        self._db_trigger: Optional[AbstractTriggerDbType] = None
        self.created = event.get("created")

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

        test_run_model.set_status(self.result, created=self.created)

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
            test_run_time = measure_time(
                end=datetime.now(timezone.utc), begin=test_run_model.submitted_time
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

        return TaskResults(success=True, details={})
