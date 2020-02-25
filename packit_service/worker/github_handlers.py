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

from github import UnknownObjectException
from ogr.abstract import GitProject
from packit.api import PackitAPI
from packit.config import (
    JobConfig,
    JobTriggerType,
    JobType,
    PackageConfig,
)
from packit.config.aliases import get_branches
from packit.exceptions import PackitException
from packit.local_project import LocalProject

from packit_service.config import ServiceConfig, GithubPackageConfigGetter
from packit_service.service.events import (
    PullRequestEvent,
    InstallationEvent,
    ReleaseEvent,
    PullRequestCommentEvent,
    IssueCommentEvent,
    CoprBuildEvent,
)
from packit_service.service.models import Installation
from packit_service.worker import sentry_integration
from packit_service.worker.build import CoprBuildJobHelper
from packit_service.worker.comment_action_handler import (
    CommentAction,
    add_to_comment_action_mapping,
    CommentActionHandler,
    add_to_comment_action_mapping_with_name,
)
from packit_service.worker.handler import (
    JobHandler,
    HandlerResults,
    add_to_mapping,
    add_to_mapping_for_job,
)
from packit_service.worker.testing_farm import TestingFarmJobHelper
from packit_service.worker.whitelist import Whitelist

logger = logging.getLogger(__name__)


class AbstractGithubJobHandler(JobHandler, GithubPackageConfigGetter):
    pass


@add_to_mapping
class GithubPullRequestHandler(AbstractGithubJobHandler):
    name = JobType.check_downstream
    triggers = [JobTriggerType.pull_request]

    # https://developer.github.com/v3/activity/events/types/#events-api-payload-28

    def __init__(
        self, config: ServiceConfig, job: JobConfig, pr_event: PullRequestEvent
    ):
        super().__init__(config=config, job=job, event=pr_event)
        self.pr_event = pr_event
        self.project: GitProject = pr_event.get_project()
        self.package_config: PackageConfig = self.get_package_config_from_repo(
            self.project, pr_event.base_ref, pr_event.pr_id
        )
        self.package_config.upstream_project_url = pr_event.project_url

    def run(self) -> HandlerResults:
        self.local_project = LocalProject(
            git_project=self.project, working_dir=self.config.command_handler_work_dir
        )

        self.api = PackitAPI(self.config, self.package_config, self.local_project)

        self.api.sync_pr(
            pr_id=self.pr_event.pr_id,
            dist_git_branch=self.job.metadata.get("dist-git-branch", "master"),
            # TODO: figure out top upstream commit for source-git here
        )
        return HandlerResults(success=True, details={})


