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
from typing import Union, Any, Optional, List, Callable

from ogr.abstract import GitProject, CommitStatus
from packit.api import PackitAPI
from packit.config import (
    JobConfig,
    JobType,
    PackageConfig,
)
from packit.config.aliases import get_branches
from packit.exceptions import PackitException
from packit.local_project import LocalProject

from packit_service import sentry_integration
from packit_service.config import ServiceConfig, GithubPackageConfigGetter
from packit_service.constants import PERMISSIONS_ERROR_WRITE_OR_ADMIN
from packit_service.service.events import (
    PullRequestEvent,
    InstallationEvent,
    ReleaseEvent,
    PullRequestCommentEvent,
    IssueCommentEvent,
    CoprBuildEvent,
    PushGitHubEvent,
    TheJobTriggerType,
)
from packit_service.models import InstallationModel
from packit_service.worker.build import CoprBuildJobHelper
from packit_service.worker.build.koji_build import KojiBuildJobHelper
from packit_service.worker.handlers import (
    CommentActionHandler,
    JobHandler,
)
from packit_service.worker.handlers.abstract import (
    required_by,
    use_for,
)
from packit_service.worker.handlers.comment_action_handler import (
    add_to_comment_action_mapping,
    add_to_comment_action_mapping_with_name,
    CommentAction,
)
from packit_service.worker.result import HandlerResults
from packit_service.worker.testing_farm import TestingFarmJobHelper
from packit_service.worker.whitelist import Whitelist

logger = logging.getLogger(__name__)


class AbstractGithubJobHandler(JobHandler, GithubPackageConfigGetter):
    pass


@use_for(job_type=JobType.check_downstream)
class PullRequestGithubCheckDownstreamHandler(AbstractGithubJobHandler):
    type = JobType.check_downstream
    triggers = [TheJobTriggerType.pull_request]
    event: PullRequestEvent

    # https://developer.github.com/v3/activity/events/types/#events-api-payload-28

    def __init__(
        self, config: ServiceConfig, job_config: JobConfig, event: PullRequestEvent
    ):
        super().__init__(config=config, job_config=job_config, event=event)
        self.event = event
        self.project: GitProject = event.get_project()
        self.package_config: PackageConfig = self.get_package_config_from_repo(
            self.project, event.base_ref, event.pr_id
        )
        self.package_config.upstream_project_url = event.project_url

    def run(self) -> HandlerResults:
        self.local_project = LocalProject(
            git_project=self.project, working_dir=self.config.command_handler_work_dir
        )

        self.api = PackitAPI(self.config, self.package_config, self.local_project)

        self.api.sync_pr(
            pr_id=self.event.pr_id,
            dist_git_branch=self.job_config.metadata.dist_git_branch or "master"
            # TODO: figure out top upstream commit for source-git here
        )
        return HandlerResults(success=True, details={})


class GithubAppInstallationHandler(AbstractGithubJobHandler):
    type = JobType.add_to_whitelist
    triggers = [TheJobTriggerType.installation]
    event: InstallationEvent

    # https://developer.github.com/v3/activity/events/types/#events-api-payload-28

    def __init__(
        self,
        config: ServiceConfig,
        job_config: Optional[JobConfig],
        event: Union[InstallationEvent, Any],
    ):
        super().__init__(config=config, job_config=job_config, event=event)

        self.event = event
        self.project = self.config.get_project(
            url="https://github.com/packit-service/notifications"
        )

    def run(self) -> HandlerResults:
        """
        Discover information about organization/user which wants to install packit on his repository
        Try to whitelist automatically if mapping from github username to FAS account can prove that
        user is a packager.
        :return: HandlerResults
        """
        InstallationModel.create(event=self.event)
        # try to add user to whitelist
        whitelist = Whitelist(
            fas_user=self.config.fas_user, fas_password=self.config.fas_password,
        )
        account_login = self.event.account_login
        account_type = self.event.account_type
        if not whitelist.add_account(self.event):
            # Create an issue in our repository, so we are notified when someone install the app
            self.project.create_issue(
                title=f"{account_type} {account_login} needs to be approved.",
                body=(
                    f"Hi @{self.event.sender_login}, we need to approve you in "
                    "order to start using Packit-as-a-Service. Someone from our team will "
                    "get back to you shortly.\n\n"
                    "For more info, please check out the documentation: "
                    "http://packit.dev/packit-as-a-service/"
                ),
            )
            msg = f"{account_type} {account_login} needs to be approved manually!"
        else:
            msg = f"{account_type} {account_login} whitelisted!"

        logger.info(msg)
        return HandlerResults(success=True, details={"msg": msg})


