# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from typing import Optional

from ogr.abstract import CommitStatus
from ogr.services.pagure import PagurePullRequest

from packit_service.worker.reporting.enums import BaseCommitStatus

from .base import StatusReporter

logger = logging.getLogger(__name__)


class StatusReporterPagure(StatusReporter):
    @staticmethod
    def get_commit_status(state: BaseCommitStatus):
        mapped_state = StatusReporter.get_commit_status(state)
        # Pagure has no running status
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
    ):
        state_to_set = self.get_commit_status(state)
        logger.debug(
            f"Setting Pagure status '{state_to_set.name}' for check '{check_name}': {description}",
        )
        if markdown_content:
            logger.debug(
                f"Markdown content not supported in {self.__class__.__name__} and is ignored.",
            )

        # Required because Pagure API doesn't accept empty url.
        if not url:
            url = "https://wiki.centos.org/Manuals/ReleaseNotes/CentOSStream"

        if self.pull_request_object:
            self.pull_request_object.set_flag(
                username=check_name, comment=description, url=url, status=state_to_set
            )

        else:
            self.project_with_commit.set_commit_status(
                self.commit_sha,
                state_to_set,
                url,
                description,
                check_name,
                trim=True,
            )
