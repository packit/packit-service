# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from typing import Optional

from packit_service.constants import (
    DOCS_TESTING_FARM,
    INTERNAL_TF_BUILDS_AND_TESTS_NOT_ALLOWED,
    INTERNAL_TF_TESTS_NOT_ALLOWED,
    KojiTaskState,
)
from packit_service.events import gitlab, testing_farm
from packit_service.models import TFTTestRunTargetModel
from packit_service.worker.checker.abstract import (
    ActorChecker,
    Checker,
)
from packit_service.worker.handlers.mixin import (
    GetCoprBuildMixin,
    GetGithubCommentEventMixin,
    GetKojiBuildFromTaskOrPullRequestMixin,
    GetTestingFarmJobHelperMixin,
)
from packit_service.worker.reporting import BaseCommitStatus

logger = logging.getLogger(__name__)


class IsJobConfigTriggerMatching(Checker, GetTestingFarmJobHelperMixin):
    def pre_check(self) -> bool:
        return self.testing_farm_job_helper.is_job_config_trigger_matching(
            self.job_config,
        )


class IsEventOk(
    Checker,
    GetTestingFarmJobHelperMixin,
    GetCoprBuildMixin,
    GetGithubCommentEventMixin,
):
    def pre_check(self) -> bool:
        if (
            self.data.event_type == gitlab.mr.Action.event_type()
            and self.data.event_dict["action"] == gitlab.enums.Action.closed.value
        ):
            # Not interested in closed merge requests
            return False

        if self.testing_farm_job_helper.is_test_comment_pr_argument_present():
            return self.testing_farm_job_helper.check_comment_pr_argument_and_report()

        return not (
            self.testing_farm_job_helper.skip_build
            and self.testing_farm_job_helper.is_copr_build_comment_event()
        )


class HasEventSuccessfulScratchBuild(
    Checker,
    GetKojiBuildFromTaskOrPullRequestMixin,
):
    def pre_check(self) -> bool:
        if not self.koji_build:
            return False

        if self.koji_build.status == "success":
            return True

        return bool(self.koji_task_event and self.koji_task_event.state == KojiTaskState.closed)


class HasEventSuccessfulRawhideELNScratchBuild(HasEventSuccessfulScratchBuild):
    _rawhide_eln_build = True


class IsEventForJob(Checker):
    def pre_check(self) -> bool:
        if self.data.identifier != self.job_config.identifier:
            logger.debug(
                f"Skipping reporting, identifiers don't match "
                f"(identifier of the test job to report: {self.data.identifier}, "
                f"identifier from job config: {self.job_config.identifier}).",
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
                    packit_comment_command_prefix=self.service_config.comment_command_prefix,
                ),
            )
            return False
        return True


class IsCoprBuildDefined(Checker, GetTestingFarmJobHelperMixin):
    """
    If the test job doesn't have enabled skip_build option, check whether
    there is matching build job present and report if there is no.
    """

    def pre_check(self) -> bool:
        if (
            not self.testing_farm_job_helper.skip_build
            and not self.testing_farm_job_helper.job_build
        ):
            logger.info(
                "Build required and no build job found in the configuration, "
                "reporting and skipping.",
            )
            self.testing_farm_job_helper.report_status_to_tests(
                description="Test job requires build job definition in the configuration.",
                state=BaseCommitStatus.neutral,
                url="",
                markdown_content="Make sure you have a `copr_build` job defined "
                f"with trigger `{self.testing_farm_job_helper.job_config.trigger.value}`.\n\n"
                f"For more info, please check out "
                f"[the documentation]({DOCS_TESTING_FARM}).\n\n",
            )
            return False

        return True


