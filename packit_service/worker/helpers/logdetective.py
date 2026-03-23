# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
Helper class for triggering Log Detective from within the koji task handler.
"""

import logging
from typing import Optional

import requests

from packit_service.events import koji
from packit_service.events.event_data import EventData
from packit_service.models import (
    LogDetectiveBuildSystem,
    LogDetectiveResult,
    LogDetectiveRunGroupModel,
    LogDetectiveRunModel,
)
from packit_service.worker.monitoring import Pushgateway

logger = logging.getLogger(__name__)


class LogDetectiveKojiTriggerHelper:
    """
    Trigger Log Detective interface server for an analysis of a failed Downstream Koji build.

    We pass the full downstream build Task (with taskID of the parent) to __init__().
    This task contains information about architectures, for which the build failed.
    Arch is then used to look up the subtask ID for the buildArch,
    where the proper failed build logs can be located.
    KojiBuildTargetModel refers to the parent task -- there can be multiple
    Log Detective runs for one BuildTarget (i.e. fedora 44 build can fail
    for x86_64 and aarch64...).
    """

    __test__ = False

    def __init__(
        self,
        koji_event: koji.result.Task,
        data: EventData,
        pushgateway: Pushgateway,
        koji_logs_url: str,
        url: str,
        logdetective_token: str,
    ):
        self.koji_event = koji_event
        self.data = data
        self.koji_logs_url = koji_logs_url
        self.url = url
        self.pushgateway = pushgateway
        self.token = logdetective_token
        # run_group created after 1st succcessful trigger, right before creating RunModel
        self.run_group: Optional[LogDetectiveRunGroupModel] = None

    def trigger_log_detective_analysis(self) -> list[bool]:
        """
        Run a trigger over all arches for which we have a failed buildArch task.

        Return a list of booleans signaling if the triggers were successful.
        """

        trigger_results = []
        for arch in self.koji_event.rpm_build_failed_arch_list:
            success = self.trigger_log_detective_analysis_for_arch(arch)
            logger.info(
                f"Triggered Log Detective for a failed Koji build ("
                f"child taskID = {self.koji_event.rpm_build_task_ids[arch]}, "
                f"arch = {arch}, "
                f"trigger = {'success' if success else 'fail'})"
            )
            trigger_results.append(success)
        return trigger_results

    def trigger_log_detective_analysis_for_arch(self, arch: str) -> bool:
        """
        Gather relevant data and send a request to LogDetective for the failed Koji build.
        This function assumes that the `self.koji_event` is already in the failed state,
        and that the `self.koji_event.build_model` is set, and can be directly used.

        Instead of returning TaskResults() object, as job handlers do,
        we just return a boolean signaling whether or not the trigger succeeded.
        """

        build_arch_task_id = self.koji_event.rpm_build_task_ids[arch]
        artifacts = {
            "build.log": koji.result.KojiEvent.get_koji_build_logs_url(
                build_arch_task_id,
                self.koji_logs_url,
            )
        }

        endpoint_url = f"{self.url}/analyze"
        request_json = {
            "artifacts": artifacts,
            "target_build": str(build_arch_task_id),
            "build_system": LogDetectiveBuildSystem.koji.value,
            "commit_sha": self.data.commit_sha,
            "project_url": self.data.project_url,
            "pr_id": self.data.pr_id,
        }

        logger.debug(f"Sending Log Detective request to {endpoint_url}: {request_json}")

        try:
            response = requests.post(
                url=endpoint_url,
                json=request_json,
                timeout=30,
                headers={"Authorization": f"Bearer {self.token}"},
            )
            response.raise_for_status()
        except (requests.exceptions.HTTPError, requests.exceptions.RequestException) as e:
            msg = f"Failed to get response from Log Detective: {e}"
            logger.warning(msg, exc_info=True)
            return False

        try:
            data = response.json()
            analysis_id = data.get("log_detective_analysis_id")
            analysis_start = data.get("creation_time")
            if analysis_id is None:
                logger.warning("Log Detective response is missing log_detective_analysis_id")
                return False
            if analysis_start is None:
                logger.warning("Log Detective response is missing creation_time")
                return False
        except requests.exceptions.JSONDecodeError as e:
            msg = f"Failed to parse Log Detective response: {e}"
            logger.warning(msg, exc_info=True)
            return False

        self.pushgateway.log_detective_runs_started.inc()

        build_target = self.koji_event.build_model

        if self.run_group is None:
            self.run_group = LogDetectiveRunGroupModel.create(
                build_target.group_of_targets.runs  # pipelines
            )

        # "target" field in LDRunModel refers to:
        # - "target-arch" for Koji builds (e.g. fc44-aarch64)
        # - "chroot" for Copr builds (e.g. fedora-rawhide-x86_64)
        LogDetectiveRunModel.create(
            LogDetectiveResult.running,
            str(build_arch_task_id),
            f"{self.koji_event.target}-{arch}",
            LogDetectiveBuildSystem.koji,
            analysis_id,
            self.run_group,
        )

        build_target.add_log_detective_run(analysis_id)

        return True
