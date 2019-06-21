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
We love you, Steve Jobs.
"""

import logging
import shutil
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Type, Any

from ogr.abstract import GitProject, GitService
from ogr.services.pagure import PagureService
from packit.api import PackitAPI
from packit.config import (
    JobConfig,
    JobTriggerType,
    JobType,
    PackageConfig,
    Config,
    get_package_config_from_repo,
)
from packit.distgit import DistGit
from packit.exceptions import FailedCreateSRPM, PackitException
from packit.local_project import LocalProject
from packit.ogr_services import get_github_project
from packit.utils import nested_get, get_namespace_and_repo_name
from sandcastle import SandcastleCommandFailed, SandcastleTimeoutReached

from packit_service.worker.whitelist import Whitelist, GithubAppData

logger = logging.getLogger(__name__)


JOB_NAME_HANDLER_MAPPING: Dict[JobType, Type["JobHandler"]] = {}
PROCESSED_FEDMSG_TOPICS = []


def add_to_mapping(kls: Type["JobHandler"]):
    JOB_NAME_HANDLER_MAPPING[kls.name] = kls
    if issubclass(kls, FedmsgHandler):
        PROCESSED_FEDMSG_TOPICS.append(kls.topic)
    return kls


def do_we_process_fedmsg_topic(topic: str) -> bool:
    """ do we process selected fedmsg topic? """
    return topic in PROCESSED_FEDMSG_TOPICS


class HandlerResults(dict):
    """
    Job handler results.
    Inherit from dict to be JSON serializable.
    """

    def __init__(self, success: bool, details: Dict[str, Any] = None):
        """

        :param success: has the job handler succeeded
        :param details: more info from job handler
                        (optional) 'msg' key contains a message
                        more keys to be defined
        """
        super().__init__(self, success=success, details=details or {})


class SteveJobs:
    """
    Steve makes sure all the jobs are done with precision.
    """

    def __init__(self):
        self._config = None
        self._pagure_service = None

    @property
    def config(self):
        if self._config is None:
            self._config = Config.get_user_config()
        return self._config

    @property
    def pagure_service(self):
        if self._pagure_service is None:
            self._pagure_service = PagureService(
                token=self.config.pagure_user_token,
                read_only=self.config.dry_run,
                # TODO: how do we change to stg here? ideally in self.config
            )
        return self._pagure_service

    def get_job_input_from_github_release(
        self, event: dict
    ) -> Optional[Tuple[JobTriggerType, PackageConfig, GitProject]]:
        """
        look into the provided event and see if it's one for a published github release;
        if it is, process it and return input for the job handler
        """
        action = nested_get(event, "action")
        logger.debug(f"action = {action}")
        release = nested_get(event, "release")
        if action == "published" and release:
            repo_namespace = nested_get(event, "repository", "owner", "login")
            repo_name = nested_get(event, "repository", "name")
            if not (repo_namespace and repo_name):
                logger.warning(
                    "We could not figure out the full name of the repository."
                )
                return None
            release_ref = nested_get(event, "release", "tag_name")
            if not release_ref:
                logger.warning("Release tag name is not set.")
                return None
            logger.info(
                f"New release event {release_ref} for repo {repo_namespace}/{repo_name}."
            )
            gh_proj = get_github_project(
                self.config, repo=repo_name, namespace=repo_namespace
            )
            package_config = get_package_config_from_repo(gh_proj, release_ref)
            https_url = event["repository"]["html_url"]
            package_config.upstream_project_url = https_url
            return JobTriggerType.release, package_config, gh_proj
        return None

    def get_job_input_from_github_pr(
        self, event: dict
    ) -> Optional[Tuple[JobTriggerType, PackageConfig, GitProject]]:
        """ look into the provided event and see if it's one for a new github pr """
        action = nested_get(event, "action")
        logger.debug(f"action = {action}")
        pr_id = nested_get(event, "number")
        is_pr = nested_get(event, "pull_request")
        if not is_pr:
            logger.info("Not a pull request event.")
            return None
        if action in ["opened", "reopened", "synchronize"] and pr_id:
            # we can't use head repo here b/c the app is set up against the upstream repo
            # and not the fork, on the other hand, we don't process packit.yaml from
            # the PR but what's in the upstream
            base_repo_namespace = nested_get(
                event, "pull_request", "base", "repo", "owner", "login"
            )
            base_repo_name = nested_get(event, "pull_request", "base", "repo", "name")

            if not (base_repo_name and base_repo_namespace):
                logger.warning(
                    "We could not figure out the full name of the repository."
                )
                return None
            base_ref = nested_get(event, "pull_request", "head", "sha")
            if not base_ref:
                logger.warning("Ref where the PR is coming from is not set.")
                return None
            target_repo = nested_get(event, "repository", "full_name")
            logger.info(f"GitHub pull request {pr_id} event for repo {target_repo}.")
            gh_proj = get_github_project(
                self.config, repo=base_repo_name, namespace=base_repo_namespace
            )
            package_config = get_package_config_from_repo(gh_proj, base_ref)
            https_url = event["repository"]["html_url"]
            package_config.upstream_project_url = https_url
            return JobTriggerType.pull_request, package_config, gh_proj
        return None

    def get_job_input_from_github_app_installation(
            self, event: dict
    ) -> Optional[JobTriggerType, GithubAppData]:
        """ look into the provided event and see github app installation details """
        action = nested_get(event, "action")  # created or deleted
        logger.debug(f"action = {action}")

        installation_id = event["installation"]["id"]

        if not installation_id:
            return None

        account_login = event["installation"]["account"]["login"]
        account_id = event["installation"]["account"]["id"]
        account_url = event["installation"]["account"]["url"]
        account_type = event["installation"]["account"]["type"]  # User or Organization
        created_at = event["installation"]["created_at"]

        sender_id = event["sender"]["id"]
        sender_login = event["sender"]["login"]

        github_app_data = GithubAppData(installation_id, account_login, account_id,
                                        account_url, account_type, created_at, sender_id,
                                        sender_login)

        return JobTriggerType.installation, github_app_data

    def get_job_input_from_dist_git_commit(
        self, event: dict
    ) -> Optional[Tuple[JobTriggerType, PackageConfig, GitProject]]:
        """ this corresponds to dist-git event when someone pushes new commits """
        topic = nested_get(event, "topic")
        logger.debug(f"topic = {topic}")
        if topic == NewDistGitCommit.topic:
            repo_namespace = nested_get(event, "msg", "commit", "namespace")
            repo_name = nested_get(event, "msg", "commit", "repo")
            ref = nested_get(event, "msg", "commit", "branch")
            if not (repo_namespace and repo_name):
                logger.warning(
                    "We could not figure out the full name of the repository."
                )
                return None
            if not ref:
                logger.warning("Target branch for the new commits is not set.")
                return None
            logger.info(
                f"New commits added to dist-git repo {repo_namespace}/{repo_name}, branch {ref}."
            )
            msg_id = nested_get(event, "msg_id")
            logger.info(f"msg_id = {msg_id}")
            dg_proj = self.pagure_service.get_project(
                repo=repo_name, namespace=repo_namespace
            )
            package_config = get_package_config_from_repo(dg_proj, ref)
            return JobTriggerType.commit, package_config, dg_proj
        return None

    def parse_event(
        self, event: dict
    ) -> Optional[Tuple[JobTriggerType, PackageConfig, GitProject]]:
        """
        When a new event arrives, we need to figure out if we are able to process it.

        :param event: webhook payload or fedmsg
        """
        if event:
            # Once we'll start processing multiple events from different sources,
            # we should probably break this method down and move it to handlers or JobTrigger

            # github webhooks
            response = self.get_job_input_from_github_release(event)
            if response:
                return response
            response = self.get_job_input_from_github_pr(event)
            if response:
                return response
            # fedmsg
            response = self.get_job_input_from_dist_git_commit(event)
            if response:
                return response

            # app installation
            response = self.get_job_input_from_github_app_installation(event)
            if response:
                return response

        return None

    def process_jobs(
        self,
        trigger: JobTriggerType,
        package_config: PackageConfig,
        event: dict,
        project: GitProject,
    ) -> Dict[str, HandlerResults]:
        """
        Run a job handler (if trigger matches) for every job defined in config.
        """
        handlers_results = {}
        for job in package_config.jobs:
            if trigger == job.trigger:
                handler_kls = JOB_NAME_HANDLER_MAPPING.get(job.job, None)
                if not handler_kls:
                    logger.warning(f"There is no handler for job {job}")
                    continue
                handler = handler_kls(
                    self.config,
                    package_config,
                    event,
                    project,
                    self.pagure_service,
                    project.service,
                    job,
                    trigger,
                )
                try:
                    handlers_results[job.job.value] = handler.run()
                    # don't break here, other handlers may react to the same event
                finally:
                    handler.clean()
        return handlers_results

    def process_message(self, event: dict, topic: str = None) -> Optional[dict]:
        """
        Entrypoint to processing messages.

        topic is meant to be a fedmsg topic for the message
        """
        if topic:
            # let's pre-filter messages: we don't need to get debug logs from processing
            # messages when we know beforehand that we are not interested in messages for such topic
            topics = [
                getattr(h, "topic", None) for h in JOB_NAME_HANDLER_MAPPING.values()
            ]
            if topic not in topics:
                return None

        response = self.parse_event(event)
        if not response:
            logger.debug("We don't process this event")
            return None
        trigger, package_config, project = response
        if not all([trigger, package_config, project]):
            logger.debug("This project is not using packit.")
            return None

        jobs_results = self.process_jobs(trigger, package_config, event, project)
        task_results = {
            "jobs": jobs_results,
            "project": project.full_repo_name,
            "trigger": trigger.value,
        }
        if any(not v["success"] for v in jobs_results.values()):
            # Any job handler failed, mark task state as FAILURE
            raise PackitException(task_results)
        # Task state SUCCESS
        return task_results


