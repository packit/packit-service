# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
This file defines classes for job handlers specific for distgit
"""

import abc
import logging
import shutil
from collections import defaultdict
from datetime import datetime
from functools import partial
from os import getenv
from typing import ClassVar, Optional

from celery import Task
from ogr.abstract import AuthMethod, PullRequest
from ogr.parsing import RepoUrl, parse_git_repo
from ogr.services.github import GithubService
from packit.config import Deployment, JobConfig, JobConfigTriggerType, JobType, aliases
from packit.config.package_config import PackageConfig
from packit.exceptions import (
    PackitCommandFailedError,
    PackitDownloadFailedException,
    PackitException,
    ReleaseSkippedPackitException,
)
from packit.utils import commands
from packit.utils.koji_helper import KojiHelper

from packit_service import sentry_integration
from packit_service.config import ServiceConfig
from packit_service.constants import (
    CONTACTS_URL,
    DEFAULT_RETRY_BACKOFF,
    MSG_DOWNSTREAM_JOB_ERROR_HEADER,
    MSG_DOWNSTREAM_JOB_ERROR_ROW,
    MSG_GET_IN_TOUCH,
    MSG_RETRIGGER,
    MSG_RETRIGGER_DISTGIT,
    RETRY_LIMIT_RELEASE_ARCHIVE_DOWNLOAD_ERROR,
    KojiBuildState,
)
from packit_service.events import (
    abstract,
    anitya,
    github,
    gitlab,
    koji,
    pagure,
)
from packit_service.models import (
    KojiBuildGroupModel,
    KojiBuildTargetModel,
    KojiTagRequestGroupModel,
    KojiTagRequestTargetModel,
    PipelineModel,
    SyncReleaseJobType,
    SyncReleaseModel,
    SyncReleasePullRequestModel,
    SyncReleaseStatus,
    SyncReleaseTargetModel,
    SyncReleaseTargetStatus,
)
from packit_service.service.urls import (
    get_koji_build_info_url,
    get_propose_downstream_info_url,
    get_pull_from_upstream_info_url,
)
from packit_service.utils import (
    collect_packit_logs,
    gather_packit_logs_to_buffer,
    get_koji_task_id_and_url_from_stdout,
    get_packit_commands_from_comment,
)
from packit_service.worker.checker.abstract import Checker
from packit_service.worker.checker.distgit import (
    HasIssueCommenterRetriggeringPermissions,
    IsProjectOk,
    IsUpstreamTagMatchingConfig,
    LabelsOnDistgitPR,
    PermissionOnDistgit,
    PermissionOnDistgitForFedoraCI,
    TaggedBuildIsNotABuildOfSelf,
    ValidInformationForPullFromUpstream,
)
from packit_service.worker.checker.run_condition import IsRunConditionSatisfied
from packit_service.worker.handlers.abstract import (
    FedoraCIJobHandler,
    JobHandler,
    RetriableJobHandler,
    TaskName,
    configured_as,
    reacts_to,
    reacts_to_as_fedora_ci,
    run_for_check_rerun,
    run_for_comment,
    run_for_comment_as_fedora_ci,
)
from packit_service.worker.handlers.mixin import GetProjectToSyncMixin
from packit_service.worker.helpers.fedora_ci import FedoraCIHelper
from packit_service.worker.helpers.sidetag import SidetagHelper
from packit_service.worker.helpers.sync_release.propose_downstream import (
    ProposeDownstreamJobHelper,
)
from packit_service.worker.helpers.sync_release.pull_from_upstream import (
    PullFromUpstreamHelper,
)
from packit_service.worker.helpers.sync_release.sync_release import SyncReleaseHelper
from packit_service.worker.mixin import (
    Config,
    ConfigFromDistGitUrlMixin,
    ConfigFromEventMixin,
    ConfigFromUrlMixin,
    GetBranchesFromIssueMixin,
    GetPagurePullRequestMixin,
    GetSyncReleaseTagMixin,
    LocalProjectMixin,
    PackitAPIWithDownstreamMixin,
    PackitAPIWithUpstreamMixin,
)
from packit_service.worker.reporting import (
    BaseCommitStatus,
    create_issue_if_needed,
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
@reacts_to(event=pagure.push.Commit)
class SyncFromDownstream(
    JobHandler,
    GetProjectToSyncMixin,
    ConfigFromEventMixin,
    LocalProjectMixin,
    PackitAPIWithUpstreamMixin,
):
    """Sync new specfile changes to upstream after a new git push in the dist-git."""

    task_name = TaskName.sync_from_downstream
    non_git_upstream = False

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
    def get_checkers() -> tuple[type[Checker], ...]:
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
    check_for_non_git_upstreams: bool = False

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
        self,
        branch: str,
        model: SyncReleaseModel,
    ) -> Optional[tuple[PullRequest, dict[str, PullRequest]]]:
        try:
            branch_suffix = f"update-{self.sync_release_job_type.value}"
            is_pull_from_upstream_job = (
                self.sync_release_job_type == SyncReleaseJobType.pull_from_upstream
            )
            kwargs = {
                "dist_git_branch": branch,
                "create_pr": True,
                "local_pr_branch_suffix": branch_suffix,
                "use_downstream_specfile": is_pull_from_upstream_job,
                "add_pr_instructions": True,
                "resolved_bugs": self.get_resolved_bugs(),
                "release_monitoring_project_id": self.data.event_dict.get(
                    "anitya_project_id",
                ),
                "sync_acls": True,
                "pr_description_footer": DistgitAnnouncement.get_announcement(),
                # [TODO] Remove for CentOS support once it gets refined
                "add_new_sources": self.package_config.pkg_tool in (None, "fedpkg"),
                "fast_forward_merge_branches": self.helper.get_fast_forward_merge_branches_for(
                    branch,
                ),
            }
            if not self.packit_api.non_git_upstream:
                kwargs["tag"] = self.tag
            elif version := self.data.event_dict.get("version"):
                kwargs["versions"] = [version]
            # check if there is a Koji build job that should trigger on PR merge
            kwargs["warn_about_koji_build_triggering_bug"] = False
            for job in self.package_config.get_job_views():
                if job.type != JobType.koji_build:
                    continue
                if job.trigger != JobConfigTriggerType.commit:
                    continue
                if branch not in aliases.get_branches(
                    *job.dist_git_branches,
                    default_dg_branch="rawhide",
                ):
                    continue
                kwargs["warn_about_koji_build_triggering_bug"] = True
                break
            downstream_pr, additional_prs = self.packit_api.sync_release(**kwargs)
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
                        with sync_release_run_id {model.id}.",
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
                raise AbortSyncRelease() from ex
            raise ex
        finally:
            if self.packit_api.up.local_project:
                self.packit_api.up.local_project.git_repo.head.reset(
                    "HEAD",
                    index=True,
                    working_tree=True,
                )
                # reset also submodules
                for submodule in self.packit_api.up.local_project.git_repo.submodules:
                    try:
                        submodule.update(init=True, recursive=True, force=True)
                    except Exception as ex:  # noqa: PERF203
                        logger.warning(f"Failed to reset submodule {submodule.name}: {ex}")

        return downstream_pr, additional_prs

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
                status=SyncReleaseTargetStatus.queued,
                branch=branch,
            )
            sync_release_model.sync_release_targets.append(sync_release_target)

        return sync_release_model

    def run_for_target(
        self,
        sync_release_run_model: SyncReleaseModel,
        model: SyncReleaseTargetModel,
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
                f"that was already processed.",
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
            downstream_pr, additional_prs = self.sync_branch(
                branch=branch,
                model=sync_release_run_model,
            )
            logger.debug("Downstream PR(s) created successfully.")
            model.set_downstream_pr_url(downstream_pr_url=downstream_pr.url)
            downstream_pr_project = downstream_pr.target_project

            pr_models = [
                SyncReleasePullRequestModel.get_or_create(
                    pr_id=downstream_pr.id,
                    namespace=downstream_pr_project.namespace,
                    repo_name=downstream_pr_project.repo,
                    project_url=downstream_pr_project.get_web_url(),
                    target_branch=branch,
                    url=downstream_pr.url,
                )
            ]

            pr_models.extend(
                SyncReleasePullRequestModel.get_or_create(
                    pr_id=pr.id,
                    namespace=downstream_pr_project.namespace,
                    repo_name=downstream_pr_project.repo,
                    project_url=downstream_pr_project.get_web_url(),
                    target_branch=branch,
                    is_fast_forward=True,
                    url=pr.url,
                )
                for branch, pr in additional_prs.items()
            )

            model.set_downstream_prs(downstream_prs=pr_models)

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
            downstream_pr,
            dashboard_url,
        )
        self.sync_release_helper.report_status_for_branch(
            branch=branch,
            description=f"{self.job_name_for_reporting.capitalize()} finished successfully.",
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
        branches_to_run = [target.branch for target in sync_release_run_model.sync_release_targets]
        logger.debug(f"Branches to run {self.job_config.type}: {branches_to_run}")

        try:
            for model in sync_release_run_model.sync_release_targets:
                if error := self.run_for_target(sync_release_run_model, model):
                    errors[model.branch] = error
        except AbortSyncRelease:
            logger.debug(
                f"{self.sync_release_job_type} is being retried because "
                "we were not able yet to download the archive. ",
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
                branch_errors += MSG_DOWNSTREAM_JOB_ERROR_ROW.format(
                    branch=model.branch, url=dashboard_url
                )
            branch_errors += "</table>\n"

            body_msg = MSG_DOWNSTREAM_JOB_ERROR_HEADER.format(
                object="pull-requests",
                dist_git_url=self.packit_api.dg.local_project.git_url,
            )
            body_msg += f"{branch_errors}\n\n"

            if self.task_name == TaskName.pull_from_upstream:
                body_msg += MSG_RETRIGGER_DISTGIT.format(
                    job="pull_from_upstream",
                    packit_comment_command_prefix=self.service_config.comment_command_prefix,
                    command="pull-from-upstream",
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

    def get_resolved_bugs(self):
        raise NotImplementedError("Use subclass.")

    @staticmethod
    def report_dashboard_url(
        pr_object: PullRequest,
        dashboard_url: str,
    ):
        msg_base = "Logs and details of the syncing: [Packit dashboard]"
        msg = f"{msg_base}({dashboard_url})"
        original_description = pr_object.description
        # this is a retrigger
        if msg_base in original_description:
            pr_object.comment(msg)
        else:
            pr_object.description = original_description + "\n---\n" + msg


class AbortSyncRelease(Exception):
    """Abort sync-release process"""


@configured_as(job_type=JobType.propose_downstream)
@run_for_comment(command="propose-downstream")
@run_for_check_rerun(prefix="propose-downstream")
@reacts_to(event=github.release.Release)
@reacts_to(event=gitlab.release.Release)
@reacts_to(event=abstract.comment.Issue)
@reacts_to(event=github.check.Release)
class ProposeDownstreamHandler(AbstractSyncReleaseHandler):
    topic = "org.fedoraproject.prod.pagure.git.receive"
    task_name = TaskName.propose_downstream
    helper_kls = ProposeDownstreamJobHelper
    sync_release_job_type = SyncReleaseJobType.propose_downstream
    job_name_for_reporting = "propose downstream"
    get_dashboard_url = staticmethod(get_propose_downstream_info_url)
    check_for_non_git_upstreams = False

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
    def get_checkers() -> tuple[type[Checker], ...]:
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
            body_msg,
            self.job_config,
        )

        create_issue_if_needed(
            project=self.project,
            title=f"{self.job_name_for_reporting.capitalize()} failed for release {self.tag}",
            message=body_msg,
            comment_to_existing=body_msg,
        )

    def get_resolved_bugs(self):
        """No bugs for propose-downsteam"""
        return []


@configured_as(job_type=JobType.pull_from_upstream)
@run_for_comment(command="pull-from-upstream")
@reacts_to(event=anitya.NewHotness)
@reacts_to(event=pagure.pr.Comment)
class PullFromUpstreamHandler(AbstractSyncReleaseHandler):
    task_name = TaskName.pull_from_upstream
    helper_kls = PullFromUpstreamHelper
    sync_release_job_type = SyncReleaseJobType.pull_from_upstream
    job_name_for_reporting = "Pull from upstream"
    get_dashboard_url = staticmethod(get_pull_from_upstream_info_url)
    check_for_non_git_upstreams = True

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
        if self.data.event_type in (pagure.pr.Comment.event_type(),):
            # use upstream project URL when retriggering from dist-git PR
            self._project_url = package_config.upstream_project_url
        # allow self.project to be None
        self._project_required = False

    @staticmethod
    def get_checkers() -> tuple[type[Checker], ...]:
        return (
            ValidInformationForPullFromUpstream,
            IsUpstreamTagMatchingConfig,
            IsRunConditionSatisfied,
        )

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

    def get_resolved_bugs(self) -> list[str]:
        """
        If we are reacting to New Hotness, return the corresponding bugzilla ID only.
        In case of comment, take the argument from comment. The format in the comment
        should be /packit pull-from-upstream --resolve-bug rhbz#123,rhbz#124
        """
        if self.data.event_type in (anitya.NewHotness.event_type(),):
            bug_id = self.data.event_dict.get("bug_id")
            return [f"rhbz#{bug_id}"]

        comment = self.data.event_dict.get("comment")
        commands = get_packit_commands_from_comment(
            comment,
            self.service_config.comment_command_prefix,
        )
        args = commands[1:] if len(commands) > 1 else ""
        bugs_keyword = "--resolve-bug"
        if bugs_keyword not in args:
            return []

        bugs = (
            args[args.index(bugs_keyword) + 1] if args.index(bugs_keyword) < len(args) - 1 else None
        )
        return bugs.split(",")

    def _report_errors_for_each_branch(self, message: str) -> None:
        body_msg = (
            f"{message}\n\n---\n\n*Get in [touch with us]({CONTACTS_URL}) if you need some help.*\n"
        )
        long_message = update_message_with_configured_failure_comment_message(
            body_msg,
            self.job_config,
        )
        short_message = update_message_with_configured_failure_comment_message(
            message,
            self.job_config,
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


@run_for_comment_as_fedora_ci(command="scratch-build")
@reacts_to_as_fedora_ci(event=pagure.pr.Action)
@reacts_to_as_fedora_ci(event=pagure.pr.Comment)
class DownstreamKojiScratchBuildHandler(
    RetriableJobHandler,
    FedoraCIJobHandler,
    ConfigFromUrlMixin,
    LocalProjectMixin,
    PackitAPIWithDownstreamMixin,
):
    """
    This handler can submit a scratch build in Koji from a dist-git (Fedora CI).
    """

    task_name = TaskName.downstream_koji_scratch_build
    check_name = "Packit - scratch build"

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
        self._project_url = self.data.project_url
        self._packit_api = None
        self._koji_group_model_id = koji_group_model_id
        self._ci_helper: Optional[FedoraCIHelper] = None

    @property
    def ci_helper(self) -> FedoraCIHelper:
        if not self._ci_helper:
            self._ci_helper = FedoraCIHelper(
                project=self.project,
                metadata=self.data,
                target_branch=self.dist_git_branch,
            )
        return self._ci_helper

    @property
    def dist_git_branch(self) -> str:
        return (
            self.project.get_pr(self.data.pr_id).target_branch
            if self.data.event_type in (pagure.pr.Comment.event_type(),)
            else self.data.event_dict.get("target_branch")
        )

    @staticmethod
    def get_checkers() -> tuple[type[Checker], ...]:
        return (PermissionOnDistgitForFedoraCI,)

    @staticmethod
    def _repo_url_with_git_ref(
        source_repo: RepoUrl,
        git_ref: str,
    ) -> str:
        """Produces a git URL that can be used for Koji to clone repo for build.

        Resulting URL is in the following form:

            git+{scheme}://{hostname}/{full_repo_path}.git#{git_ref}

        Args:
            source_repo: Parsed URL of the git repo.
            git_ref: Git reference to be included in the resulting URL.

        Returns:
            Git URL for Koji that can be used to clone the source to build
            a scratch build from.
        """
        return "git+{instance_url}/{full_repo_path}.git#{git_ref}".format(
            instance_url=source_repo.get_instance_url(),
            full_repo_path="/".join(
                [
                    part
                    for part in (
                        f"forks/{source_repo.username}" if source_repo.is_fork else None,
                        source_repo.namespace,
                        source_repo.repo,
                    )
                    if part
                ]
            ),
            git_ref=git_ref,
        )

    @property
    def repo_url(self) -> str:
        return self._repo_url_with_git_ref(
            source_repo=parse_git_repo(self.data.event_dict["source_project_url"]),
            git_ref=self.data.commit_sha,
        )

    def report(self, description: str, commit_status: BaseCommitStatus, url: Optional[str]) -> None:
        self.ci_helper.report(
            state=commit_status,
            description=description,
            url=url,
            check_name=self.check_name,
        )

    def run(self) -> TaskResults:
        try:
            self.packit_api.init_kerberos_ticket()
        except PackitCommandFailedError as ex:
            msg = f"Kerberos authentication error: {ex.stderr_output}"
            logger.error(msg)
            self.report(
                commit_status=BaseCommitStatus.error,
                description=msg,
                url=None,
            )
            return TaskResults(success=False, details={"msg": msg})

        build_group = KojiBuildGroupModel.create(
            run_model=PipelineModel.create(
                project_event=self.data.db_project_event,
            )
        )

        koji_build = KojiBuildTargetModel.create(
            task_id=None,
            web_url=None,
            target=self.dist_git_branch,
            status="pending",
            scratch=True,
            koji_build_group=build_group,
        )
        try:
            stdout = self.run_koji_build()
            if stdout:
                task_id, web_url = get_koji_task_id_and_url_from_stdout(stdout)
                koji_build.set_task_id(str(task_id))
                koji_build.set_web_url(web_url)
                koji_build.set_build_submission_stdout(stdout)
            url = get_koji_build_info_url(koji_build.id)
            self.report(
                commit_status=BaseCommitStatus.running,
                description="RPM build was submitted ...",
                url=url,
            )
        except Exception as ex:
            sentry_integration.send_to_sentry(ex)
            self.report(
                commit_status=BaseCommitStatus.error,
                description=f"Submit of the build failed: {ex}",
                url=None,
            )
            if isinstance(ex, PackitCommandFailedError):
                error = f"{ex!s}\n{ex.stderr_output}"
                koji_build.set_build_submission_stdout(ex.stdout_output)
                koji_build.set_data({"error": error})

            koji_build.set_status("error")
            return TaskResults(
                success=False,
                details={
                    "msg": "Koji scratch build submit was not successful.",
                    "error": str(ex),
                },
            )

        return TaskResults(success=True, details={})

    def run_koji_build(
        self,
    ):
        """
        Perform a `koji build` from SCM.

        Returns:
            str output
        """
        cmd = [
            "koji",
            "build",
            "--scratch",
            "--nowait",
            self.dist_git_branch,
            self.repo_url,
        ]
        logger.info("Starting a Koji scratch build.")
        return commands.run_command_remote(
            cmd=cmd,
            cwd=self.local_project.working_dir,
            output=True,
            print_live=True,
        ).stdout


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
    def get_checkers() -> tuple[type[Checker], ...]:
        return (
            LabelsOnDistgitPR,
            PermissionOnDistgit,
            HasIssueCommenterRetriggeringPermissions,
            TaggedBuildIsNotABuildOfSelf,
            IsRunConditionSatisfied,
        )

    def _get_or_create_koji_group_model(self) -> KojiBuildGroupModel:
        if self._koji_group_model_id is not None:
            return KojiBuildGroupModel.get_by_id(self._koji_group_model_id)
        group = KojiBuildGroupModel.create(
            run_model=PipelineModel.create(
                project_event=self.data.db_project_event,
                package_name=self.get_package_name(),
            ),
        )

        for branch in self.get_branches():
            sidetag = None
            if self.job_config.sidetag_group:
                # we need Kerberos ticket to create a new sidetag
                self.packit_api.init_kerberos_ticket()
                sidetag = SidetagHelper.get_or_create_sidetag(
                    self.job_config.sidetag_group,
                    branch,
                )
                # check if dependencies are satisfied within the sidetag
                dependencies = set(self.job_config.dependencies or [])
                if missing_dependencies := sidetag.get_missing_dependencies(
                    dependencies,
                ):
                    raise PackitException(
                        f"Missing dependencies for Koji build: {missing_dependencies}",
                    )

            KojiBuildTargetModel.create(
                task_id=None,
                web_url=None,
                target=branch,
                status="queued",
                scratch=self.job_config.scratch,
                sidetag=sidetag.koji_name if sidetag else None,
                nvr=self.packit_api.dg.get_nvr(branch),
                koji_build_group=group,
            )

        return group

    @abc.abstractmethod
    def get_branches(self) -> list[str]:
        """Get a list of branch (names) to be built in koji"""

    def is_already_triggered(self, nvr: str) -> bool:
        """
        Check if the build was already triggered
        (building or completed state).
        """
        existing_build = self.koji_helper.get_build_info(nvr)

        if existing_build:
            raw_state = existing_build["state"]
            if (state := KojiBuildState.from_number(raw_state)) in (
                KojiBuildState.building,
                KojiBuildState.complete,
            ):
                logger.debug(
                    f"Koji build with matching NVR ({nvr}) found with state {state}",
                )
                return True

        return False

    def run(self) -> TaskResults:
        try:
            group = self._get_or_create_koji_group_model()
        except PackitException as ex:
            logger.debug(f"Koji build failed to be submitted: {ex}")
            return TaskResults(success=True, details={})

        errors = {}
        for koji_build_model in group.grouped_targets:
            branch = koji_build_model.target

            # skip submitting build for a branch if we already did that (even if it failed)
            if koji_build_model.status not in ["queued", "pending", "retry"]:
                logger.debug(
                    f"Skipping downstream Koji build for branch {branch} "
                    f"that was already processed.",
                )
                continue

            if not self.job_config.scratch:
                existing_models = KojiBuildTargetModel.get_all_successful_or_in_progress_by_nvr(
                    koji_build_model.nvr,
                )
                if existing_models - {koji_build_model} or self.is_already_triggered(
                    koji_build_model.nvr,
                ):
                    logger.info(
                        f"Skipping downstream Koji build {koji_build_model.nvr} "
                        f"for branch {branch} that was already triggered.",
                    )
                    koji_build_model.set_status("skipped")
                    continue

            logger.debug(f"Running downstream Koji build for {branch}")
            koji_build_model.set_status("pending")

            try:
                stdout = self.packit_api.build(
                    dist_git_branch=koji_build_model.target,
                    scratch=self.job_config.scratch,
                    nowait=True,
                    from_upstream=False,
                    koji_target=koji_build_model.sidetag,
                )
                if stdout:
                    task_id, web_url = get_koji_task_id_and_url_from_stdout(stdout)
                    koji_build_model.set_task_id(str(task_id))
                    koji_build_model.set_web_url(web_url)
                    koji_build_model.set_build_submission_stdout(stdout)
            except PackitException as ex:
                if self.celery_task and not self.celery_task.is_last_try():
                    kargs = self.celery_task.task.request.kwargs.copy()
                    kargs["koji_group_model_id"] = group.id
                    for model in group.grouped_targets:
                        model.set_status("retry")

                    logger.debug(
                        "Celery task will be retried. User will not be notified about the failure.",
                    )
                    retry_backoff = int(
                        getenv("CELERY_RETRY_BACKOFF", DEFAULT_RETRY_BACKOFF),
                    )
                    delay = retry_backoff * 2**self.celery_task.retries
                    self.celery_task.task.retry(exc=ex, countdown=delay, kwargs=kargs)
                    return TaskResults(
                        success=True,
                        details={
                            "msg": f"There was an error: {ex}. Task will be retried.",
                        },
                    )
                error = str(ex)
                if isinstance(ex, PackitCommandFailedError):
                    error += f"\n{ex.stderr_output}"
                    koji_build_model.set_build_submission_stdout(ex.stdout_output)

                errors[branch] = get_koji_build_info_url(koji_build_model.id)
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
            object="Koji build",
            dist_git_url=self.packit_api.dg.local_project.git_url,
        )
        for branch, url in errors.items():
            body += MSG_DOWNSTREAM_JOB_ERROR_ROW.format(branch=branch, url=url)
        body += "</table>\n"

        msg_retrigger = MSG_RETRIGGER.format(
            job="build",
            command="koji-build",
            place="issue",
            packit_comment_command_prefix=self.service_config.comment_command_prefix,
        )

        trigger_type_description = self.get_trigger_type_description()
        body_msg = f"{body}\n{trigger_type_description}\n\n{msg_retrigger}{MSG_GET_IN_TOUCH}\n"
        body_msg = update_message_with_configured_failure_comment_message(
            body_msg,
            self.job_config,
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
@reacts_to(event=pagure.push.Commit)
@reacts_to(event=pagure.pr.Comment)
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

    def get_branches(self) -> list[str]:
        branch = (
            self.project.get_pr(self.data.pr_id).target_branch
            if self.data.event_type in (pagure.pr.Comment.event_type(),)
            else self.dg_branch
        )
        return [branch]

    def get_trigger_type_description(self) -> str:
        trigger_type_description = ""
        if self.data.event_type == pagure.pr.Comment.event_type():
            trigger_type_description += (
                f"Fedora Koji build was re-triggered "
                f"by comment in dist-git PR id {self.data.pr_id}."
            )
        elif self.data.event_type == pagure.push.Commit.event_type():
            trigger_type_description += (
                f"Fedora Koji build was triggered by push with sha {self.data.commit_sha}."
            )
        elif self.data.event_type == koji.tag.Build.event_type():
            trigger_type_description += (
                f"Fedora Koji build was triggered "
                f"by tagging of build {self.data.event_dict['build_id']} "
                f"into {self.data.event_dict['tag_name']}."
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
@reacts_to(event=github.issue.Comment)
@reacts_to(event=gitlab.issue.Comment)
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

    def get_branches(self) -> list[str]:
        return self.branches

    def get_trigger_type_description(self) -> str:
        return f"Fedora Koji build was re-triggered by comment in issue {self.data.issue_id}."


@configured_as(job_type=JobType.koji_build)
@run_for_comment(command="koji-tag")
@reacts_to(event=pagure.pr.Comment)
class TagIntoSidetagHandler(
    RetriableJobHandler,
    ConfigFromEventMixin,
    PackitAPIWithDownstreamMixin,
    GetPagurePullRequestMixin,
):
    task_name = TaskName.tag_into_sidetag

    @staticmethod
    def get_checkers() -> tuple[type[Checker], ...]:
        return (PermissionOnDistgit,)

    @staticmethod
    def get_handler_specific_task_accepted_message(
        service_config: ServiceConfig,
    ) -> str:
        dashboard_url = service_config.dashboard_url

        return (
            "You can check the recent Koji tagging requests "
            f"in [Packit dashboard]({dashboard_url}/jobs/koji-tag-requests). "
            f"{DistgitAnnouncement.get_comment_footer_with_announcement_if_present()}"
        )

    def run_for_branch(
        self,
        package: str,
        sidetag_group: str,
        branch: str,
        tag_request_group: KojiTagRequestGroupModel,
    ) -> None:
        # we need Kerberos ticket to tag a build into sidetag
        # and to create a new sidetag (if needed)
        self.packit_api.init_kerberos_ticket()
        sidetag = SidetagHelper.get_or_create_sidetag(sidetag_group, branch)
        if not (nvr := sidetag.get_latest_stable_nvr(package)):
            logger.debug(f"Failed to find the latest stable build of {package}")
            return
        task_id = sidetag.tag_build(nvr)
        web_url = koji.result.Task.get_koji_rpm_build_web_url(
            rpm_build_task_id=int(task_id),
            koji_web_url=self.service_config.koji_web_url,
        )
        KojiTagRequestTargetModel.create(
            task_id=task_id,
            web_url=web_url,
            target=branch,
            sidetag=sidetag.koji_name,
            nvr=str(nvr),
            koji_tag_request_group=tag_request_group,
        )

    def run(self) -> TaskResults:
        comment = self.data.event_dict.get("comment")
        commands = get_packit_commands_from_comment(
            comment,
            self.service_config.comment_command_prefix,
        )
        args = commands[1:] if len(commands) > 1 else ""
        packages_to_tag: dict[str, dict[str, set[str]]] = defaultdict(
            partial(defaultdict, set),
        )
        for job in self.package_config.get_job_views():
            if job.type == JobType.koji_build and job.sidetag_group and job.dist_git_branches:
                # get all configured branches including branch aliases
                # so it doesn't matter whether the PR is opened against rawhide or main,
                # if the job is configured for either it will match
                configured_branches = aliases.get_branches(
                    *job.dist_git_branches, with_aliases=True
                )
                if "--all-branches" in args:
                    branches = aliases.get_branches(*job.dist_git_branches)
                elif self.pull_request.target_branch not in configured_branches:
                    continue
                else:
                    branches = {self.pull_request.target_branch}
                packages_to_tag[job.downstream_package_name][job.sidetag_group] |= branches
        for package, sidetag_groups in packages_to_tag.items():
            tag_request_group = KojiTagRequestGroupModel.create(
                run_model=PipelineModel.create(
                    project_event=self.data.db_project_event,
                    package_name=package,
                ),
            )
            for sidetag_group, branches in sidetag_groups.items():
                logger.debug(
                    f"Running downstream Koji build tagging of {package} "
                    f"for {branches} in {sidetag_group}",
                )
                for branch in branches:
                    self.run_for_branch(package, sidetag_group, branch, tag_request_group)
        return TaskResults(success=True, details={})
