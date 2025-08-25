# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
Parser is transforming github JSONs into `events` objects
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from os import getenv
from typing import Any, Callable, ClassVar, Optional, Union

from ogr.parsing import RepoUrl, parse_git_repo
from packit.config import JobConfigTriggerType
from packit.constants import DISTGIT_INSTANCES
from packit.utils import nested_get

from packit_service.config import Deployment, ServiceConfig
from packit_service.constants import (
    TESTING_FARM_INSTALLABILITY_TEST_URL,
    KojiBuildState,
    KojiTaskState,
)
from packit_service.events import (
    abstract,
    anitya,
    copr,
    forgejo,
    github,
    gitlab,
    koji,
    openscanhub,
    pagure,
    testing_farm,
    vm_image,
)
from packit_service.events.enums import (
    IssueCommentAction,
    PullRequestAction,
    PullRequestCommentAction,
)
from packit_service.models import (
    GitBranchModel,
    ProjectEventModel,
    ProjectReleaseModel,
    PullRequestModel,
    TestingFarmResult,
    TFTTestRunTargetModel,
)
from packit_service.worker.handlers.abstract import MAP_CHECK_PREFIX_TO_HANDLER
from packit_service.worker.helpers.build import CoprBuildJobHelper, KojiBuildJobHelper
from packit_service.worker.helpers.testing_farm import TestingFarmClient

logger = logging.getLogger(__name__)


class PackitParserException(Exception):
    pass


@dataclass
class _GitlabCommonData:
    actor: str
    project_url: str
    parsed_url: Optional[RepoUrl]
    ref: str
    head_commit: dict
    commit_sha_before: str

    @property
    def commit_sha(self) -> str:
        return self.head_commit.get("id")

    @property
    def commit_title(self) -> str:
        return self.head_commit.get("title")

    @property
    def commit_message(self) -> str:
        return self.head_commit.get("message")


@dataclass
class _TestingFarmCommonData:
    project_url: str
    ref: str
    result: TestingFarmResult
    summary: str
    copr_build_id: str
    copr_chroot: str
    compose: str
    log_url: str
    created: datetime
    identifier: Optional[str]


