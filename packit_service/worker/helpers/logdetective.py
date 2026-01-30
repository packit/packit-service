# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
Helper class for triggering Log Detective from within the koji task handler.
"""

import logging

import requests

from packit_service.events import koji
from packit_service.models import (
    LogDetectiveBuildSystem,
    LogDetectiveResult,
    LogDetectiveRunGroupModel,
    LogDetectiveRunModel,
)
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.result import TaskResults

logger = logging.getLogger(__name__)


class LogDetectiveKojiTriggerHelper:
    """
    Trigger Log Detective interface server for an analysis of a failed Downstream Koji build.
    """

    __test__ = False

    def __init__(self, koji_event: koji.result.Task, pushgateway: Pushgateway, url: str):
        self.koji_event = koji_event
        self.artifacts = {
            "build.log": self.koji_event.get_koji_build_logs_url(self.koji_event.task_id),
        }
        self.url = url
        self.pushgateway = pushgateway

    def trigger_log_detective_analysis(self) -> TaskResults:
        """
        Gather relevant data and send a request to LogDetective for the failed Koji build.
        This function assumes that the `self.koji_event` is already in the failed state,
        and that the `self.koji_event.build_model` is set, and can be directly used.
        """

        endpoint_url = f"{self.url}/api/analyze"
        request_json = {
            "artifacts": self.artifacts,
            "target_build": str(self.koji_event.task_id),
            "build_system": LogDetectiveBuildSystem.koji.value,
            "commit_sha": self.koji_event.commit_sha,
            "project_url": self.koji_event.project_url,
            "pr_id": self.koji_event.pr_id,
        }

        try:
            response = requests.post(endpoint_url, json=request_json, timeout=30)
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

        build_target = self.koji_event.build_model

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
