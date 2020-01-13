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
import json
import logging
import uuid
from typing import Union, Any, Optional, List, Callable

import requests
from ogr.abstract import GitProject
from ogr.utils import RequestResponse
from packit.api import PackitAPI
from packit.config import (
    JobConfig,
    JobTriggerType,
    JobType,
    PackageConfig,
    get_package_config_from_repo,
)
from packit.config.aliases import get_branches
from packit.exceptions import PackitException
from packit.local_project import LocalProject

from packit_service.config import Deployment, ServiceConfig
from packit_service.constants import TESTING_FARM_TRIGGER_URL
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
from packit_service.worker.comment_action_handler import (
    CommentAction,
    add_to_comment_action_mapping,
    CommentActionHandler,
    add_to_comment_action_mapping_with_name,
)
from packit_service.worker.copr_build import CoprBuildHandler
from packit_service.worker.handler import (
    JobHandler,
    HandlerResults,
    add_to_mapping,
    BuildStatusReporter,
    PRCheckName,
    add_to_mapping_for_job,
)
from packit_service.worker.whitelist import Whitelist

logger = logging.getLogger(__name__)


class AbstractGithubJobHandler(JobHandler):
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
        self.package_config: PackageConfig = get_package_config_from_repo(
            self.project, pr_event.base_ref
        )
        if not self.package_config:
            raise ValueError(f"No config file found in {self.project.full_repo_name}")
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
            fas_user=self.config.fas_user, fas_password=self.config.fas_password
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
        self.package_config: PackageConfig = get_package_config_from_repo(
            self.project, release_event.tag_name
        )
        if not self.package_config:
            raise ValueError(f"No config file found in {self.project.full_repo_name}")
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
            branch_errors = "\n".join(
                f"| `{branch}` | `{err}` |" for branch, err in errors.items()
            )

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
        elif isinstance(event, ReleaseEvent):
            base_ref = event.tag_name
        else:
            raise PackitException(
                "Unknown event, only PREvent and ReleaseEvent are accepted."
            )

        self.project: GitProject = event.get_project()
        self.package_config: PackageConfig = get_package_config_from_repo(
            self.project, base_ref
        )
        if not self.package_config:
            raise ValueError(f"No config file found in {self.project.full_repo_name}")
        self.package_config.upstream_project_url = event.project_url

    def handle_pull_request(self):

        if not self.job.metadata.get("targets"):
            msg = "'targets' value is required in packit config for copr_build job"
            self.project.pr_comment(self.event.pr_id, msg)
            return HandlerResults(success=False, details={"msg": msg})

        collaborators = self.project.who_can_merge_pr()
        r = BuildStatusReporter(self.project, self.event.commit_sha)
        if self.event.github_login not in collaborators | self.config.admins:
            msg = "Only collaborators can trigger Packit-as-a-Service"
            check_names = [
                f"{PRCheckName.get_build_check(x)}"
                for x in self.job.metadata.get("targets")
            ]
            r.report("failure", msg, check_names=check_names)
            return HandlerResults(success=False, details={"msg": msg})
        cbh = CoprBuildHandler(
            self.config, self.package_config, self.project, self.event
        )
        handler_results = cbh.run_copr_build()

        return handler_results

    def run(self) -> HandlerResults:
        is_copr_build: Callable[
            [JobConfig], bool
        ] = lambda job: job.type == JobType.copr_build

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
    ):
        super().__init__(config=config, job=job, event=event)
        self.project: GitProject = event.get_project()
        if isinstance(event, CoprBuildEvent):
            self.base_ref = event.ref
        elif isinstance(event, PullRequestCommentEvent):
            self.base_ref = event.commit_sha
        self.package_config: PackageConfig = get_package_config_from_repo(
            self.project, self.base_ref
        )
        if not self.package_config:
            raise ValueError(f"No config file found in {self.project.full_repo_name}")
        self.package_config.upstream_project_url = event.project_url

        self.session = requests.session()
        adapter = requests.adapters.HTTPAdapter(max_retries=5)
        self.insecure = False
        self.session.mount("https://", adapter)
        self.header: dict = {"Content-Type": "application/json"}

    def send_testing_farm_request(
        self, url: str, method: str = None, params: dict = None, data=None
    ):
        method = method or "GET"
        try:
            response = self.get_raw_request(
                method=method, url=url, params=params, data=data
            )
        except requests.exceptions.ConnectionError as er:
            logger.error(er)
            raise Exception(f"Cannot connect to url: `{url}`.", er)
        return response

    def get_raw_request(
        self, url, method="GET", params=None, data=None, header=None
    ) -> RequestResponse:

        response = self.session.request(
            method=method,
            url=url,
            params=params,
            headers=header or self.header,
            data=data,
            verify=not self.insecure,
        )

        json_output = None
        try:
            json_output = response.json()
        except ValueError:
            logger.debug(response.text)

        return RequestResponse(
            status_code=response.status_code,
            ok=response.ok,
            content=response.content,
            json=json_output,
            reason=response.reason,
        )

    def run(self) -> HandlerResults:
        self.local_project = LocalProject(
            git_project=self.project, working_dir=self.config.command_handler_work_dir
        )

        logger.info("Running testing farm")

        r = BuildStatusReporter(self.project, self.event.commit_sha)

        copr_build_handler = CoprBuildHandler(
            self.config, self.package_config, self.project, self.event
        )
        tests_chroots = copr_build_handler.tests_chroots
        logger.debug(f"Testing farm chroots: {tests_chroots}")
        for chroot in tests_chroots:
            pipeline_id = str(uuid.uuid4())
            logger.debug(f"Pipeline id: {pipeline_id}")
            payload: dict = {
                "pipeline": {"id": pipeline_id},
                "api": {"token": self.config.testing_farm_secret},
            }

            logger.debug(f"Payload: {payload}")

            stg = "-stg" if self.config.deployment == Deployment.stg else ""
            copr_repo_name = (
                f"packit/{self.project.namespace}-{self.project.repo}-"
                f"{self.event.pr_id}{stg}"
            )

            payload["artifact"] = {
                "repo-name": self.event.base_repo_name,
                "repo-namespace": self.event.base_repo_namespace,
                "copr-repo-name": copr_repo_name,
                "copr-chroot": chroot,
                "commit-sha": self.event.commit_sha,
                "git-url": self.event.project_url,
                "git-ref": self.base_ref,
            }

            logger.debug("Sending testing farm request...")
            logger.debug(payload)

            req = self.send_testing_farm_request(
                TESTING_FARM_TRIGGER_URL, "POST", {}, json.dumps(payload)
            )
            logger.debug(f"Request sent: {req}")
            if not req:
                msg = "Failed to post request to testing farm API."
                logger.debug("Failed to post request to testing farm API.")
                r.report(
                    "failure",
                    msg,
                    None,
                    "",
                    check_names=PRCheckName.get_testing_farm_check(chroot),
                )
                return HandlerResults(success=False, details={"msg": msg})
            else:
                logger.debug(
                    f"Submitted to testing farm with return code: {req.status_code}"
                )

                """
                Response:
                {
                    "id": "9fa3cbd1-83f2-4326-a118-aad59f5",
                    "success": true,
                    "url": "https://console-testing-farm.apps.ci.centos.org/pipeline/<id>"
                }
                """

                # success set check on pending
                if req.status_code != 200:
                    # something went wrong
                    msg = req.json()["message"]
                    r.report(
                        "failure",
                        msg,
                        None,
                        check_names=PRCheckName.get_testing_farm_check(chroot),
                    )
                    return HandlerResults(success=False, details={"msg": msg})

                r.report(
                    "pending",
                    "Tests are running ...",
                    None,
                    req.json()["url"],
                    check_names=PRCheckName.get_testing_farm_check(chroot),
                )

        return HandlerResults(success=True, details={})


