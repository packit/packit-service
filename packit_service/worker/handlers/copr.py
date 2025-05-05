# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from celery import Task, signature
from ogr.services.github import GithubProject
from ogr.services.gitlab import GitlabProject
from packit.config import (
    JobConfig,
    JobConfigTriggerType,
    JobType,
)
from packit.config.package_config import PackageConfig

from packit_service import sentry_integration
from packit_service.constants import (
    COPR_API_SUCC_STATE,
    COPR_SRPM_CHROOT,
)
from packit_service.events import (
    abstract,
    copr,
    github,
    gitlab,
)
from packit_service.models import (
    BuildStatus,
    CoprBuildTargetModel,
    ProjectEventModelType,
)
from packit_service.service.urls import get_copr_build_info_url, get_srpm_build_info_url
from packit_service.utils import (
    dump_job_config,
    dump_package_config,
    elapsed_seconds,
    pr_labels_match_configuration,
)
from packit_service.worker.checker.abstract import Checker
from packit_service.worker.checker.copr import (
    AreOwnerAndProjectMatchingJob,
    BuildNotAlreadyStarted,
    CanActorRunTestsJob,
    IsGitForgeProjectAndEventOk,
    IsJobConfigTriggerMatching,
    IsPackageMatchingJobView,
)
from packit_service.worker.handlers.abstract import (
    JobHandler,
    RetriableJobHandler,
    TaskName,
    configured_as,
    reacts_to,
    run_for_check_rerun,
    run_for_comment,
)
from packit_service.worker.handlers.mixin import (
    ConfigFromEventMixin,
    GetCoprBuildEventMixin,
    GetCoprBuildJobHelperForIdMixin,
    GetCoprBuildJobHelperMixin,
)
from packit_service.worker.helpers.open_scan_hub import CoprOpenScanHubHelper
from packit_service.worker.mixin import PackitAPIWithDownstreamMixin
from packit_service.worker.reporting import BaseCommitStatus, DuplicateCheckMode
from packit_service.worker.result import TaskResults

logger = logging.getLogger(__name__)


@configured_as(job_type=JobType.copr_build)
@run_for_comment(command="build")
@run_for_comment(command="copr-build")
@run_for_comment(command="rebuild-failed")
@run_for_check_rerun(prefix="rpm-build")
@reacts_to(github.release.Release)
@reacts_to(gitlab.release.Release)
@reacts_to(github.pr.Action)
@reacts_to(github.push.Commit)
@reacts_to(gitlab.push.Commit)
@reacts_to(gitlab.mr.Action)
@reacts_to(github.check.Rerun)
@reacts_to(github.pr.Comment)
@reacts_to(gitlab.mr.Comment)
@reacts_to(abstract.comment.Commit)
class CoprBuildHandler(
    RetriableJobHandler,
    ConfigFromEventMixin,
    PackitAPIWithDownstreamMixin,
    GetCoprBuildJobHelperMixin,
):
    task_name = TaskName.copr_build

    def __init__(
        self,
        package_config: PackageConfig,
        job_config: JobConfig,
        event: dict,
        celery_task: Task,
        copr_build_group_id: Optional[int] = None,
    ):
        super().__init__(
            package_config=package_config,
            job_config=job_config,
            event=event,
            celery_task=celery_task,
        )
        self._copr_build_group_id = copr_build_group_id

    @staticmethod
    def get_checkers() -> tuple[type[Checker], ...]:
        return (
            IsJobConfigTriggerMatching,
            IsGitForgeProjectAndEventOk,
            CanActorRunTestsJob,
        )

    def run(self) -> TaskResults:
        # [XXX] For now cancel only when an environment variable is defined,
        # should allow for less stressful testing and also optionally turning
        # the cancelling on-and-off on the prod
        if os.getenv("CANCEL_RUNNING_JOBS"):
            self.copr_build_helper.cancel_running_builds()

        return self.copr_build_helper.run_copr_build_from_source_script()


class AbstractCoprBuildReportHandler(
    JobHandler,
    PackitAPIWithDownstreamMixin,
    GetCoprBuildJobHelperForIdMixin,
    GetCoprBuildEventMixin,
):
    @staticmethod
    def get_checkers() -> tuple[type[Checker], ...]:
        return (AreOwnerAndProjectMatchingJob, IsPackageMatchingJobView)


