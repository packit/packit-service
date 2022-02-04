# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from logging import getLogger
from typing import Optional, Dict, Union

from ogr.abstract import GitProject
from ogr.services.pagure import PagureProject

from packit_service.constants import COPR_SRPM_CHROOT
from packit_service.models import (
    CoprBuildModel,
    JobTriggerModelType,
    AbstractTriggerDbType,
    SRPMBuildModel,
)
from packit_service.worker.events.event import AbstractForgeIndependentEvent
from packit_service.worker.events.enums import FedmsgTopic

logger = getLogger(__name__)


class AbstractCoprBuildEvent(AbstractForgeIndependentEvent):
    build: Optional[Union[SRPMBuildModel, CoprBuildModel]]

    def __init__(
        self,
        topic: str,
        build_id: int,
        build: Union[CoprBuildModel, SRPMBuildModel],
        chroot: str,
        status: int,
        owner: str,
        project_name: str,
        pkg: str,
        timestamp,
    ):
        trigger_db = build.get_trigger_object()
        self.commit_sha = build.commit_sha
        self.base_repo_name = trigger_db.project.repo_name
        self.base_repo_namespace = trigger_db.project.namespace
        git_ref = self.commit_sha  # ref should be name of the branch, not a hash
        self.topic = FedmsgTopic(topic)

        trigger_db = build.get_trigger_object()
        pr_id = None
        if trigger_db.job_trigger_model_type == JobTriggerModelType.pull_request:
            pr_id = trigger_db.pr_id  # type: ignore
            self.identifier = str(trigger_db.pr_id)  # type: ignore
        elif trigger_db.job_trigger_model_type == JobTriggerModelType.release:
            pr_id = None
            self.identifier = trigger_db.tag_name  # type: ignore
        elif trigger_db.job_trigger_model_type == JobTriggerModelType.branch_push:
            pr_id = None
            self.identifier = trigger_db.name  # type: ignore

        super().__init__(project_url=trigger_db.project.project_url, pr_id=pr_id)

        self.git_ref = git_ref
        self.build_id = build_id
        self.build = build
        self.chroot = chroot
        self.status = status
        self.owner = owner
        self.project_name = project_name
        self.pkg = pkg
        self.timestamp = timestamp

    def get_db_trigger(self) -> Optional[AbstractTriggerDbType]:
        return self.build.get_trigger_object()

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

    @classmethod
    def from_build_id(
        cls,
        topic: str,
        build_id: int,
        chroot: str,
        status: int,
        owner: str,
        project_name: str,
        pkg: str,
        timestamp,
    ) -> Optional["AbstractCoprBuildEvent"]:
        """Return cls instance or None if build_id not in CoprBuildDB"""
        build: Optional[Union[SRPMBuildModel, CoprBuildModel]]
        if chroot == COPR_SRPM_CHROOT:
            build = SRPMBuildModel.get_by_copr_build_id(str(build_id))
        else:
            build = CoprBuildModel.get_by_build_id(str(build_id), chroot)

        if not build:
            logger.warning(
                f"Build id {build_id} not in "
                f"{'SRPMBuildDB' if chroot == COPR_SRPM_CHROOT else 'CoprBuildDB'}."
            )
            return None

        return cls(
            topic, build_id, build, chroot, status, owner, project_name, pkg, timestamp
        )

    @classmethod
    def from_event_dict(cls, event: dict):
        return AbstractCoprBuildEvent.from_build_id(
            topic=event.get("topic"),
            build_id=event.get("build_id"),
            chroot=event.get("chroot"),
            status=event.get("status"),
            owner=event.get("owner"),
            project_name=event.get("project_name"),
            pkg=event.get("pkg"),
            timestamp=event.get("timestamp"),
        )

    def pre_check(self):
        if not self.build:
            logger.warning("Copr build is not handled by this deployment.")
            return False

        return True

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        result = super().get_dict()
        result["topic"] = result["topic"].value
        result.pop("build")
        return result

    def get_copr_build_url(self) -> str:
        return (
            "https://copr.fedorainfracloud.org/coprs/"
            f"{self.owner}/{self.project_name}/build/{self.build_id}/"
        )

    def get_copr_build_logs_url(self) -> str:
        pkg = "" if self.chroot == COPR_SRPM_CHROOT else f"-{self.pkg}"
        return (
            f"https://copr-be.cloud.fedoraproject.org/results/{self.owner}/"
            f"{self.project_name}/{self.chroot}/"
            f"{self.build_id:08d}{pkg}/builder-live.log.gz"
        )


class CoprBuildStartEvent(AbstractCoprBuildEvent):
    pass


class CoprBuildEndEvent(AbstractCoprBuildEvent):
    pass
