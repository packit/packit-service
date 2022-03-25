# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from enum import Enum
from typing import Optional, Union, Dict

import github
import gitlab

from ogr.abstract import CommitStatus, GitProject
from ogr.services.github import GithubProject
from ogr.services.github.check_run import (
    create_github_check_run_output,
    GithubCheckRunResult,
    GithubCheckRunStatus,
)
from ogr.services.gitlab import GitlabProject
from ogr.services.pagure import PagureProject

from packit_service.constants import (
    MSG_TABLE_HEADER_WITH_DETAILS,
)

logger = logging.getLogger(__name__)


class BaseCommitStatus(Enum):
    failure = "failure"
    neutral = "neutral"
    success = "success"
    pending = "pending"
    running = "running"
    error = "error"


MAP_TO_COMMIT_STATUS: Dict[BaseCommitStatus, CommitStatus] = {
    BaseCommitStatus.pending: CommitStatus.pending,
    BaseCommitStatus.running: CommitStatus.running,
    BaseCommitStatus.failure: CommitStatus.failure,
    BaseCommitStatus.neutral: CommitStatus.error,
    BaseCommitStatus.success: CommitStatus.success,
    BaseCommitStatus.error: CommitStatus.error,
}

MAP_TO_CHECK_RUN: Dict[
    BaseCommitStatus, Union[GithubCheckRunResult, GithubCheckRunStatus]
] = {
    BaseCommitStatus.pending: GithubCheckRunStatus.queued,
    BaseCommitStatus.running: GithubCheckRunStatus.in_progress,
    BaseCommitStatus.failure: GithubCheckRunResult.failure,
    BaseCommitStatus.neutral: GithubCheckRunResult.neutral,
    BaseCommitStatus.success: GithubCheckRunResult.success,
    BaseCommitStatus.error: GithubCheckRunResult.failure,
}


class StatusReporter:
    def __init__(
        self,
        project: GitProject,
        commit_sha: str,
        trigger_id: int = None,
        pr_id: Optional[int] = None,
    ):
        logger.debug(
            f"Status reporter will report for {project}, commit={commit_sha}, pr={pr_id}"
        )
        self.project: GitProject = project
        self._project_with_commit: Optional[GitProject] = None

        self.commit_sha: str = commit_sha
        self.trigger_id: int = trigger_id
        self.pr_id: Optional[int] = pr_id

    @classmethod
    def get_instance(
        cls,
        project: GitProject,
        commit_sha: str,
        trigger_id: Optional[int] = None,
        pr_id: Optional[int] = None,
    ) -> "StatusReporter":
        """
        Get the StatusReporter instance.
        """
        reporter = StatusReporter
        if isinstance(project, GithubProject):
            reporter = StatusReporterGithubChecks
        elif isinstance(project, GitlabProject):
            reporter = StatusReporterGitlab
        elif isinstance(project, PagureProject):
            reporter = StatusReporterPagure
        return reporter(project, commit_sha, trigger_id, pr_id)

    @property
    def project_with_commit(self) -> GitProject:
        """
        Returns GitProject from which we can set commit status.
        """
        if self._project_with_commit is None:
            self._project_with_commit = (
                self.project.get_pr(self.pr_id).source_project
                if isinstance(self.project, GitlabProject) and self.pr_id is not None
                else self.project
            )

        return self._project_with_commit

    @staticmethod
    def get_commit_status(state: BaseCommitStatus):
        return MAP_TO_COMMIT_STATUS[state]

    @staticmethod
    def get_check_run(state: BaseCommitStatus):
        return MAP_TO_CHECK_RUN[state]

    def set_status(
        self,
        state: BaseCommitStatus,
        description: str,
        check_name: str,
        url: str = "",
        links_to_external_services: Optional[Dict[str, str]] = None,
        markdown_content: str = None,
    ):
        raise NotImplementedError()

    def report(
        self,
        state: BaseCommitStatus,
        description: str,
        url: str = "",
        links_to_external_services: Optional[Dict[str, str]] = None,
        check_names: Union[str, list, None] = None,
        markdown_content: str = None,
    ) -> None:
        """
        Set commit check status.

        Args:
            state: State accepted by github.
            description: The long text.
            url: Url to point to (logs usually).

                Defaults to empty string
            links_to_external_services: Direct links to external services.
                e.g. `{"Testing Farm": "url-to-testing-farm"}`

                Defaults to None
            check_names: Those in bold.

                Defaults to None
            markdown_content: In GitHub checks, we can provide a markdown content.

                Defaults to None

        Returns:
            None

        """

        if not check_names:
            logger.warning("No checks to set status for.")
            return

        elif isinstance(check_names, str):
            check_names = [check_names]

        for check in check_names:
            self.set_status(
                state=state,
                description=description,
                check_name=check,
                url=url,
                links_to_external_services=links_to_external_services,
                markdown_content=markdown_content,
            )

    @staticmethod
    def is_final_state(state: BaseCommitStatus) -> bool:
        return state in {
            BaseCommitStatus.success,
            BaseCommitStatus.error,
            BaseCommitStatus.failure,
        }

    def _add_commit_comment_with_status(
        self, state: BaseCommitStatus, description: str, check_name: str, url: str = ""
    ):
        body = (
            "\n".join(
                [
                    f"- name: {check_name}",
                    f"- state: {state.name}",
                    f"- url: {url if url else 'not provided'}",
                ]
            )
            + f"\n\n{description}"
        )
        if not self.is_final_state(state):
            # To avoid multiple comments for non-final states
            logger.debug(f"Not adding a '{check_name} is {state.name}' comment.")
            return

        self.project.commit_comment(
            commit=self.commit_sha,
            body=body,
        )

    def report_status_by_comment(
        self,
        state: BaseCommitStatus,
        url: str,
        check_names: Union[str, list, None],
        description: str,
    ):
        """
        Reporting build status with MR comment if no permission to the fork project
        """

        if isinstance(check_names, str):
            check_names = [check_names]

        comment_table_rows = [
            "| Job | Result |",
            "| ------------- | ------------ |",
        ] + [f"| [{check}]({url}) | {state.name.upper()} |" for check in check_names]

        table = "\n".join(comment_table_rows)
        self.comment(table + f"\n### Description\n\n{description}")

    def get_statuses(self):
        self.project_with_commit.get_commit_statuses(commit=self.commit_sha)

    def comment(self, body: str):
        if self.pr_id:
            self.project.get_pr(pr_id=self.pr_id).comment(body=body)
        else:
            self.project.commit_comment(commit=self.commit_sha, body=body)


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
        links_to_external_services: Optional[Dict[str, str]] = None,
        markdown_content: str = None,
    ):
        state_to_set = self.get_commit_status(state)
        logger.debug(
            f"Setting Pagure status '{state_to_set.name}' for check '{check_name}': {description}"
        )
        if markdown_content:
            logger.debug(
                f"Markdown content not supported in {self.__class__.__name__} and is ignored."
            )

        # Required because Pagure API doesn't accept empty url.
        if not url:
            url = "https://wiki.centos.org/Manuals/ReleaseNotes/CentOSStream"

        self.project_with_commit.set_commit_status(
            self.commit_sha, state_to_set, url, description, check_name, trim=True
        )


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
        links_to_external_services: Optional[Dict[str, str]] = None,
        markdown_content: str = None,
    ):
        state_to_set = self.get_commit_status(state)
        logger.debug(
            f"Setting Gitlab status '{state_to_set.name}' for check '{check_name}': {description}"
        )
        if markdown_content:
            logger.debug(
                f"Markdown content not supported in {self.__class__.__name__} and is ignored."
            )

        try:
            self.project_with_commit.set_commit_status(
                self.commit_sha, state_to_set, url, description, check_name, trim=True
            )
        except gitlab.exceptions.GitlabCreateError as e:
            # Ignoring Gitlab 'enqueue' error
            # https://github.com/packit-service/packit-service/issues/741
            if e.response_code != 400:
                # 403: No permissions to set status, falling back to comment
                # 404: Commit has not been found, e.g. used target project on GitLab
                logger.debug(
                    f"Failed to set status for {self.commit_sha},"
                    f"  commenting on commit as a fallback: {e}"
                )
                self._add_commit_comment_with_status(
                    state, description, check_name, url
                )
            if e.response_code not in {400, 403, 404}:
                raise


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
        links_to_external_services: Optional[Dict[str, str]] = None,
        markdown_content: str = None,
    ):
        state_to_set = self.get_commit_status(state)
        logger.debug(
            f"Setting Github status '{state_to_set.name}' for check '{check_name}': {description}"
        )
        if markdown_content:
            logger.debug(
                f"Markdown content not supported in {self.__class__.__name__} and is ignored."
            )
        try:
            self.project_with_commit.set_commit_status(
                self.commit_sha, state_to_set, url, description, check_name, trim=True
            )
        except github.GithubException as e:
            logger.debug(
                f"Failed to set status for {self.commit_sha},"
                f" commenting on commit as a fallback: {e}"
            )
            self._add_commit_comment_with_status(state, description, check_name, url)


