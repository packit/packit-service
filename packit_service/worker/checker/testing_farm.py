# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from packit_service.constants import (
    INTERNAL_TF_BUILDS_AND_TESTS_NOT_ALLOWED,
    INTERNAL_TF_TESTS_NOT_ALLOWED,
)

from packit_service.worker.checker.abstract import ActorChecker, Checker
from packit_service.worker.events.gitlab import MergeRequestGitlabEvent
from packit_service.worker.events.enums import GitlabEventAction
from packit_service.worker.handlers.mixin import (
    GetTestingFarmJobHelperMixin,
    GetCoprBuildMixin,
    GetGithubCommentEventMixin,
)
from packit_service.worker.reporting import BaseCommitStatus

logger = logging.getLogger(__name__)


class IsEventOk(
    Checker, GetTestingFarmJobHelperMixin, GetCoprBuildMixin, GetGithubCommentEventMixin
):
    def pre_check(self) -> bool:
        if (
            self.data.event_type == MergeRequestGitlabEvent.__name__
            and self.data.event_dict["action"] == GitlabEventAction.closed.value
        ):
            # Not interested in closed merge requests
            return False

        if self.testing_farm_job_helper.is_test_comment_pr_argument_present():
            return self.testing_farm_job_helper.check_comment_pr_argument_and_report()

        return not (
            self.testing_farm_job_helper.skip_build
            and self.testing_farm_job_helper.is_copr_build_comment_event()
        )


class IsEventForJob(Checker):
    def pre_check(self) -> bool:
        if self.data.identifier != self.job_config.identifier:
            logger.debug(
                f"Skipping reporting, identifiers don't match "
                f"(identifier of the test job to report: {self.data.identifier}, "
                f"identifier from job config: {self.job_config.identifier})."
            )
            return False
        return True


class CanActorRunJob(ActorChecker, GetTestingFarmJobHelperMixin):
    """For external contributors, we need to be more careful when running jobs.
    This is a handler-specific permission check
    for a user who trigger the action on a PR.

    The job is not allowed for external contributors when using internal TF.
    """

    def _pre_check(self) -> bool:
        any_internal_test_job_build_required = (
            any(
                test_job.use_internal_tf and not test_job.skip_build
                for test_job in self.testing_farm_job_helper.job_tests_all
            )
            and self.testing_farm_job_helper.build_required()
        )
        if (
            (self.job_config.use_internal_tf or any_internal_test_job_build_required)
            and not self.project.can_merge_pr(self.actor)
            and self.actor not in self.service_config.admins
        ):
            message = (
                INTERNAL_TF_BUILDS_AND_TESTS_NOT_ALLOWED
                if self.testing_farm_job_helper.job_build
                else INTERNAL_TF_TESTS_NOT_ALLOWED
            )
            self.testing_farm_job_helper.report_status_to_tests(
                description=message[0].format(actor=self.actor),
                state=BaseCommitStatus.neutral,
                markdown_content=message[1].format(
                    packit_comment_command_prefix=self.service_config.comment_command_prefix
                ),
            )
            return False
        return True
