# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from typing import Optional, Union

from ogr.abstract import GitProject
from ogr.services.pagure import PagureProject
from packit.config import JobConfigTriggerType, PackageConfig

from packit_service.constants import KojiBuildState, KojiTaskState
from packit_service.models import (
    GitBranchModel,
    ProjectReleaseModel,
    PullRequestModel,
)
from packit_service.package_config_getter import PackageConfigGetter

from ..event import (
    use_for_job_config_trigger,
)
from .abstract import KojiEvent

logger = logging.getLogger(__name__)


@use_for_job_config_trigger(trigger_type=JobConfigTriggerType.commit)
class Build(KojiEvent):
    """Represents a change in the state of the non-scratch Koji build.

    Docs: https://fedora-fedmsg.readthedocs.io/en/latest/topics.html#buildsys-build-state-change
    """

    def __init__(
        self,
        build_id: int,
        state: KojiBuildState,
        package_name: str,
        branch_name: str,
        commit_sha: str,
        namespace: str,
        repo_name: str,
        project_url: str,
        epoch: str,
        version: str,
        release: str,
        task_id: int,
        owner: str,
        web_url: Optional[str] = None,
        old_state: Optional[KojiBuildState] = None,
        rpm_build_task_ids: Optional[dict[str, int]] = None,
        start_time: Optional[Union[int, float, str]] = None,
        completion_time: Optional[Union[int, float, str]] = None,
    ):
        super().__init__(
            task_id=task_id,
            rpm_build_task_ids=rpm_build_task_ids,
            start_time=start_time,
            completion_time=completion_time,
        )
        self.build_id = build_id
        self.state = state
        self.old_state = old_state
        self.package_name = package_name
        self.task_id = task_id
        self.epoch = epoch
        self.version = version
        self.release = release
        self.web_url = web_url
        self.branch_name = branch_name
        self._commit_sha = commit_sha  # we know  it, no need to get it from db
        self.namespace = namespace
        self.repo_name = repo_name
        self.project_url = project_url
        self.owner = owner

    @classmethod
    def event_type(cls) -> str:
        return "koji.result.Build"

    def get_packages_config(self) -> Optional[PackageConfig]:
        logger.debug(
            f"Getting packages_config:\n"
            f"\tproject: {self.project}\n"
            f"\tdefault_branch: {self.project.default_branch}\n",
        )

        return PackageConfigGetter.get_package_config_from_repo(
            base_project=None,
            project=self.project,
            pr_id=None,
            reference=self.project.default_branch,
            fail_when_missing=self.fail_when_config_file_missing,
        )

    @property
    def commit_sha(self) -> Optional[str]:  # type:ignore
        # mypy does not like properties
        return self._commit_sha

    @property
    def nvr(self) -> str:
        return f"{self.package_name}-{self.version}-{self.release}"

    @property
    def git_ref(self) -> str:
        return self.branch_name

    @property
    def identifier(self) -> str:
        return self.branch_name

    def get_dict(self, default_dict: Optional[dict] = None) -> dict:
        result = super().get_dict()
        result["state"] = result["state"].value
        result["old_state"] = result["old_state"].value if self.old_state else None
        result["commit_sha"] = result.pop("_commit_sha")  # commit_sha is a property
        return result

    @classmethod
    def from_event_dict(cls, event: dict) -> "Build":
        return Build(
            build_id=event.get("build_id"),
            state=KojiBuildState(raw_new) if (raw_new := event.get("state")) else None,
            old_state=(KojiBuildState(raw_old) if (raw_old := event.get("old_state")) else None),
            task_id=event.get("task_id"),
            rpm_build_task_ids=event.get("rpm_build_task_ids"),
            package_name=event.get("package_name"),
            project_url=event.get("project_url"),
            web_url=event.get("web_url"),
            branch_name=event.get("branch_name"),
            repo_name=event.get("repo_name"),
            namespace=event.get("namespace"),
            commit_sha=event.get("commit_sha"),
            epoch=event.get("epoch"),
            version=event.get("version"),
            release=event.get("release"),
            start_time=event.get("start_time"),
            completion_time=event.get("completion_time"),
            owner=event.get("owner"),
        )


