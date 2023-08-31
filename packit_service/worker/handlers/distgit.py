# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
This file defines classes for job handlers specific for distgit
"""
import abc
import logging
import shutil
from datetime import datetime
from typing import Optional, Tuple, Type, List, ClassVar

from celery import Task
from ogr.abstract import PullRequest, AuthMethod
from ogr.services.github import GithubService

from packit.config import JobConfig, JobType
from packit.config.package_config import PackageConfig
from packit.exceptions import PackitException, PackitDownloadFailedException
from packit_service import sentry_integration
from packit_service.config import PackageConfigGetter
from packit_service.constants import (
    CONTACTS_URL,
    MSG_RETRIGGER,
    MSG_GET_IN_TOUCH,
    MSG_DOWNSTREAM_JOB_ERROR_HEADER,
)
from packit_service.models import (
    SyncReleaseTargetStatus,
    SyncReleaseTargetModel,
    SyncReleaseModel,
    SyncReleaseStatus,
    SyncReleaseJobType,
)
from packit_service.service.urls import (
    get_propose_downstream_info_url,
    get_pull_from_upstream_info_url,
)
from packit_service.utils import gather_packit_logs_to_buffer, collect_packit_logs
from packit_service.worker.checker.abstract import Checker
from packit_service.worker.checker.distgit import (
    IsProjectOk,
    PermissionOnDistgit,
    ValidInformationForPullFromUpstream,
    HasIssueCommenterRetriggeringPermissions,
    IsUpstreamTagMatchingConfig,
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
    Config,
    LocalProjectMixin,
    ConfigFromEventMixin,
    GetBranchesFromIssueMixin,
    ConfigFromUrlMixin,
    ConfigFromDistGitUrlMixin,
    GetPagurePullRequestMixin,
    PackitAPIWithUpstreamMixin,
    PackitAPIWithDownstreamMixin,
    GetSyncReleaseTagMixin,
)
from packit_service.worker.reporting import BaseCommitStatus, report_in_issue_repository
from packit_service.worker.result import TaskResults

logger = logging.getLogger(__name__)


class ChoosenGithubAuthMethod:
    """Change the preferred auth method for every Github Service.
    Restore the default auth method after having used the preferred one.

    Args:
        config_mixin: each class which has access to the git services
            through a service_config object
        auth_method: the name of the preferred auth method for the
            following calls to the GitHub service
    """

    def __init__(self, config_mixin: Config, auth_method: AuthMethod) -> None:
        self._config_mixin = config_mixin
        for service in config_mixin.service_config.services:
            if isinstance(service, GithubService):
                service.set_auth_method(auth_method)

    def __enter__(self):
        return self._config_mixin

    def __exit__(self, type, value, traceback):
        for service in self._config_mixin.service_config.services:
            if isinstance(service, GithubService):
                service.reset_auth_method()


@configured_as(job_type=JobType.sync_from_downstream)
@reacts_to(event=PushPagureEvent)
class SyncFromDownstream(
    JobHandler,
    GetProjectToSyncMixin,
    ConfigFromEventMixin,
    LocalProjectMixin,
    PackitAPIWithUpstreamMixin,
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
    RetriableJobHandler,
    ConfigFromUrlMixin,
    LocalProjectMixin,
    GetSyncReleaseTagMixin,
    PackitAPIWithUpstreamMixin,
):
    helper_kls: type[SyncReleaseHelper]
    sync_release_job_type: SyncReleaseJobType
    job_name_for_reporting: str
    get_dashboard_url: ClassVar  # static method from Callable[[int], str]

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
        self._project_url = self.data.project_url
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
                db_project_event=self.data.db_project_event,
                job_config=self.job_config,
                branches_override=self.data.branches_override,
            )
        return self.helper

    def sync_branch(
        self, branch: str, model: SyncReleaseModel
    ) -> Optional[PullRequest]:
        try:
            branch_suffix = f"update-{self.sync_release_job_type.value}"
            is_pull_from_upstream_job = (
                self.sync_release_job_type == SyncReleaseJobType.pull_from_upstream
            )
            downstream_pr = self.packit_api.sync_release(
                dist_git_branch=branch,
                tag=self.tag,
                create_pr=True,
                local_pr_branch_suffix=branch_suffix,
                use_downstream_specfile=is_pull_from_upstream_job,
                sync_default_files=not is_pull_from_upstream_job,
                add_pr_instructions=True,
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
            project_event_model=self.data.db_project_event,
            job_type=SyncReleaseJobType.propose_downstream
            if self.job_config.type == JobType.propose_downstream
            else SyncReleaseJobType.pull_from_upstream,
            package_name=self.get_package_name(),
        )

        for branch in self.sync_release_helper.branches:
            sync_release_target = SyncReleaseTargetModel.create(
                status=SyncReleaseTargetStatus.queued, branch=branch
            )
            sync_release_model.sync_release_targets.append(sync_release_target)

        return sync_release_model

    def run_for_target(
        self, sync_release_run_model: SyncReleaseModel, model: SyncReleaseTargetModel
    ) -> Optional[str]:
        """
        Run sync-release for the single target specified by the given model.

        Args:
            sync_release_run_model: Model for the whole sync release run.
            model: Model for the single target that is to be executed.

        Returns:
            String representation of the exception, if occurs.

        Raises:
            AbortSyncRelease: In case the archives cannot be downloaded.
        """
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
            return None

        logger.debug(f"Running {self.sync_release_job_type} for {branch}")
        model.set_status(status=SyncReleaseTargetStatus.running)
        # for now the url is used only for propose-downstream
        # so it does not matter URL may not be valid for pull-from-upstream
        url = get_propose_downstream_info_url(model.id)
        buffer, handler = gather_packit_logs_to_buffer(logging_level=logging.DEBUG)

        model.set_start_time(start_time=datetime.utcnow())
        self.sync_release_helper.report_status_for_branch(
            branch=branch,
            description=f"Starting {self.job_name_for_reporting}...",
            state=BaseCommitStatus.running,
            url=url,
        )

        try:
            downstream_pr = self.sync_branch(
                branch=branch, model=sync_release_run_model
            )
            logger.debug("Downstream PR created successfully.")
            model.set_downstream_pr_url(downstream_pr_url=downstream_pr.url)
        except AbortSyncRelease:
            raise
        except Exception as ex:
            logger.debug(f"{self.sync_release_job_type} failed: {ex}")
            # make sure exception message is propagated to the logs
            logging.getLogger("packit").error(str(ex))
            # eat the exception and continue with the execution
            self.sync_release_helper.report_status_for_branch(
                branch=branch,
                description=f"{self.job_name_for_reporting.capitalize()} failed: {ex}",
                state=BaseCommitStatus.failure,
                url=url,
            )
            model.set_status(status=SyncReleaseTargetStatus.error)
            sentry_integration.send_to_sentry(ex)

            return str(ex)
        finally:
            model.set_finished_time(finished_time=datetime.utcnow())
            model.set_logs(collect_packit_logs(buffer=buffer, handler=handler))

        self.sync_release_helper.report_status_for_branch(
            branch=branch,
            description=f"{self.job_name_for_reporting.capitalize()} "
            f"finished successfully.",
            state=BaseCommitStatus.success,
            url=url,
        )
        model.set_status(status=SyncReleaseTargetStatus.submitted)

        # no error occurred
        return None

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
                if error := self.run_for_target(sync_release_run_model, model):
                    errors[model.branch] = error
        except AbortSyncRelease:
            logger.debug(
                f"{self.sync_release_job_type} is being retried because "
                "we were not able yet to download the archive. "
            )

            for model in sync_release_run_model.sync_release_targets:
                model.set_status(status=SyncReleaseTargetStatus.retry)

            self.sync_release_helper.report_status_to_all(
                description=f"{self.job_name_for_reporting.capitalize()} is "
                f"being retried because "
                "we were not able yet to download the archive. ",
                state=BaseCommitStatus.pending,
                url="",
            )

            return TaskResults(
                success=True,  # do not create a Sentry issue
                details={"msg": "Not able to download archive. Task will be retried."},
            )
        finally:
            # remove temporary dist-git clone after we're done here - context:
            # 1. the dist-git repo could be cloned on worker, not sandbox
            # 2. in such case it's stored in /tmp, not in the mirrored sandbox PV
            # 3. it's not being cleaned up and it wastes pod's filesystem space
            shutil.rmtree(self.packit_api.dg.local_project.working_dir)

        models_with_errors = [
            target
            for target in sync_release_run_model.sync_release_targets
            if target.status == SyncReleaseTargetStatus.error
        ]

        if models_with_errors:
            branch_errors = ""
            for model in sorted(models_with_errors, key=lambda model: model.branch):
                dashboard_url = self.get_dashboard_url(model.id)
                branch_errors += f"| `{model.branch}` | See {dashboard_url} |\n"
            body_msg = MSG_DOWNSTREAM_JOB_ERROR_HEADER.format(
                object="pull-requests",
                dist_git_url=self.packit_api.dg.local_project.git_url,
            )
            body_msg += f"{branch_errors}\n\n"
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
    get_dashboard_url = staticmethod(get_propose_downstream_info_url)

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
        return (IsUpstreamTagMatchingConfig,)

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
            f"release {self.tag}",
            message=body_msg,
            comment_to_existing=body_msg,
        )


@configured_as(job_type=JobType.pull_from_upstream)
@run_for_comment(command="pull-from-upstream")
@reacts_to(event=NewHotnessUpdateEvent)
@reacts_to(event=PullRequestCommentPagureEvent)
class PullFromUpstreamHandler(AbstractSyncReleaseHandler):
    task_name = TaskName.pull_from_upstream
    helper_kls = PullFromUpstreamHelper
    sync_release_job_type = SyncReleaseJobType.pull_from_upstream
    job_name_for_reporting = "Pull from upstream"
    get_dashboard_url = staticmethod(get_pull_from_upstream_info_url)

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
        if self.data.event_type in (PullRequestCommentPagureEvent.__name__,):
            # use upstream project URL when retriggering from dist-git PR
            self._project_url = package_config.upstream_project_url
        # allow self.project to be None
        self._project_required = False

    @staticmethod
    def get_checkers() -> Tuple[Type[Checker], ...]:
        return (ValidInformationForPullFromUpstream, IsUpstreamTagMatchingConfig)

    def _report_errors_for_each_branch(self, message: str) -> None:
        report_in_issue_repository(
            issue_repository=self.job_config.issue_repository,
            service_config=self.service_config,
            title=f"Pull from upstream failed for release {self.tag}",
            message=message
            + f"\n\n---\n\n*Get in [touch with us]({CONTACTS_URL}) if you need some help.*",
            comment_to_existing=message,
        )

    def run(self) -> TaskResults:
        with ChoosenGithubAuthMethod(self, AuthMethod.token):
            # allow upstream git_project to be None
            self.packit_api.up._project_required = False
            return super().run()


class AbstractDownstreamKojiBuildHandler(
    abc.ABC,
    RetriableJobHandler,
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

    @abc.abstractmethod
    def get_branches(self) -> List[str]:
        """Get a list of branch (names) to be built in koji"""

    def run(self) -> TaskResults:
        try:
            branch = None
            for branch in self.get_branches():
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

            self.report_in_issue_repository(branch, ex)
            raise ex

        return TaskResults(success=True, details={})

    @abc.abstractmethod
    def get_trigger_type_description(self) -> str:
        """Describe the user's action which triggered the Koji build"""

    def report_in_issue_repository(self, branch: str, ex: PackitException) -> None:
        body = MSG_DOWNSTREAM_JOB_ERROR_HEADER.format(
            object="Koji build", dist_git_url=self.packit_api.dg.local_project.git_url
        )
        body += f"| `{branch}` | ```{ex}``` |\n"

        msg_retrigger = MSG_RETRIGGER.format(
            job="build",
            command="koji-build",
            place="issue",
            packit_comment_command_prefix=self.service_config.comment_command_prefix,
        )

        trigger_type_description = self.get_trigger_type_description()
        body_msg = (
            f"{body}\n{trigger_type_description}\n\n{msg_retrigger}{MSG_GET_IN_TOUCH}\n"
        )

        report_in_issue_repository(
            issue_repository=self.job_config.issue_repository,
            service_config=self.service_config,
            title="Fedora Koji build failed to be triggered",
            message=body_msg,
            comment_to_existing=body_msg,
        )


