# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from typing import Optional

from ogr.abstract import CommitStatus
from ogr.exceptions import ForgejoAPIException

from packit_service.worker.reporting import BaseCommitStatus
from packit_service.worker.reporting.reporters.base import StatusReporter

logger = logging.getLogger(__name__)


class StatusReporterForgejo(StatusReporter):
    @staticmethod
    def get_commit_status(state: BaseCommitStatus):
        mapped_state = StatusReporter.get_commit_status(state)

        # Forgejo supports pending, success, failure, error, warning
        if mapped_state == CommitStatus.running:
            mapped_state = CommitStatus.pending

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
        Set status of a Forgejo check.

        Discards `markdown_content`, as it isn't supported by Forgejo. If it fails
        to set a check status, it resorts to posting a comment.
        """
        state_to_set = self.get_commit_status(state)
        logger.debug(
            f"Setting Forgejo status '{state_to_set.name}' for check '{check_name}' and "
            f"target '{target_branch}': {description}"
        )
        if markdown_content:
            logger.debug(
                f"Markdown content not supported in {self.__class__.__name__} and is ignored.",
            )

        try:
            self.project_with_commit.set_commit_status(
                self.commit_sha, state_to_set, url, description, check_name, trim=True
            )
        except ForgejoAPIException as e:
            self._comment_as_set_status_fallback(e, state, description, check_name, url)
