# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from collections.abc import Iterable
from typing import Optional, Union

from ogr.abstract import GitProject, Issue, PullRequest
from packit.config import JobConfig

from packit_service.config import ServiceConfig
from packit_service.worker.reporting.enums import DuplicateCheckMode

logger = logging.getLogger(__name__)


def create_issue_if_needed(
    project: GitProject,
    title: str,
    message: str,
    comment_to_existing: Optional[str] = None,
    add_packit_prefix: Optional[bool] = True,
) -> Optional[Issue]:
    # TODO: Improve filtering
    issues = project.get_issue_list()
    packit_title = f"[packit] {title}"

    for issue in issues:
        if title in issue.title:
            logger.debug(f"Title of issue {issue.id} matches.")
            if comment_to_existing:
                comment_without_duplicating(body=comment_to_existing, pr_or_issue=issue)
                logger.debug(f"Issue #{issue.id} updated: {issue.url}")
            return None

    # TODO: store in DB
    issue = project.create_issue(
        title=packit_title if add_packit_prefix else title,
        body=message,
    )
    logger.debug(f"Issue #{issue.id} created: {issue.url}")
    return issue


def report_in_issue_repository(
    issue_repository: str,
    service_config: ServiceConfig,
    title: str,
    message: str,
    comment_to_existing: str,
):
    """
    If `issue_repository` is not empty,
    Packit will create there an issue with the details.
    If the issue already exists and is opened, comment will be added
    instead of creating a new issue.
    """
    if not issue_repository:
        logger.debug(
            "No issue repository configured. User will not be notified about the failure.",
        )
        return

    logger.debug(
        f"Issue repository configured. We will create "
        f"a new issue in {issue_repository} "
        "or update the existing one.",
    )
    issue_repo = service_config.get_project(url=issue_repository)
    create_issue_if_needed(
        project=issue_repo,
        title=title,
        message=message,
        comment_to_existing=comment_to_existing,
    )


def update_message_with_configured_failure_comment_message(
    comment: str,
    job_config: JobConfig,
) -> str:
    """
    If there is the notifications.failure_comment.message present in the configuration,
    append it to the existing message.
    """
    configured_failure_message = (
        f"\n\n---\n{configured_message}"
        if (configured_message := job_config.notifications.failure_comment.message)
        else ""
    )
    return f"{comment}{configured_failure_message}"


def has_identical_comment_in_comments(
    body: str,
    comments: Iterable,
    packit_user: str,
    mode: DuplicateCheckMode = DuplicateCheckMode.check_last_comment,
) -> bool:
    """Check if the body matches provided comments based on the duplication mode."""
    if mode == DuplicateCheckMode.do_not_check:
        return False

    for comment in comments:
        if comment.author.startswith(packit_user):
            if mode == DuplicateCheckMode.check_last_comment:
                return body == comment.body
            if mode == DuplicateCheckMode.check_all_comments and body == comment.body:
                return True
    return False


def comment_without_duplicating(
    body: str,
    pr_or_issue: Union[PullRequest, Issue],
    mode: DuplicateCheckMode = DuplicateCheckMode.check_last_comment,
):
    """
    Comment on a given pull request/issue, considering the duplication mode.
    """
    packit_user = ServiceConfig.get_service_config().get_github_account_name()
    comments = pr_or_issue.get_comments(reverse=True)
    if has_identical_comment_in_comments(
        body=body, comments=comments, packit_user=packit_user, mode=mode
    ):
        logger.debug("Identical comment already exists")
        return

    pr_or_issue.comment(body=body)
