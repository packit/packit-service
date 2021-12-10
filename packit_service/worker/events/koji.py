# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from typing import Union, Optional, Dict

from ogr.abstract import GitProject
from ogr.services.pagure import PagureProject

from packit_service.constants import KojiBuildState
from packit_service.models import (
    AbstractTriggerDbType,
    KojiBuildModel,
    PullRequestModel,
    ProjectReleaseModel,
    GitBranchModel,
)
from packit_service.worker.events.event import AbstractForgeIndependentEvent


class KojiTaskEvent(AbstractForgeIndependentEvent):
    def __init__(
        self,
        build_id: int,
        state: KojiBuildState,
        old_state: Optional[KojiBuildState] = None,
        rpm_build_task_id: Optional[int] = None,
        start_time: Optional[Union[int, float, str]] = None,
        completion_time: Optional[Union[int, float, str]] = None,
    ):
        super().__init__()
        self.build_id = build_id
        self.state = state
        self.old_state = old_state
        self.start_time: Optional[Union[int, float, str]] = start_time
        self.completion_time: Optional[Union[int, float, str]] = completion_time
        self.rpm_build_task_id = rpm_build_task_id

        # Lazy properties
        self._pr_id: Optional[int] = None
        self._commit_sha: Optional[str] = None
        self._db_trigger: Optional[AbstractTriggerDbType] = None
        self._identifier: Optional[str] = None
        self._build_model: Optional[KojiBuildModel] = None
        self._git_ref: Optional[str] = None

    @property
    def pr_id(self) -> Optional[int]:
        if not self._pr_id and isinstance(self.db_trigger, PullRequestModel):
            self._pr_id = self.db_trigger.pr_id
        return self._pr_id

    @property
    def commit_sha(self) -> Optional[str]:  # type:ignore
        if not self.build_model:
            return None

        # mypy does not like properties
        if not self._commit_sha:
            self._commit_sha = self.build_model.commit_sha
        return self._commit_sha

    @property
    def build_model(self) -> Optional[KojiBuildModel]:
        if not self._build_model:
            self._build_model = KojiBuildModel.get_by_build_id(build_id=self.build_id)
        return self._build_model

    @property
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        if not self._db_trigger and self.build_model:
            self._db_trigger = self.build_model.get_trigger_object()
        return self._db_trigger

    @property
    def git_ref(self) -> str:
        if not self._git_ref:
            if isinstance(self.db_trigger, PullRequestModel):
                self._git_ref = self.commit_sha
            elif isinstance(self.db_trigger, ProjectReleaseModel):
                self._git_ref = self.db_trigger.tag_name
            elif isinstance(self.db_trigger, GitBranchModel):
                self._git_ref = self.db_trigger.name
            else:
                self._git_ref = self.commit_sha
        return self._git_ref

    @property
    def identifier(self) -> str:
        if not self._identifier:
            if isinstance(self.db_trigger, PullRequestModel):
                self._identifier = str(self.db_trigger.pr_id)
            elif isinstance(self.db_trigger, ProjectReleaseModel):
                self._identifier = self.db_trigger.tag_name
            elif isinstance(self.db_trigger, GitBranchModel):
                self._identifier = self.db_trigger.name
            else:
                self._identifier = self.commit_sha
        return self._identifier

    @classmethod
    def from_event_dict(cls, event: dict):
        return KojiTaskEvent(
            build_id=event.get("build_id"),
            state=KojiBuildState(event.get("state")) if event.get("state") else None,
            old_state=(
                KojiBuildState(event.get("old_state"))
                if event.get("old_state")
                else None
            ),
            rpm_build_task_id=event.get("rpm_build_task_id"),
            start_time=event.get("start_time"),
            completion_time=event.get("completion_time"),
        )

    def get_base_project(self) -> Optional[GitProject]:
        if self.pr_id is not None:
            if isinstance(self.project, PagureProject):
                pull_request = self.project.get_pr(pr_id=self.pr_id)
                return self.project.service.get_project(
                    namespace=self.project.namespace,
                    username=pull_request.author,
                    repo=self.project.repo,
                    is_fork=True,
                )
            else:
                return None  # With Github app, we cannot work with fork repo
        return self.project

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        result = super().get_dict()
        result["state"] = result["state"].value
        result["old_state"] = result["old_state"].value
        result["commit_sha"] = self.commit_sha
        result["pr_id"] = self.pr_id
        result["git_ref"] = self.git_ref
        result["identifier"] = self.identifier
        result.pop("_build_model")
        result.pop("_db_trigger")
        return result

    def get_koji_build_logs_url(
        self, koji_logs_url: str = "https://kojipkgs.fedoraproject.org"
    ) -> Optional[str]:
        if not self.rpm_build_task_id:
            return None

        return (
            f"{koji_logs_url}//work/tasks/"
            f"{self.rpm_build_task_id % 10000}/{self.rpm_build_task_id}/build.log"
        )

    def get_koji_rpm_build_web_url(
        self, koji_web_url: str = "https://koji.fedoraproject.org"
    ) -> Optional[str]:
        if not self.rpm_build_task_id:
            return None

        return f"{koji_web_url}/koji/taskinfo?taskID={self.rpm_build_task_id}"
