# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from typing import Optional

from ogr.abstract import CommitStatus
from ogr.exceptions import GitlabAPIException

from packit_service.worker.reporting.enums import BaseCommitStatus

from .base import StatusReporter

logger = logging.getLogger(__name__)


class StatusReporterGitlab(StatusReporter):
    @staticmethod
    def get_commit_status(state: BaseCommitStatus):
        mapped_state = StatusReporter.get_commit_status(state)
        # Gitlab has no error status
        if mapped_state == CommitStatus.error:
            mapped_state = CommitStatus.failure
        return mapped_state

    def set_status(
        self,
        state: BaseCommitStatus,
        description: str,
        check_name: str,
        url: str = "",
        links_to_external_services: Optional[dict[str, str]] = None,
        markdown_content: Optional[str] = None,
        target_branch: Optional[str] = None,
    ):
        """
        Set status of a Gitlab check.

        Discards `markdown_content`, as it isn't supported by Gitlab.
        If attempt to set status of a commit fails with `GitlabAPIException`
        and error code 400, 403 or 404, it attempts to add a comment instead.
        """

        state_to_set = self.get_commit_status(state)
        logger.debug(
            f"Setting Gitlab status '{state_to_set.name}' for check '{check_name}': {description}",
        )
        if markdown_content:
            logger.debug(
                f"Markdown content not supported in {self.__class__.__name__} and is ignored.",
            )

        try:
            self.project_with_commit.set_commit_status(
                self.commit_sha,
                state_to_set,
                url,
                description,
                check_name,
                trim=True,
            )
        except GitlabAPIException as e:
            logger.debug(f"Failed to set the status: {e}. Response code: {e.response_code}")

            # Special case: Ignore "Cannot transition status" errors
            # https://github.com/packit-service/packit-service/issues/741
            if e.response_code == 400 and "Cannot transition status" in str(e):
                return

            # Check if error is transient and reraise is enabled
            if self.is_transient_error(e) and self.reraise_transient_errors:
                raise

            # Fall back to comment for all other errors
            self._comment_as_set_status_fallback(e, state, description, check_name, url)
