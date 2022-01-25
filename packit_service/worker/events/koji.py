# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from typing import Union, Optional, Dict

from ogr.abstract import GitProject
from ogr.services.pagure import PagureProject

from packit_service.constants import KojiBuildState
from packit_service.models import (
    AbstractTriggerDbType,
    JobTriggerModelType,
    KojiBuildModel,
    PullRequestModel,
    ProjectReleaseModel,
    GitBranchModel,
    RunModel,
)
from packit_service.worker.events.event import AbstractForgeIndependentEvent


class AbstractKojiEvent(AbstractForgeIndependentEvent):
    def __init__(
        self,
        build_id: int,
        rpm_build_task_id: Optional[int] = None,
    ):
        super().__init__()
        self.build_id = build_id
        self.rpm_build_task_id = rpm_build_task_id

        # Lazy properties
        self._target: Optional[str] = None
        self._build_model: Optional[KojiBuildModel] = None
        self._build_model_searched = False
        self._db_trigger: Optional[AbstractTriggerDbType] = None

    @property
    def build_model(self) -> Optional[KojiBuildModel]:
        if not self._build_model_searched and not self._build_model:
            self._build_model = KojiBuildModel.get_by_build_id(build_id=self.build_id)
            self._build_model_searched = True
        return self._build_model

    @property
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        if not self._db_trigger and self.build_model:
            self._db_trigger = self.build_model.get_trigger_object()
        return self._db_trigger

    @property
    def target(self) -> Optional[str]:
        if not self._target and self.build_model:
            self._target = self.build_model.target
        return self._target

    @staticmethod
    def get_koji_build_logs_url(
        rpm_build_task_id: int,
        koji_logs_url: str = "https://kojipkgs.fedoraproject.org",
    ) -> str:
        """
        Constructs the log URL for the given Koji task.
        You can redefine the Koji instance using the one defined in the service config.
        """
        return (
            f"{koji_logs_url}//work/tasks/"
            f"{rpm_build_task_id % 10000}/{rpm_build_task_id}/build.log"
        )

    @staticmethod
    def get_koji_rpm_build_web_url(
        rpm_build_task_id: int,
        koji_web_url: str = "https://koji.fedoraproject.org",
    ) -> str:
        """
        Constructs the web URL for the given Koji task.
        You can redefine the Koji instance using the one defined in the service config.
        """
        return f"{koji_web_url}/koji/taskinfo?taskID={rpm_build_task_id}"

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        result = super().get_dict()
        result.pop("_build_model")
        result.pop("_build_model_searched")
        result.pop("_db_trigger")
        return result


class KojiBuildEvent(AbstractKojiEvent):
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
        rpm_build_task_id: int,
        web_url: Optional[str] = None,
        old_state: Optional[KojiBuildState] = None,
    ):
        super().__init__(build_id)
        self.state = state
        self.old_state = old_state
        self.package_name = package_name
        self.rpm_build_task_id = rpm_build_task_id
        self.epoch = epoch
        self.version = version
        self.release = release
        self.web_url = web_url

        self.branch_name = branch_name
        self._commit_sha = commit_sha  # we know  it, no need to get it from db
        self.namespace = namespace
        self.repo_name = repo_name
        self.project_url = project_url

    @property
    def commit_sha(self) -> Optional[str]:  # type:ignore
        # mypy does not like properties
        return self._commit_sha

    @property
    def build_model(self) -> Optional[KojiBuildModel]:
        if not super().build_model:
            self._build_model = KojiBuildModel.create(
                build_id=str(self.build_id),
                commit_sha=self._commit_sha,
                web_url=self.web_url,
                target="noarch",  # TODO: where to get this info from?
                status=self.state.value,
                run_model=RunModel.create(
                    type=JobTriggerModelType.branch_push,
                    trigger_id=GitBranchModel.get_or_create(
                        branch_name=self.branch_name,
                        repo_name=self.repo_name,
                        namespace=self.namespace,
                        project_url=self.project_url,
                    ).id,
                ),
            )
        return self._build_model

    @property
    def git_ref(self) -> str:
        return self.branch_name

    @property
    def identifier(self) -> str:
        return self.branch_name

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        result = super().get_dict()
        result["state"] = result["state"].value
        result["old_state"] = result["old_state"].value
        result["commit_sha"] = result.pop("_commit_sha")  # commit_sha is a property
        return result

    @classmethod
    def from_event_dict(cls, event: dict):
        return KojiBuildEvent(
            build_id=event.get("build_id"),
            state=KojiBuildState(raw_new) if (raw_new := event.get("state")) else None,
            old_state=(
                KojiBuildState(raw_old) if (raw_old := event.get("old_state")) else None
            ),
            rpm_build_task_id=event.get("rpm_build_task_id"),
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
        )


class KojiTaskEvent(AbstractKojiEvent):
    """
    Used for scratch builds.
    """

    def __init__(
        self,
        build_id: int,
        state: KojiBuildState,
        old_state: Optional[KojiBuildState] = None,
        rpm_build_task_id: Optional[int] = None,
        start_time: Optional[Union[int, float, str]] = None,
        completion_time: Optional[Union[int, float, str]] = None,
    ):
        super().__init__(build_id=build_id, rpm_build_task_id=rpm_build_task_id)
        self.state = state
        self.old_state = old_state
        self.start_time: Optional[Union[int, float, str]] = start_time
        self.completion_time: Optional[Union[int, float, str]] = completion_time

        # Lazy properties
        self._pr_id: Optional[int] = None
        self._identifier: Optional[str] = None
        self._git_ref: Optional[str] = None
        self._commit_sha: Optional[str] = None

    @property
    def pr_id(self) -> Optional[int]:
        if not self._pr_id and isinstance(self.db_trigger, PullRequestModel):
            self._pr_id = self.db_trigger.pr_id
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
        return result
