# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from collections.abc import Iterable
from typing import Any, Callable, Optional, Union
from urllib.parse import urlparse

from ogr.abstract import GitProject
from packit.config.job_config import JobConfig, JobType
from packit.exceptions import PackitException

from packit_service.constants import (
    DENIED_MSG,
    DOCS_APPROVAL_URL,
    NAMESPACE_NOT_ALLOWED_MARKDOWN_DESCRIPTION,
    NAMESPACE_NOT_ALLOWED_MARKDOWN_ISSUE_INSTRUCTIONS,
)
from packit_service.events import (
    abstract,
    anitya,
    copr,
    forgejo,
    github,
    gitlab,
    koji,
    openscanhub,
    pagure,
    testing_farm,
)
from packit_service.events.event_data import EventData
from packit_service.worker.allowlist import Allowlist
from packit_service.worker.helpers.build import CoprBuildJobHelper
from packit_service.worker.helpers.testing_farm import TestingFarmJobHelper
from packit_service.worker.reporting import BaseCommitStatus

logger = logging.getLogger(__name__)

UncheckedEvent = Union[
    anitya.NewHotness,
    copr.CoprBuild,
    forgejo.push.Commit,
    forgejo.pr.Action,
    forgejo.pr.Comment,
    forgejo.issue.Comment,
    forgejo.action_run.Push,
    forgejo.action_run.PullRequest,
    github.check.Rerun,
    github.installation.Installation,
    koji.result.Task,
    koji.result.Build,
    pagure.pr.Comment,
    pagure.pr.Action,
    pagure.push.Commit,
    testing_farm.Result,
]


