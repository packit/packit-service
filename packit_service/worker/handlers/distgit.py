# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
This file defines classes for job handlers specific for distgit
"""
import logging
from datetime import datetime
from os import getenv

import shutil
from typing import Optional, Dict, List

from celery import Task
from ogr.abstract import PullRequest
from packit.exceptions import PackitException

from packit.api import PackitAPI
from packit.config import JobConfig, JobType
from packit.config.aliases import get_branches
from packit.config.package_config import PackageConfig
from packit.local_project import LocalProject
from packit.utils.repo import RepositoryCache

from packit_service import sentry_integration
from packit_service.config import PackageConfigGetter, ProjectToSync
from packit_service.constants import (
    CONTACTS_URL,
    DEFAULT_RETRY_LIMIT,
    FILE_DOWNLOAD_FAILURE,
    MSG_RETRIGGER,
)
from packit_service.models import (
    ProposeDownstreamTargetStatus,
    ProposeDownstreamTargetModel,
    ProposeDownstreamModel,
    ProposeDownstreamStatus,
)
from packit_service.service.urls import get_propose_downstream_info_url
from packit_service.utils import gather_packit_logs_to_buffer, collect_packit_logs
from packit_service.worker.helpers.propose_downstream import ProposeDownstreamJobHelper
from packit_service.worker.events import (
    PushPagureEvent,
    ReleaseEvent,
    AbstractIssueCommentEvent,
    CheckRerunReleaseEvent,
)
from packit_service.worker.handlers.abstract import (
    JobHandler,
    TaskName,
    configured_as,
    reacts_to,
    run_for_comment,
    run_for_check_rerun,
)
from packit_service.worker.reporting import BaseCommitStatus
from packit_service.worker.result import TaskResults

logger = logging.getLogger(__name__)


@configured_as(job_type=JobType.sync_from_downstream)
@reacts_to(event=PushPagureEvent)
class SyncFromDownstream(JobHandler):
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
        self.dg_repo_name = event.get("repo_name")
        self.dg_branch = event.get("git_ref")
        self._project_to_sync: Optional[ProjectToSync] = None

    @property
    def project_to_sync(self) -> Optional[ProjectToSync]:
        if self._project_to_sync is None:
            if project_to_sync := self.service_config.get_project_to_sync(
                dg_repo_name=self.dg_repo_name, dg_branch=self.dg_branch
            ):
                self._project_to_sync = project_to_sync
        return self._project_to_sync

    def pre_check(self) -> bool:
        return self.project_to_sync is not None

    def run(self) -> TaskResults:
        ogr_project_to_sync = self.service_config.get_project(
            url=f"{self.project_to_sync.forge}/"
            f"{self.project_to_sync.repo_namespace}/{self.project_to_sync.repo_name}"
        )
        upstream_local_project = LocalProject(
            git_project=ogr_project_to_sync,
            working_dir=self.service_config.command_handler_work_dir,
            cache=RepositoryCache(
                cache_path=self.service_config.repository_cache,
                add_new=self.service_config.add_repositories_to_repository_cache,
            )
            if self.service_config.repository_cache
            else None,
        )
        packit_api = PackitAPI(
            self.service_config,
            self.job_config,
            upstream_local_project=upstream_local_project,
        )
        # rev is a commit
        # we use branch on purpose so we get the latest thing
        # TODO: check if rev is HEAD on {branch}, warn then?
        packit_api.sync_from_downstream(
            dist_git_branch=self.dg_branch,
            upstream_branch=self.project_to_sync.branch,
            sync_only_specfile=True,
        )
        return TaskResults(success=True, details={})


class AbortProposeDownstream(Exception):
    """Abort propose-downstream process"""


@configured_as(job_type=JobType.propose_downstream)
@run_for_comment(command="propose-downstream")
@run_for_comment(command="propose-update")  # deprecated
@run_for_check_rerun(prefix="propose-downstream")
@reacts_to(event=ReleaseEvent)
@reacts_to(event=AbstractIssueCommentEvent)
@reacts_to(event=CheckRerunReleaseEvent)
class ProposeDownstreamHandler(JobHandler):
    topic = "org.fedoraproject.prod.git.receive"
    task_name = TaskName.propose_downstream

    def __init__(
        self,
        package_config: PackageConfig,
        job_config: JobConfig,
        event: dict,
        propose_downstream_run_id: Optional[int] = None,
        task: Task = None,
    ):
        super().__init__(
            package_config=package_config,
            job_config=job_config,
            event=event,
        )
        self.task = task
        self._propose_downstream_run_id = propose_downstream_run_id
        self._propose_downstream_helper: Optional[ProposeDownstreamJobHelper] = None

    @property
    def propose_downstream_helper(self) -> ProposeDownstreamJobHelper:
        if not self._propose_downstream_helper:
            self._propose_downstream_helper = ProposeDownstreamJobHelper(
                service_config=self.service_config,
                package_config=self.package_config,
                project=self.project,
                metadata=self.data,
                db_trigger=self.data.db_trigger,
                job_config=self.job_config,
                branches_override=self.data.branches_override,
            )
        return self._propose_downstream_helper

    def sync_branch(
        self, branch: str, model: ProposeDownstreamModel
    ) -> Optional[PullRequest]:
        try:
            downstream_pr = self.api.sync_release(
                dist_git_branch=branch, tag=self.data.tag_name, create_pr=True
            )
        except Exception as ex:
            # the archive has not been uploaded to PyPI yet
            if FILE_DOWNLOAD_FAILURE in str(ex):
                # retry for the archive to become available
                logger.info(f"We were not able to download the archive: {ex}")
                # when the task hits max_retries, it raises MaxRetriesExceededError
                # and the error handling code would be never executed
                retries = self.task.request.retries
                if retries < int(getenv("CELERY_RETRY_LIMIT", DEFAULT_RETRY_LIMIT)):
                    # will retry in: 1m and then again in another 2m
                    delay = 60 * 2**retries
                    logger.info(
                        f"Will retry for the {retries + 1}. time in {delay}s \
                            with propose_downstream_run_id {model.id}."
                    )
                    # throw=False so that exception is not raised and task
                    # is not retried also automatically
                    kargs = self.task.request.kwargs.copy()
                    kargs["propose_downstream_run_id"] = model.id
                    # https://celeryproject.readthedocs.io/zh_CN/latest/userguide/tasks.html#retrying
                    self.task.retry(
                        exc=ex, countdown=delay, throw=False, args=(), kwargs=kargs
                    )
                    raise AbortProposeDownstream()
            raise ex
        finally:
            self.api.up.local_project.git_repo.head.reset(
                "HEAD", index=True, working_tree=True
            )

        return downstream_pr

    @staticmethod
    def _create_new_propose_for_each_branch(
        propose_downstream_model: ProposeDownstreamModel, branches: List[str]
    ) -> None:
        for branch in branches:
            propose_downstream_target = ProposeDownstreamTargetModel.create(
                status=ProposeDownstreamTargetStatus.queued
            )
            propose_downstream_target.set_branch(branch=branch)
            propose_downstream_model.propose_downstream_targets.append(
                propose_downstream_target
            )

    def _report_errors_for_each_branch(self, errors: Dict[str, str]) -> None:
        branch_errors = ""
        for branch, err in sorted(
            errors.items(), key=lambda branch_error: branch_error[0]
        ):
            err_without_new_lines = err.replace("\n", " ")
            branch_errors += f"| `{branch}` | `{err_without_new_lines}` |\n"

        msg_retrigger = MSG_RETRIGGER.format(
            job="update",
            command="propose-downstream",
            place="issue",
            packit_comment_command_prefix=self.service_config.comment_command_prefix,
        )
        body_msg = (
            f"Packit failed on creating pull-requests in dist-git:\n\n"
            f"| dist-git branch | error |\n"
            f"| --------------- | ----- |\n"
            f"{branch_errors}\n\n"
            f"{msg_retrigger}\n"
        )

        PackageConfigGetter.create_issue_if_needed(
            project=self.project,
            title=f"Propose downstream failed for release {self.data.tag_name}",
            message=body_msg,
            comment_to_existing=body_msg,
        )

    def _get_or_create_propose_downstream_run(self) -> ProposeDownstreamModel:
        if self._propose_downstream_run_id is not None:
            return ProposeDownstreamModel.get_by_id(self._propose_downstream_run_id)

        propose_downstream_model, _ = ProposeDownstreamModel.create_with_new_run(
            status=ProposeDownstreamStatus.running,
            trigger_model=self.data.db_trigger,
        )
        return propose_downstream_model

    def run(self) -> TaskResults:
        """
        Sync the upstream release to dist-git as a pull request.
        """
        # TODO use local project and api from BaseJobHelper when LocalProject refactored
        self.local_project = LocalProject(
            git_project=self.project,
            working_dir=self.service_config.command_handler_work_dir,
            cache=RepositoryCache(
                cache_path=self.service_config.repository_cache,
                add_new=self.service_config.add_repositories_to_repository_cache,
            )
            if self.service_config.repository_cache
            else None,
        )

        self.api = PackitAPI(
            self.service_config,
            self.job_config,
            self.local_project,
        )

        errors = {}
        propose_downstream_model = self._get_or_create_propose_downstream_run()

        try:
            branches = list(self.propose_downstream_helper.branches)
            logger.debug(f"Branches to run propose downstream: {branches}")
            self._create_new_propose_for_each_branch(propose_downstream_model, branches)

            for branch, model in zip(
                branches, propose_downstream_model.propose_downstream_targets
            ):
                # skip submitting a branch if we already did that (even if it failed)
                if model.status not in [
                    ProposeDownstreamTargetStatus.running,
                    ProposeDownstreamTargetStatus.retry,
                    ProposeDownstreamTargetStatus.queued,
                ]:
                    continue
                logger.debug(f"Running propose downstream for {branch}")
                model.set_status(status=ProposeDownstreamTargetStatus.running)
                url = get_propose_downstream_info_url(model.id)
                buffer, handler = gather_packit_logs_to_buffer(
                    logging_level=logging.DEBUG
                )

                try:
                    model.set_start_time(start_time=datetime.utcnow())
                    self.propose_downstream_helper.report_status_to_branch(
                        branch=branch,
                        description="Starting propose downstream...",
                        state=BaseCommitStatus.running,
                        url=url,
                    )
                    downstream_pr = self.sync_branch(
                        branch=branch, model=propose_downstream_model
                    )
                    logger.debug("Downstream PR created successfully.")
                    model.set_downstream_pr_url(downstream_pr_url=downstream_pr.url)
                    model.set_status(status=ProposeDownstreamTargetStatus.submitted)
                    self.propose_downstream_helper.report_status_to_branch(
                        branch=branch,
                        description="Propose downstream finished successfully.",
                        state=BaseCommitStatus.success,
                        url=url,
                    )
                except AbortProposeDownstream:
                    logger.debug(
                        "Propose downstream is being retried because "
                        "we were not able yet to download the archive. "
                    )
                    model.set_status(status=ProposeDownstreamTargetStatus.retry)
                    self.propose_downstream_helper.report_status_to_branch(
                        branch=branch,
                        description="Propose downstream is being retried because "
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
                    logger.debug(f"Propose downstream failed: {ex}")
                    # eat the exception and continue with the execution
                    model.set_status(status=ProposeDownstreamTargetStatus.error)
                    self.propose_downstream_helper.report_status_to_branch(
                        branch=branch,
                        description=f"Propose downstream failed: {ex}",
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
            shutil.rmtree(self.api.dg.local_project.working_dir)

        if errors:
            self._report_errors_for_each_branch(errors)
            propose_downstream_model.set_status(status=ProposeDownstreamStatus.error)
            return TaskResults(
                success=False,
                details={"msg": "Propose downstream failed.", "errors": errors},
            )

        propose_downstream_model.set_status(status=ProposeDownstreamStatus.finished)
        return TaskResults(success=True, details={})


@configured_as(job_type=JobType.koji_build)
@reacts_to(event=PushPagureEvent)
class DownstreamKojiBuildHandler(JobHandler):
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
    ):
        super().__init__(
            package_config=package_config,
            job_config=job_config,
            event=event,
        )
        self.dg_branch = event.get("git_ref")

    def pre_check(self) -> bool:
        if self.data.event_type in (PushPagureEvent.__name__,):
            if self.data.git_ref not in (
                configured_branches := get_branches(
                    *self.job_config.metadata.dist_git_branches,
                    default="main",
                    with_aliases=True,
                )
            ):
                logger.info(
                    f"Skipping build on '{self.data.git_ref}'. "
                    f"Koji build configured only for '{configured_branches}'."
                )
                return False
        return True

    def run(self) -> TaskResults:
        self.local_project = LocalProject(
            git_project=self.project,
            working_dir=self.service_config.command_handler_work_dir,
            cache=RepositoryCache(
                cache_path=self.service_config.repository_cache,
                add_new=self.service_config.add_repositories_to_repository_cache,
            )
            if self.service_config.repository_cache
            else None,
        )
        packit_api = PackitAPI(
            self.service_config,
            self.job_config,
            downstream_local_project=self.local_project,
        )
        try:
            packit_api.build(
                dist_git_branch=self.dg_branch,
                scratch=self.job_config.metadata.scratch,
                nowait=True,
                from_upstream=False,
            )
        except PackitException as ex:
            if not self.job_config.issue_repository:
                logger.debug(
                    "No issue repository configured. "
                    "User will not be notified about the failure."
                )
                raise ex

            logger.debug(
                f"Issue repository configured. We will create "
                f"a new issue in {self.job_config.issue_repository}"
                "or update the existing one."
            )

            issue_repo = self.service_config.get_project(
                url=self.job_config.issue_repository
            )
            body = (
                f"Koji build on `{self.dg_branch}` branch failed:\n"
                "```\n"
                f"{ex}\n"
                "```"
            )
            PackageConfigGetter.create_issue_if_needed(
                project=issue_repo,
                title="Fedora Koji build failed to be triggered",
                message=body
                + f"\n\n*Get in [touch with us]({CONTACTS_URL}) if you need some help.*",
                comment_to_existing=body,
            )
            raise ex
        return TaskResults(success=True, details={})
