# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
This file defines classes for job handlers specific for Log Detective
"""

import logging
import requests
from datetime import datetime, timezone
from typing import Optional, Union

from packit.config import JobConfig
from packit.config.package_config import PackageConfig

from packit_service.constants import KojiTaskState
from packit_service.events import (
    logdetective,
    koji,
    copr,
)
from packit_service.models import (
    BuildStatus,
    CoprBuildTargetModel,
    KojiBuildTargetModel,
    LogDetectiveBuildSystem,
    LogDetectiveResult,
    LogDetectiveRunGroupModel,
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


@reacts_to_as_fedora_ci(event=koji.result.Task)
@reacts_to_as_fedora_ci(event=copr.End)
class DownstreamLogDetectiveTriggerHandler(
    FedoraCIJobHandler,
    PackitAPIWithDownstreamMixin,
    ConfigFromEventMixin,
):
    __test__ = False
    task_name = TaskName.downstream_log_detective_trigger
    check_name = "Log Detective Trigger"

    def __init__(self, package_config: PackageConfig, job_config: JobConfig, event: dict):
        super().__init__(package_config, job_config, event)
        self.build_system: Optional[LogDetectiveBuildSystem] = None
        self.build_system_event: Union[copr.End, koji.result.Task, None] = None
    
        # so far we only know how to get the one log file
        # "builder-live.log" for Copr builds, "build.log" for Koji
        # perhaps in the future we would need to figure out how to get other .log files
        self.artifacts = {}
        if self.data.event_type == copr.End:
            self.build_system = LogDetectiveBuildSystem.copr
            self.build_system_event = copr.End.from_event_dict(self.data.event_dict)
            self.build_identifier = self.build_system_event.build_id
            self.artifacts["builder-live.log"] = self.build_system_event.get_copr_build_logs_url()
        elif self.data.event_type == koji.result.Task:
            self.build_system = LogDetectiveBuildSystem.koji
            self.build_system_event = koji.result.Task.from_event_dict(self.data.event_dict)
            self.build_identifier = self.build_system_event.task_id
            self.artifacts["build.log"] = self.build_system_event.get_koji_build_logs_url(self.build_system_event.task_id)
        else:
            msg = f"Unknown event type: {self.data.event_type}"
            logger.error(msg)
            return

        self.pr_id = self.build_system_event.pr_id
        self.commit_sha = self.build_system_event.commit_sha
        self.project_url = self.build_system_event.project_url

    @staticmethod
    def get_checkers() -> tuple[type[Checker], ...]:
        """Downstream Log Detective trigger does not require any checks."""
        return ()

    def run(self) -> TaskResults:
        """
        Determine the outcome of the build (success or failure) and store it in self.build_succeeded.
        This supports both Koji result tasks and Copr End events.
        If the build failed, gather relevant data and send a request to LogDetective.
        """
        failed = False
        status = None
        if self.build_system == LogDetectiveBuildSystem.copr:
            status = self.build_system_event.status
            failed = self.build_system_event.status == BuildStatus.failure
        elif self.build_system == LogDetectiveBuildSystem.koji:
            status = self.build_system_event.state
            failed = self.build_system_event.state == KojiTaskState.failed

        if not failed:
            msg="Build did not fail, no request to Log Detective sent."
            return TaskResults(
                success=True,
                details={
                    "msg": msg,
                    "build_system": str(self.build_system),
                    "status": status,
                },
            )

        logdetective_url = self.service_config.logdetective_url

        request_json = {
            "artifacts": self.artifacts,
            "target_build": str(self.build_identifier),
            "build_system": self.build_system.value,
            "commit_sha": self.commit_sha,
            "project_url": self.project_url,
            "pr_id": self.pr_id
        }

        try:
            response = requests.post(logdetective_url, json=request_json, timeout=30)
            response.raise_for_status()
        except (requests.exceptions.HTTPError, requests.exceptions.RequestException) as e:
            msg = f"Failed to trigger Log Detective: {e}"
            logger.error(msg, exc_info=True)
            return TaskResults(success=False, details={"msg": msg})

        try:
            data = response.json()
            log_detective_analysis_id = data.get("log_detective_analysis_id")
            log_detective_analysis_start = data.get("creation_time")
        except requests.exceptions.JSONDecodeError as e:
            msg = f"Failed to parse Log Detective response: {e}"
            logger.error(msg, exc_info=True)  # exc_info=True will include stack-trace in msg
            return TaskResults(success=False, details={"msg": msg})

        self.pushgateway.log_detective_runs_started.inc()

        build_target = None
        if self.build_system == LogDetectiveBuildSystem.copr:
            build_target = CoprBuildTargetModel.get_by_build_id(
                    self.build_identifier, target=self.build_system_event.target
            )
        else:
            build_target = KojiBuildTargetModel.get_by_task_id(
                self.build_identifier, target=self.build_system_event.target
            )

        if build_target is None:
            msg = f"Could not obtain build target model: {self.build_system_event}"
            logger.error(msg)
            return TaskResults(success=False, details={"msg": msg})

        pipelines = build_target.group_of_targets.runs
        group_run = LogDetectiveRunGroupModel.create(pipelines)

        LogDetectiveRunModel.create(
            LogDetectiveResult.running,
            self.build_identifier,
            self.build_system_event.target,
            self.build_system.value,
            log_detective_analysis_id,
            group_run,
        )

        build_target.add_log_detective_run(log_detective_analysis_id)

        msg = "Successfully triggered Log Detective"
        return TaskResults(
            success=True,
            details={
                "msg": msg,
                "request_json": request_json,
                "log_detective_analysis_id": log_detective_analysis_id,
                "log_detective_analysis_start": log_detective_analysis_start,
            },
        )


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
        self.build_system = LogDetectiveBuildSystem(event.get("build_system"))
        self._ci_helper: Optional[FedoraCIHelper] = None
        self.log_detective_response = event.get("log_detective_response")
        self.branch_name = ""

    @staticmethod
    def get_checkers() -> tuple[type[Checker], ...]:
        """Downstream Log Detective analysis results don't need any additional checking."""
        return ()

    def _run(self) -> TaskResults:
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

        if self.build_system == LogDetectiveBuildSystem.copr:
            build = CoprBuildTargetModel.get_by_id(log_detective_run_model.copr_build_target_id)
        elif self.build_system == LogDetectiveBuildSystem.koji:
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
