# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from abc import abstractmethod
from typing import Any, Optional, Protocol, Iterator
from dataclasses import dataclass
from packit.exceptions import PackitException
from packit.config import PackageConfig, JobConfig
from packit.utils.koji_helper import KojiHelper
from packit_service.utils import get_packit_commands_from_comment
from packit_service.config import ProjectToSync
from packit_service.constants import COPR_SRPM_CHROOT, KojiBuildState
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

from packit_service.worker.mixin import Config, ConfigFromEventMixin, GetBranches
from packit_service.worker.events.koji import KojiBuildEvent
from packit_service.worker.monitoring import Pushgateway


logger = logging.getLogger(__name__)


class GetKojiBuildEvent(Protocol):
    data: EventData

    @property
    @abstractmethod
    def koji_build_event(self) -> KojiBuildEvent:
        ...


class GetKojiBuildEventMixin(ConfigFromEventMixin, GetKojiBuildEvent):
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


class GetKojiBuildJobHelperMixin(GetKojiBuildJobHelper, ConfigFromEventMixin):
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


@dataclass
class KojiBuildData:
    """Koji build data associated with
    a selected dist-git branch.
    """

    dist_git_branch: str
    build_id: int
    nvr: str
    state: KojiBuildState


class GetKojiBuildData(Iterator, Protocol):
    """Get the Koji build data associated with
    the selected dist-git branch.
    """

    _branch_index: int = 0

    def __iter__(self) -> Iterator[KojiBuildData]:
        self._branch_index = 0
        return self

    @property
    @abstractmethod
    def num_of_branches(self):
        ...

    def __next__(self) -> KojiBuildData:
        """Iterate over all available dist-git branches.
        Change internal pointer to the next dist-git branch.

        Returns:
            A new set of Koji Build Data associated
            with the next available dist_git_branch
        """
        if self._branch_index < self.num_of_branches:
            koji_build_data = KojiBuildData(
                dist_git_branch=self._dist_git_branch,
                build_id=self._build_id,
                nvr=self._nvr,
                state=self._state,
            )
            self._branch_index += 1
            return koji_build_data
        raise StopIteration

    @property
    @abstractmethod
    def _nvr(self) -> str:
        ...

    @property
    @abstractmethod
    def _build_id(self) -> int:
        ...

    @property
    @abstractmethod
    def _dist_git_branch(self) -> str:
        ...

    @property
    @abstractmethod
    def _state(self) -> KojiBuildState:
        ...


class GetKojiBuildDataFromKojiBuildEventMixin(GetKojiBuildData, GetKojiBuildEvent):
    @property
    def _nvr(self) -> str:
        return self.koji_build_event.nvr

    @property
    def _build_id(self) -> int:
        return self.koji_build_event.build_id

    @property
    def _dist_git_branch(self) -> str:
        return self.koji_build_event.git_ref

    @property
    def _state(self) -> KojiBuildState:
        return self.koji_build_event.state

    @property
    def num_of_branches(self):
        return 1  # just a branch in the event


class GetKojiBuildDataFromKojiService(Config, GetKojiBuildData):
    """See https://koji.fedoraproject.org/koji/api method listBuilds
    for a detailed description of a Koji build map.
    """

    _build: Optional[Any] = None
    _koji_helper: Optional[KojiHelper] = None

    @property
    def koji_helper(self):
        if not self._koji_helper:
            self._koji_helper = KojiHelper()
        return self._koji_helper

    @property
    def build(self):
        if not self._build:
            self._build = self.koji_helper.get_latest_build_in_tag(
                package=self.project.repo,
                tag=self._dist_git_branch,
            )
            if not self._build:
                raise PackitException(
                    f"No build found for package={self.project.repo} and tag={self.dist_git_branch}"
                )
        return self._build

    @property
    def _nvr(self) -> str:
        return self.build["nvr"]

    @property
    def _build_id(self) -> int:
        return self.build["build_id"]

    @property
    def _state(self) -> KojiBuildState:
        return KojiBuildState.from_number(self.build["state"])


class GetKojiBuildDataFromKojiServiceMixin(
    ConfigFromEventMixin, GetKojiBuildDataFromKojiService
):
    @property
    def _dist_git_branch(self) -> str:
        return self.project.get_pr(self.data.pr_id).target_branch

    @property
    def num_of_branches(self):
        return 1  # just a branch in the event


class GetKojiBuildDataFromKojiServiceMultipleBranches(
    GetKojiBuildDataFromKojiService, GetBranches
):
    @property
    def _dist_git_branch(self) -> str:
        return self.branches[self._branch_index]

    @property
    def num_of_branches(self):
        return len(self.branches)


class GetCoprBuildEvent(Protocol):
    data: EventData

    @property
    @abstractmethod
    def copr_event(self) -> AbstractCoprBuildEvent:
        ...


class GetCoprBuildEventMixin(ConfigFromEventMixin, GetCoprBuildEvent):
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


class GetCoprBuildMixin(GetCoprBuild, ConfigFromEventMixin):
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


class GetCoprBuildJobHelperMixin(GetCoprBuildJobHelper, ConfigFromEventMixin):
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
    GetCoprBuildJobHelper, GetCoprSRPMBuildMixin, ConfigFromEventMixin
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
    GetTestingFarmJobHelper, GetCoprBuildMixin, ConfigFromEventMixin
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


class GetGithubCommentEventMixin(GetGithubCommentEvent, ConfigFromEventMixin):
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


class GetProjectToSyncMixin(ConfigFromEventMixin, GetProjectToSync):
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