class JobHandler:
    """ Generic interface to handle different type of inputs """

    name: JobType
    triggers: List[JobTriggerType]

    def __init__(
        self,
        config: Config,
        package_config: PackageConfig,
        event: dict,
        project: GitProject,
        distgit_service: GitService,
        upstream_service: GitService,
        job: JobConfig,
        triggered_by: JobTriggerType,
        github_app: GithubAppData
    ):
        self.config: Config = config
        self.project: GitProject = project
        self.distgit_service: GitService = distgit_service
        self.upstream_service: GitService = upstream_service
        self.package_config: PackageConfig = package_config
        self.event: dict = event
        self.job: JobConfig = job
        self.triggered_by: JobTriggerType = triggered_by
        self.github_app = github_app

        self.api: Optional[PackitAPI] = None
        self.local_project: Optional[PackitAPI] = None

        if not config.command_handler_work_dir:
            raise RuntimeError(
                "Packit service has to run with command_handler_work_dir set."
            )

        self._clean_workplace()

    def run(self) -> HandlerResults:
        raise NotImplementedError("This should have been implemented.")

    def _clean_workplace(self):
        logger.debug("remove contents of the PV")
        p = Path(self.config.command_handler_work_dir)
        # remove everything in the volume, but not the volume dir
        globz = list(p.glob("*"))
        if globz:
            logger.info("volume was not empty")
            logger.debug("content of the volume: %s" % globz)
        for item in globz:
            if item.is_file():
                item.unlink()
            else:
                shutil.rmtree(item)

    def clean(self):
        """ clean up the mess once we're done """
        logger.info("cleaning up the mess")
        if self.api:
            self.api.clean()
        self._clean_workplace()


