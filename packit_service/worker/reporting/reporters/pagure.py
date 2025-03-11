# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
import hashlib
import logging
from typing import Optional

from ogr.abstract import CommitStatus

from packit_service.constants import CONTACTS_URL
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
        target_branch: Optional[str] = None,
    ):
        state_to_set = self.get_commit_status(state)
        logger.debug(
            f"Setting Pagure status '{state_to_set.name}' for check '{check_name}' and "
            f"target '{target_branch}': {description}"
        )
        if markdown_content:
            logger.debug(
                f"Markdown content not supported in {self.__class__.__name__} and is ignored.",
            )

        # Required because Pagure API doesn't accept empty url.
        if not url:
            url = CONTACTS_URL

        if self.pull_request_object:
            # generate a custom uid from the check_name and target_branch,
            # so that we can update flags we set previously,
            # instead of creating new ones (Pagure specific behaviour)
            # the max length of uid is 32 chars
            composed_check_name = (
                check_name if not target_branch else f"{check_name} - {target_branch}"
            )
            uid = hashlib.sha256(composed_check_name.encode()).hexdigest()[:32]
            self.pull_request_object.set_flag(
                username=composed_check_name,
                comment=description,
                url=url,
                status=state_to_set,
                uid=uid,
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
