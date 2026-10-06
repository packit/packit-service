# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""Submit, babysit, and report durable Log Detective analyses."""

import logging
from datetime import datetime
from typing import Optional
from uuid import uuid4

import requests

from packit_service.celerizer import celery_app
from packit_service.config import ServiceConfig
from packit_service.constants import DEFAULT_JOB_TIMEOUT
from packit_service.events import koji
from packit_service.events.event_data import EventData
from packit_service.models import (
    GitBranchModel,
    LogDetectiveBuildSystem,
    LogDetectiveResult,
    LogDetectiveRunGroupModel,
    LogDetectiveRunModel,
    ProjectReleaseModel,
    PullRequestModel,
)
from packit_service.service.urls import get_logdetective_info_url
from packit_service.utils import elapsed_seconds, get_check_name_prefix, verify_artifact
from packit_service.worker.helpers.fedora_ci import FedoraCIHelper
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.reporting import BaseCommitStatus

logger = logging.getLogger(__name__)
ACTIVE = {"scheduled", "in_progress", "cancelling"}
TERMINAL = {"done", "error", "cancelled"}
INITIAL_POLL_DELAY = 120


class LogDetectiveRunHelper:
    """Advance one saved analysis for a per-run task or recovery scan."""

    def __init__(self, run_id: int):
        self.run_id = run_id
        self.service_config = ServiceConfig.get_service_config()

    @staticmethod
    def _envelope(response: requests.Response, run: LogDetectiveRunModel, code: int) -> dict:
        """Validate a generic task response for the requested run and HTTP status."""
        if response.status_code != code:
            response.raise_for_status()
            raise ValueError(f"Unexpected Log Detective HTTP status {response.status_code}")
        body = response.json()
        if (
            not isinstance(body, dict)
            or body.get("id") != run.analysis_id
            or body.get("taskType") != "generic"
            or body.get("status") not in ACTIVE | TERMINAL
            or not isinstance(body.get("createdAt"), str)
        ):
            raise ValueError("Invalid Log Detective task envelope")
        return body

    def submit_analysis(self, run: LogDetectiveRunModel, url: str, token: str) -> None:
        """Replay the saved request and UUID after an uncertain submission."""
        response = requests.post(
            f"{url.rstrip('/')}/analyze",
            json=run.analysis_request(),
            headers={"Authorization": f"Bearer {token}"},
            timeout=self.service_config.logdetective_request_timeout,
        )
        self._envelope(response, run, 202)
        LogDetectiveRunModel.mark_api_accepted(self.run_id)

    def _finish(
        self, status: LogDetectiveResult, result: Optional[dict], error: Optional[str]
    ) -> bool:
        """Report first, then persist the first terminal transition."""
        if status not in (LogDetectiveResult.complete, LogDetectiveResult.error):
            raise ValueError(f"Invalid terminal Log Detective status: {status}")
        try:
            self._report(status)
            submitted_time = LogDetectiveRunModel.finish_api_run(self.run_id, status, result, error)
        except Exception:
            # Leave the run running so Celery or Beat can fetch the result again.
            logger.exception("Could not finish Log Detective run %s", self.run_id)
            return False
        if submitted_time is None:
            return True
        self._record_completion_metrics(submitted_time)
        return True

    def _record_completion_metrics(self, submitted_time: datetime) -> None:
        """Count only a run's first persisted terminal transition."""
        try:
            metrics = Pushgateway()
            metrics.log_detective_runs_finished.inc()
            metrics.log_detective_run_finished.observe(
                elapsed_seconds(begin=submitted_time, end=datetime.utcnow())
            )
            metrics.push()
        except Exception:
            logger.exception(
                "Could not publish Log Detective completion metrics for run %s", self.run_id
            )

    def _api_failure(self, exc: Exception) -> None:
        """Leave a failed API request pending for Celery or Beat to retry."""
        logger.warning("Log Detective request for run %s failed: %s", self.run_id, exc)

    def _report(self, status: LogDetectiveResult) -> None:
        """Send the proposed outcome before exposing it as terminal in Packit."""
        context = LogDetectiveRunModel.get_report_context(self.run_id, status)
        if context is None:
            return

        data = EventData.from_event_dict(
            {
                "project_url": context.project_url,
                "commit_sha": context.commit_sha,
                "pr_id": context.pr_id,
            }
        )
        project = self.service_config.get_project(url=context.project_url)
        branch = context.branch
        if context.pr_id:
            branch = project.get_pr(context.pr_id).target_branch
        prefix = get_check_name_prefix(self.service_config)
        check_name = f"{prefix} - Log Detective analysis" if prefix else "Log Detective analysis"
        helper = FedoraCIHelper(project, data, context.target or branch)
        helper.report(
            state=(
                BaseCommitStatus.success
                if context.status == LogDetectiveResult.complete
                else BaseCommitStatus.error
            ),
            description=f"Log Detective analysis status: {context.status.value}",
            url=get_logdetective_info_url(self.run_id),
            check_name=check_name,
        )

    def advance(self) -> None:
        """Perform one submission or poll, then report a terminal result."""
        run = LogDetectiveRunModel.get_by_id(self.run_id)
        if not run or run.selected_logs is None or run.status != LogDetectiveResult.running:
            return
        now = datetime.utcnow()
        expired = elapsed_seconds(begin=run.submitted_time, end=now) >= DEFAULT_JOB_TIMEOUT
        try:
            if not run.accepted_time:
                if expired:
                    self._finish(LogDetectiveResult.error, None, "Log Detective analysis timed out")
                else:
                    config = self.service_config
                    self.submit_analysis(run, config.logdetective_url, config.logdetective_token)
                return

            config = self.service_config
            response = requests.get(
                f"{config.logdetective_url.rstrip('/')}/tasks/{run.analysis_id}",
                headers={"Authorization": f"Bearer {config.logdetective_token}"},
                timeout=config.logdetective_request_timeout,
            )
            body = self._envelope(response, run, 200)
            state = body["status"]
            if state in ACTIVE:
                if expired:
                    self._finish(LogDetectiveResult.error, None, "Log Detective analysis timed out")
                return
            if state == "done":
                if not isinstance(body.get("result"), dict):
                    raise ValueError("Log Detective completed without a result")
                self._finish(LogDetectiveResult.complete, body["result"], None)
            else:
                error = body.get("error")
                message = error.get("message") if isinstance(error, dict) else None
                self._finish(LogDetectiveResult.error, None, message or f"Analysis {state}")
        except requests.HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else None
            if code is not None and 400 <= code < 500 and code not in (408, 429):
                self._finish(LogDetectiveResult.error, None, f"Log Detective HTTP {code}: {exc}")
            elif expired:
                self._finish(LogDetectiveResult.error, None, "Log Detective analysis timed out")
            else:
                self._api_failure(exc)
        except (requests.RequestException, ValueError) as exc:
            if expired:
                self._finish(LogDetectiveResult.error, None, "Log Detective analysis timed out")
            else:
                self._api_failure(exc)