class FedmsgHandler(JobHandler):
    """ Handlers for events from fedmsg """

    topic: str

    def run(self) -> HandlerResults:
        raise NotImplementedError("This should have been implemented.")


@add_to_mapping
class NewDistGitCommit(FedmsgHandler):
    """ A new flag was added to a dist-git pull request """

    topic = "org.fedoraproject.prod.git.receive"
    name = JobType.sync_from_downstream
    triggers = [JobTriggerType.commit]

    def run(self) -> HandlerResults:
        # rev is a commit
        # we use branch on purpose so we get the latest thing
        # TODO: check if rev is HEAD on {branch}, warn then?
        branch = nested_get(self.event, "msg", "commit", "branch")

        # self.project is dist-git, we need to get upstream

        dg = DistGit(self.config, self.package_config)
        self.package_config.upstream_project_url = (
            dg.get_project_url_from_distgit_spec()
        )

        if not self.package_config.upstream_project_url:
            return HandlerResults(
                success=False,
                details={
                    "msg": "URL in specfile is not set. "
                    "We don't know where the upstream project lives."
                },
            )

        n, r = get_namespace_and_repo_name(self.package_config.upstream_project_url)
        up = self.upstream_service.get_project(repo=r, namespace=n)
        self.local_project = LocalProject(
            git_project=up, working_dir=self.config.command_handler_work_dir
        )

        self.api = PackitAPI(self.config, self.package_config, self.local_project)
        self.api.sync_from_downstream(
            dist_git_branch=branch,
            upstream_branch="master",  # TODO: this should be configurable
        )
        return HandlerResults(success=True, details={})


