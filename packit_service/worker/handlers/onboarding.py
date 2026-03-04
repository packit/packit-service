# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
This file defines classes for job handlers specific to onboarding tasks
"""

import logging

from packit.cli.dist_git_init import (
    COMMIT_MESSAGE,
    CONFIG_FILE_NAME,
    ONBOARD_BRANCH_NAME,
    DistGitInitializer,
)
from packit.config.config import Config
from packit.config.package_config import PackageConfig

from packit_service.constants import DG_ONBOARDING_DESCRIPTION, DG_ONBOARDING_TITLE
from packit_service.events import (
    onboarding,
)
from packit_service.worker.checker.abstract import Checker
from packit_service.worker.checker.onboarding import ProjectIsNotOnboarded
from packit_service.worker.handlers.abstract import (
    JobHandler,
    TaskName,
    reacts_to,
)
from packit_service.worker.mixin import ConfigFromEventMixin, PackitAPIWithDownstreamMixin
from packit_service.worker.result import TaskResults

logger = logging.getLogger(__name__)


@reacts_to(event=onboarding.Request)
class OnboardingRequestHandler(
    JobHandler,
    ConfigFromEventMixin,
    PackitAPIWithDownstreamMixin,
):
    task_name = TaskName.onboarding_request

    @staticmethod
    def get_checkers() -> tuple[type[Checker], ...]:
        return (ProjectIsNotOnboarded,)

    def _run(self) -> TaskResults:
        package = self.project.repo
        logger.debug(f"Running onboarding for {package}")

        # generate and load config
        initializer = DistGitInitializer(
            config=Config(),
            path_or_url="",
            upstream_git_url=None,
        )
        self.package_config = self.job_config = PackageConfig.get_from_dict(
            initializer.package_config_dict | {"downstream_package_name": package},
        )

        self.perform_onboarding(
            config=initializer.package_config_content,
            open_pr=self.data.event_dict.get("open_pr", True),
        )

        return TaskResults(success=True, details={})

    def perform_onboarding(self, config: str, open_pr: bool) -> None:
        # clone the repo and fetch rawhide
        self.packit_api.dg.create_branch(
            "rawhide",
            base="remotes/origin/rawhide",
            setup_tracking=True,
        )
        self.packit_api.dg.update_branch("rawhide")
        self.packit_api.dg.switch_branch("rawhide", force=True)

        if open_pr:
            self.packit_api.dg.create_branch(ONBOARD_BRANCH_NAME)
            self.packit_api.dg.switch_branch(ONBOARD_BRANCH_NAME, force=True)
            self.packit_api.dg.reset_workdir()

        working_dir = self.packit_api.dg.local_project.working_dir

        # create config file
        (working_dir / CONFIG_FILE_NAME).write_text(config)

        self.packit_api.dg.commit(
            title=COMMIT_MESSAGE,
            msg="",
            prefix="",
        )

        if open_pr:
            self.packit_api.push_and_create_pr(
                pr_title=DG_ONBOARDING_TITLE,
                pr_description=DG_ONBOARDING_DESCRIPTION,
                git_branch="rawhide",
                repo=self.packit_api.dg,
            )
        else:
            self.packit_api.dg.push(refspec="HEAD:rawhide")