def check_log_detective_run(run_id: int) -> bool:
    """Advance one run; leave unexpected failures pending for a later retry."""
    try:
        run = LogDetectiveRunModel.get_by_id(run_id)
        if run is None or run.selected_logs is None or run.status != LogDetectiveResult.running:
            return True
        LogDetectiveRunHelper(run_id).advance()

        run = LogDetectiveRunModel.get_by_id(run_id)
        return run is None or run.selected_logs is None or run.status != LogDetectiveResult.running
    except Exception:
        logger.exception("Could not check Log Detective run %s", run_id)
        return False


class LogDetectiveKojiTriggerHelper:
    """Create one durable analysis per failed buildArch task.

    The Koji event identifies each failed child task. For each one, save the
    verified log URLs and generated commentary with a UUID and parent build
    link, then attempt submission. A per-run task or recovery scan can replay
    the same API request after a failed POST.
    """

    def __init__(
        self,
        koji_event: koji.result.Task,
        data: EventData,
        pushgateway: Pushgateway,
        koji_logs_url: str,
        url: str,
        logdetective_token: str,
    ):
        """Keep the failed Koji event and API settings for per-arch submission."""
        self.koji_event = koji_event
        self.data = data
        self.koji_logs_url = koji_logs_url
        self.url = url
        self.pushgateway = pushgateway
        self.token = logdetective_token
        # Create a group only after an architecture has verified logs.
        self.run_group: Optional[LogDetectiveRunGroupModel] = None

    def _format_duration(self) -> str:
        """Return a human-readable build duration, or empty string if unavailable."""
        try:
            start = float(self.koji_event.start_time)
            end = float(self.koji_event.completion_time)
        except (TypeError, ValueError):
            return ""
        seconds = end - start
        if seconds < 0:
            return ""
        return f"Build ran for {seconds:.0f} seconds before failing."

    def _build_commentary(self, arch: str) -> str:
        """Capture the build and architecture context once for later API retries."""
        build = self.koji_event.build_model
        parts = [
            "Build was executed in downstream Koji"
            " using containerized environment provided by Mock.",
            f"Package NVR: {build.nvr or 'unknown'},"
            f" target: {self.koji_event.target or 'unknown'}, arch: {arch}.",
            "Scratch build." if build.scratch else "Official (non-scratch) build.",
        ]
        db_project_object = self.koji_event.db_project_object
        if isinstance(db_project_object, PullRequestModel):
            parts.append(f"PR build (PR #{db_project_object.pr_id}).")
        elif isinstance(db_project_object, GitBranchModel):
            parts.append(f"Branch build ({db_project_object.name}).")
        elif isinstance(db_project_object, ProjectReleaseModel):
            parts.append(f"Release build (tag: {db_project_object.tag_name}).")
        duration = self._format_duration()
        if duration:
            parts.append(duration)
        if build.sidetag:
            parts.append(
                f"Built in sidetag: {build.sidetag}."
                " Sidetag builds use an isolated buildroot inheriting from the base tag;"
                " dependency resolution failures may reflect non-default package versions"
                " present in the sidetag."
            )
        parts += [
            "The build.log contains output of the package build"
            " and is the most likely source of the root cause.",
            "The mock_output.log is a general log from Mock.",
            "The root.log is a log from creation of the chroot environment.",
        ]
        if build.build_submission_stdout:
            parts.append(f"Build submission output: {build.build_submission_stdout}")
        return " ".join(parts)

    def trigger_log_detective_analysis(self) -> list[bool]:
        """Save and submit an analysis for every failed buildArch task.

        Return one boolean per architecture indicating whether its inputs were
        saved. POST failures are recovered from the saved run on a Beat tick.
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
        """Save selected logs and commentary, then attempt API submission.

        The Koji event must represent a failed build with its parent model set.
        Return false if no log URL verifies; a saved run returns true even when
        submission fails, because the saved request can be retried.
        """
        artifacts = {}
        build_arch_task_id = self.koji_event.rpm_build_task_ids[arch]

        # These logs are most likely to explain the failure; extra logs add latency.
        possible_koji_artifacts = [
            "root.log",
            "mock_output.log",
            "build.log",
        ]

        # Verify once and persist the exact selected URLs for every POST retry.
        for artifact in possible_koji_artifacts:
            url = koji.result.KojiEvent.get_koji_build_logs_url(
                rpm_build_task_id=build_arch_task_id,
                koji_logs_url=self.koji_logs_url,
                log_file=artifact,
            )
            if verify_artifact(url):
                artifacts[artifact] = url

        if len(artifacts) == 0:
            logger.warning("No artifact URLs passed verification")
            return False

        analysis_id = str(uuid4())
        selected_logs = [{"name": name, "url": url} for name, url in artifacts.items()]
        commentary = self._build_commentary(arch)

        build_target = self.koji_event.build_model

        if self.run_group is None:
            self.run_group = LogDetectiveRunGroupModel.create(
                build_target.group_of_targets.runs  # pipelines
            )

        # The LogDetectiveRunModel target identifies the architecture or chroot:
        # - "target-arch" for Koji builds (e.g. fc44-aarch64)
        # - "chroot" for Copr builds (e.g. fedora-rawhide-x86_64)

        # Note: Parent tasks for scratch builds of both arch-sensitive and arch-agnostic (noarch)
        # packages have arch=="noarch".
        #   - A) In case of noarch packages, child buildArch subtasks have "noarch" label.
        #     So parent task: arch=noarch, child buildArch task arch=whatever, but label=noarch.
        #   - B) In case of arch-sensitive buildArch subtasks, label is the same as arch.
        # Since koji can give builders with different arches for noarch packages when retriggering,
        # we have to use label to ensure that the new analysis CI job replaces the old one.

        status_arch = (
            "noarch" if self.koji_event.rpm_build_task_labels.get(arch) == "noarch" else arch
        )
        run = LogDetectiveRunModel.create(
            status=LogDetectiveResult.running,
            target_build=str(build_arch_task_id),
            target=f"{self.koji_event.target}-{status_arch}",
            build_system=LogDetectiveBuildSystem.koji,
            log_detective_analysis_id=analysis_id,
            log_detective_run_group=self.run_group,
            selected_logs=selected_logs,
            analysis_commentary=commentary,
            koji_build_target=build_target,
        )
        self.pushgateway.log_detective_runs_started.inc()
        # The saved run remains recoverable if submission or task delivery fails.
        helper = LogDetectiveRunHelper(run.id)
        try:
            helper.submit_analysis(run, self.url, self.token)
        except (requests.RequestException, ValueError) as exc:
            helper._api_failure(exc)
        except Exception:
            logger.warning(
                "Log Detective submission will be retried for run %s", run.id, exc_info=True
            )
        try:
            celery_app.send_task(
                "task.process_log_detective_run",
                args=(run.id,),
                countdown=INITIAL_POLL_DELAY,
            )
        except Exception:
            logger.exception(
                "Could not schedule Log Detective babysitting for run %s; Beat will recover it",
                run.id,
            )
        return True