@configured_as(job_type=JobType.koji_build)
@run_for_comment(command="koji-build")
@reacts_to(event=PushPagureEvent)
@reacts_to(event=PullRequestCommentPagureEvent)
class DownstreamKojiBuildHandler(
    AbstractDownstreamKojiBuildHandler,
    ConfigFromEventMixin,
    LocalProjectMixin,
    PackitAPIWithDownstreamMixin,
    GetPagurePullRequestMixin,
):
    task_name = TaskName.downstream_koji_build

    def get_branches(self) -> List[str]:
        branch = (
            self.project.get_pr(self.data.pr_id).target_branch
            if self.data.event_type in (PullRequestCommentPagureEvent.__name__,)
            else self.dg_branch
        )
        return [branch]

    def get_trigger_type_description(self) -> str:
        trigger_type_description = ""
        if self.data.event_type == PullRequestCommentPagureEvent.__name__:
            trigger_type_description += (
                f"Fedora Koji build was re-triggered "
                f"by comment in dist-git PR id {self.data.pr_id}."
            )
        elif self.data.event_type == PushPagureEvent.__name__:
            trigger_type_description += (
                f"Fedora Koji build was triggered "
                f"by push with sha {self.data.commit_sha}."
            )
        return trigger_type_description


@configured_as(job_type=JobType.koji_build)
@run_for_comment(command="koji-build")
@reacts_to(event=IssueCommentEvent)
@reacts_to(event=IssueCommentGitlabEvent)
class RetriggerDownstreamKojiBuildHandler(
    AbstractDownstreamKojiBuildHandler,
    ConfigFromDistGitUrlMixin,
    LocalProjectMixin,
    PackitAPIWithDownstreamMixin,
    GetBranchesFromIssueMixin,
):
    task_name = TaskName.retrigger_downstream_koji_build

    def get_branches(self) -> List[str]:
        return self.branches

    def get_trigger_type_description(self) -> str:
        return (
            f"Fedora Koji build was re-triggered "
            f"by comment in issue {self.data.issue_id}."
        )