# @add_to_mapping
# class CoprBuildFinished(FedmsgHandler):
#     topic="org.fedoraproject.prod.copr.build.end"
#     name = JobType.ReportCoprResult
#
#     def run(self):
#         msg = f"Build {self.event['msg']['build']} " \
#               f"{'passed' if self.event['msg']['status'] else 'failed'}.\n" \
#               f"\tpackage: {self.event['msg']['pkg']}\n" \
#               f"\tchroot: {self.event['msg']['chroot']}\n"
#         # TODO: lookup specific commit related to the build and comment on it
#         # local cache containing "watched" copr builds?

# class NewDistGitPRFlag(FedmsgHandler):
#     """ A new flag was added to a dist-git pull request """
#     topic = "org.fedoraproject.prod.pagure.pull-request.flag.added"
#     name = "?"
#
#     def run(self):
#         repo_name = self.event["msg"]["pull_request"]["project"]["name"]
#         namespace = self.event["msg"]["pull_request"]["project"]["namespace"]
#         pr_id = self.event["msg"]["pull_request"]["id"]
#
#         pull_request = pagure_repo.get_pr_info(pr_id=pr_id)


@add_to_mapping
class GithubPullRequestHandler(JobHandler):
    name = JobType.check_downstream
    triggers = [JobTriggerType.pull_request]
    # https://developer.github.com/v3/activity/events/types/#events-api-payload-28

    def run(self) -> HandlerResults:
        pr_id = self.event["pull_request"]["number"]

        self.local_project = LocalProject(
            git_project=self.project, working_dir=self.config.command_handler_work_dir
        )

        self.api = PackitAPI(self.config, self.package_config, self.local_project)

        self.api.sync_pr(
            pr_id=pr_id,
            dist_git_branch=self.job.metadata.get("dist-git-branch", "master"),
            # TODO: figure out top upstream commit for source-git here
        )
        return HandlerResults(success=True, details={})


@add_to_mapping
class GithubReleaseHandler(JobHandler):
    name = JobType.propose_downstream
    triggers = [JobTriggerType.release]

    def run(self) -> HandlerResults:
        """
        Sync the upstream release to dist-git as a pull request.
        """
        version = self.event["release"]["tag_name"]

        self.local_project = LocalProject(
            git_project=self.project, working_dir=self.config.command_handler_work_dir
        )

        self.api = PackitAPI(self.config, self.package_config, self.local_project)

        self.api.sync_release(
            dist_git_branch=self.job.metadata.get("dist-git-branch", "master"),
            version=version,
        )
        return HandlerResults(success=True, details={})


class GithubAppInstallationHandler(JobHandler):
    name = JobType.add_to_whitelist
    triggers = [JobTriggerType.installation]

    def run(self):
        """
        Discover information about organization/user which wants to install packit on his repository
        Try to whitelist automatically if mapping from github username to FAS account can prove that
        user is a packager.
        :return:
        """

        # try to add user to whitelist
        # if fail send email to user-cont

        whitelist = Whitelist()

        if not whitelist.add_account(self.github_app):
            # create issue using ogr
            logger.info("USER NEEDS TO BE WHITELISTED")
            # subject = "[Packit-Service] User needs to be approved."
            # receivers = ["user-cont@redhat.com"]
            # text = EMAIL_TEMPLATE.format(sender_login=self.github_app.sender_login,
            #                              account_login=self.github_app.account_login)


class BuildStatusReporter:
    def __init__(self, gh_proj: GitProject, commit_sha: str):
        self.gh_proj = gh_proj
        self.commit_sha = commit_sha

    def report(
        self,
        state: str,
        description: str,
        build_id: Optional[str] = None,
        url: str = "",
    ):
        logger.debug(
            f"Reporting state of copr build ID={build_id},"
            f" state={state}, commit={self.commit_sha}"
        )
        self.gh_proj.set_commit_status(
            self.commit_sha, state, url, description, "packit/rpm-build"
        )


