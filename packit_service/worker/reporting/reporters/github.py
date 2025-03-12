# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from typing import Optional

from ogr.abstract import CommitStatus
from ogr.exceptions import GithubAPIException
from ogr.services.github import GithubProject
from ogr.services.github.check_run import (
    GithubCheckRunResult,
    GithubCheckRunStatus,
    create_github_check_run_output,
)

from packit_service.constants import DOCS_URL, MSG_TABLE_HEADER_WITH_DETAILS
from packit_service.worker.reporting.enums import BaseCommitStatus
from packit_service.worker.reporting.news import News

from .base import StatusReporter

logger = logging.getLogger(__name__)


class StatusReporterGithubStatuses(StatusReporter):
    @staticmethod
    def get_commit_status(state: BaseCommitStatus):
        mapped_state = StatusReporter.get_commit_status(state)
        # Github has no running status
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
            f"Setting Github status '{state_to_set.name}' for check '{check_name}': {description}",
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
        except GithubAPIException as e:
            logger.debug(
                f"Failed to set status for {self.commit_sha},"
                f" commenting on commit as a fallback: {e}",
            )
            self._add_commit_comment_with_status(state, description, check_name, url)


class StatusReporterGithubChecks(StatusReporterGithubStatuses):
    project_with_commit: GithubProject

    @staticmethod
    def _create_table(
        url: str,
        links_to_external_services: Optional[dict[str, str]],
    ) -> str:
        table_content = []
        if url:
            type_of_url = ""
            if "dashboard.packit.dev" in url or "dashboard.stg.packit.dev" in url:
                type_of_url = "Dashboard"
            elif DOCS_URL in url:
                type_of_url = "Documentation"
            table_content.append(f"| {type_of_url} | {url} |\n")
        if links_to_external_services is not None:
            table_content += [
                f"| {name} | {link} |\n" for name, link in links_to_external_services.items()
            ]
        if table_content:
            table_content += "\n"

        return MSG_TABLE_HEADER_WITH_DETAILS + "".join(table_content) if table_content else ""

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
        markdown_content = markdown_content or ""
        state_to_set = self.get_check_run(state)
        logger.debug(
            f"Setting Github status check '{state_to_set.name}' for check '{check_name}':"
            f" {description}",
        )

        summary = (
            self._create_table(url, links_to_external_services)
            + markdown_content
            + "\n\n"
            + f"---\n*{News.get_sentence()}*"
        )

        try:
            status = (
                state_to_set
                if isinstance(state_to_set, GithubCheckRunStatus)
                else GithubCheckRunStatus.completed
            )
            conclusion = state_to_set if isinstance(state_to_set, GithubCheckRunResult) else None

            external_id = str(self.project_event_id) if self.project_event_id else None

            self.project_with_commit.create_check_run(
                name=check_name,
                commit_sha=self.commit_sha,
                url=url or None,  # must use the http or https scheme, cannot be ""
                external_id=external_id,
                status=status,
                conclusion=conclusion,
                output=create_github_check_run_output(description, summary),
            )
        except GithubAPIException as e:
            logger.debug(
                f"Failed to set status check, setting status as a fallback: {e!s}",
            )
            super().set_status(state, description, check_name, url)
