# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from abc import abstractmethod
import logging
from typing import Optional, Protocol
from packit.config import PackageConfig, JobConfig
from packit_service.utils import get_packit_commands_from_comment
from packit_service.config import ProjectToSync
from packit_service.constants import COPR_SRPM_CHROOT
from packit_service.models import (
    AbstractTriggerDbType,
    CoprBuildTargetModel,
    SRPMBuildModel,
)
from packit_service.worker.events.event import EventData
from packit_service.worker.events.copr import AbstractCoprBuildEvent
from packit_service.worker.events.github import PullRequestCommentGithubEvent
from packit_service.worker.events.gitlab import MergeRequestCommentGitlabEvent
from packit_service.worker.events.pagure import PullRequestCommentPagureEvent
from packit_service.worker.handlers.abstract import CeleryTask
from packit_service.worker.helpers.build.copr_build import CoprBuildJobHelper
from packit_service.worker.helpers.build.koji_build import KojiBuildJobHelper
from packit_service.worker.helpers.testing_farm import TestingFarmJobHelper

from packit_service.worker.mixin import ConfigMixin
from packit_service.worker.events.koji import KojiBuildEvent
from packit_service.worker.monitoring import Pushgateway


logger = logging.getLogger(__name__)


class GetKojiBuildEvent(Protocol):
    data: EventData

    @property
    @abstractmethod
    def koji_build_event(self) -> KojiBuildEvent:
        ...


class GetKojiBuildEventMixin(ConfigMixin, GetKojiBuildEvent):
    _koji_build_event: Optional[KojiBuildEvent] = None

    @property
    def koji_build_event(self):
        if not self._koji_build_event:
            self._koji_build_event = KojiBuildEvent.from_event_dict(
                self.data.event_dict
            )
        return self._koji_build_event


class GetKojiBuildJobHelper(Protocol):
    @property
    @abstractmethod
    def koji_build_helper(self) -> KojiBuildJobHelper:
        ...


class GetKojiBuildJobHelperMixin(GetKojiBuildJobHelper, ConfigMixin):
    _koji_build_helper: Optional[KojiBuildJobHelper] = None
    package_config: PackageConfig
    job_config: JobConfig

    @property
    def koji_build_helper(self) -> KojiBuildJobHelper:
        if not self._koji_build_helper:
            self._koji_build_helper = KojiBuildJobHelper(
                service_config=self.service_config,
                package_config=self.package_config,
                project=self.project,
                metadata=self.data,
                db_trigger=self.data.db_trigger,
                job_config=self.job_config,
                build_targets_override=self.data.build_targets_override,
                tests_targets_override=self.data.tests_targets_override,
            )
        return self._koji_build_helper


class GetCoprBuildEvent(Protocol):
    data: EventData

    @property
    @abstractmethod
    def copr_event(self) -> AbstractCoprBuildEvent:
        ...


class GetCoprBuildEventMixin(ConfigMixin, GetCoprBuildEvent):
    _copr_build_event: Optional[AbstractCoprBuildEvent] = None

    @property
    def copr_event(self):
        if not self._copr_build_event:
            self._copr_build_event = AbstractCoprBuildEvent.from_event_dict(
                self.data.event_dict
            )
        return self._copr_build_event


class GetSRPMBuild(Protocol):
    @property
    @abstractmethod
    def build(self) -> Optional[SRPMBuildModel]:
        ...

    @property
    @abstractmethod
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        ...


class GetCoprSRPMBuildMixin(GetSRPMBuild, GetCoprBuildEventMixin):
    _build: Optional[SRPMBuildModel] = None
    _db_trigger: Optional[AbstractTriggerDbType] = None

    @property
    def build(self):
        if not self._build:
            build_id = str(self.copr_event.build_id)
            if self.copr_event.chroot == COPR_SRPM_CHROOT:
                self._build = SRPMBuildModel.get_by_copr_build_id(build_id)
            else:
                self._build = CoprBuildTargetModel.get_by_build_id(
                    build_id, self.copr_event.chroot
                )
        return self._build

    @property
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        if not self._db_trigger:
            self._db_trigger = self.build.get_trigger_object()
        return self._db_trigger


class GetCoprBuild(Protocol):
    build_id: Optional[int] = None

    @property
    @abstractmethod
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        ...


class GetCoprBuildMixin(GetCoprBuild, ConfigMixin):
    _build: Optional[CoprBuildTargetModel] = None
    _db_trigger: Optional[AbstractTriggerDbType] = None

    @property
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        if not self._db_trigger:
            # copr build end
            if self.build_id:
                build = CoprBuildTargetModel.get_by_id(self.build_id)
                self._db_trigger = build.get_trigger_object()
            # other events
            else:
                self._db_trigger = self.data.db_trigger
        return self._db_trigger