@add_to_mapping
class GithubAppInstallationHandler(AbstractGithubJobHandler):
    name = JobType.add_to_whitelist
    triggers = [JobTriggerType.installation]

    # https://developer.github.com/v3/activity/events/types/#events-api-payload-28

    def __init__(
        self,
        config: ServiceConfig,
        job: JobConfig,
        installation_event: Union[InstallationEvent, Any],
    ):
        super().__init__(config=config, job=job, event=installation_event)

        self.installation_event = installation_event
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

        Installation.create(
            installation_id=self.installation_event.installation_id,
            event=self.installation_event,
        )
        # try to add user to whitelist
        whitelist = Whitelist(
            fas_user=self.config.fas_user, fas_password=self.config.fas_password,
        )
        account_login = self.installation_event.account_login
        account_type = self.installation_event.account_type
        if not whitelist.add_account(self.installation_event):
            # Create an issue in our repository, so we are notified when someone install the app
            self.project.create_issue(
                title=f"{account_type} {account_login} needs to be approved.",
                body=(
                    f"Hi @{self.installation_event.sender_login}, we need to approve you in "
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


@add_to_mapping
class GithubReleaseHandler(AbstractGithubJobHandler):
    name = JobType.propose_downstream
    triggers = [JobTriggerType.release]
    event: ReleaseEvent

    def __init__(
        self, config: ServiceConfig, job: JobConfig, release_event: ReleaseEvent
    ):
        super().__init__(config=config, job=job, event=release_event)

        self.project: GitProject = release_event.get_project()
        self.package_config: PackageConfig = self.get_package_config_from_repo(
            self.project, release_event.tag_name
        )
        self.package_config.upstream_project_url = release_event.project_url

    def run(self) -> HandlerResults:
        """
        Sync the upstream release to dist-git as a pull request.
        """

        self.local_project = LocalProject(
            git_project=self.project, working_dir=self.config.command_handler_work_dir
        )

        self.api = PackitAPI(self.config, self.package_config, self.local_project)

        errors = {}
        for branch in get_branches(self.job.metadata.get("dist-git-branch", "master")):
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


@add_to_mapping
@add_to_mapping_for_job(job_type=JobType.tests)
class GithubCoprBuildHandler(AbstractGithubJobHandler):
    name = JobType.copr_build
    triggers = [JobTriggerType.pull_request, JobTriggerType.release]
    event: Union[PullRequestEvent, ReleaseEvent]

    def __init__(
        self,
        config: ServiceConfig,
        job: JobConfig,
        event: Union[PullRequestEvent, ReleaseEvent],
    ):
        super().__init__(config=config, job=job, event=event)

        if isinstance(event, PullRequestEvent):
            base_ref = event.base_ref
            pr_id = event.pr_id
        elif isinstance(event, ReleaseEvent):
            base_ref = event.tag_name
            pr_id = None
        else:
            raise PackitException(
                "Unknown event, only PREvent and ReleaseEvent are accepted."
            )

        self.project: GitProject = event.get_project()
        self.package_config: PackageConfig = self.get_package_config_from_repo(
            self.project, base_ref, pr_id
        )
        self.package_config.upstream_project_url = event.project_url

    def handle_pull_request(self):

        if not self.job.metadata.get("targets"):
            msg = "'targets' value is required in packit config for copr_build job"
            self.project.pr_comment(self.event.pr_id, msg)
            return HandlerResults(success=False, details={"msg": msg})

        collaborators = self.project.who_can_merge_pr()
        cbh = CoprBuildJobHelper(
            self.config, self.package_config, self.project, self.event
        )
        if self.event.github_login not in collaborators | self.config.admins:
            msg = "Only collaborators can trigger Packit-as-a-Service"
            cbh.report_status_to_all("failure", msg)
            return HandlerResults(success=False, details={"msg": msg})

        handler_results = cbh.run_copr_build()

        return handler_results

    def run(self) -> HandlerResults:
        is_copr_build: Callable[
            [JobConfig], bool
        ] = lambda job: job.job == JobType.copr_build

        if self.job.job == JobType.tests and any(
            filter(is_copr_build, self.package_config.jobs)
        ):
            return HandlerResults(
                success=False,
                details={
                    "msg": "Skipping build for testing. The COPR build is defined in the config."
                },
            )
        if self.event.trigger == JobTriggerType.pull_request:
            return self.handle_pull_request()
        # We do not support this workflow officially
        # elif self.triggered_by == JobTriggerType.release:
        #     self.handle_release()
        else:
            return HandlerResults(
                success=False,
                details={"msg": f"No handler for {str(self.event.trigger)}"},
            )


class GithubTestingFarmHandler(AbstractGithubJobHandler):
    """
    This class intentionally does not have a @add_to_mapping decorator as its
    trigger is finished copr build.
    """

    name = JobType.tests
    triggers = [JobTriggerType.pull_request]
    event: Union[CoprBuildEvent, PullRequestCommentEvent]

    def __init__(
        self,
        config: ServiceConfig,
        job: JobConfig,
        event: Union[CoprBuildEvent, PullRequestCommentEvent],
        chroot: str,
    ):
        super().__init__(config=config, job=job, event=event)
        self.chroot = chroot
        self.project: GitProject = event.get_project()
        if isinstance(event, CoprBuildEvent):
            self.base_ref = event.ref
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

    name = CommentAction.copr_build
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

    def get_tests_for_build(self) -> Optional[JobConfig]:
        """
        Check if there are tests defined
        :return: JobConfig or None
        """
        for job in self.package_config.jobs:
            if job.job == JobType.tests:
                return job
        return None

    def run(self) -> HandlerResults:
        collaborators = self.project.who_can_merge_pr()
        if self.event.github_login not in collaborators | self.config.admins:
            msg = "Only collaborators can trigger Packit-as-a-Service"
            self.project.pr_comment(self.event.pr_id, msg)
            return HandlerResults(success=True, details={"msg": msg})

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

    name = CommentAction.propose_update
    event: IssueCommentEvent

    def __init__(self, config: ServiceConfig, event: IssueCommentEvent):
        super().__init__(config=config, event=event)

        self.config = config
        self.event = event
        self.project = self.event.get_project()
        # Get the latest tag release
        self.event.tag_name = self.get_tag_name()
        self.package_config: PackageConfig = self.get_package_config_from_repo(
            self.project, self.event.tag_name
        )
        if not self.package_config:
            raise ValueError(f"No config file found in {self.project.full_repo_name}")
        self.package_config.upstream_project_url = event.project_url

    def get_tag_name(self) -> Optional[str]:
        """
        Get the tag name of the latest upstream release.

        :return: tag name
        """
        try:
            tag_name = self.project.get_latest_release().tag_name
            logger.debug(f"found the tag name of the latest release: {tag_name}")
            return tag_name
        except UnknownObjectException:
            logger.debug("no upstream release found")
            return None

    @property
    def dist_git_branches_to_sync(self) -> List[str]:
        """
        Get the dist-git branches to sync to with the aliases expansion.

        :return: list of dist-git branches
        """
        configured_branches = [
            job.metadata.get("dist-git-branch")
            for job in self.package_config.jobs
            if job.job == JobType.propose_downstream
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
            msg = "Only collaborators can trigger Packit-as-a-Service"
            self.project.issue_comment(self.event.issue_id, msg)
            return HandlerResults(success=True, details={"msg": msg})

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
                f"a new update for the Fedora package "
                f"`{self.package_config.downstream_package_name}`"
                f"with the tag `{self.event.tag_name}` in the `{branch}` branch.\n"
            )
            try:
                self.api.sync_release(
                    dist_git_branch=branch, version=self.event.tag_name
                )
                msg = f"Packit-as-a-Service proposed {msg}"
                self.project.issue_comment(self.event.issue_id, msg)
            except PackitException as ex:
                msg = f"There was an error while proposing {msg} Traceback is: `{ex}`"
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

    name = CommentAction.test
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
        if not self.package_config:
            raise ValueError(f"No config file found in {self.project.full_repo_name}")
        self.package_config.upstream_project_url = event.project_url
        self.testing_farm_helper = TestingFarmJobHelper(
            self.config, self.package_config, self.project, self.event
        )

    def run(self) -> HandlerResults:

        collaborators = self.project.who_can_merge_pr()
        if self.event.github_login not in collaborators | self.config.admins:
            msg = "Only collaborators can trigger Packit-as-a-Service"
            self.project.pr_comment(self.event.pr_id, msg)
            return HandlerResults(success=True, details={"msg": msg})

        handler_results = HandlerResults(success=True, details={})

        logger.debug(f"Test job config: {self.testing_farm_helper.job_tests}")
        if self.testing_farm_helper.job_tests:
            self.testing_farm_helper.run_testing_farm_on_all()
        else:
            logger.debug("Testing farm not in the job config.")

        return handler_results