@use_for(job_type=JobType.propose_downstream)
class ProposeDownstreamHandler(AbstractGithubJobHandler):
    type = JobType.propose_downstream
    triggers = [TheJobTriggerType.release]
    event: ReleaseEvent

    def __init__(
        self, config: ServiceConfig, job_config: JobConfig, event: ReleaseEvent
    ):
        super().__init__(config=config, job_config=job_config, event=event)

        self.project: GitProject = event.get_project()
        self.package_config: PackageConfig = self.get_package_config_from_repo(
            self.project, event.tag_name
        )
        self.package_config.upstream_project_url = event.project_url

    def run(self) -> HandlerResults:
        """
        Sync the upstream release to dist-git as a pull request.
        """

        self.local_project = LocalProject(
            git_project=self.project, working_dir=self.config.command_handler_work_dir
        )

        self.api = PackitAPI(self.config, self.package_config, self.local_project)

        errors = {}
        for branch in get_branches(
            self.job_config.metadata.dist_git_branch or "master"
        ):
            try:
                self.api.sync_release(
                    dist_git_branch=branch, version=self.event.tag_name
                )
            except Exception as ex:
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
                "You can re-trigger the update by adding `/packit propose-update`"
                " to the issue comment.\n"
            )

            self.project.create_issue(
                title=f"[packit] Propose update failed for release {self.event.tag_name}",
                body=body_msg,
            )

            return HandlerResults(
                success=False,
                details={"msg": "Propose update failed.", "errors": errors},
            )

        return HandlerResults(success=True, details={})


class AbstractGithubCoprBuildHandler(AbstractGithubJobHandler):
    type = JobType.copr_build
    event: Union[PullRequestEvent, ReleaseEvent, PushGitHubEvent]

    def __init__(
        self,
        config: ServiceConfig,
        job_config: JobConfig,
        event: Union[PullRequestEvent, ReleaseEvent, PushGitHubEvent],
    ):
        super().__init__(config=config, job_config=job_config, event=event)

        if not isinstance(event, (PullRequestEvent, PushGitHubEvent, ReleaseEvent)):
            raise PackitException(
                "Unknown event, only "
                "PullRequestEvent, ReleaseEvent, and PushGitHubEvent "
                "are accepted."
            )

        # lazy property
        self._copr_build_helper: Optional[CoprBuildJobHelper] = None
        self._package_config: Optional[PackageConfig] = None
        self._project: Optional[GitProject] = None

    @property
    def copr_build_helper(self) -> CoprBuildJobHelper:
        if not self._copr_build_helper:
            self._copr_build_helper = CoprBuildJobHelper(
                config=self.config,
                package_config=self.package_config,
                project=self.project,
                event=self.event,
                job=self.job_config,
            )
        return self._copr_build_helper

    @property
    def package_config(self) -> PackageConfig:
        if not self._package_config:
            self._package_config = self.get_package_config_from_repo(
                project=self.project,
                reference=self.event.commit_sha or str(self.event.git_ref),
                pr_id=self.event.pr_id
                if isinstance(self.event, (PullRequestEvent, PullRequestCommentEvent))
                else None,
            )
            self._package_config.upstream_project_url = self.event.project_url
        return self._package_config

    @property
    def project(self) -> GitProject:
        if not self._project:
            self._project = self.event.get_project()
        return self._project

    def run(self) -> HandlerResults:
        return self.copr_build_helper.run_copr_build()

    def pre_check(self) -> bool:
        is_copr_build: Callable[
            [JobConfig], bool
        ] = lambda job: job.type == JobType.copr_build

        if self.job_config.type == JobType.tests and any(
            filter(is_copr_build, self.package_config.jobs)
        ):
            logger.info(
                "Skipping build for testing. The COPR build is defined in the config."
            )
            return False
        return True


