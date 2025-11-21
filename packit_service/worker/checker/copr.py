# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from pathlib import Path

from packit.config import JobConfigTriggerType

from packit_service.constants import (
    INTERNAL_TF_BUILDS_AND_TESTS_NOT_ALLOWED,
)
from packit_service.events import github, gitlab
from packit_service.worker.checker.abstract import (
    ActorChecker,
    Checker,
)
from packit_service.worker.handlers.mixin import (
    ConfigFromEventMixin,
    GetCoprBuildJobHelperForIdMixin,
    GetCoprBuildJobHelperMixin,
    GetCoprSRPMBuildMixin,
)
from packit_service.worker.reporting import BaseCommitStatus

logger = logging.getLogger(__name__)


class IsJobConfigTriggerMatching(
    Checker,
    ConfigFromEventMixin,
    GetCoprBuildJobHelperMixin,
):
    def pre_check(self) -> bool:
        return self.copr_build_helper.is_job_config_trigger_matching(self.job_config)


class IsGitForgeProjectAndEventOk(
    Checker,
    ConfigFromEventMixin,
    GetCoprBuildJobHelperMixin,
):
    def pre_check(
        self,
    ) -> bool:
        if (
            self.data.event_type == gitlab.mr.Action.event_type()
            and self.data.event_dict["action"] == gitlab.enums.Action.closed.value
        ):
            # Not interested in closed merge requests
            return False

        if not (self.copr_build_helper.job_build or self.copr_build_helper.job_tests_all):
            logger.info("No copr_build or tests job defined.")
            # we can't report it to end-user at this stage
            return False

        if self.copr_build_helper.is_custom_copr_project_defined():
            logger.debug(
                "Custom Copr owner/project set. "
                "Checking if this GitHub project can use this Copr project.",
            )
            if not self.copr_build_helper.check_if_custom_copr_can_be_used_and_report():
                return False

        return True


class AreOwnerAndProjectMatchingJob(Checker, GetCoprBuildJobHelperForIdMixin):
    def pre_check(self) -> bool:
        if (
            self.copr_event.owner == self.copr_build_helper.job_owner
            and self.copr_event.project_name == self.copr_build_helper.job_project
        ):
            return True

        logger.debug(
            f"The Copr project {self.copr_event.owner}/{self.copr_event.project_name} "
            f"does not match the configuration "
            f"({self.copr_build_helper.job_owner}/{self.copr_build_helper.job_project} expected).",
        )
        return False


class IsPackageMatchingJobView(Checker, GetCoprSRPMBuildMixin):
    """
    When running builds for multiple packages (in monorepo) in one job
    config, we need to check whether the package that we are handling matches
    the job configuration.
    """

    def pre_check(self) -> bool:
        build_for_package = self.build.get_package_name()
        if not self.job_config.package or build_for_package == self.job_config.package:
            return True

        logger.debug(
            f"The Copr build {self.copr_event.build_id} (pkg={build_for_package}) "
            f"does not match the package from the configuration "
            f"({self.job_config.package}).",
        )
        return False


class BuildNotAlreadyStarted(Checker, GetCoprSRPMBuildMixin):
    def pre_check(self) -> bool:
        build = self.build
        if not build:
            return True
        return not bool(build.build_start_time)


class CanActorRunTestsJob(
    ActorChecker,
    ConfigFromEventMixin,
    GetCoprBuildJobHelperMixin,
):
    """For external contributors, we need to be more careful when running jobs.
    This is a handler-specific permission check
    for a user who trigger the action on a PR.
    """

    def _pre_check(self) -> bool:
        # check the actor if there is any test job which requires
        # builds and uses internal TF
        for test_job in self.copr_build_helper.job_tests_all:
            if (
                test_job
                and test_job.use_internal_tf
                and not test_job.skip_build
                and not self.project.can_merge_pr(self.actor)
                and self.actor not in self.service_config.admins
            ):
                self.copr_build_helper.report_status_to_build(
                    description=INTERNAL_TF_BUILDS_AND_TESTS_NOT_ALLOWED[0].format(
                        actor=self.actor,
                    ),
                    state=BaseCommitStatus.neutral,
                    markdown_content=INTERNAL_TF_BUILDS_AND_TESTS_NOT_ALLOWED[1].format(
                        packit_comment_command_prefix=self.service_config.comment_command_prefix,
                    ),
                )
                return False
        return True


class AreFilesChanged(Checker, GetCoprBuildJobHelperForIdMixin, ConfigFromEventMixin):
    """
    Check if any files under the current package's `paths` field is changed.
    If not, then just skip the current copr build job.
    """

    def get_files_changed(self) -> list[Path]:
        """
        Get the list of files changed in the current commit or the current pullrequest
        """
        # Get the changes object
        if self.job_config.trigger == JobConfigTriggerType.pull_request:
            pr_event = self.data.to_event()
            if not isinstance(pr_event, (github.pr.Action, gitlab.mr.Action)):
                # TODO: What about comments?
                raise NotImplementedError()
            # TODO: How to handle PRs?
            raise NotImplementedError()
        if self.job_config.trigger == JobConfigTriggerType.commit:
            push_event = self.data.to_event()
            if not isinstance(push_event, (github.push.Commit, gitlab.push.Commit)):
                raise NotImplementedError()
            files = set()
            for commit in push_event.commits:
                files |= set(commit["modified"])
                files |= set(commit["added"])
            # TODO: Check what the path is relative to
            return [Path(file) for file in files]
        raise NotImplementedError(f"Trigger not supported: {self.job_config.trigger}")

    def pre_check(self) -> bool:
        if self.job_config.trigger == JobConfigTriggerType.release:
            # For releases we don't do any checks
            return True
        # FIXME: This is probably unnecessary
        package_config = self.package_config.get_package_config_for(self.job_config)
        # The paths that we need to check for files changed
        paths = package_config["paths"]
        # Early check if the git root was included, in which case we don't need to
        # check the files changed
        # TODO: refine this check when gitignore-like patterns are supported
        if "./" in paths:
            return True
        for changed_file in self.get_files_changed():
            # Check if any of the files changed are under the paths that are being tracked
            if any(changed_file.is_relative_to(p) for p in paths):
                return True
        return False
