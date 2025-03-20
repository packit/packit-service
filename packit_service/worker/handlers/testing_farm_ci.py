# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
This file defines classes for job handlers specific for Testing farm
"""

import logging
from typing import Optional

from celery import Task
from packit.config import JobConfig
from packit.config.package_config import PackageConfig

from packit_service.events import koji
from packit_service.models import (
    AbstractProjectObjectDbType,
    BuildStatus,
    CoprBuildTargetModel,
    KojiBuildTargetModel,
    PipelineModel,
    ProjectEventModel,
    TestingFarmResult,
    TFTTestRunGroupModel,
    TFTTestRunTargetModel,
)
from packit_service.service.urls import (
    get_copr_build_info_url,
)
from packit_service.worker.checker.abstract import Checker
from packit_service.worker.handlers.abstract import (
    RetriableJobHandler,
    TaskName,
    reacts_to_as_fedora_ci,
)
from packit_service.worker.handlers.mixin import (
    GetKojiBuildJobHelperMixin,
    GetKojiScratchBuildEventMixin,
)
from packit_service.worker.helpers.testing_farm import TestingFarmJobHelper
from packit_service.worker.mixin import PackitAPIWithDownstreamMixin
from packit_service.worker.reporting import BaseCommitStatus
from packit_service.worker.result import TaskResults

logger = logging.getLogger(__name__)


@reacts_to_as_fedora_ci(event=koji.result.Task)
class DownstreamTestingFarmHandler(
    RetriableJobHandler,
    PackitAPIWithDownstreamMixin,
    GetKojiBuildJobHelperMixin,
    GetKojiScratchBuildEventMixin,
):
    """
    TestingFarm hadler for CI is built around Koji builds
    instead of Copr builds.
    """

    __test__ = False

    task_name = TaskName.downstream_testing_farm

    def __init__(
        self,
        package_config: PackageConfig,
        job_config: JobConfig,
        event: dict,
        celery_task: Task,
        testing_farm_target_id: Optional[int] = None,
        task_id: Optional[int] = None,
    ):
        super().__init__(
            package_config=package_config,
            job_config=job_config,
            event=event,
            celery_task=celery_task,
        )
        self._db_project_object: Optional[AbstractProjectObjectDbType] = None
        self._db_project_event: Optional[ProjectEventModel] = None
        self._build: Optional[KojiBuildTargetModel] = None

        self._testing_farm_target_id = testing_farm_target_id
        self._testing_farm_ci_job_helper: Optional[TestingFarmJobHelper] = None

    @property
    def task_id(self) -> int:
        return self.koji_task_event.task_id

    @property
    def target(self) -> str:
        return self.koji_task_event.target

    @property
    def build(self) -> Optional[KojiBuildTargetModel]:
        if not self._build:
            self._build = KojiBuildTargetModel.get_by_task_id(
                task_id=self.task_id,
            )
        return self._build

    @staticmethod
    def get_checkers() -> tuple[type[Checker], ...]:
        return (
            # IsJobConfigTriggerMatching,
            # IsEventOk,
            # IsCoprBuildDefined,
            # CanActorRunJob,
            # IsIdentifierFromCommentMatching,
            # IsLabelFromCommentMatching,
        )

    def _get_or_create_group(
        self,
        builds: dict[str, CoprBuildTargetModel | KojiBuildTargetModel],
    ) -> tuple[TFTTestRunGroupModel, list[TFTTestRunTargetModel]]:
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
                    copr_build_targets=test_builds,  # TODO -> RENAME THIS FIELD TO BUILD TARGETS (both koji or copr)
                    # In _payload() we ask TF to test commit_sha of fork (PR's source).
                    # Store original url. If this proves to work, make it a separate column.
                    data={"base_project_url": self.project.get_web_url()},
                ),
            )

        return group, runs

    def run_with_koji_builds(self, targets: dict[str, int], failed: dict):
        targets_without_successful_builds = set()
        targets_with_builds = {}

        for target in targets:
            chroot = self.testing_farm_job_helper.test_target2build_target(target)
            if self.build_id:
                copr_build = CoprBuildTargetModel.get_by_id(self.build_id)
            else:
                copr_build = self.testing_farm_job_helper.get_latest_copr_build(
                    target=chroot,
                    commit_sha=self.data.commit_sha,
                )

            if copr_build and copr_build.status not in (
                BuildStatus.failure,
                BuildStatus.error,
            ):
                targets_with_builds[target] = copr_build
            else:
                targets_without_successful_builds.add(chroot)

        # Report targets missing successful build
        if targets_without_successful_builds:
            logger.info(
                f"Missing successful Copr build for targets {targets_without_successful_builds} in "
                f"{self.testing_farm_job_helper.job_owner}/"
                f"{self.testing_farm_job_helper.job_project}"
                f" and commit:{self.data.commit_sha}, tests won't be triggered for the target.",
            )

            for missing_target in targets_without_successful_builds:
                description = (
                    "Missing successful Copr build for this target, "
                    "please trigger the build first. "
                )
                self.testing_farm_job_helper.report_status_to_tests_for_chroot(
                    state=BaseCommitStatus.neutral,
                    description=description,
                    url="",
                    chroot=missing_target,
                )

        if not targets_with_builds:
            return

        group, test_runs = self._get_or_create_group(targets_with_builds)
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

    def run_for_arch(
        self,
        test_run: "TFTTestRunTargetModel",
        arch: str,
        id: int,
        failed: dict,
    ):
        if self.celery_task.retries == 0:
            self.pushgateway.test_runs_queued.inc()
        result = self.testing_farm_job_helper.run_testing_farm(
            test_run=test_run,
            build=build,
        )
        if not result["success"]:
            failed[test_run.target] = result.get("details")

    def run_for_arches(self, failed: dict):
        self._get_or_create_group(self.build)
        for arch, id in self.koji_build_event.rpm_build_task_ids:
            self.run_test_for_koji_build(arch, id, failed)

    def run(self) -> TaskResults:
        logger.debug(f"Run tests for koji task event with task_id: {self.task_id}")

        failed: dict[str, str] = {}

        self.run_for_arches(failed=failed)

        if not failed:
            return TaskResults(success=True, details={})

        result_details = {"msg": f"Failed testing farm targets: '{failed.keys()}'."}
        result_details.update(failed)

        return TaskResults(success=False, details=result_details)

        # if koji_build_target:
        #    run_model = koji_build_target.group_of_targets.runs[-1]
        ## this should not happen as we react only to Koji builds done by us,
        ## but let's cover the case
        # else:
        #    run_model = PipelineModel.create(
        #        self.data.db_project_event,
        #        package_name=self.get_package_name(),
        #    )

        # group = BodhiUpdateGroupModel.create(run_model)
        # BodhiUpdateTargetModel.create(
        #    target=koji_build_data.dist_git_branch,
        #    koji_nvrs=koji_build_data.nvr,
        #    status="queued",
        #    bodhi_update_group=group,
        # )