@use_for(job_type=JobType.copr_build)
@use_for(job_type=JobType.build)
@required_by(job_type=JobType.tests)
class ReleaseGithubCoprBuildHandler(AbstractGithubCoprBuildHandler):
    triggers = [
        TheJobTriggerType.release,
    ]

    event: ReleaseEvent

    def __init__(
        self, config: ServiceConfig, job_config: JobConfig, event: ReleaseEvent,
    ):
        super().__init__(config=config, job_config=job_config, event=event)
        self.base_ref = event.tag_name

    def pre_check(self) -> bool:
        return (
            super().pre_check()
            and isinstance(self.event, ReleaseEvent)
            and self.event.trigger == TheJobTriggerType.release
        )


@use_for(job_type=JobType.copr_build)
@use_for(job_type=JobType.build)
@required_by(job_type=JobType.tests)
class PullRequestGithubCoprBuildHandler(AbstractGithubCoprBuildHandler):
    triggers = [
        TheJobTriggerType.pull_request,
    ]
    event: PullRequestEvent

    def __init__(
        self, config: ServiceConfig, job_config: JobConfig, event: PullRequestEvent,
    ):
        super().__init__(config=config, job_config=job_config, event=event)

    def run(self) -> HandlerResults:
        if isinstance(self.event, PullRequestEvent):
            collaborators = self.project.who_can_merge_pr()
            if self.event.github_login not in collaborators | self.config.admins:
                self.copr_build_helper.report_status_to_all(
                    description=PERMISSIONS_ERROR_WRITE_OR_ADMIN,
                    state=CommitStatus.failure,
                )
                return HandlerResults(
                    success=True, details={"msg": PERMISSIONS_ERROR_WRITE_OR_ADMIN}
                )
        return super().run()

    def pre_check(self) -> bool:
        return (
            super().pre_check()
            and isinstance(self.event, PullRequestEvent)
            and self.event.trigger == TheJobTriggerType.pull_request
        )


@use_for(job_type=JobType.copr_build)
@use_for(job_type=JobType.build)
@required_by(job_type=JobType.tests)
class PushGithubCoprBuildHandler(AbstractGithubCoprBuildHandler):
    triggers = [
        TheJobTriggerType.push,
        TheJobTriggerType.commit,
    ]
    event: PushGitHubEvent

    def __init__(
        self, config: ServiceConfig, job_config: JobConfig, event: PushGitHubEvent,
    ):
        super().__init__(config=config, job_config=job_config, event=event)
        self.base_ref = event.commit_sha

    def pre_check(self) -> bool:
        valid = (
            super().pre_check()
            and isinstance(self.event, PushGitHubEvent)
            and self.event.trigger == TheJobTriggerType.push
        )
        if not valid:
            return False

        configured_branch = (
            self.copr_build_helper.job_build.metadata.dist_git_branch or "master"
        )
        if configured_branch != self.event.git_ref:
            logger.info(
                f"Skipping build on {self.event.git_ref}'. "
                f"Push configured only for ('{configured_branch}')."
            )
            return False
        return True


