# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
This file defines classes for job handlers specific for Log Detective
"""

import logging
import requests

from packit_service.constants import KojiTaskState
from packit_service.events import (
    koji,
    copr,
)
from packit_service.models import (
    CoprBuildTargetModel,
    KojiBuildTargetModel,
    LogDetectiveBuildSystem,
    LogDetectiveResult,
    LogDetectiveRunGroupModel,
    LogDetectiveRunModel,
)
from packit_service.worker.result import TaskResults
from packit_service.worker.monitoring import Pushgateway

logger = logging.getLogger(__name__)


class LogDetectiveKojiTriggerHelper(
    # FedoraCIJobHandler
):
    """
    TODO: Docstring for LogDetectiveKojiTriggerHelper
    TODO: Look into which classes to inherit from?
    """
    __test__ = False

    def __init__(self, koji_event: koji.result.Task, pushgateway: Pushgateway, url: str):
        self.koji_event = koji_event
        self.artifacts = {}
        self.artifacts["build.log"] = (
            self.koji_event.get_koji_build_logs_url(self.koji_event.task_id)
        )
        self.url = url
        self.pushgateway = pushgateway

    def trigger_log_detective_analysis(self) -> TaskResults:
        """Gather relevant data and send a request to LogDetective for the failed Koji build."""
        status = self.koji_event.state
        failed = status == KojiTaskState.failed

        if not failed:
            msg = "Build did not fail, no request to Log Detective sent."
            return TaskResults(
                success=True,
                details={
                    "msg": msg,
                    "build_system": LogDetectiveBuildSystem.koji.value,
                    "status": status,
                },
            )

        logdetective_url = self.url

        request_json = {
            "artifacts": self.artifacts,
            "target_build": str(self.koji_event.task_id),
            "build_system": LogDetectiveBuildSystem.koji.value,
            "commit_sha": self.koji_event.commit_sha,
            "project_url": self.koji_event.project_url,
            "pr_id": self.koji_event.pr_id
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
        build_target = KojiBuildTargetModel.get_by_task_id(
            self.koji_event.task_id, target=self.koji_event.target
        )

        if build_target is None:
            msg = f"Could not obtain build target model: {self.koji_event}"
            logger.error(msg)
            return TaskResults(success=False, details={"msg": msg})

        pipelines = build_target.group_of_targets.runs
        group_run = LogDetectiveRunGroupModel.create(pipelines)

        LogDetectiveRunModel.create(
            LogDetectiveResult.running,
            str(self.koji_event.task_id),
            self.koji_event.target,
            LogDetectiveBuildSystem.koji,
            log_detective_analysis_id,
            group_run,
        )

        build_target.add_log_detective_run(log_detective_analysis_id)

        msg = "Successfully triggered Log Detective for a failed Koji build"
        return TaskResults(
            success=True,
            details={
                "msg": msg,
                "request_json": request_json,
                "log_detective_analysis_id": log_detective_analysis_id,
                "log_detective_analysis_start": log_detective_analysis_start,
            },
        )



# NOTE: Leave Copr integration for later
class LogDetectiveCoprTriggerHelper(
    # FedoraCIJobHandler,
    # PackitAPIWithDownstreamMixin,
    # ConfigFromEventMixin,
):
    """
    TODO: Docstring for LogDetectiveCoprTriggerHandler
    """
    __test__ = False
    # task_name = TaskName.log_detective_copr_trigger
    # check_name = "Log Detective Copr Trigger"

    def __init__(self, copr_event: copr.End, pushgateway: Pushgateway, url: str):
        # so far we only know how to access one log file for Copr, specifically "builder-live.log"
        # perhaps in the future we would need to figure out how to get other log files
        self.pushgateway = pushgateway
        self.url = url
        self.copr_event = copr_event
        self.artifacts = {}
        self.artifacts["builder-live.log"] = self.copr_event.get_copr_build_logs_url()

    def trigger_log_detective_analysis(self) -> TaskResults:
        """Gather relevant data and send a request to LogDetective for the failed Copr build."""

        request_json = {
            "artifacts": self.artifacts,
            "target_build": str(self.copr_event.build_id),
            "build_system": LogDetectiveBuildSystem.copr.value,
            "commit_sha": self.copr_event.commit_sha,
            "project_url": self.copr_event.project_url,
            "pr_id": self.copr_event.pr_id
        }

        try:
            response = requests.post(self.url, json=request_json, timeout=30)
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

        build_target = CoprBuildTargetModel.get_by_build_id(
                self.copr_event.build_id, target=self.copr_event.chroot
        )

        if build_target is None:
            msg = f"Could not obtain copr build target model: {self.copr_event}"
            logger.error(msg)
            return TaskResults(success=False, details={"msg": msg})

        pipelines = build_target.group_of_targets.runs
        group_run = LogDetectiveRunGroupModel.create(pipelines)

        LogDetectiveRunModel.create(
            LogDetectiveResult.running,
            str(self.copr_event.build_id),
            self.copr_event.chroot,
            LogDetectiveBuildSystem.copr,
            log_detective_analysis_id,
            group_run,
        )

        build_target.add_log_detective_run(log_detective_analysis_id)

        msg = "Successfully triggered Log Detective for a failed Copr build"
        return TaskResults(
            success=True,
            details={
                "msg": msg,
                "request_json": request_json,
                "log_detective_analysis_id": log_detective_analysis_id,
                "log_detective_analysis_start": log_detective_analysis_start,
            },
        )
