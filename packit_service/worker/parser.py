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
from functools import partial
from typing import Dict, List, Optional, Type, Union

from ogr.parsing import parse_git_repo
from packit.utils import nested_get
from packit_service.constants import KojiBuildState
from packit_service.service.events import (
    AbstractPagureEvent,
    CoprBuildEndEvent,
    AbstractCoprBuildEvent,
    CoprBuildStartEvent,
    DistGitEvent,
    GitlabEventAction,
    InstallationEvent,
    IssueCommentAction,
    IssueCommentEvent,
    IssueCommentGitlabEvent,
    KojiBuildEvent,
    MergeRequestCommentGitlabEvent,
    MergeRequestGitlabEvent,
    PullRequestAction,
    PullRequestCommentAction,
    PullRequestCommentGithubEvent,
    PullRequestCommentPagureEvent,
    PullRequestGithubEvent,
    PullRequestLabelAction,
    PullRequestLabelPagureEvent,
    PullRequestPagureEvent,
    PushGitHubEvent,
    PushGitlabEvent,
    PushPagureEvent,
    ReleaseEvent,
    TestResult,
    TestingFarmResult,
    TestingFarmResultsEvent,
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
            PullRequestGithubEvent,
            InstallationEvent,
            ReleaseEvent,
            DistGitEvent,
            TestingFarmResultsEvent,
            PullRequestCommentGithubEvent,
            IssueCommentEvent,
            AbstractCoprBuildEvent,
            PushGitHubEvent,
            MergeRequestGitlabEvent,
            KojiBuildEvent,
            MergeRequestCommentGitlabEvent,
            IssueCommentGitlabEvent,
            PushGitlabEvent,
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
                PullRequestGithubEvent,
                InstallationEvent,
                ReleaseEvent,
                DistGitEvent,
                TestingFarmResultsEvent,
                PullRequestCommentGithubEvent,
                IssueCommentEvent,
                KojiBuildEvent,
                AbstractCoprBuildEvent,
                PushGitHubEvent,
                MergeRequestGitlabEvent,
                MergeRequestCommentGitlabEvent,
                IssueCommentGitlabEvent,
                PushGitlabEvent,
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

        response = Parser.parse_mr_event(event)
        if response:
            return response

        response = Parser.parse_koji_event(event)
        if response:
            return response

        response = Parser.parse_merge_request_comment_event(event)
        if response:
            return response

        response = Parser.parse_gitlab_issue_comment_event(event)
        if response:
            return response

        response = Parser.parse_gitlab_push_event(event)
        if response:
            return response

        if not response:
            logger.debug("We don't process this event.")

        return response

    @staticmethod
    def parse_mr_event(event) -> Optional[MergeRequestGitlabEvent]:
        """ Look into the provided event and see if it's one for a new gitlab MR. """
        if event.get("object_kind") != "merge_request":
            return None

        state = event["object_attributes"]["state"]
        if state != "opened":
            return None
        action = nested_get(event, "object_attributes", "action")
        if action not in {"reopen", "update"}:
            action = state

        username = event["user"]["username"]
        if not username:
            logger.warning("No Gitlab username from event.")
            return None

        object_id = event["object_attributes"]["id"]
        if not object_id:
            logger.warning("No object id from the event.")
            return None

        object_iid = event["object_attributes"]["iid"]
        if not object_iid:
            logger.warning("No object iid from the event.")
            return None

        source_project_url = nested_get(event, "object_attributes", "source", "web_url")
        if not source_project_url:
            logger.warning("Source project url not found in the event.")
            return None
        parsed_source_url = parse_git_repo(potential_url=source_project_url)
        logger.info(
            f"Source: "
            f"repo={parsed_source_url.repo} "
            f"namespace={parsed_source_url.namespace} "
            f"url={source_project_url}."
        )

        target_project_url = nested_get(event, "project", "web_url")
        if not target_project_url:
            logger.warning("Target project url not found in the event.")
            return None
        parsed_target_url = parse_git_repo(potential_url=target_project_url)
        logger.info(
            f"Target: "
            f"repo={parsed_target_url.repo} "
            f"namespace={parsed_target_url.namespace} "
            f"url={target_project_url}."
        )

        commit_sha = nested_get(event, "object_attributes", "last_commit", "id")

        return MergeRequestGitlabEvent(
            action=GitlabEventAction[action],
            username=username,
            object_id=object_id,
            object_iid=object_iid,
            source_repo_namespace=parsed_source_url.namespace,
            source_repo_name=parsed_source_url.repo,
            target_repo_namespace=parsed_target_url.namespace,
            target_repo_name=parsed_target_url.repo,
            project_url=target_project_url,
            commit_sha=commit_sha,
        )

    @staticmethod
    def parse_pr_event(event) -> Optional[PullRequestGithubEvent]:
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
            event, "pull_request", "head", "repo", "owner", "login"
        )
        base_repo_name = nested_get(event, "pull_request", "head", "repo", "name")

        if not (base_repo_name and base_repo_namespace):
            logger.warning("No full name of the repository.")
            return None

        base_ref = nested_get(event, "pull_request", "head", "sha")
        if not base_ref:
            logger.warning("Ref where the PR is coming from is not set.")
            return None

        user_login = nested_get(event, "pull_request", "user", "login")
        if not user_login:
            logger.warning("No GitHub login name from event.")
            return None

        target_repo_namespace = nested_get(
            event, "pull_request", "base", "repo", "owner", "login"
        )
        target_repo_name = nested_get(event, "pull_request", "base", "repo", "name")
        logger.info(f"Target repo: {target_repo_namespace}/{target_repo_name}.")

        commit_sha = nested_get(event, "pull_request", "head", "sha")
        https_url = event["repository"]["html_url"]
        return PullRequestGithubEvent(
            action=PullRequestAction[action],
            pr_id=pr_id,
            base_repo_namespace=base_repo_namespace,
            base_repo_name=base_repo_name,
            base_ref=base_ref,
            target_repo_namespace=target_repo_namespace,
            target_repo_name=target_repo_name,
            project_url=https_url,
            commit_sha=commit_sha,
            user_login=user_login,
        )

    @staticmethod
    def parse_gitlab_push_event(event) -> Optional[PushGitlabEvent]:
        """
        Look into the provided event and see if it's one for a new push to the gitlab branch.
        https://docs.gitlab.com/ee/user/project/integrations/webhooks.html#push-events
        """

        if event.get("object_kind") != "push":
            return None

        raw_ref = event.get("ref")
        before = event.get("before")
        pusher = event.get("user_username")

        commits = event.get("commits")

        if not (raw_ref and commits and before and pusher):
            return None
        elif event.get("after").startswith("0000000"):
            logger.info(
                f"GitLab push event on '{raw_ref}' by {pusher} to delete branch"
            )
            return None

        number_of_commits = event.get("total_commits_count")

        if not number_of_commits:
            logger.warning("No number of commits info from event.")

        raw_ref = raw_ref.split("/", maxsplit=2)

        if not raw_ref:
            logger.warning("No ref info from event.")

        ref = raw_ref[-1]

        head_commit = commits[-1]["id"]

        if not raw_ref:
            logger.warning("No commit_id info from event.")

        logger.info(
            f"Gitlab push event on '{raw_ref}': {before[:8]} -> {head_commit[:8]} "
            f"by {pusher} "
            f"({number_of_commits} {'commit' if number_of_commits == 1 else 'commits'})"
        )

        project_url = nested_get(event, "project", "web_url")
        if not project_url:
            logger.warning("Target project url not found in the event.")
            return None
        parsed_url = parse_git_repo(potential_url=project_url)
        logger.info(
            f"Project: "
            f"repo={parsed_url.repo} "
            f"namespace={parsed_url.namespace} "
            f"url={project_url}."
        )

        return PushGitlabEvent(
            repo_namespace=parsed_url.namespace,
            repo_name=parsed_url.repo,
            git_ref=ref,
            project_url=project_url,
            commit_sha=head_commit,
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
        elif event.get("deleted"):
            logger.info(
                f"GitHub push event on '{raw_ref}' by {pusher} to delete branch"
            )
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
            git_ref=ref,
            project_url=repo_url,
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

        user_login = nested_get(event, "comment", "user", "login")
        if not user_login:
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
            user_login,
            comment,
        )

    @staticmethod
    def parse_gitlab_issue_comment_event(event) -> Optional[IssueCommentGitlabEvent]:
        """ Look into the provided event and see if it is Gitlab Issue comment event. """
        if event.get("object_kind") != "note":
            return None

        issue = event.get("issue")
        if not issue:
            return None

        issue_id = nested_get(event, "issue", "id")
        if not issue_id:
            logger.warning("No issue id from the event.")
            return None
        issue_iid = nested_get(event, "issue", "iid")
        if not issue_iid:
            logger.warning("No issue iid from the event.")
            return None
        comment = nested_get(event, "object_attributes", "note")
        if not comment:
            logger.warning("No note from the event.")
            return None

        state = nested_get(event, "issue", "state")
        if not state:
            logger.warning("No state from the event.")
            return None
        if state != "opened":
            return None
        action = nested_get(event, "object_attributes", "action")
        if action not in {"reopen", "update"}:
            action = state

        logger.info(
            f"Gitlab issue ID: {issue_id} IID: {issue_iid} comment: {comment!r} {action!r} event."
        )

        project_url = nested_get(event, "project", "web_url")
        if not project_url:
            logger.warning("Target project url not found in the event.")
            return None
        parsed_url = parse_git_repo(potential_url=project_url)
        logger.info(
            f"Project: "
            f"repo={parsed_url.repo} "
            f"namespace={parsed_url.namespace} "
            f"url={project_url}."
        )

        username = nested_get(event, "user", "username")
        if not username:
            logger.warning("No Gitlab username from event.")
            return None

        return IssueCommentGitlabEvent(
            action=GitlabEventAction[action],
            issue_id=issue_id,
            issue_iid=issue_iid,
            repo_namespace=parsed_url.namespace,
            repo_name=parsed_url.repo,
            project_url=project_url,
            username=username,
            comment=comment,
        )

    @staticmethod
    def parse_merge_request_comment_event(
        event,
    ) -> Optional[MergeRequestCommentGitlabEvent]:
        """ Look into the provided event and see if it is Gitlab MR comment event. """
        if event.get("object_kind") != "note":
            return None

        merge_request = event.get("merge_request")
        if not merge_request:
            return None

        state = nested_get(event, "merge_request", "state")
        if state != "opened":
            return None

        action = nested_get(event, "merge_request", "action")
        if action not in {"reopen", "update"}:
            action = state

        object_iid = nested_get(event, "merge_request", "iid")
        if not object_iid:
            logger.warning("No object iid from the event.")

        object_id = nested_get(event, "merge_request", "id")
        if not object_id:
            logger.warning("No object id from the event.")

        comment = nested_get(event, "object_attributes", "note")
        logger.info(
            f"Gitlab MR id#{object_id} iid#{object_iid} comment: {comment!r} {action!r} event."
        )

        source_project_url = nested_get(event, "merge_request", "source", "web_url")
        if not source_project_url:
            logger.warning("Source project url not found in the event.")
            return None
        parsed_source_url = parse_git_repo(potential_url=source_project_url)
        logger.info(
            f"Source: "
            f"repo={parsed_source_url.repo} "
            f"namespace={parsed_source_url.namespace} "
            f"url={source_project_url}."
        )

        target_project_url = nested_get(event, "project", "web_url")
        if not target_project_url:
            logger.warning("Target project url not found in the event.")
            return None
        parsed_target_url = parse_git_repo(potential_url=target_project_url)
        logger.info(
            f"Target: "
            f"repo={parsed_target_url.repo} "
            f"namespace={parsed_target_url.namespace} "
            f"url={target_project_url}."
        )

        username = nested_get(event, "user", "username")
        if not username:
            logger.warning("No Gitlab username from event.")
            return None

        commit_sha = nested_get(event, "merge_request", "last_commit", "id")
        if not commit_sha:
            logger.warning("No commit_sha from the event.")
            return None

        return MergeRequestCommentGitlabEvent(
            action=GitlabEventAction[action],
            object_id=object_id,
            object_iid=object_iid,
            source_repo_namespace=parsed_source_url.namespace,
            source_repo_name=parsed_source_url.repo,
            target_repo_namespace=parsed_target_url.namespace,
            target_repo_name=parsed_target_url.repo,
            project_url=target_project_url,
            username=username,
            comment=comment,
            commit_sha=commit_sha,
        )

    @staticmethod
    def parse_pull_request_comment_event(
        event,
    ) -> Optional[PullRequestCommentGithubEvent]:
        """ Look into the provided event and see if it is Github PR comment event. """
        if not nested_get(event, "issue", "pull_request"):
            return None

        pr_id = nested_get(event, "issue", "number")
        action = event.get("action")
        if action not in {"created", "edited"} or not pr_id:
            return None

        comment = nested_get(event, "comment", "body")
        logger.info(f"Github PR#{pr_id} comment: {comment!r} {action!r} event.")

        base_repo_namespace = nested_get(event, "issue", "user", "login")
        base_repo_name = nested_get(event, "repository", "name")
        if not (base_repo_name and base_repo_namespace):
            logger.warning("No full name of the repository.")
            return None

        user_login = nested_get(event, "comment", "user", "login")
        if not user_login:
            logger.warning("No GitHub login name from event.")
            return None
        if user_login in {"packit-as-a-service[bot]", "packit-as-a-service-stg[bot]"}:
            logger.debug("Our own comment.")
            return None

        target_repo_namespace = nested_get(event, "repository", "owner", "login")
        target_repo_name = nested_get(event, "repository", "name")

        logger.info(f"Target repo: {target_repo_namespace}/{target_repo_name}.")
        https_url = event["repository"]["html_url"]
        return PullRequestCommentGithubEvent(
            action=PullRequestCommentAction[action],
            pr_id=pr_id,
            base_repo_namespace=base_repo_namespace,
            base_repo_name=None,
            base_ref=None,  # the payload does not include this info
            target_repo_namespace=target_repo_namespace,
            target_repo_name=target_repo_name,
            project_url=https_url,
            user_login=user_login,
            comment=comment,
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
        https://developer.github.com/v3/activity/events/types/#releaseevent
        https://developer.github.com/v3/repos/releases/#get-a-single-release

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
            f"New release event {release_ref!r} for repo {repo_namespace}/{repo_name}."
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
        if not (branch and rev):
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
            git_ref=rev,
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
            pipeline_id=pipeline_id,
            result=result,
            environment=environment,
            message=message,
            log_url=log_url,
            copr_repo_name=copr_repo_name,
            copr_chroot=copr_chroot,
            tests=tests,
            repo_namespace=repo_namespace,
            repo_name=repo_name,
            git_ref=ref,
            project_url=https_url,
            commit_sha=commit_sha,
        )

    @staticmethod
    def parse_copr_event(event) -> Optional[AbstractCoprBuildEvent]:
        """ this corresponds to copr build event e.g:"""
        topic = event.get("topic")

        copr_build_cls: Type["AbstractCoprBuildEvent"]
        if topic == "org.fedoraproject.prod.copr.build.start":
            copr_build_cls = CoprBuildStartEvent
        elif topic == "org.fedoraproject.prod.copr.build.end":
            copr_build_cls = CoprBuildEndEvent
        else:
            # Topic not supported.
            return None

        logger.info(f"Copr event; {event.get('what')}")

        build_id = event.get("build")
        chroot = event.get("chroot")
        status = event.get("status")
        owner = event.get("owner")
        project_name = event.get("copr")
        pkg = event.get("pkg")
        timestamp = event.get("timestamp")

        return copr_build_cls.from_build_id(
            topic, build_id, chroot, status, owner, project_name, pkg, timestamp
        )

    @staticmethod
    def parse_koji_event(event) -> Optional[KojiBuildEvent]:
        if event.get("topic") != "org.fedoraproject.prod.buildsys.task.state.change":
            return None

        build_id = event.get("id")
        logger.info(f"Koji event: build_id={build_id}")

        state = nested_get(event, "info", "state")

        if not state:
            logger.debug("Cannot find build state.")
            return None

        state_enum = KojiBuildState(event.get("new")) if "new" in event else None
        old_state = KojiBuildState(event.get("old")) if "old" in event else None

        start_time = nested_get(event, "info", "start_time")
        completion_time = nested_get(event, "info", "completion_time")

        rpm_build_task_id = None
        for children in nested_get(event, "info", "children", default=[]):
            if children.get("method") == "buildArch":
                rpm_build_task_id = children.get("id")
                break

        return KojiBuildEvent(
            build_id=build_id,
            state=state_enum,
            old_state=old_state,
            start_time=start_time,
            completion_time=completion_time,
            rpm_build_task_id=rpm_build_task_id,
        )