class AbstractGithubKojiBuildHandler(AbstractGithubJobHandler):
    type = JobType.production_build
    event: Union[PullRequestEvent, ReleaseEvent, PushGitHubEvent]

    def __init__(
        self,
        config: ServiceConfig,
        job_config: JobConfig,
        event: Union[PullRequestEvent, ReleaseEvent, PushGitHubEvent],
    ):
        super().__init__(config=config, job_config=job_config, event=event)

        if not isinstance(event, (PullRequestEvent, PushGitHubEvent, ReleaseEvent)):
            raise PackitException(
                "Unknown event, only "
                "PullRequestEvent, ReleaseEvent, and PushGitHubEvent "
                "are accepted."
            )

        # lazy property
        self._koji_build_helper: Optional[KojiBuildJobHelper] = None
        self._package_config: Optional[PackageConfig] = None
        self._project: Optional[GitProject] = None

    @property
    def koji_build_helper(self) -> KojiBuildJobHelper:
        if not self._koji_build_helper:
            self._koji_build_helper = KojiBuildJobHelper(
                config=self.config,
                package_config=self.package_config,
                project=self.project,
                event=self.event,
                job=self.job_config,
            )
        return self._koji_build_helper

    @property
    def package_config(self) -> PackageConfig:
        if not self._package_config:
            self._package_config = self.get_package_config_from_repo(
                project=self.project,
                reference=self.event.commit_sha or str(self.event.git_ref),
                pr_id=self.event.pr_id
                if isinstance(self.event, (PullRequestEvent, PullRequestCommentEvent))
                else None,
            )
            self._package_config.upstream_project_url = self.event.project_url
        return self._package_config

    @property
    def project(self) -> GitProject:
        if not self._project:
            self._project = self.event.get_project()
        return self._project

    def run(self) -> HandlerResults:
        return self.koji_build_helper.run_koji_build()

    def pre_check(self) -> bool:
        is_copr_build: Callable[
            [JobConfig], bool
        ] = lambda job: job.type == JobType.copr_build

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

    event: ReleaseEvent

    def __init__(
        self, config: ServiceConfig, job_config: JobConfig, event: ReleaseEvent,
    ):
        super().__init__(config=config, job_config=job_config, event=event)
        self.base_ref = event.tag_name

    def pre_check(self) -> bool:
        return (
            super().pre_check()
            and isinstance(self.event, ReleaseEvent)
            and self.event.trigger == TheJobTriggerType.release
        )


@use_for(job_type=JobType.production_build)
class PullRequestGithubKojiBuildHandler(AbstractGithubKojiBuildHandler):
    triggers = [
        TheJobTriggerType.pull_request,
    ]
    event: PullRequestEvent

    def __init__(
        self, config: ServiceConfig, job_config: JobConfig, event: PullRequestEvent,
    ):
        super().__init__(config=config, job_config=job_config, event=event)

    def run(self) -> HandlerResults:
        if isinstance(self.event, PullRequestEvent):
            collaborators = self.project.who_can_merge_pr()
            if self.event.github_login not in collaborators | self.config.admins:
                self.koji_build_helper.report_status_to_all(
                    description=PERMISSIONS_ERROR_WRITE_OR_ADMIN,
                    state=CommitStatus.failure,
                )
                return HandlerResults(
                    success=True, details={"msg": PERMISSIONS_ERROR_WRITE_OR_ADMIN}
                )
        return super().run()

    def pre_check(self) -> bool:
        return (
            super().pre_check()
            and isinstance(self.event, PullRequestEvent)
            and self.event.trigger == TheJobTriggerType.pull_request
        )


@use_for(job_type=JobType.production_build)
class PushGithubKojiBuildHandler(AbstractGithubKojiBuildHandler):
    triggers = [
        TheJobTriggerType.push,
        TheJobTriggerType.commit,
    ]
    event: PushGitHubEvent

    def __init__(
        self, config: ServiceConfig, job_config: JobConfig, event: PushGitHubEvent,
    ):
        super().__init__(config=config, job_config=job_config, event=event)
        self.base_ref = event.commit_sha

    def pre_check(self) -> bool:
        valid = (
            super().pre_check()
            and isinstance(self.event, PushGitHubEvent)
            and self.event.trigger == TheJobTriggerType.push
        )
        if not valid:
            return False

        configured_branch = (
            self.koji_build_helper.job_build.metadata.dist_git_branch or "master"
        )
        if configured_branch != self.event.git_ref:
            logger.info(
                f"Skipping build on {self.event.git_ref}'. "
                f"Push configured only for ('{configured_branch}')."
            )
            return False
        return True