@configured_as(job_type=JobType.copr_build)
@reacts_to(event=copr.Start)
class CoprBuildStartHandler(AbstractCoprBuildReportHandler):
    topic = "org.fedoraproject.prod.copr.build.start"
    task_name = TaskName.copr_build_start

    @staticmethod
    def get_checkers() -> tuple[type[Checker], ...]:
        return (
            *super(CoprBuildStartHandler, CoprBuildStartHandler).get_checkers(),
            BuildNotAlreadyStarted,
        )

    def set_start_time(self):
        start_time = (
            datetime.utcfromtimestamp(self.copr_event.timestamp)
            if self.copr_event.timestamp
            else None
        )
        self.build.set_start_time(start_time)

    def set_logs_url(self):
        copr_build_logs = self.copr_event.get_copr_build_logs_url()
        self.build.set_build_logs_url(copr_build_logs)

    def run(self):
        if not self.build:
            model = "SRPMBuildDB" if self.copr_event.chroot == COPR_SRPM_CHROOT else "CoprBuildDB"
            msg = f"Copr build {self.copr_event.build_id} not in {model}."
            logger.warning(msg)
            return TaskResults(success=False, details={"msg": msg})

        if self.build.build_start_time is not None:
            msg = f"Copr build start for {self.copr_event.build_id} is already processed."
            logger.debug(msg)
            return TaskResults(success=True, details={"msg": msg})

        if BuildStatus.is_final_state(self.build.status):
            msg = (
                "Copr build start is being processed, but the DB build "
                "is already in the final state, setting only start time."
            )
            logger.debug(msg)
            self.set_start_time()
            return TaskResults(success=True, details={"msg": msg})

        self.set_logs_url()

        if self.copr_event.chroot == COPR_SRPM_CHROOT:
            url = get_srpm_build_info_url(self.build.id)
            report_status = (
                self.copr_build_helper.report_status_to_all
                if self.job_config.sync_test_job_statuses_with_builds
                else self.copr_build_helper.report_status_to_build
            )
            report_status(
                description="SRPM build is in progress...",
                state=BaseCommitStatus.running,
                url=url,
            )
            msg = "SRPM build in Copr has started..."
            self.set_start_time()
            return TaskResults(success=True, details={"msg": msg})

        self.pushgateway.copr_builds_started.inc()
        url = get_copr_build_info_url(self.build.id)
        self.build.set_status(BuildStatus.pending)

        report_status_for_chroot = (
            self.copr_build_helper.report_status_to_all_for_chroot
            if self.job_config.sync_test_job_statuses_with_builds
            else self.copr_build_helper.report_status_to_build_for_chroot
        )
        report_status_for_chroot(
            description="RPM build is in progress...",
            state=BaseCommitStatus.running,
            url=url,
            chroot=self.copr_event.chroot,
        )
        msg = f"Build on {self.copr_event.chroot} in copr has started..."
        self.set_start_time()
        return TaskResults(success=True, details={"msg": msg})