class AllowlistChecker(Allowlist):
    """
    Extends Allowlist with event checking and reporting functionality.

    This class handles checking events against the allowlist and reporting
    status back to PRs/commits. It depends on job helpers for status reporting.
    """

    def _check_unchecked_event(
        self,
        event: UncheckedEvent,
        project: GitProject,
        job_configs: Iterable[JobConfig],
    ) -> bool:
        # Allowlist checks do not apply to CentOS (Pagure, GitLab) and distgit commit event.
        logger.info(f"{type(event)} event does not require allowlist checks.")
        return True

    def _check_release_push_event(
        self,
        event: Union[github.release.Release, github.push.Commit, gitlab.push.Commit],
        project: GitProject,
        job_configs: Iterable[JobConfig],
    ) -> bool:
        # TODO: modify event hierarchy so we can use some abstract classes instead
        project_url = self._strip_protocol_and_add_git(event.project_url)
        if not project_url:
            raise KeyError(f"Failed to get namespace from {type(event)!r}")
        if self.is_namespace_or_parent_denied(project_url):
            msg = f"{project_url} or parent namespaces denied!"
            project.commit_comment(event.commit_sha, msg)
            return False

        if self.is_namespace_or_parent_approved(project_url):
            return True

        msg = (
            f"Project {project_url} is not on our allowlist! "
            "See https://packit.dev/docs/guide/#2-approval"
        )
        project.commit_comment(event.commit_sha, msg)
        return False

    def _check_pr_event(
        self,
        event: Union[
            github.pr.Action,
            github.pr.Comment,
            gitlab.mr.Action,
            gitlab.mr.Comment,
        ],
        project: GitProject,
        job_configs: Iterable[JobConfig],
    ) -> bool:
        actor_name = event.actor
        if not actor_name:
            raise KeyError(f"Failed to get login of the actor from {type(event)}")

        project_url = self._strip_protocol_and_add_git(event.project_url)
        user_namespace = f"{urlparse(event.project_url).netloc}/{actor_name}"

        if user_or_project_denied := Allowlist.is_denied(user_namespace):
            msg = f"User namespace {actor_name} denied!"
            short_msg = "User namespace denied!"
        elif user_or_project_denied := self.is_namespace_or_parent_denied(project_url):
            msg = f"{project_url} or parent namespaces denied!"
            short_msg = "Project or its namespace denied!"
        else:
            namespace_approved = self.is_namespace_or_parent_approved(project_url)
            user_approved = (
                project.can_merge_pr(actor_name) or project.get_pr(event.pr_id).author == actor_name
            )
            # TODO: clear failing check when present
            if namespace_approved and user_approved:
                return True
            msg = (
                (
                    f"Project {project_url} is not on our allowlist! "
                    "See https://packit.dev/docs/guide/#2-approval"
                )
                if not namespace_approved
                else f"Account {actor_name} has no write access nor is author of PR!"
            )
            short_msg = (
                f"{project_url} not allowed!" if not namespace_approved else "User cannot trigger!"
            )

        logger.debug(msg)
        if isinstance(
            event,
            (github.pr.Comment, gitlab.mr.Comment),
        ):
            project.get_pr(event.pr_id).comment(msg)
        else:
            self._check_pr_report_status(
                job_configs=job_configs,
                event=event,
                project=project,
                user_or_project_denied=user_or_project_denied,
                short_msg=short_msg,
            )
        return False

    def _check_pr_report_status(
        self,
        job_configs,
        event,
        project,
        user_or_project_denied,
        short_msg,
    ):
        for job_config in job_configs:
            job_helper_kls: type[Union[TestingFarmJobHelper, CoprBuildJobHelper]]
            if job_config.type == JobType.tests:
                job_helper_kls = TestingFarmJobHelper
            else:
                job_helper_kls = CoprBuildJobHelper

            job_helper = job_helper_kls(
                service_config=self.service_config,
                package_config=event.get_packages_config().get_package_config_for(
                    job_config,
                ),
                project=project,
                metadata=EventData.from_event_dict(event.get_dict()),
                db_project_event=event.db_project_event,
                job_config=job_config,
                build_targets_override=event.build_targets_override,
                tests_targets_override=event.tests_targets_override,
            )
            if user_or_project_denied:
                url = None
                markdown_content = DENIED_MSG
            else:
                issue_url = self.get_approval_issue(namespace=project.namespace)
                url = issue_url or DOCS_APPROVAL_URL
                markdown_content = NAMESPACE_NOT_ALLOWED_MARKDOWN_DESCRIPTION.format(
                    instructions=(
                        NAMESPACE_NOT_ALLOWED_MARKDOWN_ISSUE_INSTRUCTIONS.format(
                            issue_url=issue_url,
                        )
                        if issue_url
                        else ""
                    ),
                )
            job_helper.report_status_to_configured_job(
                description=short_msg,
                state=BaseCommitStatus.neutral,
                url=url,
                markdown_content=markdown_content,
            )

    def _check_issue_comment_event(
        self,
        event: Union[github.issue.Comment, gitlab.issue.Comment],
        project: GitProject,
        job_configs: Iterable[JobConfig],
    ) -> bool:
        return self._check_issue_and_commit_comment_event(
            event=event,
            project=project,
            comment_fn=lambda msg: project.get_issue(event.issue_id).comment(msg),
        )

    def _check_commit_comment_event(
        self,
        event: abstract.comment.Commit,
        project: GitProject,
        job_configs: Iterable[JobConfig],
    ) -> bool:
        return self._check_issue_and_commit_comment_event(
            event=event,
            project=project,
            comment_fn=lambda msg: project.commit_comment(
                commit=event.commit_sha,
                body=msg,
            ),
        )

    def _check_issue_and_commit_comment_event(
        self,
        event: Union[abstract.comment.Commit, github.issue.Comment, gitlab.issue.Comment],
        project: GitProject,
        comment_fn: Callable[[str], Any],
    ) -> bool:
        actor_name = event.actor
        if not actor_name:
            raise KeyError(f"Failed to get login of the actor from {type(event)}")
        project_url = self._strip_protocol_and_add_git(event.project_url)
        user_namespace = f"{urlparse(event.project_url).netloc}/{actor_name}"

        if Allowlist.is_denied(user_namespace):
            msg = f"User namespace {actor_name} denied!"
        elif self.is_namespace_or_parent_denied(project_url):
            msg = f"{project_url} or parent namespaces denied!"
        else:
            namespace_approved = self.is_namespace_or_parent_approved(project_url)
            user_approved = project.can_merge_pr(actor_name)
            if namespace_approved and user_approved:
                return True
            msg = (
                (
                    f"Project {project_url} is not on our allowlist! "
                    "See https://packit.dev/docs/guide/#2-approval"
                )
                if not namespace_approved
                else f"Account {actor_name} has no write access!"
            )

        logger.debug(msg)
        comment_fn(msg)
        return False

    def check_and_report(
        self,
        event: Optional[Any],
        project: GitProject,
        job_configs: Iterable[JobConfig],
    ) -> bool:
        """
        Check if account is approved and report status back in case of PR
        :param event: PullRequest and Release TODO: handle more
        :param project: GitProject
        :param job_configs: iterable of jobconfigs - so we know how to update status of the PR
        :return:
        """
        CALLBACKS: dict[
            Union[type, tuple[Union[type, tuple[Any, ...]], ...]],
            Callable,
        ] = {
            (  # events that are not checked against allowlist
                # [XXX] dist-git Forgejo events are unchecked
                # upstream Forgejo events would require authorization checking
                forgejo.push.Commit,
                forgejo.pr.Action,
                forgejo.pr.Comment,
                forgejo.issue.Comment,
                forgejo.action_run.Push,
                forgejo.action_run.PullRequest,
                pagure.push.Commit,
                pagure.pr.Action,
                pagure.pr.Comment,
                copr.CoprBuild,
                testing_farm.Result,
                github.installation.Installation,
                koji.result.Task,
                koji.result.Build,
                koji.tag.Build,
                github.check.Rerun,
                anitya.NewHotness,
                openscanhub.task.Started,
                openscanhub.task.Finished,
            ): self._check_unchecked_event,
            (
                github.release.Release,
                gitlab.release.Release,
                github.push.Commit,
                gitlab.push.Commit,
            ): self._check_release_push_event,
            (
                github.pr.Action,
                github.pr.Comment,
                gitlab.mr.Action,
                gitlab.mr.Comment,
            ): self._check_pr_event,
            (
                github.issue.Comment,
                gitlab.issue.Comment,
            ): self._check_issue_comment_event,
            (abstract.comment.Commit,): self._check_commit_comment_event,
        }

        # Administrators
        user_login = getattr(  # some old events with user_login can still be there
            event,
            "user_login",
            None,
        ) or getattr(event, "actor", None)

        if user_login and user_login in self.service_config.admins:
            logger.info(f"{user_login} is admin, you shall pass.")
            return True

        for related_events, callback in CALLBACKS.items():
            if isinstance(event, related_events):
                return callback(event, project, job_configs)

        msg = f"Failed to validate account: Unrecognized event type {type(event)!r}."
        logger.debug(msg)
        raise PackitException(msg)
