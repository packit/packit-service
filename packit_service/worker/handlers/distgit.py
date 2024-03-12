# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
This file defines classes for job handlers specific for distgit
"""
import abc
import logging
import shutil
from datetime import datetime
from os import getenv
from typing import Optional, Tuple, Type, List, ClassVar

from celery import Task

from ogr.abstract import PullRequest, AuthMethod
from ogr.services.github import GithubService
from packit.config import JobConfig, JobType, Deployment
from packit.config.package_config import PackageConfig
from packit.exceptions import (
    PackitException,
    PackitDownloadFailedException,
    ReleaseSkippedPackitException,
)
from packit.utils.koji_helper import KojiHelper
from packit_service import sentry_integration
from packit_service.config import PackageConfigGetter, ServiceConfig
from packit_service.constants import (
    CONTACTS_URL,
    MSG_RETRIGGER,
    MSG_GET_IN_TOUCH,
    MSG_DOWNSTREAM_JOB_ERROR_HEADER,
    DEFAULT_RETRY_BACKOFF,
    RETRY_LIMIT_RELEASE_ARCHIVE_DOWNLOAD_ERROR,
)
from packit_service.models import (
    SyncReleasePullRequestModel,
    SyncReleaseTargetStatus,
    SyncReleaseTargetModel,
    SyncReleaseModel,
    SyncReleaseStatus,
    SyncReleaseJobType,
    KojiBuildTargetModel,
    PipelineModel,
    KojiBuildGroupModel,
    SidetagGroupModel,
    SidetagModel,
)
from packit_service.service.urls import (
    get_propose_downstream_info_url,
    get_pull_from_upstream_info_url,
)
from packit_service.utils import (
    gather_packit_logs_to_buffer,
    collect_packit_logs,
    get_packit_commands_from_comment,
    get_koji_task_id_and_url_from_stdout,
)
from packit_service.worker.checker.abstract import Checker
from packit_service.worker.checker.distgit import (
    IsProjectOk,
    PermissionOnDistgit,
    ValidInformationForPullFromUpstream,
    HasIssueCommenterRetriggeringPermissions,
    IsUpstreamTagMatchingConfig,
    LabelsOnDistgitPR,
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
from packit_service.worker.reporting import (
    BaseCommitStatus,
    report_in_issue_repository,
    update_message_with_configured_failure_comment_message,
)
from packit_service.worker.reporting.news import DistgitAnnouncement
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
                resolved_bugs=self.get_resolved_bugs(),
                release_monitoring_project_id=self.data.event_dict.get(
                    "release_monitoring_project_id"
                ),
                sync_acls=True,
                pr_description_footer=DistgitAnnouncement.get_announcement(),
                # [TODO] Remove for CentOS support once it gets refined
                add_new_sources=self.package_config.pkg_tool in (None, "fedpkg"),
            )
        except PackitDownloadFailedException as ex:
            # the archive has not been uploaded to PyPI yet
            # retry for the archive to become available
            logger.info(f"We were not able to download the archive: {ex}")
            # when the task hits max_retries, it raises MaxRetriesExceededError
            # and the error handling code would be never executed
            retries = self.celery_task.retries
            if retries < RETRY_LIMIT_RELEASE_ARCHIVE_DOWNLOAD_ERROR:
                # retry after 1 min, 2 mins, 4 mins, 8 mins, 16 mins, 32 mins
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
                # https://docs.celeryq.dev/en/stable/reference/celery.app.task.html#celery.app.task.Task.retry
                self.celery_task.task.retry(
                    exc=ex,
                    countdown=delay,
                    throw=False,
                    args=(),
                    kwargs=kargs,
                    max_retries=RETRY_LIMIT_RELEASE_ARCHIVE_DOWNLOAD_ERROR,
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
            job_type=(
                SyncReleaseJobType.propose_downstream
                if self.job_config.type == JobType.propose_downstream
                else SyncReleaseJobType.pull_from_upstream
            ),
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
            downstream_pr_project = downstream_pr.target_project
            sync_release_pull_request = SyncReleasePullRequestModel.get_or_create(
                pr_id=downstream_pr.id,
                namespace=downstream_pr_project.namespace,
                repo_name=downstream_pr_project.repo,
                project_url=downstream_pr_project.get_web_url(),
            )
            model.set_downstream_pr(downstream_pr=sync_release_pull_request)
        except AbortSyncRelease:
            raise
        except Exception as ex:
            logger.debug(f"{self.sync_release_job_type} failed: {ex}")
            # make sure exception message is propagated to the logs
            logging.getLogger("packit").error(str(ex))
            (state, status) = (
                (BaseCommitStatus.neutral, SyncReleaseTargetStatus.skipped)
                if isinstance(ex, ReleaseSkippedPackitException)
                else (BaseCommitStatus.failure, SyncReleaseTargetStatus.error)
            )
            # eat the exception and continue with the execution
            self.sync_release_helper.report_status_for_branch(
                branch=branch,
                description=f"{self.job_name_for_reporting.capitalize()} failed: {ex}",
                state=state,
                url=url,
            )
            model.set_status(status=status)
            sentry_integration.send_to_sentry(ex)

            return str(ex)
        finally:
            model.set_finished_time(finished_time=datetime.utcnow())
            model.set_logs(collect_packit_logs(buffer=buffer, handler=handler))

        dashboard_url = self.get_dashboard_url(model.id)
        self.report_dashboard_url(
            sync_release_pull_request, downstream_pr, dashboard_url
        )
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

    def get_resolved_bugs(self):
        raise NotImplementedError("Use subclass.")

    @staticmethod
    def report_dashboard_url(
        pr_model: SyncReleasePullRequestModel,
        pr_object: PullRequest,
        dashboard_url: str,
    ):
        msg = f"Logs and details of the syncing: [Packit dashboard]({dashboard_url})"
        # this is a retrigger
        if len(pr_model.sync_release_targets) > 1:
            pr_object.comment(msg)
        else:
            original_description = pr_object.description
            pr_object.description = original_description + "\n---\n" + msg


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
    topic = "org.fedoraproject.prod.pagure.git.receive"
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
        if not self.job_config.notifications.failure_issue.create:
            logger.debug("Reporting via issues disabled in config, skipping.")
            return

        msg_retrigger = MSG_RETRIGGER.format(
            job="update",
            command="propose-downstream",
            place="issue",
            packit_comment_command_prefix=self.service_config.comment_command_prefix,
        )
        body_msg = f"{message}{msg_retrigger}\n"

        body_msg = update_message_with_configured_failure_comment_message(
            body_msg, self.job_config
        )

        PackageConfigGetter.create_issue_if_needed(
            project=self.project,
            title=f"{self.job_name_for_reporting.capitalize()} failed for "
            f"release {self.tag}",
            message=body_msg,
            comment_to_existing=body_msg,
        )

    def get_resolved_bugs(self):
        """No bugs for propose-downsteam"""
        return []


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

    @staticmethod
    def get_handler_specific_task_accepted_message(
        service_config: ServiceConfig,
    ) -> str:
        dashboard_url = service_config.dashboard_url
        return (
            "You can check the recent runs of pull from upstream jobs "
            f"in [Packit dashboard]({dashboard_url}/jobs/pull-from-upstreams)"
            f"{DistgitAnnouncement.get_comment_footer_with_announcement_if_present()}"
        )

    def get_resolved_bugs(self) -> List[str]:
        """
        If we are reacting to New Hotness, return the corresponding bugzilla ID only.
        In case of comment, take the argument from comment. The format in the comment
        should be /packit pull-from-upstream --resolved-bugs rhbz#123,rhbz#124
        """
        if self.data.event_type in (NewHotnessUpdateEvent.__name__,):
            bug_id = self.data.event_dict.get("bug_id")
            return [f"rhbz#{bug_id}"]

        comment = self.data.event_dict.get("comment")
        commands = get_packit_commands_from_comment(
            comment, self.service_config.comment_command_prefix
        )
        args = commands[1:] if len(commands) > 1 else ""
        bugs_keyword = "--resolved-bugs"
        if bugs_keyword not in args:
            return []

        bugs = (
            args[args.index(bugs_keyword) + 1]
            if args.index(bugs_keyword) < len(args) - 1
            else None
        )
        return bugs.split(",")

    def _report_errors_for_each_branch(self, message: str) -> None:
        body_msg = (
            f"{message}\n\n---\n\n*Get in [touch with us]({CONTACTS_URL}) if you "
            f"need some help.*\n"
        )
        long_message = update_message_with_configured_failure_comment_message(
            body_msg, self.job_config
        )
        short_message = update_message_with_configured_failure_comment_message(
            message, self.job_config
        )
        report_in_issue_repository(
            issue_repository=self.job_config.issue_repository,
            service_config=self.service_config,
            title=f"Pull from upstream failed for release {self.tag}",
            message=long_message,
            comment_to_existing=short_message,
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

    topic = "org.fedoraproject.prod.pagure.git.receive"
    task_name = TaskName.downstream_koji_build

    _koji_helper: Optional[KojiHelper] = None

    def __init__(
        self,
        package_config: PackageConfig,
        job_config: JobConfig,
        event: dict,
        celery_task: Task,
        koji_group_model_id: Optional[int] = None,
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
        self._koji_group_model_id = koji_group_model_id

    @property
    def koji_helper(self):
        if not self._koji_helper:
            self._koji_helper = KojiHelper()
        return self._koji_helper

    @staticmethod
    def get_checkers() -> Tuple[Type[Checker], ...]:
        return (
            LabelsOnDistgitPR,
            PermissionOnDistgit,
            HasIssueCommenterRetriggeringPermissions,
        )

    def _get_or_create_koji_group_model(self) -> KojiBuildGroupModel:
        if self._koji_group_model_id is not None:
            return KojiBuildGroupModel.get_by_id(self._koji_group_model_id)
        group = KojiBuildGroupModel.create(
            run_model=PipelineModel.create(
                project_event=self.data.db_project_event,
                package_name=self.get_package_name(),
            )
        )

        for branch in self.get_branches():
            KojiBuildTargetModel.create(
                task_id=None,
                web_url=None,
                target=branch,
                status="queued",
                scratch=self.job_config.scratch,
                koji_build_group=group,
            )

        return group

    @abc.abstractmethod
    def get_branches(self) -> List[str]:
        """Get a list of branch (names) to be built in koji"""

    def run(self) -> TaskResults:
        errors = {}

        if self.job_config.sidetag_group:
            sidetag_group = SidetagGroupModel.get_or_create(
                self.job_config.sidetag_group
            )
        else:
            sidetag_group = None

        group = self._get_or_create_koji_group_model()
        for koji_build_model in group.grouped_targets:
            branch = koji_build_model.target

            # skip submitting build for a branch if we already did that (even if it failed)
            if koji_build_model.status not in ["queued", "pending", "retry"]:
                logger.debug(
                    f"Skipping downstream Koji build for branch {branch} "
                    f"that was already processed."
                )
                continue

            logger.debug(f"Running downstream Koji build for {branch}")
            koji_build_model.set_status("pending")

            try:
                if sidetag_group:
                    sidetag = SidetagModel.get_or_create(sidetag_group, branch)
                    if not sidetag.koji_name or not self.koji_helper.get_tag_info(
                        sidetag.koji_name
                    ):
                        # we need Kerberos ticket to create a new sidetag
                        self.packit_api.init_kerberos_ticket()
                        tag_info = self.koji_helper.create_sidetag(branch)
                        if not tag_info:
                            raise PackitException(
                                f"Failed to create sidetag for {branch}"
                            )
                        sidetag.set_koji_name(tag_info["name"])
                else:
                    sidetag = None

                stdout = self.packit_api.build(
                    dist_git_branch=koji_build_model.target,
                    scratch=self.job_config.scratch,
                    nowait=True,
                    from_upstream=False,
                    koji_target=sidetag.koji_name if sidetag else None,
                )
                if stdout:
                    task_id, web_url = get_koji_task_id_and_url_from_stdout(stdout)
                    koji_build_model.set_task_id(str(task_id))
                    koji_build_model.set_web_url(web_url)
            except PackitException as ex:
                if self.celery_task and not self.celery_task.is_last_try():
                    kargs = self.celery_task.task.request.kwargs.copy()
                    kargs["koji_group_model_id"] = group.id
                    for model in group.grouped_targets:
                        model.set_status("retry")

                    logger.debug(
                        "Celery task will be retried. User will not be notified about the failure."
                    )
                    retry_backoff = int(
                        getenv("CELERY_RETRY_BACKOFF", DEFAULT_RETRY_BACKOFF)
                    )
                    delay = retry_backoff * 2**self.celery_task.retries
                    self.celery_task.task.retry(exc=ex, countdown=delay, kwargs=kargs)
                    return TaskResults(
                        success=True,
                        details={
                            "msg": f"There was an error: {ex}. Task will be retried."
                        },
                    )
                error = str(ex)
                errors[branch] = error
                koji_build_model.set_data({"error": error})
                koji_build_model.set_status("error")
                continue

        if errors:
            self.report_in_issue_repository(errors)

        return TaskResults(success=True, details={})

    @abc.abstractmethod
    def get_trigger_type_description(self) -> str:
        """Describe the user's action which triggered the Koji build"""

    def report_in_issue_repository(self, errors: dict[str, str]) -> None:
        body = MSG_DOWNSTREAM_JOB_ERROR_HEADER.format(
            object="Koji build", dist_git_url=self.packit_api.dg.local_project.git_url
        )
        for branch, ex in errors.items():
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
        body_msg = update_message_with_configured_failure_comment_message(
            body_msg, self.job_config
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

    def __init__(
        self,
        package_config: PackageConfig,
        job_config: JobConfig,
        event: dict,
        celery_task: Task,
        koji_group_model_id: Optional[int] = None,
    ):
        super().__init__(
            package_config=package_config,
            job_config=job_config,
            event=event,
            celery_task=celery_task,
            koji_group_model_id=koji_group_model_id,
        )

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

    @staticmethod
    def get_handler_specific_task_accepted_message(
        service_config: ServiceConfig,
    ) -> str:
        if service_config.deployment == Deployment.prod:
            user = "packit"
            user_id = 4641
        else:
            user = "packit-stg"
            user_id = 5279

        dashboard_url = service_config.dashboard_url

        return (
            "You can check the recent runs of downstream Koji jobs "
            f"in [Packit dashboard]({dashboard_url}/jobs/downstream-koji-builds). "
            f"You can also check the recent Koji build activity of `{user}` in [the Koji interface]"
            f"(https://koji.fedoraproject.org/koji/userinfo?userID={user_id})."
            f"{DistgitAnnouncement.get_comment_footer_with_announcement_if_present()}"
        )


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

    def __init__(
        self,
        package_config: PackageConfig,
        job_config: JobConfig,
        event: dict,
        celery_task: Task,
        koji_group_model_id: Optional[int] = None,
    ):
        super().__init__(
            package_config=package_config,
            job_config=job_config,
            event=event,
            celery_task=celery_task,
            koji_group_model_id=koji_group_model_id,
        )

    def get_branches(self) -> List[str]:
        return self.branches

    def get_trigger_type_description(self) -> str:
        return (
            f"Fedora Koji build was re-triggered "
            f"by comment in issue {self.data.issue_id}."
        )