@configured_as(job_type=JobType.copr_build)
@reacts_to(event=copr.End)
class CoprBuildEndHandler(AbstractCoprBuildReportHandler):
    topic = "org.fedoraproject.prod.copr.build.end"
    task_name = TaskName.copr_build_end

    def set_srpm_url(self) -> None:
        # TODO how to do better
        srpm_build = (
            self.build
            if self.copr_event.chroot == COPR_SRPM_CHROOT
            else self.build.get_srpm_build()
        )

        if srpm_build.url is not None:
            # URL has been already set
            return

        srpm_url = self.copr_build_helper.get_build(
            self.copr_event.build_id,
        ).source_package.get("url")

        if srpm_url is not None:
            srpm_build.set_url(srpm_url)

    def set_end_time(self):
        end_time = (
            datetime.utcfromtimestamp(self.copr_event.timestamp)
            if self.copr_event.timestamp
            else None
        )
        self.build.set_end_time(end_time)

    def measure_time_after_reporting(self):
        reported_time = datetime.now(timezone.utc)
        build_ended_on = self.copr_build_helper.get_build_chroot(
            int(self.build.build_id),
            self.build.target,
        ).ended_on

        reported_after_time = elapsed_seconds(
            begin=datetime.fromtimestamp(build_ended_on, timezone.utc),
            end=reported_time,
        )
        logger.debug(
            f"Copr build end reported after {reported_after_time / 60} minutes.",
        )

        self.pushgateway.copr_build_end_reported_after_time.observe(reported_after_time)

    def set_built_packages(self):
        if self.build.built_packages:
            # packages have been already set
            return

        built_packages = self.copr_build_helper.get_built_packages(
            int(self.build.build_id),
            self.build.target,
        )
        self.build.set_built_packages(built_packages)

    def run(self):
        if not self.build:
            # TODO: how could this happen?
            model = "SRPMBuildDB" if self.copr_event.chroot == COPR_SRPM_CHROOT else "CoprBuildDB"
            msg = f"Copr build {self.copr_event.build_id} not in {model}."
            logger.warning(msg)
            return TaskResults(success=False, details={"msg": msg})

        if self.build.status in [
            BuildStatus.success,
            BuildStatus.failure,
        ]:
            msg = (
                f"Copr build {self.copr_event.build_id} is already"
                f" processed (status={self.copr_event.build.status})."
            )
            logger.info(msg)
            return TaskResults(success=True, details={"msg": msg})

        self.set_end_time()
        self.set_srpm_url()

        if self.copr_event.chroot == COPR_SRPM_CHROOT:
            return self.handle_srpm_end()

        self.pushgateway.copr_builds_finished.inc()

        # if the build is needed only for test, it doesn't have the task_accepted_time
        if self.build.task_accepted_time:
            copr_build_time = elapsed_seconds(
                begin=self.build.task_accepted_time,
                end=datetime.now(timezone.utc),
            )
            self.pushgateway.copr_build_finished_time.observe(copr_build_time)

        # https://pagure.io/copr/copr/blob/master/f/common/copr_common/enums.py#_42
        if self.copr_event.status != COPR_API_SUCC_STATE:
            failed_msg = "RPMs failed to be built."
            packit_dashboard_url = get_copr_build_info_url(self.build.id)
            # if SRPM build failed it has been reported already so skip reporting
            if self.build.get_srpm_build().status != BuildStatus.failure:
                self.copr_build_helper.report_status_to_all_for_chroot(
                    state=BaseCommitStatus.failure,
                    description=failed_msg,
                    url=packit_dashboard_url,
                    chroot=self.copr_event.chroot,
                )
                self.measure_time_after_reporting()
                self.copr_build_helper.notify_about_failure_if_configured(
                    packit_dashboard_url=packit_dashboard_url,
                    external_dashboard_url=self.build.web_url,
                    logs_url=self.build.build_logs_url,
                )
            self.build.set_status(BuildStatus.failure)
            return TaskResults(success=False, details={"msg": failed_msg})

        self.report_successful_build()
        self.measure_time_after_reporting()

        self.set_built_packages()
        self.build.set_status(BuildStatus.success)
        self.handle_testing_farm()

        if (
            not CoprOpenScanHubHelper.osh_disabled()
            and self.db_project_event.type == ProjectEventModelType.pull_request
            and self.build.target == "fedora-rawhide-x86_64"
            and self.job_config.osh_diff_scan_after_copr_build
        ):
            try:
                CoprOpenScanHubHelper(
                    copr_build_helper=self.copr_build_helper,
                    build=self.build,
                ).handle_scan()
            except Exception as ex:
                sentry_integration.send_to_sentry(ex)
                logger.debug(
                    f"Handling the scan raised an exception: {ex}. Skipping "
                    f"as this is only experimental functionality for now.",
                )

        return TaskResults(success=True, details={})

    def report_successful_build(self):
        if (
            self.copr_build_helper.job_build
            and self.copr_build_helper.job_build.trigger == JobConfigTriggerType.pull_request
            and self.copr_event.pr_id
            and isinstance(self.project, (GithubProject, GitlabProject))
            and self.job_config.notifications.pull_request.successful_build
        ):
            msg = (
                f"Congratulations! One of the builds has completed. :champagne:\n\n"
                "You can install the built RPMs by following these steps:\n\n"
                "* `sudo yum install -y dnf-plugins-core` on RHEL 8\n"
                "* `sudo dnf install -y dnf-plugins-core` on Fedora\n"
                f"* `dnf copr enable {self.copr_event.owner}/{self.copr_event.project_name}`\n"
                "* And now you can install the packages.\n"
                "\nPlease note that the RPMs should be used only in a testing environment."
            )
            self.copr_build_helper.status_reporter.comment(
                msg,
                duplicate_check=DuplicateCheckMode.check_last_comment,
            )

        url = get_copr_build_info_url(self.build.id)

        self.copr_build_helper.report_status_to_build_for_chroot(
            state=BaseCommitStatus.success,
            description="RPMs were built successfully.",
            url=url,
            chroot=self.copr_event.chroot,
        )
        if self.job_config.sync_test_job_statuses_with_builds:
            self.copr_build_helper.report_status_to_all_test_jobs_for_chroot(
                state=BaseCommitStatus.pending,
                description="RPMs were built successfully.",
                url=url,
                chroot=self.copr_event.chroot,
            )

    def handle_srpm_end(self):
        url = get_srpm_build_info_url(self.build.id)

        if self.copr_event.status != COPR_API_SUCC_STATE:
            failed_msg = "SRPM build failed, check the logs for details."
            self.copr_build_helper.report_status_to_all(
                state=BaseCommitStatus.failure,
                description=failed_msg,
                url=url,
            )
            self.copr_build_helper.notify_about_failure_if_configured(
                packit_dashboard_url=url,
                external_dashboard_url=self.build.copr_web_url,
                logs_url=self.build.logs_url,
            )
            self.build.set_status(BuildStatus.failure)
            self.copr_build_helper.monitor_not_submitted_copr_builds(
                len(self.copr_build_helper.build_targets),
                "srpm_failure",
            )
            return TaskResults(success=False, details={"msg": failed_msg})

        for build in CoprBuildTargetModel.get_all_by_build_id(
            str(self.copr_event.build_id),
        ):
            # from waiting_for_srpm to pending
            build.set_status(BuildStatus.pending)

        self.build.set_status(BuildStatus.success)
        report_status = (
            self.copr_build_helper.report_status_to_all
            if self.job_config.sync_test_job_statuses_with_builds
            else self.copr_build_helper.report_status_to_build
        )
        report_status(
            state=BaseCommitStatus.running,
            description="SRPM build succeeded. Waiting for RPM build to start...",
            url=url,
        )
        msg = "SRPM build in Copr has finished."
        logger.debug(msg)
        return TaskResults(success=True, details={"msg": msg})

    def handle_testing_farm(self):
        if not self.copr_build_helper.job_tests_all:
            logger.debug("Testing farm not in the job config.")
            return

        event_dict = self.data.get_dict()

        for job_config in self.copr_build_helper.job_tests_all:
            if (
                not job_config.skip_build
                and not job_config.manual_trigger
                # we need to check the labels here
                # the same way as when scheduling jobs for event
                and (
                    job_config.trigger != JobConfigTriggerType.pull_request
                    or not (job_config.require.label.present or job_config.require.label.absent)
                    or pr_labels_match_configuration(
                        pull_request=self.copr_build_helper.pull_request_object,
                        configured_labels_absent=job_config.require.label.absent,
                        configured_labels_present=job_config.require.label.present,
                    )
                )
                and self.copr_event.chroot
                in self.copr_build_helper.build_targets_for_test_job(job_config)
            ):
                event_dict["tests_targets_override"] = [
                    (target, job_config.identifier)
                    for target in self.copr_build_helper.build_target2test_targets_for_test_job(
                        self.copr_event.chroot,
                        job_config,
                    )
                ]
                signature(
                    TaskName.testing_farm.value,
                    kwargs={
                        "package_config": dump_package_config(self.package_config),
                        "job_config": dump_job_config(job_config),
                        "event": event_dict,
                        "build_id": self.build.id,
                    },
                ).apply_async()
