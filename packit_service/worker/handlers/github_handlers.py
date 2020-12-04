# MIT License
#
# Copyright (c) 2018-2019 Red Hat, Inc.

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""
This file defines classes for job handlers specific for Github hooks
"""
import logging
from typing import Optional, Callable, Set

from celery.app.task import Task

from ogr.abstract import GitProject, CommitStatus
from packit.api import PackitAPI
from packit.config import (
    JobConfig,
    JobType,
)
from packit.config.aliases import get_branches
from packit.config.package_config import PackageConfig
from packit.exceptions import PackitException
from packit.local_project import LocalProject

from packit_service import sentry_integration
from packit_service.constants import (
    PERMISSIONS_ERROR_WRITE_OR_ADMIN,
    FAQ_URL_HOW_TO_RETRIGGER,
    FILE_DOWNLOAD_FAILURE,
    MSG_RETRIGGER,
    RETRY_LIMIT,
)
from packit_service.models import (
    InstallationModel,
    AbstractTriggerDbType,
    CoprBuildModel,
)
from packit_service.service.events import (
    TheJobTriggerType,
    ReleaseEvent,
    PullRequestGithubEvent,
    PullRequestPagureEvent,
    PushGitHubEvent,
    PushPagureEvent,
    MergeRequestGitlabEvent,
    PushGitlabEvent,
    PullRequestCommentGithubEvent,
    MergeRequestCommentGitlabEvent,
    InstallationEvent,
    EventData,
)
from packit_service.worker.build import CoprBuildJobHelper
from packit_service.worker.build.koji_build import KojiBuildJobHelper
from packit_service.worker.handlers import (
    CommentActionHandler,
    JobHandler,
)
from packit_service.worker.handlers.abstract import required_by, use_for, TaskName
from packit_service.worker.handlers.comment_action_handler import (
    add_to_comment_action_mapping,
    add_to_comment_action_mapping_with_name,
    CommentAction,
)
from packit_service.worker.result import TaskResults
from packit_service.worker.testing_farm import TestingFarmJobHelper
from packit_service.worker.whitelist import Whitelist

logger = logging.getLogger(__name__)


class GithubAppInstallationHandler(JobHandler):
    type = JobType.add_to_whitelist
    triggers = [TheJobTriggerType.installation]
    task_name = TaskName.installation

    # https://developer.github.com/v3/activity/events/types/#events-api-payload-28

    def __init__(
        self,
        package_config: PackageConfig,
        job_config: JobConfig,
        data: EventData,
        installation_event: InstallationEvent,
    ):
        super().__init__(
            package_config=package_config,
            job_config=job_config,
            data=data,
        )
        self.installation_event = installation_event
        self.account_type = installation_event.account_type
        self.account_login = installation_event.account_login
        self.sender_login = installation_event.sender_login
        self._project = self.service_config.get_project(
            url="https://github.com/packit/notifications"
        )

    def run(self) -> TaskResults:
        """
        Discover information about organization/user which wants to install packit on his repository
        Try to whitelist automatically if mapping from github username to FAS account can prove that
        user is a packager.
        :return: TaskResults
        """
        InstallationModel.create(event=self.installation_event)
        # try to add user to whitelist
        whitelist = Whitelist(
            fas_user=self.service_config.fas_user,
            fas_password=self.service_config.fas_password,
        )
        if not whitelist.add_account(self.account_login, self.sender_login):
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
            msg = f"{self.account_type} {self.account_login} whitelisted!"

        logger.info(msg)
        return TaskResults(success=True, details={"msg": msg})


@use_for(job_type=JobType.propose_downstream)
class ProposeDownstreamHandler(JobHandler):
    type = JobType.propose_downstream
    triggers = [TheJobTriggerType.release]
    task_name = TaskName.propose_downstream

    def __init__(
        self,
        package_config: PackageConfig,
        job_config: JobConfig,
        data: EventData,
        task: Task,
    ):
        super().__init__(
            package_config=package_config,
            job_config=job_config,
            data=data,
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

        self.api = PackitAPI(self.service_config, self.job_config, self.local_project)

        errors = {}
        for branch in get_branches(
            *self.job_config.metadata.dist_git_branches, default="master"
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
                    if retries < RETRY_LIMIT:
                        logger.info(f"Retrying for the {retries + 1}. time...")
                        self.task.retry(exc=ex, countdown=15 * 2 ** retries)
                sentry_integration.send_to_sentry(ex)
                errors[branch] = str(ex)

        if errors:
            branch_errors = ""
            for branch, err in sorted(
                errors.items(), key=lambda branch_error: branch_error[0]
            ):
                err_without_new_lines = err.replace("\n", " ")
                branch_errors += f"| `{branch}` | `{err_without_new_lines}` |\n"

            body_msg = (
                f"Packit failed on creating pull-requests in dist-git:\n\n"
                f"| dist-git branch | error |\n"
                f"| --------------- | ----- |\n"
                f"{branch_errors}\n\n"
                f"{MSG_RETRIGGER.format(job='update', command='propose-update', place='issue')}\n"
            )

            self.project.create_issue(
                title=f"[packit] Propose update failed for release {self.data.tag_name}",
                body=body_msg,
            )

            return TaskResults(
                success=False,
                details={"msg": "Propose update failed.", "errors": errors},
            )

        return TaskResults(success=True, details={})


class AbstractCoprBuildHandler(JobHandler):
    type = JobType.copr_build

    def __init__(
        self,
        package_config: PackageConfig,
        job_config: JobConfig,
        data: EventData,
    ):
        super().__init__(
            package_config=package_config,
            job_config=job_config,
            data=data,
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
                db_trigger=self.db_trigger,
                job_config=self.job_config,
            )
        return self._copr_build_helper

    def run(self) -> TaskResults:
        return self.copr_build_helper.run_copr_build()

    def pre_check(self) -> bool:
        is_copr_build: Callable[[JobConfig], bool] = (
            lambda job: job.type == JobType.copr_build
            and job.trigger == self.job_config.trigger
        )

        if self.job_config.type == JobType.tests and any(
            filter(is_copr_build, self.package_config.jobs)
        ):
            logger.info(
                "Skipping build for testing. The COPR build is defined "
                "in the config with the same trigger."
            )
            return False
        return True


@use_for(job_type=JobType.copr_build)
@use_for(job_type=JobType.build)
@required_by(job_type=JobType.tests)
class ReleaseCoprBuildHandler(AbstractCoprBuildHandler):
    triggers = [
        TheJobTriggerType.release,
    ]
    task_name = TaskName.release_copr_build

    def pre_check(self) -> bool:
        return (
            self.data.event_type == ReleaseEvent.__name__
            and self.data.trigger == TheJobTriggerType.release
            and super().pre_check()
        )


@use_for(job_type=JobType.copr_build)
@use_for(job_type=JobType.build)
@required_by(job_type=JobType.tests)
class PullRequestCoprBuildHandler(AbstractCoprBuildHandler):
    triggers = [
        TheJobTriggerType.pull_request,
    ]
    task_name = TaskName.pr_copr_build

    def run(self) -> TaskResults:
        if self.data.event_type in (
            PullRequestGithubEvent.__name__,
            MergeRequestGitlabEvent.__name__,
        ):
            user_can_merge_pr = self.project.can_merge_pr(self.data.user_login)
            if not (
                user_can_merge_pr or self.data.user_login in self.service_config.admins
            ):
                self.copr_build_helper.report_status_to_all(
                    description=PERMISSIONS_ERROR_WRITE_OR_ADMIN,
                    state=CommitStatus.failure,
                    url=FAQ_URL_HOW_TO_RETRIGGER,
                )
                return TaskResults(
                    success=True, details={"msg": PERMISSIONS_ERROR_WRITE_OR_ADMIN}
                )
        return super().run()

    def pre_check(self) -> bool:
        return (
            self.data.event_type
            in (
                PullRequestGithubEvent.__name__,
                PullRequestPagureEvent.__name__,
                MergeRequestGitlabEvent.__name__,
            )
            and self.data.trigger == TheJobTriggerType.pull_request
            and super().pre_check()
        )


@use_for(job_type=JobType.copr_build)
@use_for(job_type=JobType.build)
@required_by(job_type=JobType.tests)
class PushCoprBuildHandler(AbstractCoprBuildHandler):
    triggers = [
        TheJobTriggerType.push,
        TheJobTriggerType.commit,
    ]
    task_name = TaskName.push_copr_build

    def pre_check(self) -> bool:
        valid = (
            self.data.event_type
            in (
                PushGitHubEvent.__name__,
                PushPagureEvent.__name__,
                PushGitlabEvent.__name__,
            )
            and self.data.trigger == TheJobTriggerType.push
            and super().pre_check()
        )
        if not valid:
            return False

        configured_branch = self.copr_build_helper.job_build_branch
        if self.data.git_ref != configured_branch:
            logger.info(
                f"Skipping build on '{self.data.git_ref}'. "
                f"Push configured only for '{configured_branch}'."
            )
            return False
        return True


class AbstractGithubKojiBuildHandler(JobHandler):
    type = JobType.production_build

    def __init__(
        self,
        package_config: PackageConfig,
        job_config: JobConfig,
        data: EventData,
    ):
        super().__init__(
            package_config=package_config,
            job_config=job_config,
            data=data,
        )

        if not (
            self.data.event_type
            in (
                PullRequestGithubEvent.__name__,
                PushGitHubEvent.__name__,
                ReleaseEvent.__name__,
            )
        ):
            raise PackitException(
                "Unknown event, only "
                "PullRequestEvent, ReleaseEvent, and PushGitHubEvent "
                "are accepted."
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
                db_trigger=self.db_trigger,
                job_config=self.job_config,
            )
        return self._koji_build_helper

    def run(self) -> TaskResults:
        return self.koji_build_helper.run_koji_build()

    def pre_check(self) -> bool:
        is_copr_build: Callable[[JobConfig], bool] = (
            lambda job: job.type == JobType.copr_build
        )

        if self.job_config.type == JobType.tests and any(
            filter(is_copr_build, self.package_config.jobs)
        ):
            logger.info(
                "Skipping build for testing. The COPR build is defined in the config."
            )
            return False
        return True


@use_for(job_type=JobType.production_build)
class ReleaseGithubKojiBuildHandler(AbstractGithubKojiBuildHandler):
    triggers = [
        TheJobTriggerType.release,
    ]
    task_name = TaskName.release_koji_build

    def pre_check(self) -> bool:
        return (
            super().pre_check()
            and self.data.event_type == ReleaseEvent.__name__
            and self.data.trigger == TheJobTriggerType.release
        )


@use_for(job_type=JobType.production_build)
class PullRequestGithubKojiBuildHandler(AbstractGithubKojiBuildHandler):
    triggers = [
        TheJobTriggerType.pull_request,
    ]
    task_name = TaskName.pr_koji_build

    def run(self) -> TaskResults:
        if self.data.event_type == PullRequestGithubEvent.__name__:
            user_can_merge_pr = self.project.can_merge_pr(self.data.user_login)
            if not (
                user_can_merge_pr or self.data.user_login in self.service_config.admins
            ):
                self.koji_build_helper.report_status_to_all(
                    description=PERMISSIONS_ERROR_WRITE_OR_ADMIN,
                    state=CommitStatus.failure,
                )
                return TaskResults(
                    success=True, details={"msg": PERMISSIONS_ERROR_WRITE_OR_ADMIN}
                )
        return super().run()

    def pre_check(self) -> bool:
        return (
            super().pre_check()
            and self.data.event_type == PullRequestGithubEvent.__name__
            and self.data.trigger == TheJobTriggerType.pull_request
        )


@use_for(job_type=JobType.production_build)
class PushGithubKojiBuildHandler(AbstractGithubKojiBuildHandler):
    triggers = [
        TheJobTriggerType.push,
        TheJobTriggerType.commit,
    ]
    task_name = TaskName.push_koji_build

    def pre_check(self) -> bool:
        valid = (
            super().pre_check()
            and self.data.event_type == PushGitHubEvent.__name__
            and self.data.trigger == TheJobTriggerType.push
        )
        if not valid:
            return False

        configured_branch = self.koji_build_helper.job_build_branch
        if self.data.git_ref != configured_branch:
            logger.info(
                f"Skipping build on '{self.data.git_ref}'. "
                f"Push configured only for '{configured_branch}'."
            )
            return False
        return True


class GithubTestingFarmHandler(JobHandler):
    """
    This class intentionally does not have a @add_to_mapping decorator as its
    trigger is finished copr build.
    """

    triggers = [
        TheJobTriggerType.pull_request,
        TheJobTriggerType.release,
        TheJobTriggerType.commit,
    ]

    def __init__(
        self,
        package_config: PackageConfig,
        job_config: JobConfig,
        data: EventData,
        chroot: str,
        build_id: int,
    ):
        super().__init__(
            package_config=package_config,
            job_config=job_config,
            data=data,
        )
        self.chroot = chroot
        self.build_id = build_id
        self._db_trigger: Optional[AbstractTriggerDbType] = None

    @property
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        if not self._db_trigger:
            build = CoprBuildModel.get_by_id(self.build_id)
            self._db_trigger = build.job_trigger.get_trigger_object()
        return self._db_trigger

    def run(self) -> TaskResults:
        # TODO: once we turn hanadlers into respective celery tasks, we should iterate
        #       here over *all* matching jobs and do them all, not just the first one
        testing_farm_helper = TestingFarmJobHelper(
            service_config=self.service_config,
            package_config=self.package_config,
            project=self.project,
            metadata=self.data,
            db_trigger=self.db_trigger,
            job_config=self.job_config,
        )
        logger.info("Running testing farm.")
        return testing_farm_helper.run_testing_farm(chroot=self.chroot)


@add_to_comment_action_mapping
@add_to_comment_action_mapping_with_name(name=CommentAction.build)
@use_for(JobType.build)
@use_for(JobType.copr_build)
@required_by(JobType.tests)
class GitHubPullRequestCommentCoprBuildHandler(CommentActionHandler):
    """ Handler for PR comment `/packit copr-build` """

    type = CommentAction.copr_build
    triggers = [TheJobTriggerType.pr_comment]
    task_name = TaskName.pr_comment_copr_build

    def run(self) -> TaskResults:
        user_can_merge_pr = self.project.can_merge_pr(self.data.user_login)
        if not (
            user_can_merge_pr or self.data.user_login in self.service_config.admins
        ):
            self.project.pr_comment(
                self.db_trigger.pr_id, PERMISSIONS_ERROR_WRITE_OR_ADMIN
            )
            return TaskResults(
                success=True, details={"msg": PERMISSIONS_ERROR_WRITE_OR_ADMIN}
            )

        cbh = CoprBuildJobHelper(
            service_config=self.service_config,
            package_config=self.package_config,
            project=self.project,
            metadata=self.data,
            db_trigger=self.db_trigger,
            job_config=self.job_config,
        )
        handler_results = cbh.run_copr_build()

        return handler_results

    def pre_check(self) -> bool:
        return (
            self.data.event_type
            in (
                (
                    PullRequestCommentGithubEvent.__name__,
                    MergeRequestCommentGitlabEvent.__name__,
                )
            )
            and self.data.trigger == TheJobTriggerType.pr_comment
            and super().pre_check()
        )


@add_to_comment_action_mapping
@use_for(JobType.propose_downstream)
class GitHubIssueCommentProposeUpdateHandler(CommentActionHandler):
    """ Handler for issue comment `/packit propose-update` """

    type = CommentAction.propose_update
    triggers = [TheJobTriggerType.issue_comment]
    task_name = TaskName.propose_update_comment

    @property
    def dist_git_branches_to_sync(self) -> Set[str]:
        """
        Get the dist-git branches to sync to with the aliases expansion.

        :return: list of dist-git branches
        """
        configured_branches = set()
        configured_branches.update(self.job_config.metadata.dist_git_branches)

        if configured_branches:
            return get_branches(*configured_branches)
        return set()

    def run(self) -> TaskResults:
        local_project = LocalProject(
            git_project=self.project,
            working_dir=self.service_config.command_handler_work_dir,
        )

        api = PackitAPI(
            config=self.service_config,
            # job_config and package_config are the same for PackitAPI
            # and we want to use job_config since people can override things in there
            package_config=self.job_config,
            upstream_local_project=local_project,
        )

        user_can_merge_pr = self.project.can_merge_pr(self.data.user_login)
        if not (
            user_can_merge_pr or self.data.user_login in self.service_config.admins
        ):
            self.project.issue_comment(
                self.db_trigger.issue_id, PERMISSIONS_ERROR_WRITE_OR_ADMIN
            )
            return TaskResults(
                success=True, details={"msg": PERMISSIONS_ERROR_WRITE_OR_ADMIN}
            )

        if not self.data.tag_name:
            msg = (
                "There was an error while proposing a new update for the Fedora package: "
                "no upstream release found."
            )
            self.project.issue_comment(self.db_trigger.issue_id, msg)
            return TaskResults(success=False, details={"msg": "Propose update failed"})

        sync_failed = False
        for branch in self.dist_git_branches_to_sync:
            msg = (
                f"for the Fedora package `{self.job_config.downstream_package_name}`"
                f"with the tag `{self.data.tag_name}` in the `{branch}` branch.\n"
            )
            try:
                new_pr = api.sync_release(
                    dist_git_branch=branch, tag=self.data.tag_name, create_pr=True
                )
                msg = f"Packit-as-a-Service proposed [a new update]({new_pr.url}) {msg}"
                self.project.issue_comment(self.db_trigger.issue_id, msg)
            except PackitException as ex:
                msg = f"There was an error while proposing a new update {msg} Traceback is: `{ex}`"
                self.project.issue_comment(self.db_trigger.issue_id, msg)
                logger.error(f"Error while running a build: {ex}")
                sync_failed = True
        if sync_failed:
            return TaskResults(success=False, details={"msg": "Propose update failed"})

        # Close issue if propose-update was successful in all branches
        self.project.issue_close(self.db_trigger.issue_id)

        return TaskResults(success=True, details={})


@add_to_comment_action_mapping
@use_for(JobType.tests)
class GitHubPullRequestCommentTestingFarmHandler(CommentActionHandler):
    """ Issue handler for comment `/packit test` """

    type = CommentAction.test
    triggers = [TheJobTriggerType.pr_comment]
    task_name = TaskName.testing_farm_comment

    def run(self) -> TaskResults:
        testing_farm_helper = TestingFarmJobHelper(
            service_config=self.service_config,
            package_config=self.package_config,
            project=self.project,
            metadata=self.data,
            db_trigger=self.db_trigger,
            job_config=self.job_config,
        )
        user_can_merge_pr = self.project.can_merge_pr(self.data.user_login)
        if not (
            user_can_merge_pr or self.data.user_login in self.service_config.admins
        ):
            self.project.pr_comment(
                self.db_trigger.pr_id, PERMISSIONS_ERROR_WRITE_OR_ADMIN
            )
            return TaskResults(
                success=True, details={"msg": PERMISSIONS_ERROR_WRITE_OR_ADMIN}
            )

        handler_results = TaskResults(success=True, details={})

        logger.debug(f"Test job config: {testing_farm_helper.job_tests}")
        if testing_farm_helper.job_tests:
            testing_farm_helper.run_testing_farm_on_all()
        else:
            logger.debug("Testing farm not in the job config.")

        return handler_results
