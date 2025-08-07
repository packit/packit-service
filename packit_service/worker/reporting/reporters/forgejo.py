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
        state_to_set = self.get_commit_status(state)
        logger.debug(f"Setting Forgejo status '{state_to_set.name}'")

        try:
            self.project_with_commit.set_commit_status(
                self.commit_sha, state_to_set, url, description, check_name, trim=True
            )

        except ForgejoAPIException as e:
            logger.debug(f"Failed to set status: {e}")

            self._add_commit_comment_with_status(state, description, check_name, url)