class Parser:
    """
    Once we receive a new event (GitHub/GitLab webhook) for every event
    we need to have method inside the `Parser` class to create objects defined in `event.py`.
    """

    @staticmethod
    def parse_event(
        event: dict,
    ) -> Optional[
        Union[
            abstract.comment.Commit,
            anitya.NewHotness,
            anitya.VersionUpdate,
            copr.CoprBuild,
            github.check.Commit,
            github.check.PullRequest,
            github.check.Release,
            github.pr.Comment,
            github.pr.Action,
            github.issue.Comment,
            github.installation.Installation,
            github.push.Commit,
            github.release.Release,
            gitlab.issue.Comment,
            gitlab.mr.Comment,
            gitlab.mr.Action,
            gitlab.pipeline.Pipeline,
            gitlab.push.Commit,
            gitlab.push.Tag,
            gitlab.release.Release,
            koji.result.Build,
            koji.tag.Build,
            koji.result.Task,
            openscanhub.task.Finished,
            openscanhub.task.Started,
            pagure.pr.Comment,
            pagure.pr.Flag,
            pagure.pr.Action,
            pagure.push.Commit,
            testing_farm.Result,
            vm_image.Result,
        ]
    ]:
        """
        Try to parse all JSONs that we process.

        When reacting to fedmsg events, be aware that we are squashing the structure
        so we take only `body` with the `topic` key included.
        See: https://github.com/packit/packit-service-fedmsg/blob/
             e53586bf7ace0c46fd6812fe8dc11491e5e6cf41/packit_service_fedmsg/consumer.py#L137

        :param event: JSON from GitHub/GitLab
        :return: event object
        """

        if not event:
            logger.warning("No event to process!")
            return None

        for response in (
            parser(event)
            for parser in (
                Parser.parse_pr_event,
                Parser.parse_pull_request_comment_event,
                Parser.parse_issue_comment_event,
                Parser.parse_release_event,
                Parser.parse_github_push_event,
                Parser.parse_check_rerun_event,
                Parser.parse_installation_event,
                Parser.parse_testing_farm_results_event,
                Parser.parse_copr_event,
                Parser.parse_mr_event,
                Parser.parse_koji_task_event,
                Parser.parse_koji_build_event,
                Parser.parse_koji_build_tag_event,
                Parser.parse_merge_request_comment_event,
                Parser.parse_gitlab_issue_comment_event,
                Parser.parse_gitlab_commit_comment_event,
                Parser.parse_gitlab_push_event,
                Parser.parse_pipeline_event,
                Parser.parse_pagure_push_event,
                Parser.parse_pagure_pr_flag_event,
                Parser.parse_pagure_pull_request_comment_event,
                Parser.parse_new_hotness_update_event,
                Parser.parse_gitlab_release_event,
                Parser.parse_gitlab_tag_push_event,
                Parser.parse_anitya_version_update_event,
                Parser.parse_openscanhub_task_finished_event,
                Parser.parse_openscanhub_task_started_event,
                Parser.parse_commit_comment_event,
                Parser.parse_pagure_pull_request_event,
            )
        ):
            if response:
                return response

        logger.debug("We don't process this event.")
        return None

    @staticmethod
    def parse_mr_event(event) -> Optional[gitlab.mr.Action]:
        """Look into the provided event and see if it's one for a new gitlab MR."""
        if event.get("object_kind") != "merge_request":
            return None

        state = event["object_attributes"]["state"]
        if state not in {"opened", "closed"}:
            return None
        action = nested_get(event, "object_attributes", "action")
        if action not in {"reopen", "update"}:
            action = state

        actor = event["user"]["username"]
        if not actor:
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
        source_repo_branch = nested_get(event, "object_attributes", "source_branch")
        logger.info(
            f"Source: "
            f"url={source_project_url} "
            f"namespace={parsed_source_url.namespace} "
            f"repo={parsed_source_url.repo} "
            f"branch={source_repo_branch}.",
        )

        target_project_url = nested_get(event, "project", "web_url")
        if not target_project_url:
            logger.warning("Target project url not found in the event.")
            return None
        parsed_target_url = parse_git_repo(potential_url=target_project_url)
        target_repo_branch = nested_get(event, "object_attributes", "target_branch")
        logger.info(
            f"Target: "
            f"url={target_project_url} "
            f"namespace={parsed_target_url.namespace} "
            f"repo={parsed_target_url.repo} "
            f"branch={target_repo_branch}.",
        )

        commit_sha = nested_get(event, "object_attributes", "last_commit", "id")
        oldrev = nested_get(event, "object_attributes", "oldrev")

        title = nested_get(event, "object_attributes", "title")
        description = nested_get(event, "object_attributes", "description")
        url = nested_get(event, "object_attributes", "url")

        return gitlab.mr.Action(
            action=gitlab.enums.Action[action],
            actor=actor,
            object_id=object_id,
            object_iid=object_iid,
            source_repo_namespace=parsed_source_url.namespace,
            source_repo_name=parsed_source_url.repo,
            source_repo_branch=source_repo_branch,
            source_project_url=source_project_url,
            target_repo_namespace=parsed_target_url.namespace,
            target_repo_name=parsed_target_url.repo,
            target_repo_branch=target_repo_branch,
            project_url=target_project_url,
            commit_sha=commit_sha,
            commit_sha_before=oldrev,
            title=title,
            description=description,
            url=url,
        )

    @staticmethod
    def parse_pr_event(event) -> Optional[github.pr.Action]:
        """Look into the provided event and see if it's one for a new github PR."""
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
            event,
            "pull_request",
            "head",
            "repo",
            "owner",
            "login",
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
            event,
            "pull_request",
            "base",
            "repo",
            "owner",
            "login",
        )
        target_repo_name = nested_get(event, "pull_request", "base", "repo", "name")
        logger.info(f"Target repo: {target_repo_namespace}/{target_repo_name}.")

        commit_sha = nested_get(event, "pull_request", "head", "sha")
        commit_sha_before = event.get("before")
        https_url = event["repository"]["html_url"]
        return github.pr.Action(
            action=PullRequestAction[action],
            pr_id=pr_id,
            base_repo_namespace=base_repo_namespace,
            base_repo_name=base_repo_name,
            base_ref=base_ref,
            target_repo_namespace=target_repo_namespace,
            target_repo_name=target_repo_name,
            project_url=https_url,
            commit_sha=commit_sha,
            commit_sha_before=commit_sha_before,
            actor=user_login,
        )

    @staticmethod
    def parse_gitlab_release_event(event) -> Optional[gitlab.release.Release]:
        """
        Look into the provided event and see if it's one for a new push to the gitlab branch.
        https://docs.gitlab.com/ee/user/project/integrations/webhooks.html#release-events
        """

        if event.get("object_kind") != "release":
            return None

        if event.get("action") != "create":
            return None

        project_url = nested_get(event, "project", "web_url")
        if not project_url:
            logger.warning("Target project url not found in the event.")
            return None
        parsed_url = parse_git_repo(potential_url=project_url)
        tag_name = event.get("tag")

        logger.info(
            f"Gitlab release with tag {tag_name} event on Project: "
            f"repo={parsed_url.repo} "
            f"namespace={parsed_url.namespace} "
            f"url={project_url}.",
        )
        commit_sha = nested_get(event, "commit", "id")

        return gitlab.release.Release(
            repo_namespace=parsed_url.namespace,
            repo_name=parsed_url.repo,
            project_url=project_url,
            tag_name=tag_name,
            git_ref=tag_name,
            commit_sha=commit_sha,
        )

    @staticmethod
    def is_gitlab_push_a_create_event(event) -> bool:
        """The given push event is a create push event?

        Returns:
            True if the push event is a create
            branch/tag event and not a delete one.
            False otherwise.
        """

        ref = event.get("ref")
        actor = event.get("user_username")

        if not (ref and event.get("commits") and event.get("before") and actor):
            return False

        if event.get("after").startswith("0000000"):
            logger.info(f"GitLab push event on '{ref}' by {actor} to delete branch/tag")
            return False

        return True

    @staticmethod
    def get_gitlab_push_common_data(event) -> _GitlabCommonData:
        """A gitlab push and a gitlab tag push have many common data
        parsable in the same way.

        Returns:
            An instance of `_GitlabCommonData` data class.

        Raises:
            PackitParserException
        """
        if not (raw_ref := event.get("ref")):
            raise PackitParserException("No ref info from event.")
        before = event.get("before")
        checkout_sha = event.get("checkout_sha")
        actor = event.get("user_username")
        commits = event.get("commits", [])
        number_of_commits = event.get("total_commits_count")

        if not Parser.is_gitlab_push_a_create_event(event):
            raise PackitParserException(
                "Event is not a push create event, stop parsing",
            )

        # The first item in the list should be the head (newest) commit,
        # but rather not assume anything and select the "checkout_sha" one.
        head_commit = next(c for c in commits if c["id"] == checkout_sha)

        logger.info(
            f"Gitlab push event on '{raw_ref}': {before[:8]} -> {checkout_sha[:8]} "
            f"by {actor} "
            f"({number_of_commits} {'commit' if number_of_commits == 1 else 'commits'})",
        )

        if not (project_url := nested_get(event, "project", "web_url")):
            raise PackitParserException(
                "Target project url not found in the event, stop parsing",
            )
        parsed_url = parse_git_repo(potential_url=project_url)
        logger.info(
            f"Project: repo={parsed_url.repo} namespace={parsed_url.namespace} url={project_url}.",
        )
        ref = raw_ref.split("/", maxsplit=2)[-1]

        return _GitlabCommonData(
            actor=actor,
            project_url=project_url,
            parsed_url=parsed_url,
            ref=ref,
            head_commit=head_commit,
            commit_sha_before=before,
        )

    @staticmethod
    def parse_gitlab_tag_push_event(event) -> Optional[gitlab.push.Tag]:
        """
        Look into the provided event and see if it's one for a new push to the gitlab branch.
        https://docs.gitlab.com/ee/user/project/integrations/webhooks.html#tag-events
        """

        if event.get("object_kind") != "tag_push":
            return None

        try:
            data = Parser.get_gitlab_push_common_data(event)
        except PackitParserException as e:
            logger.info(e)
            return None

        logger.info(
            f"Gitlab tag push {data.ref} event with commit_sha {data.head_commit.get('id')} "
            f"by actor {data.actor} on Project: "
            f"repo={data.parsed_url.repo} "
            f"namespace={data.parsed_url.namespace} "
            f"url={data.project_url}.",
        )

        return gitlab.push.Tag(
            repo_namespace=data.parsed_url.namespace,
            repo_name=data.parsed_url.repo,
            actor=data.actor,
            git_ref=data.ref,
            project_url=data.project_url,
            commit_sha=data.commit_sha,
            title=data.commit_title,
            message=data.commit_message,
        )

    @staticmethod
    def parse_gitlab_push_event(event) -> Optional[gitlab.push.Commit]:
        """
        Look into the provided event and see if it's one for a new push to the gitlab branch.
        https://docs.gitlab.com/ee/user/project/integrations/webhooks.html#push-events
        """

        if event.get("object_kind") != "push":
            return None

        try:
            data = Parser.get_gitlab_push_common_data(event)
        except PackitParserException as e:
            logger.info(e)
            return None

        return gitlab.push.Commit(
            repo_namespace=data.parsed_url.namespace,
            repo_name=data.parsed_url.repo,
            git_ref=data.ref,
            project_url=data.project_url,
            commit_sha=data.commit_sha,
            commit_sha_before=data.commit_sha_before,
        )

    @staticmethod
    def parse_github_push_event(event) -> Optional[github.push.Commit]:
        """
        Look into the provided event and see if it's one for a new push to the github branch.
        """
        raw_ref = event.get("ref")
        before = event.get("before")
        pusher = nested_get(event, "pusher", "name")

        # https://developer.github.com/v3/activity/events/types/#pushevent
        # > Note: The webhook payload example following the table differs
        # > significantly from the Events API payload described in the table.
        head_commit = event.get("head") or event.get("after") or event.get("head_commit")

        if not (raw_ref and head_commit and before and pusher):
            return None

        if event.get("deleted"):
            logger.info(
                f"GitHub push event on '{raw_ref}' by {pusher} to delete branch",
            )
            return None

        number_of_commits = event.get("size")
        if number_of_commits is None and "commits" in event:
            number_of_commits = len(event.get("commits"))

        ref = raw_ref.split("/", maxsplit=2)[-1]

        logger.info(
            f"GitHub push event on '{raw_ref}': {before[:8]} -> {head_commit[:8]} "
            f"by {pusher} "
            f"({number_of_commits} {'commit' if number_of_commits == 1 else 'commits'})",
        )

        repo_namespace = nested_get(event, "repository", "owner", "login")
        repo_name = nested_get(event, "repository", "name")

        if not (repo_namespace and repo_name):
            logger.warning("No full name of the repository.")
            return None

        repo_url = nested_get(event, "repository", "html_url")

        return github.push.Commit(
            repo_namespace=repo_namespace,
            repo_name=repo_name,
            git_ref=ref,
            project_url=repo_url,
            commit_sha=head_commit,
            commit_sha_before=before,
        )

    @staticmethod
    def parse_github_comment_event(
        event,
    ) -> Optional[Union[github.pr.Comment, github.issue.Comment]]:
        """Check whether the comment event from GitHub comes from a PR or issue,
        and parse accordingly.
        """
        if nested_get(event, "issue", "pull_request"):
            return Parser.parse_pull_request_comment_event(event)
        return Parser.parse_issue_comment_event(event)

    @staticmethod
    def parse_pull_request_comment_event(
        event,
    ) -> Optional[github.pr.Comment]:
        """Look into the provided event and see if it is Github PR comment event."""
        # This check is redundant when the method is called from parse_github_comment_event(),
        # but it's needed when called from parse_event().
        if not nested_get(event, "issue", "pull_request"):
            return None

        pr_id = nested_get(event, "issue", "number")
        action = event.get("action")
        if action not in {"created", "edited"} or not pr_id:
            return None

        comment = nested_get(event, "comment", "body")
        comment_id = nested_get(event, "comment", "id")
        logger.info(
            f"Github PR#{pr_id} comment: {comment!r} id#{comment_id} {action!r} event.",
        )

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
        return github.pr.Comment(
            action=PullRequestCommentAction[action],
            pr_id=pr_id,
            base_repo_namespace=base_repo_namespace,
            base_repo_name=None,
            base_ref=None,  # the payload does not include this info
            target_repo_namespace=target_repo_namespace,
            target_repo_name=target_repo_name,
            project_url=https_url,
            actor=user_login,
            comment=comment,
            comment_id=comment_id,
        )

    @staticmethod
    def parse_issue_comment_event(event) -> Optional[github.issue.Comment]:
        """Look into the provided event and see if it is Github issue comment event."""
        # This check is redundant when the method is called from parse_github_comment_event(),
        # but it's needed when called from parse_event().
        if nested_get(event, "issue", "pull_request"):
            return None

        issue_id = nested_get(event, "issue", "number")
        action = event.get("action")
        if action != "created" or not issue_id:
            return None

        comment = nested_get(event, "comment", "body")
        comment_id = nested_get(event, "comment", "id")
        if not (comment and comment_id):
            logger.warning("No comment or comment id from the event.")
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
        return github.issue.Comment(
            IssueCommentAction[action],
            issue_id,
            base_repo_namespace,
            base_repo_name,
            target_repo,
            https_url,
            user_login,
            comment,
            comment_id,
        )

    @staticmethod
    def parse_commit_comment_event(
        event,
    ) -> Optional[abstract.comment.Commit]:
        """Look into the provided event and see if it is Github commit comment event."""
        if not (commit_sha := nested_get(event, "comment", "commit_id")):
            return None

        comment = nested_get(event, "comment", "body")
        comment_id = nested_get(event, "comment", "id")
        logger.info(
            f"Github commit comment on #{commit_sha}: {comment!r} id#{comment_id} event.",
        )

        user_login = nested_get(event, "comment", "user", "login")
        if not user_login:
            logger.warning("No GitHub login name from event.")
            return None
        if user_login in {"packit-as-a-service[bot]", "packit-as-a-service-stg[bot]"}:
            logger.debug("Our own comment.")
            return None

        repo_namespace = nested_get(event, "repository", "owner", "login")
        repo_name = nested_get(event, "repository", "name")

        logger.info(f"Repo: {repo_namespace}/{repo_name}.")
        https_url = event["repository"]["html_url"]
        return github.commit.Comment(
            commit_sha=commit_sha,
            repo_namespace=repo_namespace,
            repo_name=repo_name,
            project_url=https_url,
            actor=user_login,
            comment=comment,
            comment_id=comment_id,
        )

    @staticmethod
    def parse_gitlab_comment_event(
        event,
    ) -> Optional[
        Union[
            abstract.comment.Commit,
            gitlab.mr.Comment,
            gitlab.issue.Comment,
        ]
    ]:
        """Check whether the comment event from Gitlab comes from an MR or issue,
        and parse accordingly.
        """
        if event.get("merge_request"):
            return Parser.parse_merge_request_comment_event(event)

        if event.get("commit"):
            return Parser.parse_gitlab_commit_comment_event(event)

        return Parser.parse_gitlab_issue_comment_event(event)

    @staticmethod
    def parse_gitlab_issue_comment_event(event) -> Optional[gitlab.issue.Comment]:
        """Look into the provided event and see if it is Gitlab Issue comment event."""
        if event.get("object_kind") != "note":
            return None

        issue = event.get("issue")
        if not issue:
            return None

        issue_id = nested_get(event, "issue", "iid")
        if not issue_id:
            logger.warning("No issue id from the event.")
            return None
        comment = nested_get(event, "object_attributes", "note")
        comment_id = nested_get(event, "object_attributes", "id")
        if not (comment and comment_id):
            logger.warning("No note or note id from the event.")
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
            f"Gitlab issue ID: {issue_id} comment: {comment!r} {action!r} event.",
        )

        project_url = nested_get(event, "project", "web_url")
        if not project_url:
            logger.warning("Target project url not found in the event.")
            return None
        parsed_url = parse_git_repo(potential_url=project_url)
        logger.info(
            f"Project: repo={parsed_url.repo} namespace={parsed_url.namespace} url={project_url}.",
        )

        actor = nested_get(event, "user", "username")
        if not actor:
            logger.warning("No Gitlab username from event.")
            return None

        return gitlab.issue.Comment(
            action=gitlab.enums.Action[action],
            issue_id=issue_id,
            repo_namespace=parsed_url.namespace,
            repo_name=parsed_url.repo,
            project_url=project_url,
            actor=actor,
            comment=comment,
            comment_id=comment_id,
        )

    @staticmethod
    def parse_merge_request_comment_event(
        event,
    ) -> Optional[gitlab.mr.Comment]:
        """Look into the provided event and see if it is Gitlab MR comment event."""
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
        comment_id = nested_get(event, "object_attributes", "id")
        logger.info(
            f"Gitlab MR id#{object_id} iid#{object_iid} comment: {comment!r} id#{comment_id} "
            f"{action!r} event.",
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
            f"url={source_project_url}.",
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
            f"url={target_project_url}.",
        )

        actor = nested_get(event, "user", "username")
        if not actor:
            logger.warning("No Gitlab username from event.")
            return None

        if actor in {"packit-as-a-service", "packit-as-a-service-stg"}:
            logger.debug("Our own comment.")
            return None

        commit_sha = nested_get(event, "merge_request", "last_commit", "id")
        if not commit_sha:
            logger.warning("No commit_sha from the event.")
            return None

        return gitlab.mr.Comment(
            action=gitlab.enums.Action[action],
            object_id=object_id,
            object_iid=object_iid,
            source_repo_namespace=parsed_source_url.namespace,
            source_repo_name=parsed_source_url.repo,
            target_repo_namespace=parsed_target_url.namespace,
            target_repo_name=parsed_target_url.repo,
            project_url=target_project_url,
            actor=actor,
            comment=comment,
            commit_sha=commit_sha,
            comment_id=comment_id,
        )

    @staticmethod
    def parse_gitlab_commit_comment_event(
        event,
    ) -> Optional[abstract.comment.Commit]:
        """Look into the provided event and see if it is Gitlab commit comment event."""
        if event.get("object_kind") != "note":
            return None

        commit = event.get("commit")
        if not commit:
            return None

        commit_sha = nested_get(event, "commit", "id")

        comment = nested_get(event, "object_attributes", "note")
        comment_id = nested_get(event, "object_attributes", "id")
        logger.info(
            f"Gitlab commit comment on #{commit_sha}: {comment!r} id#{comment_id}  event.",
        )

        project_url = nested_get(event, "project", "web_url")
        if not project_url:
            logger.warning("Project url not found in the event.")
            return None

        parsed_url = parse_git_repo(potential_url=project_url)
        logger.info(
            f"Project: repo={parsed_url.repo} namespace={parsed_url.namespace} url={project_url}.",
        )

        actor = nested_get(event, "user", "username")
        if not actor:
            logger.warning("No Gitlab username from event.")
            return None

        if actor in {"packit-as-a-service", "packit-as-a-service-stg"}:
            logger.debug("Our own comment.")
            return None

        return gitlab.commit.Comment(
            commit_sha=commit_sha,
            repo_namespace=parsed_url.namespace,
            repo_name=parsed_url.repo,
            project_url=project_url,
            actor=actor,
            comment=comment,
            comment_id=comment_id,
        )

    @staticmethod
    def parse_check_name(
        check_name: str,
        db_project_event: ProjectEventModel,
    ) -> Optional[tuple[str, str, str]]:
        """
        Parse the given name of the check run.

        Check name examples:
        "rpm-build:fedora-34-x86_64"
        "rpm-build:fedora-34-x86_64:identifier"
        "rpm-build:main:fedora-34-x86_64:identifier"
        "propose-downstream:f35"

        For the build and test runs, if the project event is release/commit, the branch
        name or release name is included in the check name - it can be ignored,
        since we are having the DB project event (obtained via external ID of the check).

        Returns:
            tuple of job name (e.g. rpm-build), target and identifier obtained from check run
            (or None if the name cannot be parsed)
        """
        check_name_parts = check_name.split(":", maxsplit=3)
        if len(check_name_parts) < 1:
            logger.warning(f"{check_name} cannot be parsed")
            return None
        check_name_job = check_name_parts[0]

        if check_name_job not in MAP_CHECK_PREFIX_TO_HANDLER:
            logger.warning(
                f"{check_name_job} not in {list(MAP_CHECK_PREFIX_TO_HANDLER.keys())}",
            )
            return None

        check_name_target, check_name_identifier = None, None
        db_project_object = db_project_event.get_project_event_object()

        if len(check_name_parts) == 2:
            _, check_name_target = check_name_parts
        elif len(check_name_parts) == 3:
            build_test_job_names = (
                CoprBuildJobHelper.status_name_build,
                CoprBuildJobHelper.status_name_test,
                KojiBuildJobHelper.status_name_build,
            )
            if (
                check_name_job in build_test_job_names
                and db_project_object.job_config_trigger_type
                in (
                    JobConfigTriggerType.commit,
                    JobConfigTriggerType.release,
                )
            ):
                (
                    _,
                    _,
                    check_name_target,
                ) = check_name_parts
            else:
                (
                    _,
                    check_name_target,
                    check_name_identifier,
                ) = check_name_parts
        elif len(check_name_parts) == 4:
            (
                _,
                _,
                check_name_target,
                check_name_identifier,
            ) = check_name_parts
        else:
            logger.warning(f"{check_name_job} cannot be parsed")
            check_name_job = None

        if not (check_name_job and check_name_target):
            logger.warning(
                f"We were not able to parse the job and target "
                f"from the check run name {check_name}.",
            )
            return None

        logger.info(
            f"Check name job: {check_name_job}, check name target: {check_name_target}, "
            f"check name identifier: {check_name_identifier}",
        )

        return check_name_job, check_name_target, check_name_identifier

    @staticmethod
    def parse_check_rerun_event(
        event,
    ) -> Optional[Union[github.check.PullRequest, github.check.Release, github.check.Commit]]:
        """Look into the provided event and see if it is Github check rerun event."""
        if not (nested_get(event, "check_run") and nested_get(event, "action") == "rerequested"):
            return None

        check_name = nested_get(event, "check_run", "name")
        logger.info(f"Github check run {check_name} rerun event.")

        deployment = ServiceConfig.get_service_config().deployment
        app = nested_get(event, "check_run", "app", "slug")
        if (deployment == Deployment.prod and app != "packit-as-a-service") or (
            deployment == Deployment.stg and app != "packit-as-a-service-stg"
        ):
            logger.warning(f"Check run created by {app} and not us.")
            return None

        external_id = nested_get(event, "check_run", "external_id")

        if not external_id:
            logger.warning(
                "No external_id to identify the original project event provided.",
            )
            return None

        db_project_event = ProjectEventModel.get_by_id(int(external_id))
        if not db_project_event:
            logger.warning(f"Job project event with ID {external_id} not found.")
            return None

        db_project_object = db_project_event.get_project_event_object()
        logger.info(f"Original project event: {db_project_event}")
        logger.info(f"Original project object: {db_project_object}")

        parse_result = Parser.parse_check_name(check_name, db_project_event)
        if parse_result is None:
            return None

        check_name_job, check_name_target, check_name_identifier = parse_result

        repo_namespace = nested_get(event, "repository", "owner", "login")
        repo_name = nested_get(event, "repository", "name")
        actor = nested_get(event, "sender", "login")

        if not (repo_namespace and repo_name):
            logger.warning("No full name of the repository.")
            return None

        https_url = event["repository"]["html_url"]

        commit_sha = nested_get(event, "check_run", "head_sha")

        event = None
        if isinstance(db_project_object, PullRequestModel):
            event = github.check.PullRequest(
                repo_namespace=repo_namespace,
                repo_name=repo_name,
                project_url=https_url,
                commit_sha=commit_sha,
                pr_id=db_project_object.pr_id,
                check_name_job=check_name_job,
                check_name_target=check_name_target,
                db_project_event=db_project_event,
                actor=actor,
                job_identifier=check_name_identifier,
            )

        elif isinstance(db_project_object, ProjectReleaseModel):
            event = github.check.Release(
                repo_namespace=repo_namespace,
                repo_name=repo_name,
                project_url=https_url,
                commit_sha=commit_sha,
                tag_name=db_project_object.tag_name,
                check_name_job=check_name_job,
                check_name_target=check_name_target,
                db_project_event=db_project_event,
                actor=actor,
                job_identifier=check_name_identifier,
            )

        elif isinstance(db_project_object, GitBranchModel):
            event = github.check.Commit(
                repo_namespace=repo_namespace,
                repo_name=repo_name,
                project_url=https_url,
                commit_sha=commit_sha,
                git_ref=db_project_object.name,
                check_name_job=check_name_job,
                check_name_target=check_name_target,
                db_project_event=db_project_event,
                actor=actor,
                job_identifier=check_name_identifier,
            )

        return event

    @staticmethod
    def parse_installation_event(event) -> Optional[github.installation.Installation]:
        """Look into the provided event and see if it is Github App installation details."""
        # Check if installation key in JSON isn't enough, we have to check the account as well
        if not nested_get(event, "installation", "account"):
            return None

        action = event["action"]
        if action != "created":
            # We're currently not interested in removed/deleted/updated event.
            return None
        installation_id = event["installation"]["id"]
        # if action == 'created' then repos are in repositories
        repositories = event.get("repositories", [])
        repo_names = [repo["full_name"] for repo in repositories]

        logger.info(f"Github App installation {action!r} event. id: {installation_id}")
        logger.debug(
            f"account: {event['installation']['account']}, "
            f"repositories: {repo_names}, sender: {event['sender']}",
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

        return github.installation.Installation(
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
    def parse_release_event(event) -> Optional[github.release.Release]:
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
            f"New release event {release_ref!r} for repo {repo_namespace}/{repo_name}.",
        )
        https_url = event["repository"]["html_url"]
        return github.release.Release(repo_namespace, repo_name, release_ref, https_url)

    @staticmethod
    def parse_pagure_push_event(event) -> Optional[pagure.push.Commit]:
        """this corresponds to dist-git event when someone pushes new commits"""
        topic = event.get("topic")
        if topic != "org.fedoraproject.prod.pagure.git.receive":
            return None

        logger.info(f"Dist-git commit event, topic: {topic}")

        dg_repo_namespace = nested_get(event, "repo", "namespace")
        dg_repo_name = nested_get(event, "repo", "name")

        if not (dg_repo_namespace and dg_repo_name):
            logger.warning("No full name of the repository.")
            return None

        dg_branch = nested_get(event, "branch")
        dg_commit = nested_get(event, "end_commit")
        if not (dg_branch and dg_commit):
            logger.warning("Target branch/rev for the new commits is not set.")
            return None

        username = nested_get(event, "agent")

        logger.info(
            f"New commits added to dist-git repo {dg_repo_namespace}/{dg_repo_name},"
            f"rev: {dg_commit}, branch: {dg_branch}",
        )

        dg_base_url = getenv("DISTGIT_URL", DISTGIT_INSTANCES["fedpkg"].url)
        dg_project_url = f"{dg_base_url}{dg_repo_namespace}/{dg_repo_name}"

        dg_pr_id = nested_get(event, "pull_request_id")

        return pagure.push.Commit(
            repo_namespace=dg_repo_namespace,
            repo_name=dg_repo_name,
            git_ref=dg_branch,
            project_url=dg_project_url,
            commit_sha=dg_commit,
            committer=username,
            pr_id=dg_pr_id,
        )

    @staticmethod
    def parse_data_from_testing_farm(
        tft_test_run: TFTTestRunTargetModel,
        event: dict[Any, Any],
    ) -> _TestingFarmCommonData:
        """Parses common data from testing farm response.

        Such common data is environment, os, summary and others.

        Args:
            tft_test_run (TFTTestRunTargetModel): Entry of the related test run in DB.
            event (dict): Response from testing farm converted to a dict.

        Returns:
            An instance of `_TestingFarmCommonData` data class.
        """
        tf_state = event.get("state")
        tf_result = nested_get(event, "result", "overall")

        logger.debug(f"TF payload: state = {tf_state}, result['overall'] = {tf_result}")

        # error and complete are the end states
        if tf_state not in ("complete", "error"):
            result = TestingFarmResult.from_string(tf_state or "unknown")
        else:
            result = TestingFarmResult.from_string(tf_result or tf_state or "unknown")

        summary: str = nested_get(event, "result", "summary") or ""
        env: dict = nested_get(event, "environments_requested", 0, default={})
        compose: str = nested_get(env, "os", "compose")
        created: str = event.get("created")
        identifier: Optional[str] = None
        created_dt: Optional[datetime] = None
        if created:
            created_dt = datetime.fromisoformat(created)
            created_dt = created_dt.replace(tzinfo=timezone.utc)

        ref: str = nested_get(event, "test", "fmf", "ref")
        fmf_url: str = nested_get(event, "test", "fmf", "url")

        # ["test"]["fmf"]["ref"] contains ref to the TF test, i.e. "master",
        # but we need the original commit_sha to be able to continue
        if tft_test_run:
            ref = tft_test_run.commit_sha
            identifier = tft_test_run.identifier

        if fmf_url == TESTING_FARM_INSTALLABILITY_TEST_URL:
            # There are no artifacts in install-test results
            copr_build_id = copr_chroot = ""
            summary = {
                TestingFarmResult.passed: "Installation passed",
                TestingFarmResult.failed: "Installation failed",
            }.get(result, summary)
        else:
            artifact: dict = nested_get(env, "artifacts", 0, default={})
            a_type: str = artifact.get("type")
            if a_type == "fedora-copr-build":
                copr_build_id = artifact["id"].split(":")[0]
                copr_chroot = artifact["id"].split(":")[1]
            else:
                logger.debug(f"{a_type} != fedora-copr-build")
                copr_build_id = copr_chroot = ""

        if not copr_chroot and tft_test_run:
            copr_chroot = tft_test_run.target

        # ["test"]["fmf"]["url"] contains PR's source/fork url or TF's install test url.
        # We need the original/base project url stored in db.
        if tft_test_run and tft_test_run.data and "base_project_url" in tft_test_run.data:
            project_url = tft_test_run.data["base_project_url"]
        else:
            project_url = fmf_url if fmf_url != TESTING_FARM_INSTALLABILITY_TEST_URL else None

        log_url: str = nested_get(event, "run", "artifacts")

        return _TestingFarmCommonData(
            project_url=project_url,
            ref=ref,
            result=result,
            summary=summary,
            copr_build_id=copr_build_id,
            copr_chroot=copr_chroot,
            compose=compose,
            log_url=log_url,
            created=created_dt,
            identifier=identifier,
        )

    @staticmethod
    def parse_testing_farm_results_event(
        event: dict,
    ) -> Optional[testing_farm.Result]:
        """this corresponds to testing farm results event"""
        if event.get("source") != "testing-farm" or not event.get("request_id"):
            return None

        request_id: str = event["request_id"]
        logger.info(f"Testing farm notification event. Request ID: {request_id}")

        tft_test_run = TFTTestRunTargetModel.get_by_pipeline_id(request_id)

        # Testing Farm sends only request/pipeline id in a notification.
        # We need to get more details ourselves.
        # It'd be much better to do this in TestingFarmResultsHandler.run(),
        # but all the code along the way to get there expects we already know the details.
        # TODO: Get missing info from db instead of querying TF
        event = TestingFarmClient.get_request_details(request_id)
        if not event:
            # Something's wrong with TF, raise exception so that we can re-try later.
            raise Exception(f"Failed to get {request_id} details from TF.")

        data = Parser.parse_data_from_testing_farm(tft_test_run, event)

        logger.debug(
            f"project_url: {data.project_url}, ref: {data.ref}, result: {data.result}, "
            f"summary: {data.summary!r}, copr-build: {data.copr_build_id}:{data.copr_chroot},\n"
            f"log_url: {data.log_url}",
        )

        return testing_farm.Result(
            pipeline_id=request_id,
            result=data.result,
            compose=data.compose,
            summary=data.summary,
            log_url=data.log_url,
            copr_build_id=data.copr_build_id,
            copr_chroot=data.copr_chroot,
            commit_sha=data.ref,
            project_url=data.project_url,
            created=data.created,
            identifier=data.identifier,
        )

    @staticmethod
    def parse_copr_event(event) -> Optional[copr.CoprBuild]:
        """this corresponds to copr build event e.g:"""
        topic = event.get("topic")

        copr_build_cls: type[copr.CoprBuild]
        if topic == "org.fedoraproject.prod.copr.build.start":
            copr_build_cls = copr.Start
        elif topic == "org.fedoraproject.prod.copr.build.end":
            copr_build_cls = copr.End
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
            topic,
            build_id,
            chroot,
            status,
            owner,
            project_name,
            pkg,
            timestamp,
        )

    @staticmethod
    def parse_koji_task_event(event) -> Optional[koji.result.Task]:
        if event.get("topic") != "org.fedoraproject.prod.buildsys.task.state.change":
            return None

        task_id = event.get("id")
        logger.info(f"Koji task event: task ID={task_id}")

        state = nested_get(event, "info", "state")

        if not state:
            logger.debug("Cannot find build state.")
            return None

        state_enum = KojiTaskState(event.get("new")) if "new" in event else None
        old_state = KojiTaskState(event.get("old")) if "old" in event else None

        start_time = nested_get(event, "info", "start_time")
        completion_time = nested_get(event, "info", "completion_time")

        rpm_build_task_ids = {}
        for children in nested_get(event, "info", "children", default=[]):
            if children.get("method") == "buildArch":
                rpm_build_task_ids[children.get("arch")] = children.get("id")

        return koji.result.Task(
            task_id=task_id,
            state=state_enum,
            old_state=old_state,
            start_time=start_time,
            completion_time=completion_time,
            rpm_build_task_ids=rpm_build_task_ids,
        )

    @staticmethod
    def parse_koji_build_event(event) -> Optional[koji.result.Build]:
        if event.get("topic") != "org.fedoraproject.prod.buildsys.build.state.change":
            return None

        build_id = event.get("build_id")
        task_id = event.get("task_id")
        owner = event.get("owner")
        logger.info(f"Koji event: build_id={build_id} task_id={task_id} owner={owner}")

        new_state = (
            KojiBuildState.from_number(raw_new)
            if (raw_new := event.get("new")) is not None
            else None
        )
        if new_state == KojiBuildState.deleted:
            logger.debug("We are not interested in deleted builds.")
            return None

        old_state = (
            KojiBuildState.from_number(raw_old)
            if (raw_old := event.get("old")) is not None
            else None
        )

        start_time = event.get("creation_time")
        completion_time = event.get("completion_time")

        version = event.get("version")
        epoch = event.get("epoch")

        # "release": "1.fc36"
        release = event.get("release")

        # "request": [
        #       "git+https://src.fedoraproject.org/rpms/packit.git#0eb3e12005cb18f15d3054020f7ac934c01eae08",
        #       "rawhide",
        #       {}
        #     ],
        raw_git_ref, fedora_target, _ = event.get("request")
        project_url = raw_git_ref.split("#")[0].removeprefix("git+").removesuffix(".git")
        package_name, commit_hash = raw_git_ref.split("/")[-1].split(".git#")
        branch_name = fedora_target.removesuffix("-candidate")

        rpm_build_task_ids = {}
        for children in nested_get(event, "task", "children", default=[]):
            if children.get("method") == "buildArch":
                rpm_build_task_ids[children.get("arch")] = children.get("id")

        return koji.result.Build(
            build_id=build_id,
            rpm_build_task_ids=rpm_build_task_ids,
            state=new_state,
            package_name=package_name,
            branch_name=branch_name,
            commit_sha=commit_hash,
            namespace="rmps",
            repo_name=package_name,
            project_url=project_url,
            epoch=epoch,
            version=version,
            release=release,
            task_id=task_id,
            web_url=koji.result.Build.get_koji_rpm_build_web_url(
                rpm_build_task_id=task_id,
                koji_web_url=ServiceConfig.get_service_config().koji_web_url,
            ),
            old_state=old_state,
            start_time=start_time,
            completion_time=completion_time,
            owner=owner,
        )

    @staticmethod
    def parse_koji_build_tag_event(event) -> Optional[koji.tag.Build]:
        if event.get("topic") != "org.fedoraproject.prod.buildsys.tag":
            return None

        build_id = event.get("build_id")
        tag_name = event.get("tag")
        tag_id = event.get("tag_id")
        owner = event.get("owner")

        logger.info(
            f"Koji build tag event: build_id={build_id} tag={tag_name} owner={owner}",
        )

        package_name = event.get("name")
        epoch = event.get("epoch")
        version = event.get("version")
        release = event.get("release")

        dg_base_url = getenv("DISTGIT_URL", DISTGIT_INSTANCES["fedpkg"].url)
        distgit_project_url = f"{dg_base_url}rpms/{package_name}"

        return koji.tag.Build(
            build_id=build_id,
            tag_name=tag_name,
            tag_id=tag_id,
            project_url=distgit_project_url,
            package_name=package_name,
            epoch=epoch,
            version=version,
            release=release,
            owner=owner,
        )

    @staticmethod
    def parse_pipeline_event(event) -> Optional[gitlab.pipeline.Pipeline]:
        """
        Look into the provided event and see if it is Gitlab Pipeline event.
        https://docs.gitlab.com/ee/user/project/integrations/webhooks.html#pipeline-events
        """

        if event.get("object_kind") != "pipeline":
            return None

        # Project where the pipeline runs. In case of MR pipeline this can be
        # either source project or target project depending on pipeline type.
        project_url = nested_get(event, "project", "web_url")
        project_name = nested_get(event, "project", "name")

        pipeline_id = nested_get(event, "object_attributes", "id")

        # source branch name
        git_ref = nested_get(event, "object_attributes", "ref")
        # source commit sha
        commit_sha = nested_get(event, "object_attributes", "sha")
        status = nested_get(event, "object_attributes", "status")
        detailed_status = nested_get(event, "object_attributes", "detailed_status")
        # merge_request_event or push
        source = nested_get(event, "object_attributes", "source")
        # merge_request is null if source == "push"
        merge_request_url = nested_get(event, "merge_request", "url")

        return gitlab.pipeline.Pipeline(
            project_url=project_url,
            project_name=project_name,
            pipeline_id=pipeline_id,
            git_ref=git_ref,
            status=status,
            detailed_status=detailed_status,
            commit_sha=commit_sha,
            source=source,
            merge_request_url=merge_request_url,
        )

    @staticmethod
    def parse_pagure_pr_flag_event(event) -> Optional[pagure.pr.Flag]:
        """
        Look into the provided event and see if it is Pagure PR Flag added/updated event.
        https://fedora-fedmsg.readthedocs.io/en/latest/topics.html#pagure-pull-request-flag-added
        https://fedora-fedmsg.readthedocs.io/en/latest/topics.html#pagure-pull-request-flag-updated
        """

        if ".pagure.pull-request.flag." not in (topic := event.get("topic", "")):
            return None
        logger.info(f"Pagure PR flag event, topic: {topic}")

        if (flag := event.get("flag")) is None:
            return None
        username = flag.get("username")
        comment = flag.get("comment")
        status = flag.get("status")
        date_updated = int(d) if (d := flag.get("date_updated")) else None
        url = flag.get("url")
        commit_sha = flag.get("commit_hash")

        pr_id: int = nested_get(event, "pullrequest", "id")
        pr_url = nested_get(event, "pullrequest", "full_url")
        pr_source_branch = nested_get(event, "pullrequest", "branch_from")

        project_url = nested_get(event, "pullrequest", "project", "full_url")
        project_name = nested_get(event, "pullrequest", "project", "name")
        project_namespace = nested_get(event, "pullrequest", "project", "namespace")

        return pagure.pr.Flag(
            username=username,
            comment=comment,
            status=status,
            date_updated=date_updated,
            url=url,
            commit_sha=commit_sha,
            pr_id=pr_id,
            pr_url=pr_url,
            pr_source_branch=pr_source_branch,
            project_url=project_url,
            project_name=project_name,
            project_namespace=project_namespace,
        )

    @staticmethod
    def parse_pagure_pull_request_comment_event(
        event,
    ) -> Optional[pagure.pr.Comment]:
        if ".pagure.pull-request.comment." not in (topic := event.get("topic", "")):
            return None
        logger.info(f"Pagure PR comment event, topic: {topic}")

        action = PullRequestCommentAction.created.value
        pr_id = event["pullrequest"]["id"]
        pagure_login = event["agent"]
        if pagure_login in {"packit", "packit-stg"}:
            logger.debug("Our own comment.")
            return None

        base_repo_namespace = event["pullrequest"]["project"]["namespace"]
        base_repo_name = event["pullrequest"]["project"]["name"]
        repo_from = event["pullrequest"]["repo_from"]
        base_repo_owner = repo_from["user"]["name"] if repo_from else pagure_login
        target_repo = repo_from["name"] if repo_from else base_repo_name
        https_url = event["pullrequest"]["project"]["full_url"]
        source_project_url = repo_from["full_url"] if repo_from else https_url
        commit_sha = event["pullrequest"]["commit_stop"]

        if "added" in event["topic"]:
            comment = event["pullrequest"]["comments"][-1]["comment"]
            comment_id = event["pullrequest"]["comments"][-1]["id"]
        else:
            raise ValueError(
                f"Unknown comment location in response for {event['topic']}",
            )

        return pagure.pr.Comment(
            action=PullRequestCommentAction[action],
            pr_id=pr_id,
            base_repo_namespace=base_repo_namespace,
            base_repo_name=base_repo_name,
            base_repo_owner=base_repo_owner,
            base_ref=None,
            target_repo=target_repo,
            project_url=https_url,
            source_project_url=source_project_url,
            commit_sha=commit_sha,
            user_login=pagure_login,
            comment=comment,
            comment_id=comment_id,
        )

    @staticmethod
    def parse_pagure_pull_request_event(
        event,
    ) -> Optional[pagure.pr.Action]:
        if (topic := event.get("topic", "")) not in (
            "org.fedoraproject.prod.pagure.pull-request.new",
            "org.fedoraproject.prod.pagure.pull-request.updated",
            "org.fedoraproject.prod.pagure.pull-request.rebased",
        ):
            return None

        logger.info(f"Pagure PR event, topic: {topic}")

        action = (
            PullRequestAction.opened.value
            if topic.endswith("new")
            else PullRequestAction.synchronize.value
        )
        pr_id = event["pullrequest"]["id"]
        pagure_login = event["agent"]

        base_repo_namespace = event["pullrequest"]["project"]["namespace"]
        base_repo_name = event["pullrequest"]["project"]["name"]
        repo_from = event["pullrequest"]["repo_from"]
        base_repo_owner = repo_from["user"]["name"] if repo_from else pagure_login
        target_repo = repo_from["name"] if repo_from else base_repo_name
        https_url = event["pullrequest"]["project"]["full_url"]
        source_project_url = repo_from["full_url"] if repo_from else https_url
        commit_sha = event["pullrequest"]["commit_stop"]
        target_branch = event["pullrequest"]["branch"]

        return pagure.pr.Action(
            action=PullRequestAction[action],
            pr_id=pr_id,
            base_repo_namespace=base_repo_namespace,
            base_repo_name=base_repo_name,
            base_repo_owner=base_repo_owner,
            base_ref=None,
            target_repo=target_repo,
            project_url=https_url,
            source_project_url=source_project_url,
            commit_sha=commit_sha,
            user_login=pagure_login,
            target_branch=target_branch,
        )

    @staticmethod
    def parse_new_hotness_update_event(event) -> Optional[anitya.NewHotness]:
        if "hotness.update.bug.file" not in event.get("topic", ""):
            return None

        # "package" should contain the Fedora package name directly
        # see https://github.com/fedora-infra/the-new-hotness/blob/
        # 363acd33623dadd5fc3b60a83a528926c7c21fc1/hotness/hotness_consumer.py#L385
        # and https://github.com/fedora-infra/the-new-hotness/blob/
        # 363acd33623dadd5fc3b60a83a528926c7c21fc1/hotness/hotness_consumer.py#L444-L455
        #
        # we could get it also like this:
        # [package["package_name"]
        #   for package in event["trigger"]["msg"]["message"]["packages"]
        #   if package["distro"] == "Fedora"][0]
        package_name = event.get("package")
        dg_base_url = getenv("DISTGIT_URL", DISTGIT_INSTANCES["fedpkg"].url)

        distgit_project_url = f"{dg_base_url}rpms/{package_name}"

        version = nested_get(event, "trigger", "msg", "project", "version")

        bug_id = nested_get(event, "bug", "bug_id")
        anitya_project_id = nested_get(event, "trigger", "msg", "project", "id")
        anitya_project_name = nested_get(event, "trigger", "msg", "project", "name")

        logger.info(
            f"New hotness update event for package: {package_name}, version: {version},"
            f" bug ID: {bug_id}",
        )

        return anitya.NewHotness(
            package_name=package_name,
            version=version,
            distgit_project_url=distgit_project_url,
            bug_id=bug_id,
            anitya_project_id=anitya_project_id,
            anitya_project_name=anitya_project_name,
        )

    @staticmethod
    def parse_anitya_version_update_event(event) -> Optional[anitya.VersionUpdate]:
        if "anitya.project.version.update.v2" not in event.get("topic", ""):
            return None

        # FIXME: Handle Fedora too in case we want to support multiple releases
        # such as for Go.
        package_name = next(
            package["package_name"]
            for package in event["message"]["packages"]
            if package["distro"] == "CentOS"
        )
        distgit_project_url = DISTGIT_INSTANCES["centpkg"].distgit_project_url(
            package_name,
        )

        # ‹upstream_versions› contain the new releases
        versions = nested_get(event, "message", "upstream_versions")

        anitya_project_id = nested_get(event, "message", "project", "id")
        anitya_project_name = nested_get(event, "message", "project", "name")

        logger.info(
            f"Anitya version update event for package: {package_name}, versions: {versions}",
        )
        return anitya.VersionUpdate(
            package_name=package_name,
            versions=versions,
            distgit_project_url=distgit_project_url,
            anitya_project_id=anitya_project_id,
            anitya_project_name=anitya_project_name,
        )

    @staticmethod
    def parse_openscanhub_task_finished_event(
        event,
    ) -> Optional[openscanhub.task.Finished]:
        if "openscanhub.task.finished" not in event.get("topic", ""):
            return None

        task_id = event.get("task_id")
        status = event.get("status")
        logger.info(f"OpenScanHub task: {task_id} finished with status {status}.")

        event = openscanhub.task.Finished(
            task_id=task_id,
            status=status,
            issues_added_url=event.get("added.js", ""),
            issues_fixed_url=event.get("fixed.js", ""),
            scan_results_url=event.get("scan-results.js", ""),
        )

        if not event.build:
            logger.warning(
                "OpenScanHub task.finished is missing association with build. "
                "Package config can not be resolved without it. "
                "Skipping the event.",
            )
            return None
        return event

    @staticmethod
    def parse_openscanhub_task_started_event(
        event,
    ) -> Optional[openscanhub.task.Started]:
        if "openscanhub.task.started" not in event.get("topic", ""):
            return None

        task_id = event.get("task_id")
        logger.info(f"OpenScanHub task: {task_id} started.")

        event = openscanhub.task.Started(task_id=task_id)
        if not event.build:
            logger.warning(
                "OpenScanHub task.started is missing association with build. "
                "Package config can not be resolved without it. "
                "Skipping the event.",
            )
            return None
        return event

    @staticmethod
    def parse_forgejo_push_event(event: dict) -> Optional[forgejo.push.Commit]:
        raw_ref = event.get("ref")
        before = event.get("before")
        after = event.get("after")
        pusher = nested_get(event, "pusher", "login") or nested_get(event, "pusher", "name")

        if not (raw_ref and after and before and pusher):
            return None

        # Forgejo sets `deleted` identically to GitHub
        if event.get("deleted"):
            logger.info(f"Forgejo push event on '{raw_ref}' by {pusher} to delete ref")

            return None

        # Number of commits introduced by this push
        commits = event.get("commits") or []
        num_commits = len(commits)

        # Strip the ref prefix to get the branch/tag name
        _, ref_type, ref_name = raw_ref.split("/", 2)
        if ref_type != "heads":
            logger.debug(f"Forgejo push event ignored – not a branch push ('{raw_ref}')")
            return None

        logger.info(
            f"Forgejo push event on '{ref_name}': "
            f"{before[:8]} → {after[:8]} by {pusher} "
            f"({num_commits} {'commit' if num_commits == 1 else 'commits'})"
        )

        repo_namespace = nested_get(event, "repository", "owner", "login")
        repo_name = nested_get(event, "repository", "name")
        repo_url = nested_get(event, "repository", "html_url")

        if not (repo_namespace and repo_name):
            logger.warning("Forgejo push event missing repository namespace/name")
            return None

        return forgejo.push.Commit(
            repo_namespace=repo_namespace,
            repo_name=repo_name,
            git_ref=ref_name,
            project_url=repo_url,
            commit_sha=after,
            commit_sha_before=before,
        )

    @staticmethod
    def parse_forgejo_pr_event(event: dict) -> Optional[forgejo.pr.Action]:
        """
        Parse Forgejo PR action events, only triggering for relevant actions.
        Supported actions: 'opened', 'reopened', 'synchronize'.
        Skips others like 'closed'.

        """
        action_str = event.get("action")
        # Only trigger for these actions
        supported_actions = {"opened", "reopened", "synchronize"}
        if action_str not in supported_actions:
            logger.info(f"Skipping PR action: {action_str}")
            return None

        pr = event.get("pull_request")
        if not pr:
            logger.warning("No pull_request in event.")
            return None

        pr_id = pr.get("number")
        actor = event.get("sender", {}).get("login")
        repo = event.get("repository", {})
        base = pr.get("base")
        head = pr.get("head")
        body = pr.get("body")

        # Check all required nested fields
        try:
            base_repo_namespace = base["repo"]["owner"]["login"]
            base_repo_name = base["repo"]["name"]
            base_ref = base["ref"]
            target_repo_namespace = head["repo"]["owner"]["login"]
            target_repo_name = head["repo"]["name"]
            project_url = repo["html_url"]
            commit_sha = head["sha"]
        except (TypeError, KeyError):
            logger.warning("Missing required nested fields in PR event.")
            return None

        return forgejo.pr.Action(
            action=PullRequestAction[action_str],
            pr_id=pr_id,
            base_repo_namespace=base_repo_namespace,
            base_repo_name=base_repo_name,
            base_ref=base_ref,
            target_repo_namespace=target_repo_namespace,
            target_repo_name=target_repo_name,
            project_url=project_url,
            commit_sha=commit_sha,
            commit_sha_before=event.get("before", ""),  # Optional, might be empty
            actor=actor,
            body=body,
        )

    @staticmethod
    def parse_forgejo_comment_event(
        event: dict,
    ) -> Optional[Union[forgejo.pr.Comment, forgejo.issue.Comment]]:
        """Since Forgejo treats PR as special issues the comments are basically on issues,
        we need to distinguish between Forgejo issue and PR comments and parse accordingly."""

        issue_id = nested_get(event, "issue", "number")
        action = event.get("action")
        if action not in {"created", "edited"} or not issue_id:
            return None

        # Only treat as PR if 'pull_request' is present and not None
        issue_dict = event.get("issue", {})
        is_pr = "pull_request" in issue_dict and issue_dict["pull_request"] is not None

        comment = nested_get(event, "comment", "body")
        comment_id = nested_get(event, "comment", "id")
        logger.info(
            f"Forgejo {'PR' if is_pr else 'issue'}#{issue_id} "
            f"comment: {comment!r} id#{comment_id} {action!r} event."
        )

        base_repo_namespace = nested_get(event, "issue", "user", "login")
        base_repo_name = nested_get(event, "repository", "name")

        user_login = nested_get(event, "comment", "user", "login")
        target_repo_namespace = nested_get(event, "repository", "owner", "login")

        target_repo_name = nested_get(event, "repository", "name")
        https_url = nested_get(event, "repository", "html_url")

        if not (
            base_repo_name and base_repo_namespace and target_repo_name and target_repo_namespace
        ):
            logger.warning("Missing repo info in Forgejo event.")
            return None

        if not user_login:
            logger.warning("No user login in comment.")
            return None

        if is_pr:
            return forgejo.pr.Comment(
                action=PullRequestCommentAction[action],
                pr_id=issue_id,
                base_ref="",
                base_repo_namespace=base_repo_namespace,
                base_repo_name=base_repo_name,
                target_repo_namespace=target_repo_namespace,
                target_repo_name=target_repo_name,
                project_url=https_url,
                actor=user_login,
                comment=comment,
                comment_id=comment_id,
                commit_sha=None,
            )
        return forgejo.issue.Comment(
            action=IssueCommentAction[action],
            issue_id=issue_id,
            repo_namespace=base_repo_namespace,
            repo_name=base_repo_name,
            target_repo=f"{target_repo_namespace}/{target_repo_name}",
            project_url=https_url,
            actor=user_login,
            comment=comment,
            comment_id=comment_id,
            tag_name="",
            base_ref="",
            dist_git_project_url=None,
        )

    # The .__func__ are needed for Python < 3.10
    MAPPING: ClassVar[dict[str, dict[str, Callable]]] = {
        "github": {
            "check_run": parse_check_rerun_event.__func__,  # type: ignore
            "pull_request": parse_pr_event.__func__,  # type: ignore
            "issue_comment": parse_github_comment_event.__func__,  # type: ignore
            "release": parse_release_event.__func__,  # type: ignore
            "push": parse_github_push_event.__func__,  # type: ignore
            "installation": parse_installation_event.__func__,  # type: ignore
            "commit_comment": parse_commit_comment_event.__func__,  # type: ignore
        },
        # https://docs.gitlab.com/ee/user/project/integrations/webhook_events.html
        "gitlab": {
            "Merge Request Hook": parse_mr_event.__func__,  # type: ignore
            "Note Hook": parse_gitlab_comment_event.__func__,  # type: ignore
            "Push Hook": parse_gitlab_push_event.__func__,  # type: ignore
            "Tag Push Hook": parse_gitlab_tag_push_event.__func__,  # type: ignore
            "Pipeline Hook": parse_pipeline_event.__func__,  # type: ignore
            "Release Hook": parse_gitlab_release_event.__func__,  # type: ignore
        },
        "forgejo": {
            "push": parse_forgejo_push_event.__func__,  # type: ignore
            "issue_comment": parse_forgejo_comment_event.__func__,  # type: ignore
            "pull_request": parse_forgejo_pr_event.__func__,  # type: ignore
        },
        "fedora-messaging": {
            "pagure.pull-request.flag.added": parse_pagure_pr_flag_event.__func__,  # type: ignore
            "pagure.pull-request.flag.updated": parse_pagure_pr_flag_event.__func__,  # type: ignore
            "pagure.pull-request.comment.added": parse_pagure_pull_request_comment_event.__func__,  # type: ignore
            "pagure.pull-request.new": parse_pagure_pull_request_event.__func__,  # type: ignore
            "pagure.pull-request.updated": parse_pagure_pull_request_event.__func__,  # type: ignore
            "pagure.pull-request.rebased": parse_pagure_pull_request_event.__func__,  # type: ignore
            "pagure.git.receive": parse_pagure_push_event.__func__,  # type: ignore
            "copr.build.start": parse_copr_event.__func__,  # type: ignore
            "copr.build.end": parse_copr_event.__func__,  # type: ignore
            "buildsys.task.state.change": parse_koji_task_event.__func__,  # type: ignore
            "buildsys.build.state.change": parse_koji_build_event.__func__,  # type: ignore
            "buildsys.tag": parse_koji_build_tag_event.__func__,  # type: ignore
            "hotness.update.bug.file": parse_new_hotness_update_event.__func__,  # type: ignore
            "org.release-monitoring.prod.anitya.project.version.update.v2": (
                parse_anitya_version_update_event.__func__  # type: ignore
            ),
            "openscanhub.task.started": (
                parse_openscanhub_task_started_event.__func__  # type: ignore
            ),
            "openscanhub.task.finished": (
                parse_openscanhub_task_finished_event.__func__  # type: ignore
            ),
        },
        "testing-farm": {
            "results": parse_testing_farm_results_event.__func__,  # type: ignore
        },
    }
