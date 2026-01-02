# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
This file defines classes for job handlers specific for Log Detective
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Union

from packit.config import JobConfig
from packit.config.package_config import PackageConfig

from packit_service.events import (
    logdetective,
)
from packit_service.models import (
    CoprBuildTargetModel,
    KojiBuildTargetModel,
    LogDetectiveResult,
    LogDetectiveRunModel,
)
from packit_service.utils import elapsed_seconds
from packit_service.worker.checker.abstract import Checker
from packit_service.worker.handlers.abstract import (
    FedoraCIJobHandler,
    TaskName,
    reacts_to_as_fedora_ci,
)
from packit_service.worker.helpers.fedora_ci import FedoraCIHelper
from packit_service.worker.mixin import ConfigFromEventMixin, PackitAPIWithDownstreamMixin
from packit_service.worker.reporting import BaseCommitStatus
from packit_service.worker.result import TaskResults

logger = logging.getLogger(__name__)


@reacts_to_as_fedora_ci(event=logdetective.Result)
class DownstreamLogDetectiveResultsHandler(
    FedoraCIJobHandler,
    PackitAPIWithDownstreamMixin,
    ConfigFromEventMixin,
):
    __test__ = False
    task_name = TaskName.downstream_log_detective_results
    check_name = "Log Detective Analysis"

    def __init__(self, package_config: PackageConfig, job_config: JobConfig, event: dict):
        super().__init__(package_config, job_config, event)

        self.status = LogDetectiveResult.from_string(event.get("status", ""))
        self.analysis_id = event.get("log_detective_analysis_id", "")
        self.log_detective_analysis_start = datetime.fromisoformat(
            event.get("log_detective_analysis_start")
        ).replace(tzinfo=None)
        self.target_build = event.get("target_build")
        self.build_system = event.get("build_system")
        self._ci_helper: Optional[FedoraCIHelper] = None
        self.log_detective_response = event.get("log_detective_response")
        self.branch_name = ""

    @staticmethod
    def get_checkers() -> tuple[type[Checker], ...]:
        """Downstream Log Detective analysis results don't need any additional checking."""
        return ()

    def run(self) -> TaskResults:
        """Submit report about result of Log Detective analysis.

        Information about Log Detective run, is retrieved and recorded state compared
        to the change. If the state matches no further processing occurs.
        Number of started runs is incremented and information about elapsed time recorded
        by Pushgateway. New state of the run is then recorded.
        """
        logger.debug(f"Log Detective run {self.analysis_id} result: {self.status}")

        if not self.project:
            msg = f"No project set for Log Detective run: {self.analysis_id}"
            logger.error(msg=msg)
            return TaskResults(success=False, details={"msg": msg})

        log_detective_run_model = LogDetectiveRunModel.get_by_log_detective_analysis_id(
            analysis_id=self.analysis_id
        )
        if not log_detective_run_model:
            msg = f"Unknown identifier received from Log Detective: {self.analysis_id}"
            logger.warning(msg=msg)
            return TaskResults(success=False, details={"msg": msg})

        if log_detective_run_model.status == self.status:
            msg = f"Log Detective result for run {self.analysis_id} already processed"
            logger.debug(msg)
            return TaskResults(success=True, details={"msg": msg})

        status = BaseCommitStatus.error
        if self.status == LogDetectiveResult.complete:
            status = BaseCommitStatus.success
        elif self.status == LogDetectiveResult.running:
            status = BaseCommitStatus.running
            self.pushgateway.log_detective_runs_started.inc()
        elif self.status == LogDetectiveResult.error or self.status == LogDetectiveResult.unknown:
            status = BaseCommitStatus.error

        if self.status != LogDetectiveResult.running:
            self.pushgateway.log_detective_runs_finished.inc()
            log_detective_run_time = elapsed_seconds(
                begin=log_detective_run_model.submitted_time,
                end=datetime.now(timezone.utc).replace(tzinfo=None),
            )
            self.pushgateway.log_detective_run_finished.observe(log_detective_run_time)

        build: Union[None, CoprBuildTargetModel, KojiBuildTargetModel] = None

        if self.build_system == "copr":
            build = CoprBuildTargetModel.get_by_id(log_detective_run_model.copr_build_target_id)
        elif self.build_system == "koji":
            build = KojiBuildTargetModel.get_by_id(log_detective_run_model.koji_build_target_id)
        if build is None:
            msg = f"No build with id: {self.target_build} found in build system {self.build_system}"
            logger.error(msg)
            return TaskResults(success=False, details={"msg": msg})

        # When dealing with a `PullRequestModel` the `get_branch_name` method
        # returns `None`. In such a case we need to get the branch in another way.
        if self.data.pr_id:
            self.branch_name = self.project.get_pr(self.data.pr_id).target_branch
        else:
            self.branch_name = build.get_branch_name()

        url = build.web_url or ""
        self.report(
            state=status, description=f"Log Detective analysis status: {self.status.value}", url=url
        )

        if self.log_detective_response:
            log_detective_run_model.set_log_detective_response(
                self.log_detective_response, self.status
            )

        log_detective_run_model.set_status(
            self.status, log_detective_analysis_start=self.log_detective_analysis_start
        )

        return TaskResults(success=True, details={})

    def report(
        self,
        state: BaseCommitStatus,
        description: str,
        url: str,
    ):
        self.ci_helper.report(
            state=state,
            description=description,
            url=url,
            check_name="Log Detective Analysis",
        )

    @property
    def ci_helper(self) -> FedoraCIHelper:
        if not self._ci_helper:
            self._ci_helper = FedoraCIHelper(
                project=self.project,
                metadata=self.data,
                target_branch=self.branch_name,
            )
        return self._ci_helper
