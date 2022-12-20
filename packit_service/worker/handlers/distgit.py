# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
This file defines classes for job handlers specific for distgit
"""
import logging
import shutil
from datetime import datetime
from typing import Optional, Tuple, Type

from celery import Task
from ogr.abstract import PullRequest

from packit.config import JobConfig, JobType
from packit.config.package_config import PackageConfig
from packit.exceptions import PackitException, PackitDownloadFailedException
from packit_service import sentry_integration
from packit_service.config import PackageConfigGetter
from packit_service.constants import (
    CONTACTS_URL,
    MSG_RETRIGGER,
)
from packit_service.models import (
    SyncReleaseTargetStatus,
    SyncReleaseTargetModel,
    SyncReleaseModel,
    SyncReleaseStatus,
    SyncReleaseJobType,
)
from packit_service.service.urls import get_propose_downstream_info_url
from packit_service.utils import gather_packit_logs_to_buffer, collect_packit_logs
from packit_service.worker.checker.abstract import Checker
from packit_service.worker.checker.distgit import (
    IsProjectOk,
    PermissionOnDistgit,
    ValidInformationForPullFromUpstream,
    HasIssueCommenterRetriggeringPermissions,
)
from packit_service.worker.events import (
    PushPagureEvent,
    ReleaseEvent,
    ReleaseGitlabEvent,
    AbstractIssueCommentEvent,
    CheckRerunReleaseEvent,
    PullRequestCommentPagureEvent,
    IssueCommentEvent,
    IssueCommentGitlabEvent,
)
from packit_service.worker.events.new_hotness import NewHotnessUpdateEvent
from packit_service.worker.handlers.abstract import (
    JobHandler,
    TaskName,
    configured_as,
    reacts_to,
    run_for_comment,
    run_for_check_rerun,
    RetriableJobHandler,
)
from packit_service.worker.handlers.mixin import GetProjectToSyncMixin
from packit_service.worker.helpers.sync_release.propose_downstream import (
    ProposeDownstreamJobHelper,
)
from packit_service.worker.helpers.sync_release.pull_from_upstream import (
    PullFromUpstreamHelper,
)
from packit_service.worker.helpers.sync_release.sync_release import SyncReleaseHelper
from packit_service.worker.mixin import (
    LocalProjectMixin,
    PackitAPIWithUpstreamMixin,
    PackitAPIWithDownstreamMixin,
    GetPagurePullRequestMixin,
)
from packit_service.worker.reporting import BaseCommitStatus, report_in_issue_repository
from packit_service.worker.result import TaskResults

logger = logging.getLogger(__name__)


@configured_as(job_type=JobType.sync_from_downstream)
@reacts_to(event=PushPagureEvent)
class SyncFromDownstream(
    JobHandler, GetProjectToSyncMixin, LocalProjectMixin, PackitAPIWithUpstreamMixin
):
    """Sync new specfile changes to upstream after a new git push in the dist-git."""

    task_name = TaskName.sync_from_downstream

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

    @staticmethod
    def get_checkers() -> Tuple[Type[Checker], ...]:
        return (IsProjectOk,)

    @property
    def project_url(self) -> str:
        url = f"{self.project_to_sync.forge}/"
        f"{self.project_to_sync.repo_namespace}/{self.project_to_sync.repo_name}"
        return url

    def run(self) -> TaskResults:
        # rev is a commit
        # we use branch on purpose so we get the latest thing
        # TODO: check if rev is HEAD on {branch}, warn then?
        self.packit_api.sync_from_downstream(
            dist_git_branch=self.dg_branch,
            upstream_branch=self.project_to_sync.branch,
            sync_only_specfile=True,
        )
        return TaskResults(success=True, details={})


class AbstractSyncReleaseHandler(
    PackitAPIWithUpstreamMixin, LocalProjectMixin, RetriableJobHandler
):
    helper_kls: type[SyncReleaseHelper]
    sync_release_job_type: SyncReleaseJobType
    job_name_for_reporting: str

    def __init__(
        self,
        package_config: PackageConfig,
        job_config: JobConfig,
        event: dict,
        celery_task: Task,
        sync_release_run_id: Optional[int] = None,
    ):
        super().__init__(
            package_config=package_config,
            job_config=job_config,
            event=event,
            celery_task=celery_task,
        )
        self._sync_release_run_id = sync_release_run_id
        self.helper: Optional[SyncReleaseHelper] = None

    @property
    def sync_release_helper(self) -> SyncReleaseHelper:
        if not self.helper:
            self.helper = self.helper_kls(
                service_config=self.service_config,
                package_config=self.package_config,
                project=self.project,
                metadata=self.data,
                db_trigger=self.data.db_trigger,
                job_config=self.job_config,
                branches_override=self.data.branches_override,
            )
        return self.helper

    def sync_branch(
        self, branch: str, model: SyncReleaseModel
    ) -> Optional[PullRequest]:
        try:
            branch_suffix = f"update-{self.sync_release_job_type.value}"
            downstream_pr = self.packit_api.sync_release(
                dist_git_branch=branch,
                tag=self.data.tag_name,
                create_pr=True,
                local_pr_branch_suffix=branch_suffix,
                use_downstream_specfile=self.sync_release_job_type
                == SyncReleaseJobType.pull_from_upstream,
            )
        except PackitDownloadFailedException as ex:
            # the archive has not been uploaded to PyPI yet
            # retry for the archive to become available
            logger.info(f"We were not able to download the archive: {ex}")
            # when the task hits max_retries, it raises MaxRetriesExceededError
            # and the error handling code would be never executed
            retries = self.celery_task.retries
            if not self.celery_task.is_last_try():
                # will retry in: 1m and then again in another 2m
                delay = 60 * 2**retries
                logger.info(
                    f"Will retry for the {retries + 1}. time in {delay}s \
                        with sync_release_run_id {model.id}."
                )
                # throw=False so that exception is not raised and task
                # is not retried also automatically
                kargs = self.celery_task.task.request.kwargs.copy()
                kargs["sync_release_run_id"] = model.id
                # https://docs.celeryq.dev/en/stable/userguide/tasks.html#retrying
                self.celery_task.task.retry(
                    exc=ex, countdown=delay, throw=False, args=(), kwargs=kargs
                )
                raise AbortSyncRelease()
            raise ex
        finally:
            self.packit_api.up.local_project.git_repo.head.reset(
                "HEAD", index=True, working_tree=True
            )

        return downstream_pr

    def _get_or_create_sync_release_run(self) -> SyncReleaseModel:
        if self._sync_release_run_id is not None:
            return SyncReleaseModel.get_by_id(self._sync_release_run_id)

        sync_release_model, _ = SyncReleaseModel.create_with_new_run(
            status=SyncReleaseStatus.running,
            trigger_model=self.data.db_trigger,
            job_type=SyncReleaseJobType.propose_downstream
            if self.job_config.type == JobType.propose_downstream
            else SyncReleaseJobType.pull_from_upstream,
        )

        for branch in self.sync_release_helper.branches:
            sync_release_target = SyncReleaseTargetModel.create(
                status=SyncReleaseTargetStatus.queued, branch=branch
            )
            sync_release_model.sync_release_targets.append(sync_release_target)

        return sync_release_model

    def run(self) -> TaskResults:
        """
        Sync the upstream release to dist-git as a pull request.
        """
        errors = {}
        sync_release_run_model = self._get_or_create_sync_release_run()
        branches_to_run = [
            target.branch for target in sync_release_run_model.sync_release_targets
        ]
        logger.debug(f"Branches to run {self.job_config.type}: {branches_to_run}")

        try:
            for model in sync_release_run_model.sync_release_targets:
                branch = model.branch
                # skip submitting a branch if we already did that (even if it failed)
                if model.status not in [
                    SyncReleaseTargetStatus.running,
                    SyncReleaseTargetStatus.retry,
                    SyncReleaseTargetStatus.queued,
                ]:
                    logger.debug(
                        f"Skipping {self.sync_release_job_type} for branch {branch} "
                        f"that was already processed."
                    )
                    continue
                logger.debug(f"Running {self.sync_release_job_type} for {branch}")
                model.set_status(status=SyncReleaseTargetStatus.running)
                # for now the url is used only for propose-downstream
                # so it does not matter URL may not be valid for pull-from-upstream
                url = get_propose_downstream_info_url(model.id)
                buffer, handler = gather_packit_logs_to_buffer(
                    logging_level=logging.DEBUG
                )

                try:
                    model.set_start_time(start_time=datetime.utcnow())
                    self.sync_release_helper.report_status_for_branch(
                        branch=branch,
                        description=f"Starting {self.job_name_for_reporting}...",
                        state=BaseCommitStatus.running,
                        url=url,
                    )
                    downstream_pr = self.sync_branch(
                        branch=branch, model=sync_release_run_model
                    )
                    logger.debug("Downstream PR created successfully.")
                    model.set_downstream_pr_url(downstream_pr_url=downstream_pr.url)
                    model.set_status(status=SyncReleaseTargetStatus.submitted)
                    self.sync_release_helper.report_status_for_branch(
                        branch=branch,
                        description=f"{self.job_name_for_reporting.capitalize()} "
                        f"finished successfully.",
                        state=BaseCommitStatus.success,
                        url=url,
                    )
                except AbortSyncRelease:
                    logger.debug(
                        f"{self.sync_release_job_type} is being retried because "
                        "we were not able yet to download the archive. "
                    )
                    model.set_status(status=SyncReleaseTargetStatus.retry)
                    self.sync_release_helper.report_status_for_branch(
                        branch=branch,
                        description=f"{self.job_name_for_reporting.capitalize()} is "
                        f"being retried because "
                        "we were not able yet to download the archive. ",
                        state=BaseCommitStatus.pending,
                        url=url,
                    )
                    return TaskResults(
                        success=True,  # do not create a Sentry issue
                        details={
                            "msg": "Not able to download archive. Task will be retried."
                        },
                    )
                except Exception as ex:
                    logger.debug(f"{self.sync_release_job_type} failed: {ex}")
                    # eat the exception and continue with the execution
                    model.set_status(status=SyncReleaseTargetStatus.error)
                    self.sync_release_helper.report_status_for_branch(
                        branch=branch,
                        description=f"{self.job_name_for_reporting.capitalize()} failed: {ex}",
                        state=BaseCommitStatus.failure,
                        url=url,
                    )
                    errors[branch] = str(ex)
                    sentry_integration.send_to_sentry(ex)
                finally:
                    model.set_finished_time(finished_time=datetime.utcnow())
                    model.set_logs(collect_packit_logs(buffer=buffer, handler=handler))

        finally:
            # remove temporary dist-git clone after we're done here - context:
            # 1. the dist-git repo is cloned on worker, not sandbox
            # 2. it's stored in /tmp, not in the mirrored sandbox PV
            # 3. it's not being cleaned up and it wastes pod's filesystem space
            shutil.rmtree(self.packit_api.dg.local_project.working_dir)

        if errors:
            branch_errors = ""
            for branch, err in sorted(
                errors.items(), key=lambda branch_error: branch_error[0]
            ):
                err_without_new_lines = err.replace("\n", " ")
                branch_errors += f"| `{branch}` | `{err_without_new_lines}` |\n"
            body_msg = (
                f"Packit failed on creating pull-requests in dist-git "
                f"({self.packit_api.dg.local_project.git_url}):\n\n"
                f"| dist-git branch | error |\n"
                f"| --------------- | ----- |\n"
                f"{branch_errors}\n\n"
            )
            self._report_errors_for_each_branch(body_msg)
            sync_release_run_model.set_status(status=SyncReleaseStatus.error)
            return TaskResults(
                success=False,
                details={
                    "msg": f"{self.sync_release_job_type}  failed.",
                    "errors": errors,
                },
            )

        sync_release_run_model.set_status(status=SyncReleaseStatus.finished)
        return TaskResults(success=True, details={})

    def _report_errors_for_each_branch(self, message: str):
        raise NotImplementedError("Use subclass.")


class AbortSyncRelease(Exception):
    """Abort sync-release process"""


@configured_as(job_type=JobType.propose_downstream)
@run_for_comment(command="propose-downstream")
@run_for_comment(command="propose-update")  # deprecated
@run_for_check_rerun(prefix="propose-downstream")
@reacts_to(event=ReleaseEvent)
@reacts_to(event=ReleaseGitlabEvent)
@reacts_to(event=AbstractIssueCommentEvent)
@reacts_to(event=CheckRerunReleaseEvent)
class ProposeDownstreamHandler(AbstractSyncReleaseHandler):
    topic = "org.fedoraproject.prod.git.receive"
    task_name = TaskName.propose_downstream
    helper_kls = ProposeDownstreamJobHelper
    sync_release_job_type = SyncReleaseJobType.propose_downstream
    job_name_for_reporting = "propose downstream"

    def __init__(
        self,
        package_config: PackageConfig,
        job_config: JobConfig,
        event: dict,
        celery_task: Task,
        sync_release_run_id: Optional[int] = None,
    ):
        super().__init__(
            package_config=package_config,
            job_config=job_config,
            event=event,
            celery_task=celery_task,
            sync_release_run_id=sync_release_run_id,
        )

    def _report_errors_for_each_branch(self, message: str) -> None:
        msg_retrigger = MSG_RETRIGGER.format(
            job="update",
            command="propose-downstream",
            place="issue",
            packit_comment_command_prefix=self.service_config.comment_command_prefix,
        )
        body_msg = f"{message}{msg_retrigger}\n"

        PackageConfigGetter.create_issue_if_needed(
            project=self.project,
            title=f"{self.job_name_for_reporting.capitalize()} failed for "
            f"release {self.data.tag_name}",
            message=body_msg,
            comment_to_existing=body_msg,
        )


@configured_as(job_type=JobType.pull_from_upstream)
@reacts_to(event=NewHotnessUpdateEvent)
class PullFromUpstreamHandler(AbstractSyncReleaseHandler):
    task_name = TaskName.pull_from_upstream
    helper_kls = PullFromUpstreamHelper
    sync_release_job_type = SyncReleaseJobType.pull_from_upstream
    job_name_for_reporting = "Pull from upstream"

    def __init__(
        self,
        package_config: PackageConfig,
        job_config: JobConfig,
        event: dict,
        celery_task: Task,
        sync_release_run_id: Optional[int] = None,
    ):
        super().__init__(
            package_config=package_config,
            job_config=job_config,
            event=event,
            celery_task=celery_task,
            sync_release_run_id=sync_release_run_id,
        )

    @staticmethod
    def get_checkers() -> Tuple[Type[Checker], ...]:
        return (ValidInformationForPullFromUpstream,)

    def _report_errors_for_each_branch(self, message: str) -> None:

        report_in_issue_repository(
            issue_repository=self.job_config.issue_repository,
            service_config=self.service_config,
            title=f"Pull from upstream failed for release {self.data.tag_name}",
            message=message
            + f"\n\n---\n\n*Get in [touch with us]({CONTACTS_URL}) if you need some help.*",
            comment_to_existing=message,
        )


@configured_as(job_type=JobType.koji_build)
@run_for_comment(command="koji-build")
@reacts_to(event=PushPagureEvent)
@reacts_to(event=IssueCommentEvent)
@reacts_to(event=IssueCommentGitlabEvent)
@reacts_to(event=PullRequestCommentPagureEvent)
class DownstreamKojiBuildHandler(
    RetriableJobHandler,
    LocalProjectMixin,
    PackitAPIWithDownstreamMixin,
    GetPagurePullRequestMixin,
):
    """
    This handler can submit a build in Koji from a dist-git.
    """

    topic = "org.fedoraproject.prod.git.receive"
    task_name = TaskName.downstream_koji_build

    def __init__(
        self,
        package_config: PackageConfig,
        job_config: JobConfig,
        event: dict,
        celery_task: Task,
    ):
        super().__init__(
            package_config=package_config,
            job_config=job_config,
            event=event,
            celery_task=celery_task,
        )
        self.dg_branch = event.get("git_ref")
        self._pull_request: Optional[PullRequest] = None
        self._packit_api = None

    @staticmethod
    def get_checkers() -> Tuple[Type[Checker], ...]:
        return (PermissionOnDistgit, HasIssueCommenterRetriggeringPermissions)

    def run(self) -> TaskResults:
        branch = (
            self.project.get_pr(self.data.pr_id).target_branch
            if self.data.event_type in (PullRequestCommentPagureEvent.__name__,)
            else self.dg_branch
        )
        try:
            self.packit_api.build(
                dist_git_branch=branch,
                scratch=self.job_config.scratch,
                nowait=True,
                from_upstream=False,
            )
        except PackitException as ex:
            if self.celery_task and not self.celery_task.is_last_try():
                logger.debug(
                    "Celery task will be retried. User will not be notified about the failure."
                )
                raise ex

            body = f"Koji build on `{branch}` branch failed:\n" "```\n" f"{ex}\n" "```"

            report_in_issue_repository(
                issue_repository=self.job_config.issue_repository,
                service_config=self.service_config,
                title="Fedora Koji build failed to be triggered",
                message=body
                + f"\n\n*Get in [touch with us]({CONTACTS_URL}) if you need some help.*",
                comment_to_existing=body,
            )

            raise ex

        return TaskResults(success=True, details={})
