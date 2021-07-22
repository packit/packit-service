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

from packit_service.constants import MSG_MORE_DETAILS, MSG_RERUN_NOT_SUPPORTED

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
        pr_id: Optional[int] = None,
    ):
        logger.debug(
            f"Status reporter will report for {project}, commit={commit_sha}, pr={pr_id}"
        )
        self.project: GitProject = project
        self._project_with_commit: Optional[GitProject] = None
        self.commit_sha: str = commit_sha
        self.pr_id: Optional[int] = pr_id

    @classmethod
    def get_instance(
        cls, project: GitProject, commit_sha: str, pr_id: Optional[int] = None
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
        return reporter(project, commit_sha, pr_id)

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
    ):
        raise NotImplementedError()

    def report(
        self,
        state: BaseCommitStatus,
        description: str,
        url: str = "",
        check_names: Union[str, list, None] = None,
    ) -> None:
        """
        set commit check status

        :param state: state accepted by github
        :param description: the long text
        :param url: url to point to (logs usually)
        :param check_names: those in bold
        """

        if not check_names:
            logger.warning("No checks to set status for.")
            return

        elif isinstance(check_names, str):
            check_names = [check_names]

        for check in check_names:
            self.set_status(
                state=state, description=description, check_name=check, url=url
            )

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
        state = StatusReporter.get_commit_status(state)
        # Pagure has no running status
        if state == CommitStatus.running:
            state = CommitStatus.pending

        return state

    def set_status(
        self,
        state: BaseCommitStatus,
        description: str,
        check_name: str,
        url: str = "",
    ):
        state_to_set = self.get_commit_status(state)
        logger.debug(
            f"Setting Pagure status '{state_to_set.name}' for check '{check_name}': {description}"
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
        state = StatusReporter.get_commit_status(state)
        # Gitlab has no error status
        if state == CommitStatus.error:
            state = CommitStatus.failure
        return state

    def set_status(
        self,
        state: BaseCommitStatus,
        description: str,
        check_name: str,
        url: str = "",
    ):
        state_to_set = self.get_commit_status(state)
        logger.debug(
            f"Setting Gitlab status '{state_to_set.name}' for check '{check_name}': {description}"
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
                    f"Failed to set status for {self.commit_sha}, commenting on"
                    f" commit as a fallback: {str(e)}"
                )
                self._add_commit_comment_with_status(
                    state, description, check_name, url
                )
            if e.response_code not in {400, 403, 404}:
                raise


class StatusReporterGithubStatuses(StatusReporter):
    @staticmethod
    def get_commit_status(state: BaseCommitStatus):
        state = StatusReporter.get_commit_status(state)
        # Github has no running status
        if state == CommitStatus.running:
            state = CommitStatus.pending
        return state

    def set_status(
        self,
        state: BaseCommitStatus,
        description: str,
        check_name: str,
        url: str = "",
    ):
        state_to_set = self.get_commit_status(state)
        logger.debug(
            f"Setting Github status '{state_to_set.name}' for check '{check_name}': {description}"
        )
        try:
            self.project_with_commit.set_commit_status(
                self.commit_sha, state_to_set, url, description, check_name, trim=True
            )
        except github.GithubException as e:
            logger.debug(
                f"Failed to set status for {self.commit_sha}, commenting on"
                f" commit as a fallback: {str(e)}"
            )
            self._add_commit_comment_with_status(state, description, check_name, url)


class StatusReporterGithubChecks(StatusReporterGithubStatuses):
    def set_status(
        self,
        state: BaseCommitStatus,
        description: str,
        check_name: str,
        url: str = "",
    ):
        state_to_set = self.get_check_run(state)
        logger.debug(
            f"Setting Github status check '{state_to_set.name}' for check '{check_name}':"
            f" {description}"
        )
        summary = (MSG_MORE_DETAILS.format(url=url) if url else "") + (
            MSG_RERUN_NOT_SUPPORTED
            if state_to_set == GithubCheckRunResult.failure
            else ""
        )
        try:
            self.project_with_commit.create_check_run(
                name=check_name,
                commit_sha=self.commit_sha,
                url=url,
                status=state_to_set
                if isinstance(state_to_set, GithubCheckRunStatus)
                else GithubCheckRunStatus.completed,
                conclusion=state_to_set
                if isinstance(state_to_set, GithubCheckRunResult)
                else None,
                output=create_github_check_run_output(description, summary),
            )
        except github.GithubException as e:
            logger.debug(
                f"Failed to set status check, setting status as a fallback: {str(e)}"
            )
            super().set_status(state, description, check_name, url)
