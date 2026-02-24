# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from datetime import datetime, timezone
from typing import Callable, Optional, Union

from ogr.abstract import GitProject, PullRequest
from ogr.exceptions import GithubAPIException, GitlabAPIException, PagureAPIException
from ogr.services.github import GithubProject
from ogr.services.gitlab import GitlabProject
from ogr.services.pagure import PagureProject

from packit_service.worker.reporting.enums import (
    MAP_TO_CHECK_RUN,
    MAP_TO_COMMIT_STATUS,
    BaseCommitStatus,
    DuplicateCheckMode,
)
from packit_service.worker.reporting.utils import has_identical_comment_in_comments

logger = logging.getLogger(__name__)


class StatusReporter:
    def __init__(
        self,
        project: GitProject,
        commit_sha: str,
        packit_user: str,
        project_event_id: Optional[int] = None,
        pr_id: Optional[int] = None,
        reraise_transient_errors: bool = False,
    ):
        logger.debug(
            f"Status reporter will report for {project}, commit={commit_sha}, pr={pr_id}",
        )
        self.project: GitProject = project
        self._project_with_commit: Optional[GitProject] = None
        self._packit_user = packit_user

        self.commit_sha: str = commit_sha
        self.project_event_id: int = project_event_id
        self.pr_id: Optional[int] = pr_id
        self._pull_request_object: Optional[PullRequest] = None
        self.reraise_transient_errors: bool = reraise_transient_errors

    @classmethod
    def get_instance(
        cls,
        project: GitProject,
        commit_sha: str,
        packit_user: str,
        project_event_id: Optional[int] = None,
        pr_id: Optional[int] = None,
        reraise_transient_errors: bool = False,
    ) -> "StatusReporter":
        """
        Get the StatusReporter instance.
        The `project` determines type of the reporter returned. All other
        parameters are passed to the initializer of the chosen reporter.
        """
        from .github import StatusReporterGithubChecks
        from .gitlab import StatusReporterGitlab
        from .pagure import StatusReporterPagure

        reporter = StatusReporter
        if isinstance(project, GithubProject):
            reporter = StatusReporterGithubChecks
        elif isinstance(project, GitlabProject):
            reporter = StatusReporterGitlab
        elif isinstance(project, PagureProject):
            reporter = StatusReporterPagure
        return reporter(
            project, commit_sha, packit_user, project_event_id, pr_id, reraise_transient_errors
        )

    @property
    def project_with_commit(self) -> GitProject:
        """
        Returns GitProject from which we can set commit status.
        """
        if self._project_with_commit is None:
            self._project_with_commit = (
                self.pull_request_object.source_project
                if isinstance(self.project, GitlabProject) and self.pr_id is not None
                else self.project
            )

        return self._project_with_commit

    @property
    def pull_request_object(self) -> Optional[PullRequest]:
        if not self._pull_request_object and self.pr_id:
            self._pull_request_object = self.project.get_pr(self.pr_id)
        return self._pull_request_object

    @staticmethod
    def get_commit_status(state: BaseCommitStatus):
        return MAP_TO_COMMIT_STATUS[state]

    @staticmethod
    def get_check_run(state: BaseCommitStatus):
        return MAP_TO_CHECK_RUN[state]

    @staticmethod
    def is_transient_error(
        exception: Union[GithubAPIException, GitlabAPIException, PagureAPIException],
    ) -> bool:
        """
        Check if an API exception represents a transient error that should be retried.

        Transient errors include:
        - Network errors (no response_code attribute)
        - Rate limiting (HTTP 429)
        - Server errors (HTTP 5xx)

        Args:
            exception: An API exception from ogr

        Returns:
            True if the error is transient and should be retried, False otherwise
        """
        response_code = getattr(exception, "response_code", None)

        if response_code is None:
            # Network errors (no response code) are transient
            return True

        return response_code == 429 or (500 <= response_code < 600)

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
        """Set status of the check."""
        raise NotImplementedError()

    def report(
        self,
        state: BaseCommitStatus,
        description: str,
        url: str = "",
        links_to_external_services: Optional[dict[str, str]] = None,
        check_names: Union[str, list, None] = None,
        markdown_content: Optional[str] = None,
        update_feedback_time: Optional[Callable] = None,
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
            check_names: List of check names

                Defaults to None
            markdown_content: In GitHub checks, we can provide a markdown content.

                Defaults to None

            update_feedback_time: a callable which tells the caller when a check
                status has been updated.

        Returns:
            None
        """
        if not check_names:
            logger.warning("No checks to set status for.")
            return

        if isinstance(check_names, str):
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

            if update_feedback_time:
                update_feedback_time(datetime.now(timezone.utc))

    @staticmethod
    def is_final_state(state: BaseCommitStatus) -> bool:
        return state in {
            BaseCommitStatus.success,
            BaseCommitStatus.error,
            BaseCommitStatus.failure,
            BaseCommitStatus.neutral,
        }

    def _add_commit_comment_with_status(
        self,
        state: BaseCommitStatus,
        description: str,
        check_name: str,
        url: str = "",
    ):
        """Add a comment with status to the commit.

        A fallback solution when setting commit status fails.
        """
        body = (
            "\n".join(
                [
                    f"- name: {check_name}",
                    f"- state: {state.name}",
                    f"- url: {url or 'not provided'}",
                ],
            )
            + f"\n\n{description}"
        )

        if self.is_final_state(state):
            self.comment(body, DuplicateCheckMode.check_all_comments, to_commit=True)
        else:
            logger.debug(f"Ain't comment as {state!r} is not a final state")

    def _comment_as_set_status_fallback(
        self,
        exception: Exception,
        state: BaseCommitStatus,
        description: str,
        check_name: str,
        url: str,
    ):
        """Handle failure to set commit status by falling back to comments.

        If commit_sha exists, adds a comment to the commit.
        If pr_id exists, adds a comment to the PR.
        Otherwise, logs a warning.
        """
        if self.commit_sha:
            logger.debug(
                f"Failed to set status for {self.commit_sha},"
                f" commenting on commit as a fallback: {exception}",
            )
            self._add_commit_comment_with_status(state, description, check_name, url)
        elif self.pr_id:
            logger.debug(
                f"Failed to set status and no commit SHA available,"
                f" commenting on PR {self.pr_id} as a fallback: {exception}",
            )
            self.report_status_by_comment(
                state,
                url,
                check_name,
                description,
            )
        else:
            logger.warning(
                f"Failed to set status and cannot comment as a fallback,"
                f" no commit SHA and no PR id: {exception}",
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

        if self.is_final_state(state):
            self.comment(table + f"\n### Description\n\n{description}")
        else:
            logger.debug(f"Ain't comment as {state!r} is not a final state")

    def get_statuses(self):
        self.project_with_commit.get_commit_statuses(commit=self.commit_sha)

    def _has_identical_comment(
        self,
        body: str,
        mode: DuplicateCheckMode,
        check_commit: bool = False,
    ) -> bool:
        """Checks if the body is the same as the last or any (based on mode) comment.

        Check either commit comments or PR comments (if specified).
        """
        if mode == DuplicateCheckMode.do_not_check:
            return False

        comments = (
            reversed(self.project.get_commit_comments(self.commit_sha))
            if check_commit or not self.pr_id
            else self.pull_request_object.get_comments(reverse=True)
        )

        return has_identical_comment_in_comments(
            body=body,
            mode=mode,
            comments=comments,
            packit_user=self._packit_user,
        )

    def comment(
        self,
        body: str,
        duplicate_check: DuplicateCheckMode = DuplicateCheckMode.do_not_check,
        to_commit: bool = False,
    ):
        """Add a comment.

        It's added either to a commit or to a PR (if specified).

        Args:
            body: The comment text.
            duplicate_check: Determines if the comment will be added if
                the same comment is already present in the PR
                (if the instance is tied to a PR) or in a commit.
            to_commit: Add the comment to the commit even if PR is specified.
        """
        if (to_commit or not self.pr_id) and not self.commit_sha:
            logger.debug("Cannot comment on commit, commit_sha is None.")
            return

        if self._has_identical_comment(body, duplicate_check, to_commit):
            logger.debug("Identical comment already exists")
            return

        if to_commit or not self.pr_id:
            self.project.commit_comment(commit=self.commit_sha, body=body)
        else:
            self.pull_request_object.comment(body=body)
