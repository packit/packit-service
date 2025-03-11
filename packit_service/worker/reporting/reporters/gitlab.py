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
            # Ignoring Gitlab error regarding reporting a status of the same state
            # https://github.com/packit-service/packit-service/issues/741
            if e.response_code != 400 or "Cannot transition status" not in str(e):
                # 403: No permissions to set status, falling back to comment
                # 404: Commit has not been found, e.g. used target project on GitLab
                logger.debug(
                    f"Failed to set status for {self.commit_sha},"
                    f"  commenting on commit as a fallback: {e}",
                )
                self._add_commit_comment_with_status(
                    state,
                    description,
                    check_name,
                    url,
                )
            if e.response_code not in {400, 403, 404}:
                raise