@add_to_mapping
class GithubCoprBuildHandler(JobHandler):
    name = JobType.copr_build
    triggers = [JobTriggerType.pull_request, JobTriggerType.release]

    # We do not support this workflow officially
    # def handle_release(self):
    #     if not self.job.metadata.get("targets"):
    #         logger.error(
    #             "'targets' value is required in packit config for copr_build job"
    #         )
    #     tag_name = self.event["release"]["tag_name"]

    #     local_project = LocalProject(git_project=self.project, ref=tag_name)
    #     api = PackitAPI(self.config, self.package_config, local_project)

    #     build_id, repo_url = api.run_copr_build(
    #         owner=self.job.metadata.get("owner") or "packit",
    #         project=self.job.metadata.get("project")
    #         or f"{self.project.namespace}-{self.project.repo}",
    #         chroots=self.job.metadata.get("targets"),
    #     )

    #     # report
    #     commit_sha = self.project.get_sha_from_tag(tag_name)
    #     r = self.BuildStatusReporter(self.project, commit_sha, build_id, repo_url)
    #     timeout = 60 * 60 * 2
    #     timeout_config = self.job.metadata.get("timeout")
    #     if timeout_config:
    #         timeout = int(timeout_config)
    #     api.watch_copr_build(build_id, timeout, report_func=r.report)

    def handle_pull_request(self):
        if not self.job.metadata.get("targets"):
            logger.error(
                "'targets' value is required in packit config for copr_build job"
            )
        pr_id_int = nested_get(self.event, "number")
        pr_id = str(pr_id_int)

        self.local_project = LocalProject(
            git_project=self.project,
            pr_id=pr_id,
            git_service=self.project.service,
            working_dir=self.config.command_handler_work_dir,
        )
        self.api = PackitAPI(self.config, self.package_config, self.local_project)

        default_project_name = f"{self.project.namespace}-{self.project.repo}-{pr_id}"
        owner = self.job.metadata.get("owner") or "packit"
        project = self.job.metadata.get("project") or default_project_name
        commit_sha = nested_get(self.event, "pull_request", "head", "sha")
        r = BuildStatusReporter(self.project, commit_sha)

        try:
            build_id, repo_url = self.api.run_copr_build(
                owner=owner, project=project, chroots=self.job.metadata.get("targets")
            )
        except SandcastleTimeoutReached:
            msg = "You have reached 10-minute timeout while creating the SRPM."
            self.project.pr_comment(pr_id_int, msg)
            msg = "Timeout reached while creating a SRPM."
            r.report("failure", msg)
            return HandlerResults(success=False, details={"msg": msg})
        except SandcastleCommandFailed as ex:
            max_log_size = 1024 * 16  # is 16KB enough?
            if len(ex.output) > max_log_size:
                output = "Earlier output was truncated\n\n" + ex.output[-max_log_size:]
            else:
                output = ex.output
            msg = (
                "There was an error while creating a SRPM.\n"
                "\nOutput:"
                "\n```\n"
                f"{output}"
                "\n```"
                f"\nReturn code: {ex.rc}"
            )
            self.project.pr_comment(pr_id_int, msg)
            msg = "Failed to create a SRPM."
            r.report("failure", msg)
            return HandlerResults(success=False, details={"msg": msg})
        except FailedCreateSRPM:
            msg = "Failed to create a SRPM."
            r.report("failure", msg)
            return HandlerResults(success=False, details={"msg": msg})
        timeout = 60 * 60 * 2
        # TODO: document this and enforce int in config
        timeout_config = self.job.metadata.get("timeout")
        if timeout_config:
            timeout = int(timeout_config)
        build_state = self.api.watch_copr_build(build_id, timeout, report_func=r.report)
        if build_state == "succeeded":
            msg = (
                f"Congratulations! The build [has finished]({repo_url})"
                " successfully. :champagne:\n\n"
                "You can install the built RPMs by following these steps:\n\n"
                "* `sudo yum install -y dnf-plugins-core` on RHEL 8\n"
                "* `sudo dnf install -y dnf-plugins-core` on Fedora\n"
                f"* `dnf copr enable {owner}/{project}`\n"
                "* And now you can install the packages.\n"
                "\nPlease note that the RPMs should be used only in a testing environment."
            )
            self.project.pr_comment(pr_id_int, msg)
            return HandlerResults(success=True, details={})

    def run(self) -> HandlerResults:
        if self.triggered_by == JobTriggerType.pull_request:
            return self.handle_pull_request()
        # We do not support this workflow officially
        # elif self.triggered_by == JobTriggerType.release:
        #     self.handle_release()
        else:
            return HandlerResults(
                success=False, details={"msg": f"No handler for {self.triggered_by}"}
            )