class StatusReporterGithubChecks(StatusReporterGithubStatuses):
    project_with_commit: GithubProject

    @staticmethod
    def _create_table(
        url: str, links_to_external_services: Optional[Dict[str, str]]
    ) -> str:
        table_content = []
        if url:
            table_content.append(f"| Dashboard | {url} |\n")
        if links_to_external_services is not None:
            table_content += [
                f"| {name} | {link} |\n"
                for name, link in links_to_external_services.items()
            ]

        return (
            MSG_TABLE_HEADER_WITH_DETAILS + "".join(table_content)
            if table_content
            else ""
        )

    def set_status(
        self,
        state: BaseCommitStatus,
        description: str,
        check_name: str,
        url: str = "",
        links_to_external_services: Optional[Dict[str, str]] = None,
        markdown_content: str = None,
    ):
        markdown_content = markdown_content or ""
        state_to_set = self.get_check_run(state)
        logger.debug(
            f"Setting Github status check '{state_to_set.name}' for check '{check_name}':"
            f" {description}"
        )

        summary = self._create_table(url, links_to_external_services) + markdown_content

        try:
            status = (
                state_to_set
                if isinstance(state_to_set, GithubCheckRunStatus)
                else GithubCheckRunStatus.completed
            )
            conclusion = (
                state_to_set if isinstance(state_to_set, GithubCheckRunResult) else None
            )

            external_id = str(self.trigger_id) if self.trigger_id else None

            self.project_with_commit.create_check_run(
                name=check_name,
                commit_sha=self.commit_sha,
                url=url or None,  # must use the http or https scheme, cannot be ""
                external_id=external_id,
                status=status,
                conclusion=conclusion,
                output=create_github_check_run_output(description, summary),
            )
        except github.GithubException as e:
            logger.debug(
                f"Failed to set status check, setting status as a fallback: {str(e)}"
            )
            super().set_status(state, description, check_name, url)