class GithubTestingFarmHandler(AbstractGithubJobHandler):
    """
    This class intentionally does not have a @add_to_mapping decorator as its
    trigger is finished copr build.
    """

    triggers = [TheJobTriggerType.pull_request]
    event: Union[CoprBuildEvent, PullRequestCommentEvent]

    def __init__(
        self,
        config: ServiceConfig,
        job_config: JobConfig,
        event: Union[CoprBuildEvent, PullRequestCommentEvent],
        chroot: str,
    ):
        super().__init__(config=config, job_config=job_config, event=event)
        self.chroot = chroot
        self.project: GitProject = event.get_project()
        if isinstance(event, CoprBuildEvent):
            self.base_ref = event.git_ref
            pr_id = None
        elif isinstance(event, PullRequestCommentEvent):
            self.base_ref = event.commit_sha
            pr_id = event.pr_id
        else:
            raise PackitException(
                "Unknown event, only PREvent and CoprBuildEvent are accepted."
            )
        self.package_config: PackageConfig = self.get_package_config_from_repo(
            self.project, self.base_ref, pr_id
        )
        self.package_config.upstream_project_url = event.project_url
        self.testing_farm_helper = TestingFarmJobHelper(
            self.config, self.package_config, self.project, self.event
        )

    def run(self) -> HandlerResults:
        self.local_project = LocalProject(
            git_project=self.project, working_dir=self.config.command_handler_work_dir
        )

        logger.info("Running testing farm")
        return self.testing_farm_helper.run_testing_farm(chroot=self.chroot)


@add_to_comment_action_mapping
@add_to_comment_action_mapping_with_name(name=CommentAction.build)
class GitHubPullRequestCommentCoprBuildHandler(
    CommentActionHandler, GithubPackageConfigGetter
):
    """ Handler for PR comment `/packit copr-build` """

    type = CommentAction.copr_build
    triggers = [TheJobTriggerType.pr_comment]
    event: PullRequestCommentEvent

    def __init__(self, config: ServiceConfig, event: PullRequestCommentEvent):
        super().__init__(config=config, event=event)
        self.config = config
        self.event = event
        self.project: GitProject = event.get_project()
        # Get the latest pull request commit
        self.event.commit_sha = self.project.get_all_pr_commits(self.event.pr_id)[-1]
        self.event.base_ref = self.event.commit_sha
        self.package_config: PackageConfig = self.get_package_config_from_repo(
            self.project, self.event.commit_sha, self.event.pr_id
        )
        self.package_config.upstream_project_url = event.project_url

    def run(self) -> HandlerResults:
        collaborators = self.project.who_can_merge_pr()
        if self.event.github_login not in collaborators | self.config.admins:
            self.project.pr_comment(self.event.pr_id, PERMISSIONS_ERROR_WRITE_OR_ADMIN)
            return HandlerResults(
                success=True, details={"msg": PERMISSIONS_ERROR_WRITE_OR_ADMIN}
            )

        cbh = CoprBuildJobHelper(
            self.config, self.package_config, self.project, self.event
        )
        handler_results = cbh.run_copr_build()

        return handler_results


