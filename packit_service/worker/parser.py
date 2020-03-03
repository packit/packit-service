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
    CoprBuildEvent,
    PushGitHubEvent,
)
from packit_service.worker.handlers import NewDistGitCommitHandler

logger = logging.getLogger(__name__)


class Parser:
    """
    Once we receive a new event (GitHub webhook or Fedmsg event) for every event we need
    to have method inside the `Parser` class to create objects defined in `events.py`.
    """

    @staticmethod
    def parse_event(
        event: dict,
    ) -> Optional[
        Union[
            PullRequestEvent,
            InstallationEvent,
            ReleaseEvent,
            DistGitEvent,
            TestingFarmResultsEvent,
            PullRequestCommentEvent,
            IssueCommentEvent,
            CoprBuildEvent,
            PushGitHubEvent,
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
                CoprBuildEvent,
                PushGitHubEvent,
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

        response = Parser.parse_push_event(event)
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

        response = Parser.parse_copr_event(event)
        if response:
            return response

        if not response:
            logger.debug("We don't process this event")

        return response

    @staticmethod
    def parse_pr_event(event) -> Optional[PullRequestEvent]:
        """ Look into the provided event and see if it's one for a new github PR. """
        if not event.get("pull_request"):
            return None

        pr_id = event.get("number")
        action = event.get("action")
        if action not in {"opened", "reopened", "synchronize"} or not pr_id:
            return None

        logger.info(f"GitHub PR#{pr_id} {action!r} event.")

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

    @staticmethod
    def parse_push_event(event) -> Optional[PushGitHubEvent]:
        """
        Look into the provided event and see if it's one for a new push to the github branch.
        """
        raw_ref = event.get("ref")
        before = event.get("before")
        pusher = nested_get(event, "pusher", "name")

        # https://developer.github.com/v3/activity/events/types/#pushevent
        # > Note: The webhook payload example following the table differs
        # > significantly from the Events API payload described in the table.
        head_commit = (
            event.get("head") or event.get("after") or event.get("head_commit")
        )

        if not (raw_ref and head_commit and before and pusher):
            return None

        number_of_commits = event.get("size")
        if number_of_commits is None and "commits" in event:
            number_of_commits = len(event.get("commits"))

        ref = raw_ref.split("/", maxsplit=2)[-1]

        logger.info(
            f"GitHub push event on '{raw_ref}': {before[:8]} -> {head_commit[:8]} "
            f"by {pusher} "
            f"({number_of_commits} {'commit' if number_of_commits == 1 else 'commits'})"
        )

        repo_namespace = nested_get(event, "repository", "owner", "login")
        repo_name = nested_get(event, "repository", "name")

        if not (repo_namespace and repo_name):
            logger.warning("No full name of the repository.")
            return None

        repo_url = nested_get(event, "repository", "html_url")

        return PushGitHubEvent(
            repo_namespace=repo_namespace,
            repo_name=repo_name,
            ref=ref,
            https_url=repo_url,
            commit_sha=head_commit,
        )

    @staticmethod
    def parse_issue_comment_event(event) -> Optional[IssueCommentEvent]:
        """ Look into the provided event and see if it is Github issue comment event. """
        if nested_get(event, "issue", "pull_request"):
            return None

        issue_id = nested_get(event, "issue", "number")
        action = event.get("action")
        comment = nested_get(event, "comment", "body")
        if action != "created" or not issue_id or not comment:
            return None

        logger.info(f"Github issue#{issue_id} comment: {comment!r} {action!r} event.")

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

    @staticmethod
    def parse_pull_request_comment_event(event) -> Optional[PullRequestCommentEvent]:
        """ Look into the provided event and see if it is Github PR comment event. """
        if not nested_get(event, "issue", "pull_request"):
            return None

        pr_id = nested_get(event, "issue", "number")
        action = event.get("action")
        if action not in {"created", "edited"} or not pr_id:
            return None

        comment = nested_get(event, "comment", "body")
        logger.info(f"Github PR#{pr_id} comment: {comment!r} {action!r} event.")

        base_repo_namespace = nested_get(event, "repository", "owner", "login")
        base_repo_name = nested_get(event, "repository", "name")
        if not (base_repo_name and base_repo_namespace):
            logger.warning("No full name of the repository.")
            return None

        github_login = nested_get(event, "comment", "user", "login")
        if not github_login:
            logger.warning("No GitHub login name from event.")
            return None
        if github_login in {"packit-as-a-service[bot]", "packit-as-a-service-stg[bot]"}:
            logger.debug("Our own comment.")
            return None

        target_repo = nested_get(event, "repository", "full_name")
        logger.info(f"Target repo: {target_repo}.")
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

    @staticmethod
    def parse_installation_event(event) -> Optional[InstallationEvent]:
        """ Look into the provided event and see Github App installation details. """
        # Check if installation key in JSON isn't enough, we have to check the account as well
        if not nested_get(event, "installation", "account"):
            return None

        action = event["action"]
        if action not in {"created", "added"}:
            # We're currently not interested in removed/deleted/updated event.
            return None
        installation_id = event["installation"]["id"]
        # if action == 'created' then repos are in repositories
        # if action == 'added' then repos are in repositories_added
        repositories = event.get("repositories") or event.get("repositories_added", [])
        repo_names = [repo["full_name"] for repo in repositories]

        logger.info(f"Github App installation {action!r} event. id: {installation_id}")
        logger.debug(
            f"account: {event['installation']['account']}, "
            f"repositories: {repo_names}, sender: {event['sender']}"
        )

        # namespace (user/organization) into which the app has been installed
        account_login = event["installation"]["account"]["login"]
        account_id = event["installation"]["account"]["id"]
        account_url = event["installation"]["account"]["url"]
        account_type = event["installation"]["account"]["type"]  # User or Organization
        created_at = event["installation"]["created_at"]

        # user who installed the app into 'account'
        sender_id = event["sender"]["id"]
        sender_login = event["sender"]["login"]

        return InstallationEvent(
            installation_id,
            account_login,
            account_id,
            account_url,
            account_type,
            created_at,
            repo_names,
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
        if action != "published" or not release:
            return None

        logger.info(f"GitHub release {release} {action!r} event.")

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

    @staticmethod
    def parse_distgit_event(event) -> Optional[DistGitEvent]:
        """ this corresponds to dist-git event when someone pushes new commits """
        topic = event.get("topic")
        if topic != NewDistGitCommitHandler.topic:
            return None

        logger.info(f"Dist-git commit event, topic: {topic}")

        repo_namespace = nested_get(event, "msg", "commit", "namespace")
        repo_name = nested_get(event, "msg", "commit", "repo")

        if not (repo_namespace and repo_name):
            logger.warning("No full name of the repository.")
            return None

        branch = nested_get(event, "msg", "commit", "branch")
        rev = nested_get(event, "msg", "commit", "rev")
        if not branch or not rev:
            logger.warning("Target branch/rev for the new commits is not set.")
            return None

        msg_id = event.get("msg_id")
        logger.info(
            f"New commits added to dist-git repo {repo_namespace}/{repo_name},"
            f"rev: {rev}, branch: {branch}, msg_id: {msg_id}"
        )

        # TODO: get the right hostname without hardcoding
        project_url = f"https://src.fedoraproject.org/{repo_namespace}/{repo_name}"
        return DistGitEvent(
            topic=topic,
            repo_namespace=repo_namespace,
            repo_name=repo_name,
            ref=rev,
            branch=branch,
            msg_id=msg_id,
            project_url=project_url,
        )

    @staticmethod
    def parse_testing_farm_results_event(event) -> Optional[TestingFarmResultsEvent]:
        """ this corresponds to testing farm results event """
        pipeline_id: str = nested_get(event, "pipeline", "id")
        if not pipeline_id:
            return None

        result: TestingFarmResult = TestingFarmResult(event.get("result"))
        environment: str = nested_get(event, "environment", "image")
        message: str = event.get("message")
        log_url: str = event.get("url")
        copr_repo_name: str = nested_get(event, "artifact", "copr-repo-name")
        copr_chroot: str = nested_get(event, "artifact", "copr-chroot")
        repo_name: str = nested_get(event, "artifact", "repo-name")
        repo_namespace: str = nested_get(event, "artifact", "repo-namespace")
        ref: str = nested_get(event, "artifact", "git-ref")
        https_url: str = nested_get(event, "artifact", "git-url")
        commit_sha: str = nested_get(event, "artifact", "commit-sha")
        tests: List[TestResult] = [
            TestResult(
                name=raw_test["name"],
                result=TestingFarmResult(raw_test["result"]),
                log_url=raw_test.get("log"),
            )
            for raw_test in event.get("tests", [])
        ]

        logger.info(f"Results from Testing farm event. Pipeline ID: {pipeline_id}")
        logger.debug(
            f"environment: {environment}, message: {message}, "
            f"log_url: {log_url}, artifact: {event.get('artifact')}"
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

    @staticmethod
    def parse_copr_event(event) -> Optional[CoprBuildEvent]:
        """ this corresponds to copr build event e.g:"""
        topic = event.get("topic")
        if topic not in {
            "org.fedoraproject.prod.copr.build.start",
            "org.fedoraproject.prod.copr.build.end",
        }:
            return None

        logger.info(f"Copr event; {event.get('what')}")

        build_id = event.get("build")
        chroot = event.get("chroot")
        status = event.get("status")
        owner = event.get("owner")
        project_name = event.get("copr")
        pkg = event.get("pkg")

        return CoprBuildEvent.from_build_id(
            topic, build_id, chroot, status, owner, project_name, pkg
        )
