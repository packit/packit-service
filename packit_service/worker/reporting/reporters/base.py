# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from datetime import datetime, timezone
from typing import Callable, Optional, Union

from ogr.abstract import GitProject, PullRequest
from ogr.services.forgejo import ForgejoProject
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

    @classmethod
    def get_instance(
        cls,
        project: GitProject,
        commit_sha: str,
        packit_user: str,
        project_event_id: Optional[int] = None,
        pr_id: Optional[int] = None,
    ) -> "StatusReporter":
        """
        Get the StatusReporter instance.
        """
        from .forgejo import StatusReporterForgejo
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
        elif isinstance(project, ForgejoProject):
            reporter = StatusReporterForgejo
        return reporter(project, commit_sha, packit_user, project_event_id, pr_id)

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
            check_names: Those in bold.

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
        if self._has_identical_comment(body, duplicate_check, to_commit):
            logger.debug("Identical comment already exists")
            return

        if to_commit or not self.pr_id:
            self.project.commit_comment(commit=self.commit_sha, body=body)
        else:
            self.pull_request_object.comment(body=body)
