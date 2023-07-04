# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
This file defines classes for job handlers specific for Fedmsg events
"""

import logging
from datetime import datetime
from typing import Optional, Tuple, Type

from ogr.abstract import GitProject
from packit.config import (
    JobConfig,
    JobType,
)
from packit.config.package_config import PackageConfig
from packit_service.constants import (
    KojiBuildState,
)
from packit_service.constants import KojiTaskState
from packit_service.models import (
    AbstractProjectObjectDbType,
    KojiBuildTargetModel,
    ProjectEventModel,
)
from packit_service.service.urls import (
    get_koji_build_info_url,
)
from packit_service.worker.checker.abstract import Checker
from packit_service.worker.checker.koji import (
    PermissionOnKoji,
    IsJobConfigTriggerMatching,
)
from packit_service.worker.events import (
    CheckRerunCommitEvent,
    CheckRerunPullRequestEvent,
    CheckRerunReleaseEvent,
    KojiTaskEvent,
    MergeRequestGitlabEvent,
    PullRequestGithubEvent,
    PushGitHubEvent,
    PushGitlabEvent,
    ReleaseEvent,
    AbstractPRCommentEvent,
    ReleaseGitlabEvent,
)
from packit_service.worker.events.koji import KojiBuildEvent
from packit_service.worker.handlers.abstract import (
    JobHandler,
    TaskName,
    configured_as,
    reacts_to,
    run_for_check_rerun,
    run_for_comment,
)
from packit_service.worker.handlers.mixin import GetKojiBuildJobHelperMixin
from packit_service.worker.helpers.build.koji_build import KojiBuildJobHelper
from packit_service.worker.mixin import ConfigFromEventMixin
from packit_service.worker.mixin import PackitAPIWithDownstreamMixin
from packit_service.worker.reporting import BaseCommitStatus
from packit_service.worker.result import TaskResults

logger = logging.getLogger(__name__)


@configured_as(job_type=JobType.production_build)
@configured_as(job_type=JobType.upstream_koji_build)
@run_for_comment(command="production-build")
@run_for_comment(command="upstream-koji-build")
@run_for_check_rerun(prefix="production-build")
@run_for_check_rerun(prefix="koji-build")
@reacts_to(ReleaseEvent)
@reacts_to(ReleaseGitlabEvent)
@reacts_to(PullRequestGithubEvent)
@reacts_to(PushGitHubEvent)
@reacts_to(PushGitlabEvent)
@reacts_to(MergeRequestGitlabEvent)
@reacts_to(AbstractPRCommentEvent)
@reacts_to(CheckRerunPullRequestEvent)
@reacts_to(CheckRerunCommitEvent)
@reacts_to(CheckRerunReleaseEvent)
class KojiBuildHandler(
    JobHandler, PackitAPIWithDownstreamMixin, GetKojiBuildJobHelperMixin
):
    task_name = TaskName.upstream_koji_build

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

        # lazy property
        self._koji_build_helper: Optional[KojiBuildJobHelper] = None
        self._project: Optional[GitProject] = None

    @staticmethod
    def get_checkers() -> Tuple[Type[Checker], ...]:
        return (
            IsJobConfigTriggerMatching,
            PermissionOnKoji,
        )

    def run(self) -> TaskResults:
        return self.koji_build_helper.run_koji_build()


@configured_as(job_type=JobType.production_build)
@configured_as(job_type=JobType.upstream_koji_build)
@reacts_to(event=KojiTaskEvent)
class KojiTaskReportHandler(
    JobHandler, PackitAPIWithDownstreamMixin, ConfigFromEventMixin
):
    task_name = TaskName.upstream_koji_build_report

    def __init__(
        self, package_config: PackageConfig, job_config: JobConfig, event: dict
    ):
        super().__init__(
            package_config=package_config,
            job_config=job_config,
            event=event,
        )
        self.koji_task_event: KojiTaskEvent = KojiTaskEvent.from_event_dict(event)
        self._db_project_object: Optional[AbstractProjectObjectDbType] = None
        self._db_project_event: Optional[ProjectEventModel] = None
        self._build: Optional[KojiBuildTargetModel] = None

    @property
    def build(self) -> Optional[KojiBuildTargetModel]:
        if not self._build:
            self._build = KojiBuildTargetModel.get_by_build_id(
                build_id=str(self.koji_task_event.build_id)
            )
        return self._build

    @property
    def db_project_object(self) -> Optional[AbstractProjectObjectDbType]:
        if not self._db_project_object and self.build:
            self._db_project_object = self.build.get_project_event_object()
        return self._db_project_object

    @property
    def db_project_event(self) -> Optional[ProjectEventModel]:
        if not self._db_project_event and self.build:
            self._db_project_event = self.build.get_project_event_model()
        return self._db_project_event

    def run(self):
        build = KojiBuildTargetModel.get_by_build_id(
            build_id=str(self.koji_task_event.build_id)
        )

        if not build:
            msg = (
                f"Koji build {self.koji_task_event.build_id} not found in the database."
            )
            logger.warning(msg)
            return TaskResults(success=False, details={"msg": msg})

        logger.debug(
            f"Build on {build.target} in koji changed state "
            f"from {self.koji_task_event.old_state} to {self.koji_task_event.state}."
        )

        build.set_build_start_time(
            datetime.utcfromtimestamp(self.koji_task_event.start_time)
            if self.koji_task_event.start_time
            else None
        )

        build.set_build_finished_time(
            datetime.utcfromtimestamp(self.koji_task_event.completion_time)
            if self.koji_task_event.completion_time
            else None
        )

        url = get_koji_build_info_url(build.id)
        build_job_helper = KojiBuildJobHelper(
            service_config=self.service_config,
            package_config=self.package_config,
            project=self.project,
            metadata=self.data,
            db_project_object=self.db_project_object,
            db_project_event=self.db_project_event,
            job_config=self.job_config,
        )

        new_commit_status = {
            KojiTaskState.free: BaseCommitStatus.pending,
            KojiTaskState.open: BaseCommitStatus.running,
            KojiTaskState.closed: BaseCommitStatus.success,
            KojiTaskState.canceled: BaseCommitStatus.error,
            KojiTaskState.assigned: None,
            KojiTaskState.failed: BaseCommitStatus.failure,
        }.get(self.koji_task_event.state)

        description = {
            KojiTaskState.free: "RPM build has been submitted...",
            KojiTaskState.open: "RPM build is in progress...",
            KojiTaskState.closed: "RPM build succeeded.",
            KojiTaskState.canceled: "RPM build was canceled.",
            KojiTaskState.assigned: None,
            KojiTaskState.failed: "RPM build failed.",
        }.get(self.koji_task_event.state)

        if not (new_commit_status and description):
            logger.debug(
                f"We don't react to this koji build state change: {self.koji_task_event.state}"
            )
        elif new_commit_status.value == build.status:
            logger.debug(
                "Status was already processed (status in the DB is the "
                "same as the one about to report)"
            )
            return TaskResults(
                success=True, details={"msg": "State change already processed"}
            )

        else:
            build.set_status(new_commit_status.value)
            build_job_helper.report_status_to_all_for_chroot(
                description=description,
                state=new_commit_status,
                url=url,
                chroot=build.target,
            )

        koji_build_logs = KojiTaskEvent.get_koji_build_logs_url(
            rpm_build_task_id=int(build.build_id),
            koji_logs_url=self.service_config.koji_logs_url,
        )
        build.set_build_logs_url(koji_build_logs)
        koji_rpm_task_web_url = KojiTaskEvent.get_koji_rpm_build_web_url(
            rpm_build_task_id=int(build.build_id),
            koji_web_url=self.service_config.koji_web_url,
        )
        build.set_web_url(koji_rpm_task_web_url)

        msg = (
            f"Build on {build.target} in koji changed state "
            f"from {self.koji_task_event.old_state} to {self.koji_task_event.state}."
        )
        return TaskResults(success=True, details={"msg": msg})


@configured_as(job_type=JobType.koji_build)
@configured_as(job_type=JobType.bodhi_update)
@reacts_to(event=KojiBuildEvent)
class KojiBuildReportHandler(
    JobHandler, PackitAPIWithDownstreamMixin, ConfigFromEventMixin
):
    task_name = TaskName.downstream_koji_build_report

    def __init__(
        self, package_config: PackageConfig, job_config: JobConfig, event: dict
    ):
        super().__init__(
            package_config=package_config,
            job_config=job_config,
            event=event,
        )
        self.koji_build_event: KojiBuildEvent = KojiBuildEvent.from_event_dict(event)
        self._db_project_object: Optional[AbstractProjectObjectDbType] = None
        self._build: Optional[KojiBuildTargetModel] = None

    @property
    def build(self) -> Optional[KojiBuildTargetModel]:
        if not self._build:
            self._build = KojiBuildTargetModel.get_by_build_id(
                build_id=self.koji_build_event.build_id
            )
        return self._build

    @property
    def db_project_object(self) -> Optional[AbstractProjectObjectDbType]:
        if not self._db_project_object and self.build:
            self._db_project_object = self.build.get_project_event_object()
        return self._db_project_object

    def run(self):
        if not self.build:
            msg = f"Koji build {self.koji_build_event.build_id} not found in the database."
            logger.debug(msg)
            return TaskResults(success=True, details={"msg": msg})

        msg = (
            f"Build on {self.build.target} in koji changed state "
            f"from {self.koji_build_event.old_state} to {self.koji_build_event.state}."
        )
        logger.debug(msg)

        new_commit_status = {
            KojiBuildState.building: BaseCommitStatus.running,
            KojiBuildState.complete: BaseCommitStatus.success,
            KojiBuildState.deleted: BaseCommitStatus.error,
            KojiBuildState.failed: BaseCommitStatus.failure,
            KojiBuildState.canceled: BaseCommitStatus.error,
        }.get(self.koji_build_event.state)

        if (
            new_commit_status
            and self.build.status
            and self.build.status != KojiBuildState.building
        ):
            logger.warning(
                f"We should not overwrite the final state {self.build.status} "
                f"to {self.koji_build_event.state}. "
                f"Not updating the status."
            )
        elif new_commit_status:
            self.build.set_status(new_commit_status.value)
        else:
            logger.debug(
                f"We don't react to this koji build state change: {self.koji_task_event.state}"
            )

        if not self.build.web_url:
            self.build.set_web_url(
                KojiBuildEvent.get_koji_rpm_build_web_url(
                    rpm_build_task_id=self.koji_build_event.rpm_build_task_id,
                    koji_web_url=self.service_config.koji_web_url,
                )
            )
        # TODO: update logs URL (the access via task number does not work for non-scratch builds)

        return TaskResults(success=True, details={"msg": msg})