class Task(KojiEvent):
    """Represents a change in the result of the scratch build task.

    Docs: https://fedora-fedmsg.readthedocs.io/en/latest/topics.html#buildsys-task-state-change
    """

    def __init__(
        self,
        task_id: int,
        state: KojiTaskState,
        old_state: Optional[KojiTaskState] = None,
        rpm_build_task_ids: Optional[dict[str, int]] = None,
        start_time: Optional[Union[int, float, str]] = None,
        completion_time: Optional[Union[int, float, str]] = None,
    ):
        super().__init__(
            task_id=task_id,
            rpm_build_task_ids=rpm_build_task_ids,
            start_time=start_time,
            completion_time=completion_time,
        )
        self.state = state
        self.old_state = old_state

        # Lazy properties
        self._pr_id: Optional[int] = None
        self._identifier: Optional[str] = None
        self._git_ref: Optional[str] = None
        self._commit_sha: Optional[str] = None

    @classmethod
    def event_type(cls) -> str:
        return "koji.result.Task"

    @property
    def pr_id(self) -> Optional[int]:
        if not self._pr_id and isinstance(self.db_project_object, PullRequestModel):
            self._pr_id = self.db_project_object.pr_id
        return self._pr_id

    @property
    def commit_sha(self) -> Optional[str]:  # type:ignore
        # mypy does not like properties
        if not self.build_model:
            return None

        if not self._commit_sha:
            self._commit_sha = self.build_model.commit_sha
        return self._commit_sha

    @property
    def git_ref(self) -> str:
        if not self._git_ref:
            if isinstance(self.db_project_object, PullRequestModel):
                self._git_ref = self.commit_sha
            elif isinstance(self.db_project_object, ProjectReleaseModel):
                self._git_ref = self.db_project_object.tag_name
            elif isinstance(self.db_project_object, GitBranchModel):
                self._git_ref = self.db_project_object.name
            else:
                self._git_ref = self.commit_sha
        return self._git_ref

    @property
    def identifier(self) -> str:
        if not self._identifier:
            if isinstance(self.db_project_object, PullRequestModel):
                self._identifier = str(self.db_project_object.pr_id)
            elif isinstance(self.db_project_object, ProjectReleaseModel):
                self._identifier = self.db_project_object.tag_name
            elif isinstance(self.db_project_object, GitBranchModel):
                self._identifier = self.db_project_object.name
            else:
                self._identifier = self.commit_sha
        return self._identifier

    @classmethod
    def from_event_dict(cls, event: dict) -> "Task":
        return Task(
            task_id=event.get("task_id"),
            state=KojiTaskState(event.get("state")) if event.get("state") else None,
            old_state=(KojiTaskState(event.get("old_state")) if event.get("old_state") else None),
            rpm_build_task_ids=event.get("rpm_build_task_ids"),
            start_time=event.get("start_time"),
            completion_time=event.get("completion_time"),
        )

    def get_base_project(self) -> Optional[GitProject]:
        if self.pr_id is not None:
            if isinstance(self.project, PagureProject):
                return self.project.service.get_project(
                    namespace=self.project.namespace,
                    username=self.pull_request_object.author,
                    repo=self.project.repo,
                    is_fork=True,
                )
            return None  # With Github app, we cannot work with fork repo
        return self.project

    def get_dict(self, default_dict: Optional[dict] = None) -> dict:
        result = super().get_dict()
        result["state"] = result["state"].value
        result["old_state"] = result["old_state"].value
        result["commit_sha"] = self.commit_sha
        result["pr_id"] = self.pr_id
        result["git_ref"] = self.git_ref
        result["identifier"] = self.identifier
        result["target"] = self.target
        return result