class IsIdentifierFromCommentMatching(Checker, GetTestingFarmJobHelperMixin):
    """
    Check that job identifier is matching comment --identifier option when it is specified.
    If identifier is not specified it will allow all jobs execution,
    otherwise only jobs with the same identifier.
    """

    def pre_check(self) -> bool:
        if (
            not self.testing_farm_job_helper.comment_arguments.labels
            and not self.testing_farm_job_helper.comment_arguments.identifier
            and (default_identifier := self.job_config.test_command.default_identifier)
        ):
            logger.info(
                f"Using the default identifier for test command: {default_identifier}",
            )
            return self.job_config.identifier == default_identifier

        if (
            not self.testing_farm_job_helper.comment_arguments.identifier
            or self.testing_farm_job_helper.comment_arguments.identifier
            == self.job_config.identifier
        ):
            return True

        logger.info(
            f"Skipping running tests for the job, identifiers doesn't match "
            f"(job:{self.job_config.identifier} "
            f"!= comment:${self.testing_farm_job_helper.comment_arguments.identifier})",
        )
        return False


class IsLabelFromCommentMatching(Checker, GetTestingFarmJobHelperMixin):
    """
    Check that job label is matching comment --labels option when it is specified.
    If labels are not specified it will allow all jobs execution,
    otherwise only jobs with the same label.
    """

    def pre_check(self) -> bool:
        if (
            not self.testing_farm_job_helper.comment_arguments.labels
            and not self.testing_farm_job_helper.comment_arguments.identifier
            and (default_labels := self.job_config.test_command.default_labels)
        ):
            logger.info(f"Using the default labels for test command: {default_labels}")
            if not self.job_config.labels:
                return False

            return any(x in default_labels for x in self.job_config.labels)

        if not self.testing_farm_job_helper.comment_arguments.labels or (
            self.job_config.labels
            and any(
                x in self.testing_farm_job_helper.comment_arguments.labels
                for x in self.job_config.labels
            )
        ):
            return True

        logger.info(
            f"Skipping running tests for the job, labels don't match "
            f"(job:{self.job_config.labels} "
            f"!= comment:${self.testing_farm_job_helper.comment_arguments.labels})",
        )
        return False


class _TestingFarmTestTypeChecker(Checker):
    """
    Base checker for determining test type (upstream vs downstream).
    Extracts common logic for checking if a test run is upstream or downstream.
    """

    def _get_test_run_model(self) -> Optional[TFTTestRunTargetModel]:
        """
        Get the test run model for the current event.

        Returns:
            TFTTestRunTargetModel or None if:
            - Event is not a testing_farm.Result event
            - pipeline_id is missing
            - test_run_model doesn't exist yet
        """
        # Only check for testing_farm.Result events
        if self.data.event_type != testing_farm.Result.event_type():
            return None

        pipeline_id = self.data.event_dict.get("pipeline_id")
        if not pipeline_id:
            return None

        return TFTTestRunTargetModel.get_by_pipeline_id(pipeline_id=pipeline_id)

    @staticmethod
    def _is_downstream_test(test_run_model: TFTTestRunTargetModel) -> bool:
        """
        Check if a test run is a downstream/Fedora CI test.

        Args:
            test_run_model: The test run model to check

        Returns:
            True if the test is downstream (has fedora_ci_test in data), False otherwise
        """
        return bool(test_run_model.data and test_run_model.data.get("fedora_ci_test"))


class IsUpstreamTest(_TestingFarmTestTypeChecker):
    """
    Check that the test is an upstream test (not a downstream/Fedora CI test).
    This checker filters out downstream tests from the upstream handler.
    """

    def pre_check(self) -> bool:
        test_run_model = self._get_test_run_model()
        if test_run_model is None:
            # Can't determine test type - allow it through
            return True

        if self._is_downstream_test(test_run_model):
            logger.debug(
                f"Skipping downstream test (pipeline_id: {test_run_model.pipeline_id}). "
                "This test is handled by DownstreamTestingFarmResultsHandler.",
            )
            return False

        return True


class IsDownstreamTest(_TestingFarmTestTypeChecker):
    """
    Check that the test is a downstream/Fedora CI test (not an upstream test).
    This checker filters out upstream tests from the downstream handler.
    """

    def pre_check(self) -> bool:
        test_run_model = self._get_test_run_model()
        if test_run_model is None:
            # Can't determine test type - allow it through
            return True

        if not self._is_downstream_test(test_run_model):
            logger.debug(
                f"Skipping upstream test (pipeline_id: {test_run_model.pipeline_id}). "
                "This test is handled by TestingFarmResultsHandler.",
            )
            return False

        return True