class GetCoprBuildJobHelper(Protocol):
    package_config: PackageConfig
    job_config: JobConfig
    celery_task: Optional[CeleryTask] = None
    pushgateway: Optional[Pushgateway] = None

    @property
    @abstractmethod
    def copr_build_helper(self) -> CoprBuildJobHelper:
        ...


class GetCoprBuildJobHelperMixin(GetCoprBuildJobHelper, ConfigMixin):
    _copr_build_helper: Optional[CoprBuildJobHelper] = None

    @property
    def copr_build_helper(self) -> CoprBuildJobHelper:
        if not self._copr_build_helper:
            self._copr_build_helper = CoprBuildJobHelper(
                service_config=self.service_config,
                package_config=self.package_config,
                project=self.project,
                metadata=self.data,
                db_trigger=self.data.db_trigger,
                job_config=self.job_config,
                build_targets_override=self.data.build_targets_override,
                tests_targets_override=self.data.tests_targets_override,
                pushgateway=self.pushgateway,
                celery_task=self.celery_task,
            )
        return self._copr_build_helper


class GetCoprBuildJobHelperForIdMixin(
    GetCoprBuildJobHelper, GetCoprSRPMBuildMixin, ConfigMixin
):
    _copr_build_helper: Optional[CoprBuildJobHelper] = None

    @property
    def copr_build_helper(self) -> CoprBuildJobHelper:
        # when reporting state of SRPM build built in Copr
        build_targets_override = (
            {
                build.target
                for build in CoprBuildTargetModel.get_all_by_build_id(
                    str(self.copr_event.build_id)
                )
            }
            if self.copr_event.chroot == COPR_SRPM_CHROOT
            else None
        )
        if not self._copr_build_helper:
            self._copr_build_helper = CoprBuildJobHelper(
                service_config=self.service_config,
                package_config=self.package_config,
                project=self.project,
                metadata=self.data,
                db_trigger=self.db_trigger,
                job_config=self.job_config,
                pushgateway=self.pushgateway,
                build_targets_override=build_targets_override,
            )
        return self._copr_build_helper


class GetTestingFarmJobHelper(Protocol):
    package_config: PackageConfig
    job_config: JobConfig
    celery_task: Optional[CeleryTask] = None

    @property
    @abstractmethod
    def testing_farm_job_helper(self) -> TestingFarmJobHelper:
        ...


class GetTestingFarmJobHelperMixin(
    GetTestingFarmJobHelper, GetCoprBuildMixin, ConfigMixin
):
    _testing_farm_job_helper: Optional[TestingFarmJobHelper] = None

    @property
    def testing_farm_job_helper(self) -> TestingFarmJobHelper:
        if not self._testing_farm_job_helper:
            self._testing_farm_job_helper = TestingFarmJobHelper(
                service_config=self.service_config,
                package_config=self.package_config,
                project=self.project,
                metadata=self.data,
                db_trigger=self.db_trigger,
                job_config=self.job_config,
                build_targets_override=self.data.build_targets_override,
                tests_targets_override=self.data.tests_targets_override,
                celery_task=self.celery_task,
            )
        return self._testing_farm_job_helper


class GetGithubCommentEvent(Protocol):
    @abstractmethod
    def is_comment_event(self) -> bool:
        ...

    @abstractmethod
    def is_copr_build_comment_event(self) -> bool:
        ...


class GetGithubCommentEventMixin(GetGithubCommentEvent, ConfigMixin):
    def is_comment_event(self) -> bool:
        return self.data.event_type in (
            PullRequestCommentGithubEvent.__name__,
            MergeRequestCommentGitlabEvent.__name__,
            PullRequestCommentPagureEvent.__name__,
        )

    def is_copr_build_comment_event(self) -> bool:
        return self.is_comment_event() and get_packit_commands_from_comment(
            self.data.event_dict.get("comment"),
            packit_comment_command_prefix=self.service_config.comment_command_prefix,
        )[0] in ("build", "copr-build")


class GetProjectToSync(Protocol):
    @property
    @abstractmethod
    def dg_repo_name(self) -> str:
        ...

    @property
    @abstractmethod
    def dg_branch(self) -> str:
        ...

    @property
    @abstractmethod
    def project_to_sync(self) -> Optional[ProjectToSync]:
        ...


class GetProjectToSyncMixin(ConfigMixin, GetProjectToSync):
    _project_to_sync: Optional[ProjectToSync] = None

    @property
    def dg_repo_name(self) -> str:
        return self.data.event_dict.get("repo_name")

    @property
    def dg_branch(self) -> str:
        return self.data.event_dict.get("git_ref")

    @property
    def project_to_sync(self) -> Optional[ProjectToSync]:
        if self._project_to_sync is None:
            if project_to_sync := self.service_config.get_project_to_sync(
                dg_repo_name=self.dg_repo_name, dg_branch=self.dg_branch
            ):
                self._project_to_sync = project_to_sync
        return self._project_to_sync
