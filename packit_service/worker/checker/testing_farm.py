# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from typing import Optional

from packit_service.constants import (
    INTERNAL_TF_TESTS_NOT_ALLOWED,
)
from packit_service.models import PullRequestModel
from packit_service.worker.checker.abstract import Checker, ActorChecker
from packit_service.worker.events.enums import GitlabEventAction
from packit_service.worker.events.gitlab import MergeRequestGitlabEvent
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

    @property
    def actor(self) -> Optional[str]:
        if isinstance(self.testing_farm_job_helper.db_trigger, PullRequestModel):
            return self.testing_farm_job_helper.db_trigger.actor

        logger.debug("DB trigger other than PullRequestModel.")
        return None

    def _pre_check(self) -> bool:
        logger.debug(f"Actor from the DB trigger: {self.actor}")
        if (
            self.job_config.use_internal_tf
            and not self.project.can_merge_pr(self.actor)
            and self.actor not in self.service_config.admins
        ):
            self.testing_farm_job_helper.report_status_to_tests(
                description=INTERNAL_TF_TESTS_NOT_ALLOWED[0].format(actor=self.actor),
                state=BaseCommitStatus.neutral,
                markdown_content=INTERNAL_TF_TESTS_NOT_ALLOWED[1].format(
                    packit_comment_command_prefix=self.service_config.comment_command_prefix
                ),
            )
            return False
        return True