@add_to_comment_action_mapping
@add_to_comment_action_mapping_with_name(name=CommentAction.build)
class GitHubPullRequestCommentCoprBuildHandler(CommentActionHandler):
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
        self.package_config: PackageConfig = get_package_config_from_repo(
            self.project, self.event.commit_sha
        )
        if not self.package_config:
            raise ValueError(f"No config file found in {self.project.full_repo_name}")
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

        cbh = CoprBuildHandler(
            self.config, self.package_config, self.project, self.event
        )
        handler_results = cbh.run_copr_build()

        return handler_results


@add_to_comment_action_mapping
class GitHubIssueCommentProposeUpdateHandler(CommentActionHandler):
    """ Handler for issue comment `/packit propose-update` """

    name = CommentAction.propose_update
    event: IssueCommentEvent

    def __init__(self, config: ServiceConfig, event: IssueCommentEvent):
        super().__init__(config=config, event=event)

        self.config = config
        self.event = event
        self.project = self.event.get_project()
        # Get the latest tag release
        self.event.tag_name = self.project.get_latest_release().tag_name
        self.package_config: PackageConfig = get_package_config_from_repo(
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
            return HandlerResults(success=False, details={})

        # Close issue if propose-update was successful in all branches
        self.project.issue_close(self.event.issue_id)

        return HandlerResults(success=True, details={})


@add_to_comment_action_mapping
class GitHubPullRequestCommentTestingFarmHandler(CommentActionHandler):
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
        self.package_config: PackageConfig = get_package_config_from_repo(
            self.project, self.event.commit_sha
        )
        if not self.package_config:
            raise ValueError(f"No config file found in {self.project.full_repo_name}")
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

        handler_results = HandlerResults(success=True, details={})

        test_job_config = self.get_tests_for_build()
        logger.debug(f"Test job config: {test_job_config}")
        if test_job_config:
            testing_farm_handler = GithubTestingFarmHandler(
                self.config, test_job_config, self.event
            )
            handler_results = testing_farm_handler.run()
        else:
            logger.debug("Testing farm not in the job config.")

        return handler_results