class CentosEventParser:
    """
    Class responsible for parsing events received from CentOS infrastructure
    """

    def __init__(self):
        """
        self.event_mapping: dictionary mapping of topics to corresponding parsing methods

        ..note: action in partial is github counterpart value, as this value is used in code

            e.g.
            pagure pull-request.update == github pull-request.synchronize -> in code is used
            synchronize
        """
        self.event_mapping = {
            "pull-request.new": partial(self._pull_request_event, action="opened"),
            "pull-request.reopened": partial(
                self._pull_request_event, action="reopened"
            ),
            "pull-request.updated": partial(
                self._pull_request_event, action="synchronize"
            ),
            "pull-request.comment.added": partial(
                self._pull_request_comment, action="added"
            ),
            "pull-request.comment.edited": partial(
                self._pull_request_comment, action="edited"
            ),
            "pull-request.tag.added": partial(self._pull_request_label, action="added"),
            "git.receive": self._push_event,
        }

    def parse_event(self, event: dict) -> Optional[AbstractPagureEvent]:
        """
        Entry point for parsing event
        :param event: contains event data
        :return: event object or None
        """
        logger.debug(f"Parsing {event.get('topic')}")

        # e.g. "topic": "git.stg.centos.org/pull-request.tag.added"
        source, git_topic = event.get("topic").split("/")
        event["source"] = source
        event["git_topic"] = git_topic

        if git_topic not in self.event_mapping:
            logger.info(f"Event type {git_topic!r} is not processed.")
            return None

        return self.event_mapping[git_topic](event)

    @staticmethod
    def _pull_request_event(event: dict, action: str) -> PullRequestPagureEvent:
        pullrequest = event["pullrequest"]
        pr_id = pullrequest["id"]
        base_repo_namespace = pullrequest["repo_from"]["namespace"]
        base_repo_name = pullrequest["repo_from"]["name"]
        base_repo_owner = pullrequest["repo_from"]["user"]["name"]
        base_ref = pullrequest["branch"]
        target_repo = pullrequest["project"]["name"]
        https_url = f"https://{event['source']}/{pullrequest['project']['url_path']}"
        commit_sha = pullrequest["commit_stop"]
        pagure_login = pullrequest["user"]["name"]

        return PullRequestPagureEvent(
            action=PullRequestAction[action],
            pr_id=pr_id,
            base_repo_namespace=base_repo_namespace,
            base_repo_name=base_repo_name,
            base_repo_owner=base_repo_owner,
            base_ref=base_ref,
            target_repo=target_repo,
            project_url=https_url,
            commit_sha=commit_sha,
            user_login=pagure_login,
        )

    @staticmethod
    def _pull_request_comment(
        event: dict, action: str
    ) -> PullRequestCommentPagureEvent:
        event[
            "https_url"
        ] = f"https://{event['source']}/{event['pullrequest']['project']['url_path']}"
        action = PullRequestCommentAction.created.value
        pr_id = event["pullrequest"]["id"]
        base_repo_namespace = event["pullrequest"]["project"]["namespace"]
        base_repo_name = event["pullrequest"]["project"]["name"]
        base_repo_owner = event["pullrequest"]["repo_from"]["user"]["name"]
        target_repo = event["pullrequest"]["repo_from"]["name"]
        https_url = (
            f"https://{event['source']}/{event['pullrequest']['project']['url_path']}"
        )
        pagure_login = event["agent"]
        commit_sha = event["pullrequest"]["commit_stop"]

        # gets comment from event.
        # location differs based on topic (pull-request.comment.edited/pull-request.comment.added)
        if "edited" in event["git_topic"]:
            comment = event["comment"]["comment"]
        elif "added" in event["git_topic"]:
            comment = event["pullrequest"]["comments"][-1]["comment"]
        else:
            raise ValueError(
                f"Unknown comment location in response for {event['git_topic']}"
            )

        return PullRequestCommentPagureEvent(
            action=PullRequestCommentAction[action],
            pr_id=pr_id,
            base_repo_namespace=base_repo_namespace,
            base_repo_name=base_repo_name,
            base_repo_owner=base_repo_owner,
            base_ref=None,
            target_repo=target_repo,
            project_url=https_url,
            commit_sha=commit_sha,
            user_login=pagure_login,
            comment=comment,
        )

    @staticmethod
    def _pull_request_label(event: dict, action: str) -> PullRequestLabelPagureEvent:
        # Yes, API really uses "pull_request" in this case and "pullrequest" in others.
        # Fallback to "pullrequest" in case it gets 'synchronized' in future.
        pr: Dict = event.get("pull_request") or event["pullrequest"]

        return PullRequestLabelPagureEvent(
            action=PullRequestLabelAction[action],
            pr_id=pr["id"],
            base_repo_namespace=pr["project"]["namespace"],
            base_repo_name=pr["project"]["name"],
            base_repo_owner=pr["project"]["user"]["name"],
            base_ref=pr["branch"],
            commit_sha=pr["commit_stop"],
            project_url=f"https://{event['source']}/{pr['project']['url_path']}",
            labels=event["tags"],
        )

    @staticmethod
    def _push_event(event: dict) -> PushPagureEvent:
        return PushPagureEvent(
            repo_namespace=event["repo"]["namespace"],
            repo_name=event["repo"]["name"],
            git_ref=f"refs/head/{event['branch']}",
            project_url=f"https://{event['source']}/{event['repo']['url_path']}",
            commit_sha=event["end_commit"],
        )
