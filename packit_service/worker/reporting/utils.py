# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from packit.config import JobConfig

from packit_service.config import ServiceConfig, PackageConfigGetter

logger = logging.getLogger(__name__)


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
            "No issue repository configured. User will not be notified about the failure."
        )
        return

    logger.debug(
        f"Issue repository configured. We will create "
        f"a new issue in {issue_repository} "
        "or update the existing one."
    )
    issue_repo = service_config.get_project(url=issue_repository)
    PackageConfigGetter.create_issue_if_needed(
        project=issue_repo,
        title=title,
        message=message,
        comment_to_existing=comment_to_existing,
    )


def update_message_with_configured_failure_comment_message(
    comment: str, job_config: JobConfig
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
