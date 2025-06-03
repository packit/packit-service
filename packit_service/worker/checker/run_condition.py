# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from ogr.services.pagure import PagureProject
from packit.actions import ActionName
from packit.actions_handler import ActionsHandler
from packit.command_handler import (
    RUN_COMMAND_HANDLER_MAPPING,
    CommandHandler,
    SandcastleCommandHandler,
)
from packit.config import JobConfig, PackageConfig
from packit.exceptions import PackitCommandFailedError
from specfile import Specfile

from packit_service.events import (
    anitya,
    github,
    gitlab,
    koji,
    pagure,
)
from packit_service.worker.checker.abstract import Checker
from packit_service.worker.mixin import ConfigFromEventMixin, PackitAPIWithUpstreamMixin

logger = logging.getLogger(__name__)


class IsRunConditionSatisfied(Checker, ConfigFromEventMixin, PackitAPIWithUpstreamMixin):
    def __init__(
        self,
        package_config: PackageConfig,
        job_config: JobConfig,
        event: dict,
        task_name: Optional[str] = None,
    ):
        super().__init__(
            package_config=package_config,
            job_config=job_config,
            event=event,
            task_name=task_name,
        )
        self._handler_kls = None
        self._working_dir: Optional[Path] = None
        self._command_handler: Optional[CommandHandler] = None
        self._actions_handler: Optional[ActionsHandler] = None

    @property
    def handler_kls(self):
        if self._handler_kls is None:
            logger.debug(f"Command handler: {self.service_config.command_handler}")
            self._handler_kls = RUN_COMMAND_HANDLER_MAPPING[self.service_config.command_handler]
        return self._handler_kls

    @property
    def working_dir(self) -> Optional[Path]:
        if not self._working_dir:
            if self.handler_kls == SandcastleCommandHandler:
                path = (
                    Path(self.service_config.command_handler_work_dir) / "run-condition-working-dir"
                )
                path.mkdir(parents=True, exist_ok=True)
                self._working_dir = path
            else:
                self._working_dir = Path(tempfile.mkdtemp())
            logger.info(
                f"Created directory for the run-condition action: {self._working_dir}",
            )
        return self._working_dir

    @property
    def command_handler(self) -> CommandHandler:
        if self._command_handler is None:
            self._command_handler = self.handler_kls(
                config=self.service_config,
                working_dir=self.working_dir,
            )
        return self._command_handler

    @property
    def actions_handler(self) -> ActionsHandler:
        if not self._actions_handler:
            self._actions_handler = ActionsHandler(
                self.job_config,
                self.command_handler,
            )
        return self._actions_handler

    def common_env(
        self, version: Optional[str] = None, extra_env: Optional[dict[str, str]] = None
    ) -> dict[str, str]:
        env = self.job_config.get_base_env()
        if version:
            env["PACKIT_PROJECT_VERSION"] = version
        if extra_env:
            env.update(extra_env)
        return env

    def pre_check(self) -> bool:
        project = self.project
        git_ref = self.data.commit_sha
        version = None

        if self.data.event_type in (
            github.pr.Action.event_type(),
            pagure.pr.Action.event_type(),
            gitlab.mr.Action.event_type(),
            github.pr.Comment.event_type(),
            pagure.pr.Comment.event_type(),
            gitlab.mr.Comment.event_type(),
            pagure.pr.Flag.event_type(),
            github.check.PullRequest.event_type(),
        ):
            project = self.project.get_pr(int(self.data.pr_id)).source_project
        elif self.data.event_type in (anitya.NewHotness.event_type(),):
            event = anitya.NewHotness.from_event_dict(self.data.event_dict)
            project = event.project
            git_ref = event.tag_name
            version = event.version
        elif self.data.event_type in (
            github.issue.Comment.event_type(),
            gitlab.issue.Comment.event_type(),
        ):
            if self.task_name not in ("task.run_propose_downstream_handler",):
                project = self.service_config.get_project(
                    url=self.data.event_dict.get("dist_git_project_url")
                )
            git_ref = "HEAD"
        elif self.data.event_type in (koji.tag.Build.event_type(),):
            git_ref = "HEAD"
            version = self.data.event_dict.get("version")
        elif self.data.event_type in (koji.result.Build.event_type(),):
            git_ref = self.data.event_dict.get("commit_sha")
            version = self.data.event_dict.get("version")

        extra_env = {}

        if self.job_config.clone_repos_before_run_condition:
            actions_handler = self.packit_api.up.actions_handler
            if isinstance(project, PagureProject):
                self.packit_api.dg.local_project.checkout_release(git_ref)
                extra_env["PACKIT_DOWNSTREAM_REPO"] = str(
                    self.packit_api.dg.local_project.working_dir
                )
                if version is None:
                    version = self.packit_api.dg.specfile.expanded_version
            else:
                self.packit_api.up.local_project.checkout_release(git_ref)
                extra_env["PACKIT_UPSTREAM_REPO"] = str(
                    self.packit_api.up.local_project.working_dir
                )
                if version is None:
                    version = self.packit_api.up.get_current_version()
        else:
            actions_handler = self.actions_handler
            specfile_path = (
                f"{self.job_config.downstream_package_name}.spec"
                if isinstance(project, PagureProject)
                else self.job_config.specfile_path
            )
            if version is None:
                try:
                    specfile_content = project.get_file_content(path=specfile_path, ref=git_ref)
                except FileNotFoundError:
                    pass
                else:
                    with Specfile(
                        content=specfile_content, sourcedir=".", force_parse=True
                    ) as specfile:
                        version = specfile.expanded_version
        try:
            from sandcastle.exceptions import SandcastleCommandFailed

            if not actions_handler.has_action(ActionName.run_condition):
                return True
            try:
                actions_handler.run_action(
                    actions=ActionName.run_condition, env=self.common_env(version, extra_env)
                )
            except PackitCommandFailedError:
                return False
            except SandcastleCommandFailed as ex:
                if json.loads(ex.reason).get("reason") != "NonZeroExitCode":
                    raise ex
                return False
            return True
        finally:
            self.clean_working_dir()

    def clean_working_dir(self) -> None:
        if self.job_config.clone_repos_before_run_condition:
            self.packit_api.up.clean_working_dir()
        else:
            if self._working_dir:
                logger.debug(f"Cleaning: {self.working_dir}")
                shutil.rmtree(self.working_dir, ignore_errors=True)
