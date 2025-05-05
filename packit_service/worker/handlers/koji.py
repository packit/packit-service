# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
This file defines classes for job handlers specific for Fedmsg events
"""

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from os import getenv
from typing import Optional

from celery import signature
from ogr.abstract import GitProject
from packit.config import (
    JobConfig,
    JobConfigTriggerType,
    JobType,
)
from packit.config.package_config import PackageConfig
from packit.constants import DISTGIT_INSTANCES

from packit_service.constants import (
    KojiBuildState,
    KojiTaskState,
)
from packit_service.events import (
    github,
    gitlab,
    koji,
)
from packit_service.models import (
    AbstractProjectObjectDbType,
    KojiBuildTargetModel,
    ProjectEventModel,
)
from packit_service.package_config_getter import PackageConfigGetter
from packit_service.service.urls import (
    get_koji_build_info_url,
)
from packit_service.utils import (
    dump_job_config,
    dump_package_config,
)
from packit_service.worker.checker.abstract import Checker
from packit_service.worker.checker.koji import (
    IsJobConfigTriggerMatching,
    IsUpstreamKojiScratchBuild,
    PermissionOnKoji,
    SidetagExists,
)
from packit_service.worker.handlers.abstract import (
    FedoraCIJobHandler,
    JobHandler,
    TaskName,
    configured_as,
    reacts_to,
    reacts_to_as_fedora_ci,
    run_for_check_rerun,
    run_for_comment,
)
from packit_service.worker.handlers.bodhi import BodhiUpdateFromSidetagHandler
from packit_service.worker.handlers.distgit import DownstreamKojiBuildHandler
from packit_service.worker.handlers.mixin import (
    GetKojiBuildJobHelperMixin,
)
from packit_service.worker.helpers.build.koji_build import KojiBuildJobHelper
from packit_service.worker.helpers.fedora_ci import FedoraCIHelper
from packit_service.worker.helpers.sidetag import SidetagHelper
from packit_service.worker.mixin import (
    ConfigFromEventMixin,
    PackitAPIWithDownstreamMixin,
)
from packit_service.worker.reporting import BaseCommitStatus
from packit_service.worker.result import TaskResults

logger = logging.getLogger(__name__)


@configured_as(job_type=JobType.upstream_koji_build)
@run_for_comment(command="upstream-koji-build")
@run_for_check_rerun(prefix="koji-build")
@reacts_to(github.release.Release)
@reacts_to(gitlab.release.Release)
@reacts_to(github.pr.Action)
@reacts_to(github.push.Commit)
@reacts_to(gitlab.push.Commit)
@reacts_to(gitlab.mr.Action)
@reacts_to(github.pr.Comment)
@reacts_to(gitlab.mr.Comment)
@reacts_to(github.check.Rerun)
class KojiBuildHandler(
    JobHandler,
    PackitAPIWithDownstreamMixin,
    GetKojiBuildJobHelperMixin,
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
    def get_checkers() -> tuple[type[Checker], ...]:
        return (
            IsJobConfigTriggerMatching,
            PermissionOnKoji,
        )

    def run(self) -> TaskResults:
        return self.koji_build_helper.run_koji_build()


class AbstractKojiTaskReportHandler(
    ABC, JobHandler, PackitAPIWithDownstreamMixin, ConfigFromEventMixin
):
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
        self.koji_task_event: koji.result.Task = koji.result.Task.from_event_dict(event)
        self._db_project_object: Optional[AbstractProjectObjectDbType] = None
        self._db_project_event: Optional[ProjectEventModel] = None
        self._build: Optional[KojiBuildTargetModel] = None

    @abstractmethod
    def report(self, description: str, commit_status: BaseCommitStatus, url: str): ...

    @abstractmethod
    def notify_about_failure_if_configured(
        self, packit_dashboard_url: str, external_dashboard_url: str, logs_url: str
    ): ...

    @property
    def build(self) -> Optional[KojiBuildTargetModel]:
        if not self._build:
            self._build = KojiBuildTargetModel.get_by_task_id(
                task_id=str(self.koji_task_event.task_id),
            )
        return self._build

    @property
    def db_project_event(self) -> Optional[ProjectEventModel]:
        if not self._db_project_event and self.build:
            self._db_project_event = self.build.get_project_event_model()
        return self._db_project_event

    def run(self):
        if not self.build:
            msg = f"Koji task {self.koji_task_event.task_id} not found in the database."
            logger.warning(msg)
            return TaskResults(success=False, details={"msg": msg})

        logger.debug(
            f"Build on {self.build.target} in Koji changed state "
            f"from {self.koji_task_event.old_state} to {self.koji_task_event.state}.",
        )

        self.build.set_build_start_time(
            (
                datetime.utcfromtimestamp(self.koji_task_event.start_time)
                if self.koji_task_event.start_time
                else None
            ),
        )

        self.build.set_build_finished_time(
            (
                datetime.utcfromtimestamp(self.koji_task_event.completion_time)
                if self.koji_task_event.completion_time
                else None
            ),
        )

        url = get_koji_build_info_url(self.build.id)

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
                f"We don't react to this koji build state change: {self.koji_task_event.state}",
            )
        elif new_commit_status.value == self.build.status:
            logger.debug(
                "Status was already processed (status in the DB is the "
                "same as the one about to report)",
            )
            return TaskResults(
                success=True,
                details={"msg": "State change already processed"},
            )

        else:
            self.build.set_status(new_commit_status.value)
            self.report(description, new_commit_status, url)
            koji_build_logs = self.koji_task_event.get_koji_build_rpm_tasks_logs_urls(
                self.service_config.koji_logs_url,
            )

            self.build.set_build_logs_urls(koji_build_logs)
            koji_rpm_task_web_url = koji.result.Task.get_koji_rpm_build_web_url(
                rpm_build_task_id=int(self.build.task_id),
                koji_web_url=self.service_config.koji_web_url,
            )
            self.build.set_web_url(koji_rpm_task_web_url)

            if self.koji_task_event.state == KojiTaskState.failed:
                self.notify_about_failure_if_configured(
                    packit_dashboard_url=url,
                    external_dashboard_url=koji_rpm_task_web_url,
                    logs_url=koji_build_logs,
                )

        msg = (
            f"Build on {self.build.target} in koji changed state "
            f"from {self.koji_task_event.old_state} to {self.koji_task_event.state}."
        )
        return TaskResults(success=True, details={"msg": msg})


@configured_as(job_type=JobType.upstream_koji_build)
@reacts_to(event=koji.result.Task)
class KojiTaskReportHandler(AbstractKojiTaskReportHandler):
    task_name = TaskName.upstream_koji_build_report
    _helper: Optional[KojiBuildJobHelper] = None

    @property
    def helper(self):
        if not self._helper:
            self._helper = KojiBuildJobHelper(
                service_config=self.service_config,
                package_config=self.package_config,
                project=self.project,
                metadata=self.data,
                db_project_event=self.db_project_event,
                job_config=self.job_config,
            )
        return self._helper

    @staticmethod
    def get_checkers() -> tuple[type[Checker], ...]:
        return (IsUpstreamKojiScratchBuild,)

    def report(self, description: str, commit_status: BaseCommitStatus, url: str):
        self.helper.report_status_to_all_for_chroot(
            description=description,
            state=commit_status,
            url=url,
            chroot=self.build.target,
        )

    def notify_about_failure_if_configured(
        self, packit_dashboard_url: str, external_dashboard_url: str, logs_url: str
    ):
        self.helper.notify_about_failure_if_configured(
            packit_dashboard_url=packit_dashboard_url,
            external_dashboard_url=external_dashboard_url,
            logs_url=logs_url,
        )


@reacts_to_as_fedora_ci(event=koji.result.Task)
class KojiTaskReportDownstreamHandler(AbstractKojiTaskReportHandler, FedoraCIJobHandler):
    task_name = TaskName.downstream_koji_scratch_build_report
    check_name = "Packit - scratch build"
    _helper: Optional[FedoraCIHelper] = None

    @property
    def helper(self):
        if not self._helper:
            self._helper = FedoraCIHelper(
                project=self.project,
                metadata=self.data,
                target_branch=self.data.event_dict.get("target"),
            )
        return self._helper

    def report(self, description: str, commit_status: BaseCommitStatus, url: str):
        self.helper.report(
            state=commit_status,
            description=description,
            url=url,
            check_name=self.check_name,
        )

    def notify_about_failure_if_configured(
        self, packit_dashboard_url: str, external_dashboard_url: str, logs_url: str
    ):
        pass


@configured_as(job_type=JobType.koji_build)
@configured_as(job_type=JobType.bodhi_update)
@reacts_to(event=koji.result.Build)
class KojiBuildReportHandler(
    JobHandler,
    PackitAPIWithDownstreamMixin,
    ConfigFromEventMixin,
):
    task_name = TaskName.downstream_koji_build_report

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
        self.koji_build_event: koji.result.Build = koji.result.Build.from_event_dict(event)
        self._db_project_object: Optional[AbstractProjectObjectDbType] = None
        self._build: Optional[KojiBuildTargetModel] = None

    @property
    def build(self) -> Optional[KojiBuildTargetModel]:
        if not self._build:
            self._build = KojiBuildTargetModel.get_by_task_id(
                task_id=self.koji_build_event.task_id,
            )
        return self._build

    @property
    def db_project_object(self) -> Optional[AbstractProjectObjectDbType]:
        if not self._db_project_object and self.build:
            self._db_project_object = self.build.get_project_event_object()
        return self._db_project_object

    def run(self):
        if not self.build:
            msg = (
                f"Koji build with task ID {self.koji_build_event.task_id} not found in "
                f"the database."
            )
            logger.debug(msg)
            return TaskResults(success=True, details={"msg": msg})

        msg = (
            f"Build {self.koji_build_event.build_id} on {self.build.target} in koji changed state "
            f"from {self.koji_build_event.old_state} to {self.koji_build_event.state}."
        )
        logger.debug(msg)

        self.build.set_build_start_time(
            (
                datetime.fromisoformat(self.koji_build_event.start_time)
                if self.koji_build_event.start_time
                else None
            ),
        )

        self.build.set_build_finished_time(
            (
                datetime.fromisoformat(self.koji_build_event.completion_time)
                if self.koji_build_event.completion_time
                else None
            ),
        )

        new_commit_status = {
            KojiBuildState.building: BaseCommitStatus.running,
            KojiBuildState.complete: BaseCommitStatus.success,
            KojiBuildState.deleted: BaseCommitStatus.error,
            KojiBuildState.failed: BaseCommitStatus.failure,
            KojiBuildState.canceled: BaseCommitStatus.error,
        }.get(self.koji_build_event.state)

        logger.info(f"Build status in DB: {self.build.status}")
        if (
            new_commit_status
            and self.build.status
            and self.build.status
            in (
                BaseCommitStatus.failure.value,
                BaseCommitStatus.error.value,
                BaseCommitStatus.success.value,
            )
        ):
            logger.warning(
                f"We should not overwrite the final state {self.build.status} "
                f"to {new_commit_status}. "
                f"Not updating the status.",
            )
        elif new_commit_status:
            self.build.set_status(new_commit_status.value)
        else:
            logger.debug(
                f"We don't react to this koji build state change: {self.koji_task_event.state}",
            )

        if not self.build.web_url:
            self.build.set_web_url(
                koji.result.Build.get_koji_rpm_build_web_url(
                    rpm_build_task_id=self.koji_build_event.task_id,
                    koji_web_url=self.service_config.koji_web_url,
                ),
            )

        koji_build_logs = self.koji_build_event.get_koji_build_rpm_tasks_logs_urls(
            self.service_config.koji_logs_url,
        )
        self.build.set_build_logs_urls(koji_build_logs)

        return TaskResults(success=True, details={"msg": msg})


@configured_as(job_type=JobType.koji_build_tag)
@reacts_to(event=koji.tag.Build)
class KojiBuildTagHandler(
    JobHandler,
    ConfigFromEventMixin,
    PackitAPIWithDownstreamMixin,
):
    task_name = TaskName.koji_build_tag

    @staticmethod
    def get_checkers() -> tuple[type[Checker], ...]:
        return (SidetagExists,)

    def run(self) -> TaskResults:
        dg_base_url = getenv("DISTGIT_URL", DISTGIT_INSTANCES["fedpkg"].url)
        sidetag = SidetagHelper.get_sidetag_by_koji_name(self.data.tag_name)
        tagged_packages = sidetag.get_packages()
        logger.debug(f"Packages tagged into {sidetag.koji_name}: {tagged_packages}")

        packages_to_trigger = set()
        for package_name in tagged_packages:
            distgit_project_url = f"{dg_base_url}rpms/{package_name}"
            project = self.service_config.get_project(url=distgit_project_url)
            packages_config = PackageConfigGetter.get_package_config_from_repo(
                base_project=None,
                project=project,
                pr_id=None,
                reference=None,
                fail_when_missing=False,
            )
            if not packages_config:
                logger.debug(
                    f"Packit config not found for package {package_name}, skipping.",
                )
                continue
            for job in packages_config.get_job_views():
                if job.type == JobType.koji_build and job.sidetag_group == sidetag.sidetag_group:
                    if job.dependents:
                        packages_to_trigger.update(job.dependents)
                    elif job.downstream_package_name == self.package_config.downstream_package_name:
                        # implicitly include self in dependents
                        packages_to_trigger.add(job.downstream_package_name)
        logger.debug(f"Packages to trigger: {packages_to_trigger}")

        for package_name in packages_to_trigger:
            distgit_project_url = f"{dg_base_url}rpms/{package_name}"
            project = self.service_config.get_project(url=distgit_project_url)
            packages_config = PackageConfigGetter.get_package_config_from_repo(
                base_project=None,
                project=project,
                pr_id=None,
                reference=None,
                fail_when_missing=False,
            )
            if not packages_config:
                logger.debug(
                    f"Packit config not found for package {package_name}, skipping.",
                )
                continue
            for job in packages_config.get_job_views():
                if (
                    job.type in (JobType.koji_build, JobType.bodhi_update)
                    and job.trigger == JobConfigTriggerType.koji_build
                    and job.sidetag_group == sidetag.sidetag_group
                ):
                    event_dict = self.data.get_dict().get("event_dict", {})
                    event_dict["project_url"] = distgit_project_url
                    event_dict["git_ref"] = sidetag.dist_git_branch
                    handler = (
                        DownstreamKojiBuildHandler
                        if job.type == JobType.koji_build
                        else BodhiUpdateFromSidetagHandler
                    )
                    if not handler.pre_check(
                        package_config=packages_config,
                        job_config=job,
                        event=event_dict,
                    ):
                        continue
                    signature(
                        handler.task_name.value,
                        kwargs={
                            "event": event_dict,
                            "package_config": dump_package_config(packages_config),
                            "job_config": dump_job_config(job),
                        },
                    ).apply_async()

        msg = f"Tag {self.data.tag_name} event handled."
        return TaskResults(success=True, details={"msg": msg})