@add_to_comment_action_mapping
class GitHubIssueCommentProposeUpdateHandler(
    CommentActionHandler, GithubPackageConfigGetter
):
    """ Handler for issue comment `/packit propose-update` """

    type = CommentAction.propose_update
    triggers = [TheJobTriggerType.issue_comment]
    event: IssueCommentEvent

    def __init__(self, config: ServiceConfig, event: IssueCommentEvent):
        super().__init__(config=config, event=event)

        self.config = config
        self.event = event
        self.project = self.event.get_project()
        self.package_config: PackageConfig = self.get_package_config_from_repo(
            self.project, self.event.tag_name
        )
        if not self.package_config:
            raise ValueError(f"No config file found in {self.project.full_repo_name}")
        self.package_config.upstream_project_url = event.project_url

    @property
    def dist_git_branches_to_sync(self) -> List[str]:
        """
        Get the dist-git branches to sync to with the aliases expansion.

        :return: list of dist-git branches
        """
        configured_branches = [
            job.metadata.dist_git_branch
            for job in self.package_config.jobs
            if job.type == JobType.propose_downstream
        ]
        if configured_branches:
            return list(get_branches(*configured_branches))
        return []

    def run(self) -> HandlerResults:
        self.local_project = LocalProject(
            git_project=self.project, working_dir=self.config.command_handler_work_dir
        )

        self.api = PackitAPI(self.config, self.package_config, self.local_project)

        collaborators = self.project.who_can_merge_pr()
        if self.event.github_login not in collaborators | self.config.admins:
            self.project.issue_comment(
                self.event.issue_id, PERMISSIONS_ERROR_WRITE_OR_ADMIN
            )
            return HandlerResults(
                success=True, details={"msg": PERMISSIONS_ERROR_WRITE_OR_ADMIN}
            )

        if not self.event.tag_name:
            msg = (
                "There was an error while proposing a new update for the Fedora package: "
                "no upstream release found."
            )
            self.project.issue_comment(self.event.issue_id, msg)
            return HandlerResults(
                success=False, details={"msg": "Propose update failed"}
            )

        sync_failed = False
        for branch in self.dist_git_branches_to_sync:
            msg = (
                f"for the Fedora package `{self.package_config.downstream_package_name}`"
                f"with the tag `{self.event.tag_name}` in the `{branch}` branch.\n"
            )
            try:
                new_pr = self.api.sync_release(
                    dist_git_branch=branch, version=self.event.tag_name, create_pr=True
                )
                msg = f"Packit-as-a-Service proposed [a new update]({new_pr.url}) {msg}"
                self.project.issue_comment(self.event.issue_id, msg)
            except PackitException as ex:
                msg = f"There was an error while proposing a new update {msg} Traceback is: `{ex}`"
                self.project.issue_comment(self.event.issue_id, msg)
                logger.error(f"error while running a build: {ex}")
                sync_failed = True
        if sync_failed:
            return HandlerResults(
                success=False, details={"msg": "Propose update failed"}
            )

        # Close issue if propose-update was successful in all branches
        self.project.issue_close(self.event.issue_id)

        return HandlerResults(success=True, details={})


@add_to_comment_action_mapping
class GitHubPullRequestCommentTestingFarmHandler(
    CommentActionHandler, GithubPackageConfigGetter
):
    """ Issue handler for comment `/packit test` """

    type = CommentAction.test
    event: PullRequestCommentEvent
    triggers = [TheJobTriggerType.pr_comment]

    def __init__(self, config: ServiceConfig, event: PullRequestCommentEvent):
        super().__init__(config=config, event=event)
        self.config = config
        self.event = event
        self.project: GitProject = event.get_project()
        # Get the latest pull request commit
        self.event.commit_sha = self.project.get_all_pr_commits(self.event.pr_id)[-1]
        self.event.base_ref = self.event.commit_sha
        self.package_config: PackageConfig = self.get_package_config_from_repo(
            self.project, self.event.commit_sha, self.event.pr_id
        )
        if not self.package_config:
            raise ValueError(f"No config file found in {self.project.full_repo_name}")
        self.package_config.upstream_project_url = event.project_url
        self.testing_farm_helper = TestingFarmJobHelper(
            self.config, self.package_config, self.project, self.event
        )

    def run(self) -> HandlerResults:

        collaborators = self.project.who_can_merge_pr()
        if self.event.github_login not in collaborators | self.config.admins:
            self.project.pr_comment(self.event.pr_id, PERMISSIONS_ERROR_WRITE_OR_ADMIN)
            return HandlerResults(
                success=True, details={"msg": PERMISSIONS_ERROR_WRITE_OR_ADMIN}
            )

        handler_results = HandlerResults(success=True, details={})

        logger.debug(f"Test job config: {self.testing_farm_helper.job_tests}")
        if self.testing_farm_helper.job_tests:
            self.testing_farm_helper.run_testing_farm_on_all()
        else:
            logger.debug("Testing farm not in the job config.")

        return handler_results
