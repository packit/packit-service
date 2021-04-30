# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
This file defines classes for job handlers specific for Github hooks
TODO: The build and test handlers are independent and should be moved away.
"""
import logging
from os import getenv
from typing import Optional

from celery.app.task import Task
from ogr.abstract import CommitStatus, GitProject, PullRequest
from packit.api import PackitAPI
from packit.config import (
    JobConfig,
    JobType,
)
from packit.config.aliases import get_branches
from packit.config.package_config import PackageConfig
from packit.local_project import LocalProject

from packit_service import sentry_integration
from packit_service.constants import (
    DEFAULT_RETRY_LIMIT,
    FILE_DOWNLOAD_FAILURE,
    KOJI_PRODUCTION_BUILDS_ISSUE,
    MSG_RETRIGGER,
    PERMISSIONS_ERROR_WRITE_OR_ADMIN,
)
from packit_service.models import (
    InstallationModel,
    BugzillaModel,
)
from packit_service.service.events import (
    InstallationEvent,
    IssueCommentEvent,
    IssueCommentGitlabEvent,
    MergeRequestCommentGitlabEvent,
    MergeRequestGitlabEvent,
    PullRequestCommentGithubEvent,
    PullRequestCommentPagureEvent,
    PullRequestGithubEvent,
    PushGitHubEvent,
    PushGitlabEvent,
    PushPagureEvent,
    ReleaseEvent,
    PullRequestLabelPagureEvent,
    PullRequestLabelAction,
)
from packit_service.worker.allowlist import Allowlist
from packit_service.worker.build import CoprBuildJobHelper
from packit_service.worker.build.koji_build import KojiBuildJobHelper
from packit_service.worker.handlers import (
    JobHandler,
)
from packit_service.worker.handlers.abstract import (
    TaskName,
    configured_as,
    reacts_to,
    required_for,
    run_for_comment,
)
from packit_service.worker.psbugzilla import Bugzilla
from packit_service.worker.reporting import StatusReporter
from packit_service.worker.result import TaskResults

logger = logging.getLogger(__name__)


@reacts_to(event=InstallationEvent)
class GithubAppInstallationHandler(JobHandler):
    task_name = TaskName.installation

    # https://developer.github.com/v3/activity/events/types/#events-api-payload-28

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

        self.installation_event = InstallationEvent.from_event_dict(event)
        self.account_type = self.installation_event.account_type
        self.account_login = self.installation_event.account_login
        self.sender_login = self.installation_event.sender_login
        self._project = self.service_config.get_project(
            url="https://github.com/packit/notifications"
        )

    def run(self) -> TaskResults:
        """
        Discover information about organization/user which wants to install packit on his repository
        Try to allowlist automatically if mapping from github username to FAS account can prove that
        user is a packager.
        :return: TaskResults
        """
        InstallationModel.create(event=self.installation_event)
        # try to add user to allowlist
        allowlist = Allowlist(
            fas_user=self.service_config.fas_user,
            fas_password=self.service_config.fas_password,
        )
        if not allowlist.add_namespace(
            f"github.com/{self.account_login}", self.sender_login
        ):
            # Create an issue in our repository, so we are notified when someone install the app
            self.project.create_issue(
                title=f"{self.account_type} {self.account_login} needs to be approved.",
                body=(
                    f"Hi @{self.sender_login}, we need to approve you in "
                    "order to start using Packit-as-a-Service. Someone from our team will "
                    "get back to you shortly.\n\n"
                    "For more info, please check out the documentation: "
                    "http://packit.dev/packit-as-a-service/"
                ),
            )
            msg = f"{self.account_type} {self.account_login} needs to be approved manually!"
        else:
            msg = f"{self.account_type} {self.account_login} allowlisted!"

        logger.info(msg)
        return TaskResults(success=True, details={"msg": msg})


@configured_as(job_type=JobType.propose_downstream)
@run_for_comment(command="propose-downstream")
@run_for_comment(command="propose-update")  # deprecated
@reacts_to(event=ReleaseEvent)
@reacts_to(event=IssueCommentEvent)
@reacts_to(event=IssueCommentGitlabEvent)
class ProposeDownstreamHandler(JobHandler):
    task_name = TaskName.propose_downstream

    def __init__(
        self,
        package_config: PackageConfig,
        job_config: JobConfig,
        event: dict,
        task: Task = None,
    ):
        super().__init__(
            package_config=package_config,
            job_config=job_config,
            event=event,
        )
        self.task = task

    def run(self) -> TaskResults:
        """
        Sync the upstream release to dist-git as a pull request.
        """

        self.local_project = LocalProject(
            git_project=self.project,
            working_dir=self.service_config.command_handler_work_dir,
        )

        self.api = PackitAPI(
            self.service_config,
            self.job_config,
            self.local_project,
            stage=self.service_config.use_stage(),
        )

        errors = {}
        default_dg_branch = self.api.dg.local_project.git_project.default_branch
        for branch in get_branches(
            *self.job_config.metadata.dist_git_branches, default=default_dg_branch
        ):
            try:
                self.api.sync_release(dist_git_branch=branch, tag=self.data.tag_name)
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
                        delay = 60 * 2 ** retries
                        logger.info(
                            f"Will retry for the {retries + 1}. time in {delay}s."
                        )
                        # throw=False so that exception is not raised and task
                        # is not retried also automatically
                        self.task.retry(exc=ex, countdown=delay, throw=False)
                        return TaskResults(
                            success=False,
                            details={
                                "msg": "Not able to download archive. Task will be retried."
                            },
                        )
                sentry_integration.send_to_sentry(ex)
                errors[branch] = str(ex)

        if errors:
            branch_errors = ""
            for branch, err in sorted(
                errors.items(), key=lambda branch_error: branch_error[0]
            ):
                err_without_new_lines = err.replace("\n", " ")
                branch_errors += f"| `{branch}` | `{err_without_new_lines}` |\n"

            msg_retrigger = MSG_RETRIGGER.format(
                job="update", command="propose-downstream", place="issue"
            )
            body_msg = (
                f"Packit failed on creating pull-requests in dist-git:\n\n"
                f"| dist-git branch | error |\n"
                f"| --------------- | ----- |\n"
                f"{branch_errors}\n\n"
                f"{msg_retrigger}\n"
            )

            self.project.create_issue(
                title=f"[packit] Propose downstream failed for release {self.data.tag_name}",
                body=body_msg,
            )

            return TaskResults(
                success=False,
                details={"msg": "Propose downstream failed.", "errors": errors},
            )

        return TaskResults(success=True, details={})


@configured_as(job_type=JobType.copr_build)
@configured_as(job_type=JobType.build)
@required_for(job_type=JobType.tests)
@run_for_comment(command="build")
@run_for_comment(command="copr-build")
@reacts_to(ReleaseEvent)
@reacts_to(PullRequestGithubEvent)
@reacts_to(PushGitHubEvent)
@reacts_to(PushGitlabEvent)
@reacts_to(MergeRequestGitlabEvent)
@reacts_to(PullRequestCommentGithubEvent)
@reacts_to(MergeRequestCommentGitlabEvent)
@reacts_to(PullRequestCommentPagureEvent)
class CoprBuildHandler(JobHandler):
    task_name = TaskName.copr_build

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

        self._copr_build_helper: Optional[CoprBuildJobHelper] = None

    @property
    def copr_build_helper(self) -> CoprBuildJobHelper:
        if not self._copr_build_helper:
            self._copr_build_helper = CoprBuildJobHelper(
                service_config=self.service_config,
                package_config=self.package_config,
                project=self.project,
                metadata=self.data,
                db_trigger=self.data.db_trigger,
                job_config=self.job_config,
            )
        return self._copr_build_helper

    def run(self) -> TaskResults:
        return self.copr_build_helper.run_copr_build()

    def pre_check(self) -> bool:
        if self.data.event_type in (
            PushGitHubEvent.__name__,
            PushGitlabEvent.__name__,
            PushPagureEvent.__name__,
        ):
            configured_branch = self.copr_build_helper.job_build_branch
            if self.data.git_ref != configured_branch:
                logger.info(
                    f"Skipping build on '{self.data.git_ref}'. "
                    f"Push configured only for '{configured_branch}'."
                )
                return False

        if not (self.copr_build_helper.job_build or self.copr_build_helper.job_tests):
            logger.info("No copr_build or tests job defined.")
            # we can't report it to end-user at this stage
            return False

        return True


@configured_as(job_type=JobType.production_build)
@run_for_comment(command="production-build")
@reacts_to(ReleaseEvent)
@reacts_to(PullRequestGithubEvent)
@reacts_to(PushGitHubEvent)
@reacts_to(PushGitlabEvent)
@reacts_to(MergeRequestGitlabEvent)
@reacts_to(PullRequestCommentGithubEvent)
@reacts_to(MergeRequestCommentGitlabEvent)
@reacts_to(PullRequestCommentPagureEvent)
class KojiBuildHandler(JobHandler):
    task_name = TaskName.koji_build

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

    @property
    def koji_build_helper(self) -> KojiBuildJobHelper:
        if not self._koji_build_helper:
            self._koji_build_helper = KojiBuildJobHelper(
                service_config=self.service_config,
                package_config=self.package_config,
                project=self.project,
                metadata=self.data,
                db_trigger=self.data.db_trigger,
                job_config=self.job_config,
            )
        return self._koji_build_helper

    def run(self) -> TaskResults:
        return self.koji_build_helper.run_koji_build()

    def pre_check(self) -> bool:
        if self.data.event_type in (
            PushGitHubEvent.__name__,
            PushGitlabEvent.__name__,
            PushPagureEvent.__name__,
        ):
            configured_branch = self.koji_build_helper.job_build_branch
            if self.data.git_ref != configured_branch:
                logger.info(
                    f"Skipping build on '{self.data.git_ref}'. "
                    f"Push configured only for '{configured_branch}'."
                )
                return False

        if self.data.event_type == PullRequestGithubEvent.__name__:
            user_can_merge_pr = self.project.can_merge_pr(self.data.user_login)
            if not (
                user_can_merge_pr or self.data.user_login in self.service_config.admins
            ):
                self.koji_build_helper.report_status_to_all(
                    description=PERMISSIONS_ERROR_WRITE_OR_ADMIN,
                    state=CommitStatus.failure,
                )
                return False

        if not self.koji_build_helper.is_scratch:
            msg = "Non-scratch builds not possible from upstream."
            self.koji_build_helper.report_status_to_all(
                description=msg,
                state=CommitStatus.error,
                url=KOJI_PRODUCTION_BUILDS_ISSUE,
            )
            return False

        return True


@reacts_to(event=PullRequestLabelPagureEvent)
class PagurePullRequestLabelHandler(JobHandler):
    task_name = TaskName.pagure_pr_label

    def __init__(
        self, package_config: PackageConfig, job_config: JobConfig, event: dict
    ):
        super().__init__(
            package_config=package_config,
            job_config=job_config,
            event=event,
        )
        self.labels = set(event.get("labels"))
        self.action = PullRequestLabelAction(event.get("action"))
        self.base_repo_owner = event.get("base_repo_owner")
        self.base_repo_name = event.get("base_repo_name")
        self.base_repo_namespace = event.get("base_repo_namespace")

        self.pr: PullRequest = self.project.get_pr(self.data.pr_id)
        # lazy properties
        self._bz_model: Optional[BugzillaModel] = None
        self._bugzilla: Optional[Bugzilla] = None
        self._status_reporter: Optional[StatusReporter] = None

    @property
    def bz_model(self) -> Optional[BugzillaModel]:
        if self._bz_model is None:
            self._bz_model = BugzillaModel.get_by_pr(
                pr_id=self.data.pr_id,
                namespace=self.base_repo_namespace,
                repo_name=self.base_repo_name,
                project_url=self.data.project_url,
            )
        return self._bz_model

    @property
    def bugzilla(self) -> Bugzilla:
        if self._bugzilla is None:
            self._bugzilla = Bugzilla(
                url=self.service_config.bugzilla_url,
                api_key=self.service_config.bugzilla_api_key,
            )
        return self._bugzilla

    @property
    def status_reporter(self) -> StatusReporter:
        if not self._status_reporter:
            self._status_reporter = StatusReporter(
                self.project, self.data.commit_sha, self.data.pr_id
            )
        return self._status_reporter

    def _create_bug(self):
        """Fill a Bugzilla bug and store in db."""
        bug_id, bug_url = self.bugzilla.create_bug(
            product="Red Hat Enterprise Linux 8",
            version="CentOS Stream",
            component=self.base_repo_name,
            summary=self.pr.title,
            description=f"Based on approved CentOS Stream pull-request: {self.pr.url}",
        )
        self._bz_model = BugzillaModel.get_or_create(
            pr_id=self.data.pr_id,
            namespace=self.base_repo_namespace,
            repo_name=self.base_repo_name,
            project_url=self.data.project_url,
            bug_id=bug_id,
            bug_url=bug_url,
        )

    def _attach_patch(self):
        """Attach a patch from the pull request to the bug."""
        if not (self.bz_model and self.bz_model.bug_id):
            raise RuntimeError(
                "PagurePullRequestLabelHandler._attach_patch(): bug_id not set"
            )

        self.bugzilla.add_patch(
            bzid=self.bz_model.bug_id,
            content=self.pr.patch,
            file_name=f"pr-{self.data.pr_id}.patch",
        )

    def _set_status(self):
        """
        Set commit status & pull-request flag with bug id as a name and a link to the created bug.
        """
        if not (self.bz_model and self.bz_model.bug_id and self.bz_model.bug_url):
            raise RuntimeError(
                "PagurePullRequestLabelHandler._set_status(): bug_id or bug_url not set"
            )

        self.status_reporter.set_status(
            state=CommitStatus.success,
            description="Bugzilla bug created.",
            check_name=f"RHBZ#{self.bz_model.bug_id}",
            url=self.bz_model.bug_url,
        )

    def run(self) -> TaskResults:
        logger.debug(
            f"Handling labels/tags {self.labels} {self.action.value} to Pagure PR "
            f"{self.base_repo_owner}/{self.base_repo_namespace}/"
            f"{self.base_repo_name}/{self.data.identifier}"
        )
        if self.labels.intersection(self.service_config.pr_accepted_labels):
            if not self.bz_model:
                self._create_bug()
            self._attach_patch()
            self._set_status()
        else:
            logger.debug(
                f"We accept only {self.service_config.pr_accepted_labels} labels/tags"
            )
        return TaskResults(success=True)
