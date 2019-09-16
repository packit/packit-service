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
Parser is transforming github JSONs into `events` objects
"""
import logging
from typing import Optional, Union, List

from packit.utils import nested_get
from packit_service.service.events import (
    PullRequestEvent,
    PullRequestCommentEvent,
    InstallationEvent,
    ReleaseEvent,
    DistGitEvent,
    PullRequestAction,
    TestingFarmResultsEvent,
    TestingFarmResult,
    TestResult,
    PullRequestCommentAction,
    IssueCommentEvent,
    IssueCommentAction,
)
from packit_service.worker.fedmsg_handlers import NewDistGitCommit

logger = logging.getLogger(__name__)


class Parser:
    """
    Once we receive a new event (GitHub webhook or Fedmsg event) for every event we need
    to have method inside the `Parser` class to create objects defined in `events.py`.
    """

    @staticmethod
    def parse_event(
        event: dict
    ) -> Optional[
        Union[
            PullRequestEvent,
            InstallationEvent,
            ReleaseEvent,
            DistGitEvent,
            TestingFarmResultsEvent,
            PullRequestCommentEvent,
            IssueCommentEvent,
        ]
    ]:
        """
        Try to parse all JSONs that we process
        :param event: JSON from Github or fedmsg
        :return: event object
        """

        if not event:
            logger.warning("No event to process!")
            return None

        response: Optional[
            Union[
                PullRequestEvent,
                InstallationEvent,
                ReleaseEvent,
                DistGitEvent,
                TestingFarmResultsEvent,
                PullRequestCommentEvent,
                IssueCommentEvent,
            ]
        ] = Parser.parse_pr_event(event)
        if response:
            return response

        response = Parser.parse_pull_request_comment_event(event)
        if response:
            return response

        response = Parser.parse_issue_comment_event(event)
        if response:
            return response

        response = Parser.parse_release_event(event)
        if response:
            return response

        response = Parser.parse_installation_event(event)
        if response:
            return response

        response = Parser.parse_distgit_event(event)
        if response:
            return response

        response = Parser.parse_testing_farm_results_event(event)
        if response:
            return response

        return response

    @staticmethod
    def parse_pr_event(event) -> Optional[PullRequestEvent]:
        """ Look into the provided event and see if it's one for a new github PR. """
        if not event.get("pull_request"):
            return None

        pr_id = event.get("number")
        action = event.get("action")
        if action in ["opened", "reopened", "synchronize"] and pr_id:
            logger.info(f"GitHub PR#{pr_id} event. Action: {action}.")

            # we can't use head repo here b/c the app is set up against the upstream repo
            # and not the fork, on the other hand, we don't process packit.yaml from
            # the PR but what's in the upstream
            base_repo_namespace = nested_get(
                event, "pull_request", "base", "repo", "owner", "login"
            )
            base_repo_name = nested_get(event, "pull_request", "base", "repo", "name")

            if not (base_repo_name and base_repo_namespace):
                logger.warning("No full name of the repository.")
                return None

            base_ref = nested_get(event, "pull_request", "head", "sha")
            if not base_ref:
                logger.warning("Ref where the PR is coming from is not set.")
                return None

            github_login = nested_get(event, "pull_request", "user", "login")
            if not github_login:
                logger.warning("No GitHub login name from event.")
                return None

            target_repo = nested_get(event, "repository", "full_name")
            logger.info(f"Target repo: {target_repo}.")

            commit_sha = nested_get(event, "pull_request", "head", "sha")
            https_url = event["repository"]["html_url"]
            return PullRequestEvent(
                PullRequestAction[action],
                pr_id,
                base_repo_namespace,
                base_repo_name,
                base_ref,
                target_repo,
                https_url,
                commit_sha,
                github_login,
            )
        return None

    @staticmethod
    def parse_issue_comment_event(event) -> Optional[IssueCommentEvent]:
        """ Look into the provided event and see if it is Github issue comment event. """

        if nested_get(event, "issue", "pull_request"):
            return None

        issue_id = nested_get(event, "issue", "number")
        action = event.get("action")
        comment = nested_get(event, "comment", "body")
        if action == "created" and issue_id and comment:
            logger.info(f"Github issue {issue_id} comment event.")

            base_repo_namespace = nested_get(event, "repository", "owner", "login")
            base_repo_name = nested_get(event, "repository", "name")
            if not (base_repo_namespace and base_repo_name):
                logger.warning("No full name of the repository.")

            github_login = nested_get(event, "comment", "user", "login")
            if not github_login:
                logger.warning("No Github login name from event.")
                return None

            target_repo = nested_get(event, "repository", "full_name")
            logger.info(f"Target repo: {target_repo}.")
            https_url = nested_get(event, "repository", "html_url")
            return IssueCommentEvent(
                IssueCommentAction[action],
                issue_id,
                base_repo_namespace,
                base_repo_name,
                target_repo,
                https_url,
                github_login,
                comment,
            )
        return None

    @staticmethod
    def parse_pull_request_comment_event(event) -> Optional[PullRequestCommentEvent]:
        """ Look into the provided event and see if it is Github PR comment event. """

        if not nested_get(event, "issue", "pull_request"):
            return None

        pr_id = nested_get(event, "issue", "number")
        action = event.get("action")
        if action in ["created", "edited"] and pr_id:
            logger.info(f"GitHub PR#{pr_id} comment event. Action: {action}.")

            base_repo_namespace = nested_get(event, "repository", "owner", "login")
            base_repo_name = nested_get(event, "repository", "name")
            if not (base_repo_name and base_repo_namespace):
                logger.warning("No full name of the repository.")
                return None

            github_login = nested_get(event, "comment", "user", "login")
            if not github_login:
                logger.warning("No GitHub login name from event.")
                return None

            target_repo = nested_get(event, "repository", "full_name")
            logger.info(f"Target repo: {target_repo}.")
            comment = nested_get(event, "comment", "body")
            https_url = event["repository"]["html_url"]
            return PullRequestCommentEvent(
                PullRequestCommentAction[action],
                pr_id,
                base_repo_namespace,
                base_repo_name,
                None,  # the payload does not include this info
                target_repo,
                https_url,
                github_login,
                comment,
            )
        return None

    @staticmethod
    def parse_installation_event(event) -> Optional[InstallationEvent]:
        """ Look into the provided event and see Github App installation details. """
        # Check if installation key in JSON isn't enough, we have to check the account as well
        if not nested_get(event, "installation", "account"):
            return None

        action = event.get("action")  # created or deleted
        installation_id = event["installation"]["id"]

        logger.info(
            f"Github App installation event. Action: {action}, "
            f"id: {installation_id}, account: {event['installation']['account']}, "
            f"sender: {event['sender']}"
        )

        account_login = event["installation"]["account"]["login"]
        account_id = event["installation"]["account"]["id"]
        account_url = event["installation"]["account"]["url"]
        account_type = event["installation"]["account"]["type"]  # User or Organization
        created_at = event["installation"]["created_at"]

        sender_id = event["sender"]["id"]
        sender_login = event["sender"]["login"]

        return InstallationEvent(
            installation_id,
            account_login,
            account_id,
            account_url,
            account_type,
            created_at,
            sender_id,
            sender_login,
        )

    @staticmethod
    def parse_release_event(event) -> Optional[ReleaseEvent]:
        """
        look into the provided event and see if it's one for a published github release;
        if it is, process it and return input for the job handler
        """
        action = event.get("action")
        release = event.get("release")
        if action == "published" and release:
            logger.info(f"GitHub release {release} event, action = {action}.")

            repo_namespace = nested_get(event, "repository", "owner", "login")
            repo_name = nested_get(event, "repository", "name")
            if not (repo_namespace and repo_name):
                logger.warning("No full name of the repository.")
                return None

            release_ref = nested_get(event, "release", "tag_name")
            if not release_ref:
                logger.warning("Release tag name is not set.")
                return None

            logger.info(
                f"New release event {release_ref} for repo {repo_namespace}/{repo_name}."
            )
            https_url = event["repository"]["html_url"]
            return ReleaseEvent(repo_namespace, repo_name, release_ref, https_url)
        return None

    @staticmethod
    def parse_distgit_event(event) -> Optional[DistGitEvent]:
        """ this corresponds to dist-git event when someone pushes new commits """
        topic = event.get("topic")
        if topic == NewDistGitCommit.topic:
            logger.info(f"Dist-git commit event, topic: {topic}")

            repo_namespace = nested_get(event, "msg", "commit", "namespace")
            repo_name = nested_get(event, "msg", "commit", "repo")
            ref = nested_get(event, "msg", "commit", "branch")
            if not (repo_namespace and repo_name):
                logger.warning("No full name of the repository.")
                return None

            if not ref:
                logger.warning("Target branch for the new commits is not set.")
                return None

            logger.info(
                f"New commits added to dist-git repo {repo_namespace}/{repo_name}, branch {ref}."
            )
            msg_id = event.get("msg_id")
            logger.info(f"msg_id = {msg_id}")

            branch = nested_get(event, "msg", "commit", "branch")
            return DistGitEvent(topic, repo_namespace, repo_name, ref, branch, msg_id)
        return None

    @staticmethod
    def parse_testing_farm_results_event(event) -> Optional[TestingFarmResultsEvent]:
        """ this corresponds to testing farm results event """
        pipeline_id: str = nested_get(event, "pipeline", "id")
        if pipeline_id:
            result: TestingFarmResult = TestingFarmResult(nested_get(event, "result"))
            environment: str = nested_get(event, "environment", "image")
            message: str = nested_get(event, "message")
            log_url: str = nested_get(event, "url")
            copr_repo_name: str = nested_get(event, "artifact", "copr-repo-name")
            copr_chroot: str = nested_get(event, "artifact", "copr-chroot")
            repo_name: str = nested_get(event, "artifact", "repo-name")
            repo_namespace: str = nested_get(event, "artifact", "repo-namespace")
            ref: str = nested_get(event, "artifact", "git-ref")
            https_url: str = nested_get(event, "artifact", "git-url")
            commit_sha: str = nested_get(event, "artifact", "commit-sha")
            tests: List[TestResult] = []

            logger.info(
                f"New testing results arrived from testing farm!. Pipeline ID: {pipeline_id}"
            )

            return TestingFarmResultsEvent(
                pipeline_id,
                result,
                environment,
                message,
                log_url,
                copr_repo_name,
                copr_chroot,
                tests,
                repo_namespace,
                repo_name,
                ref,
                https_url,
                commit_sha,
            )

        return None
