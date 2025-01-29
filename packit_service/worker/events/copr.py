# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import os
from logging import getLogger
from typing import Optional, Union

from ogr.abstract import GitProject
from ogr.services.pagure import PagureProject

from packit_service.constants import COPR_SRPM_CHROOT
from packit_service.models import (
    AbstractProjectObjectDbType,
    CoprBuildTargetModel,
    ProjectEventModel,
    ProjectEventModelType,
    SRPMBuildModel,
)

from .abstract.base import Result
from .enums import FedmsgTopic

logger = getLogger(__name__)


class CoprBuild(Result):
    build: Optional[Union[SRPMBuildModel, CoprBuildTargetModel]]

    def __init__(
        self,
        topic: str,
        build_id: int,
        build: Union[CoprBuildTargetModel, SRPMBuildModel],
        chroot: str,
        status: int,
        owner: str,
        project_name: str,
        pkg: str,
        timestamp,
    ):
        trigger_db = build.get_project_event_object()
        self.commit_sha = build.commit_sha
        self.base_repo_name = trigger_db.project.repo_name
        self.base_repo_namespace = trigger_db.project.namespace
        git_ref = self.commit_sha  # ref should be name of the branch, not a hash
        self.topic = FedmsgTopic(topic)

        trigger_db = build.get_project_event_object()
        pr_id = None
        if trigger_db.project_event_model_type == ProjectEventModelType.pull_request:
            pr_id = trigger_db.pr_id  # type: ignore
            self.identifier = str(trigger_db.pr_id)  # type: ignore
        elif trigger_db.project_event_model_type == ProjectEventModelType.release:
            pr_id = None
            self.identifier = trigger_db.tag_name  # type: ignore
        elif trigger_db.project_event_model_type == ProjectEventModelType.branch_push:
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

    @classmethod
    def event_type(cls) -> str:
        assert os.environ.get("PYTEST_VERSION"), "Should be initialized only during tests"
        return "test.copr.Build"

    def get_db_project_object(self) -> Optional[AbstractProjectObjectDbType]:
        return self.build.get_project_event_object()

    def get_db_project_event(self) -> Optional[ProjectEventModel]:
        return self.build.get_project_event_model()

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
    ) -> Optional["CoprBuild"]:
        """Return cls instance or None if build_id not in CoprBuildDB"""
        build: Optional[Union[SRPMBuildModel, CoprBuildTargetModel]]
        if chroot == COPR_SRPM_CHROOT:
            build = SRPMBuildModel.get_by_copr_build_id(str(build_id))
        else:
            build = CoprBuildTargetModel.get_by_build_id(str(build_id), chroot)

        if not build:
            logger.warning(
                f"Build id {build_id} not in "
                f"{'SRPMBuildDB' if chroot == COPR_SRPM_CHROOT else 'CoprBuildDB'}.",
            )
            return None

        return cls(
            topic,
            build_id,
            build,
            chroot,
            status,
            owner,
            project_name,
            pkg,
            timestamp,
        )

    @classmethod
    def from_event_dict(cls, event: dict):
        return CoprBuild.from_build_id(
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

    def get_non_serializable_attributes(self):
        return [*super().get_non_serializable_attributes(), "build"]

    def get_dict(self, default_dict: Optional[dict] = None) -> dict:
        result = super().get_dict()
        result["topic"] = result["topic"].value
        return result

    def get_copr_build_url(self) -> str:
        return (
            "https://copr.fedorainfracloud.org/coprs/"
            f"{self.owner}/{self.project_name}/build/{self.build_id}/"
        )

    def get_copr_build_logs_url(self) -> str:
        pkg = "" if self.chroot == COPR_SRPM_CHROOT else f"-{self.pkg}"
        # https://github.com/packit/packit-service/issues/1387
        return (
            "https://download.copr.fedorainfracloud.org/"
            f"results/{self.owner}/{self.project_name}/{self.chroot}/"
            f"{self.build_id:08d}{pkg}/builder-live.log"
        )


class Start(CoprBuild):
    @classmethod
    def event_type(cls) -> str:
        return "copr.build.Start"


class End(CoprBuild):
    @classmethod
    def event_type(cls) -> str:
        return "copr.build.End"
