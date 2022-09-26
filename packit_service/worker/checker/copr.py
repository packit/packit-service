# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging

from packit_service.worker.checker.abstract import ActorChecker, Checker
from packit_service.worker.events.enums import GitlabEventAction
from packit_service.worker.events import (
    MergeRequestGitlabEvent,
    PushGitHubEvent,
    PushGitlabEvent,
    PushPagureEvent,
)
from packit_service.worker.handlers.mixin import (
    GetCoprBuildJobHelperForIdMixin,
    GetCoprBuildJobHelperMixin,
)
from packit_service.constants import (
    INTERNAL_TF_BUILDS_AND_TESTS_NOT_ALLOWED,
)
from packit_service.worker.reporting import BaseCommitStatus

logger = logging.getLogger(__name__)


class Permission(Checker, GetCoprBuildJobHelperMixin):
    def pre_check(
        self,
    ) -> bool:
        if (
            self.data.event_type == MergeRequestGitlabEvent.__name__
            and self.data.event_dict["action"] == GitlabEventAction.closed.value
        ):
            # Not interested in closed merge requests
            return False

        if self.data.event_type in (
            PushGitHubEvent.__name__,
            PushGitlabEvent.__name__,
            PushPagureEvent.__name__,
        ):
            configured_branch = self.copr_build_helper.job_build_branch
            if self.data.git_ref != configured_branch:
                logger.info(
                    f"Skipping build on '{self.data.git_ref}'. "
                    f"Push configured only for '{configured_branch}'."
                )
                return False

        if not (self.copr_build_helper.job_build or self.copr_build_helper.job_tests):
            logger.info("No copr_build or tests job defined.")
            # we can't report it to end-user at this stage
            return False

        if self.copr_build_helper.is_custom_copr_project_defined():
            logger.debug(
                "Custom Copr owner/project set. "
                "Checking if this GitHub project can use this Copr project."
            )
            if not self.copr_build_helper.check_if_custom_copr_can_be_used_and_report():
                return False

        return True


class CoprBuildPermission(Checker, GetCoprBuildJobHelperForIdMixin):
    def pre_check(self) -> bool:
        if (
            self.copr_event.owner == self.copr_build_helper.job_owner
            and self.copr_event.project_name == self.copr_build_helper.job_project
        ):
            return True

        logger.debug(
            f"The Copr project {self.copr_event.owner}/{self.copr_event.project_name} "
            f"does not match the configuration "
            f"({self.copr_build_helper.job_owner}/{self.copr_build_helper.job_project} expected)."
        )
        return False


class CanActorRunJob(ActorChecker, GetCoprBuildJobHelperMixin):
    """
    For external contributors, we need to be more careful when running jobs.
    This is a handler-specific permission check
    for a user who trigger the action on a PR.
    """

    def _pre_check(self) -> bool:
        test_job = self.copr_build_helper.job_tests
        if (
            test_job
            and test_job.use_internal_tf
            and not self.project.can_merge_pr(self.actor)
        ):
            self.copr_build_helper.report_status_to_build(
                description=INTERNAL_TF_BUILDS_AND_TESTS_NOT_ALLOWED[0].format(
                    actor=self.actor
                ),
                state=BaseCommitStatus.neutral,
                markdown_content=INTERNAL_TF_BUILDS_AND_TESTS_NOT_ALLOWED[1].format(
                    packit_comment_command_prefix=self.service_config.comment_command_prefix
                ),
            )
            return False
        return True
