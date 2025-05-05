# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from abc import abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Optional, Protocol, Union

from packit.config import JobConfig, PackageConfig
from packit.exceptions import PackitException
from packit.utils.koji_helper import KojiHelper
from packit.vm_image_build import ImageBuilder

from packit_service.config import ProjectToSync
from packit_service.constants import COPR_SRPM_CHROOT, KojiBuildState
from packit_service.events import (
    copr,
    github,
    gitlab,
    koji,
    pagure,
)
from packit_service.events.event_data import EventData
from packit_service.models import (
    BuildStatus,
    CoprBuildTargetModel,
    KojiBuildTargetModel,
    ProjectEventModel,
    SRPMBuildModel,
)
from packit_service.utils import get_packit_commands_from_comment
from packit_service.worker.handlers.abstract import CeleryTask
from packit_service.worker.helpers.build.copr_build import CoprBuildJobHelper
from packit_service.worker.helpers.build.koji_build import KojiBuildJobHelper
from packit_service.worker.helpers.sidetag import Sidetag, SidetagHelper
from packit_service.worker.helpers.testing_farm import (
    DownstreamTestingFarmJobHelper,
    TestingFarmJobHelper,
)
from packit_service.worker.mixin import Config, ConfigFromEventMixin, GetBranches
from packit_service.worker.monitoring import Pushgateway

logger = logging.getLogger(__name__)


class GetKojiBuildEvent(Protocol):
    data: EventData

    @property
    @abstractmethod
    def koji_build_event(self) -> koji.result.Build: ...


class GetKojiBuildEventMixin(ConfigFromEventMixin, GetKojiBuildEvent):
    _koji_build_event: Optional[koji.result.Build] = None

    @property
    def koji_build_event(self):
        if not self._koji_build_event:
            self._koji_build_event = koji.result.Build.from_event_dict(
                self.data.event_dict,
            )
        return self._koji_build_event


class GetKojiTaskEvent(Protocol):
    data: EventData

    @property
    @abstractmethod
    def koji_task_event(self) -> Optional[koji.result.Task]: ...


class GetKojiTaskEventMixin(ConfigFromEventMixin, GetKojiTaskEvent):
    _koji_task_event: Optional[koji.result.Task] = None

    @property
    def koji_task_event(self) -> Optional[koji.result.Task]:
        if not self._koji_task_event and "task_id" in self.data.event_dict:
            self._koji_task_event = koji.result.Task.from_event_dict(
                self.data.event_dict,
            )
        return self._koji_task_event


class GetKojiBuild(Protocol):
    @property
    @abstractmethod
    def koji_build(self) -> Optional[KojiBuildTargetModel]: ...

    @property
    @abstractmethod
    def db_project_event(self) -> Optional[ProjectEventModel]: ...


class GetKojiBuildFromTaskOrPullRequestMixin(GetKojiBuild, GetKojiTaskEventMixin):
    _koji_build: Optional[KojiBuildTargetModel] = None
    _db_project_event: Optional[ProjectEventModel] = None

    @property
    def koji_build(self) -> Optional[KojiBuildTargetModel]:
        if not self._koji_build:
            if self.koji_task_event:
                self._koji_build = KojiBuildTargetModel.get_by_task_id(
                    str(self.koji_task_event.task_id)
                )
            else:
                pull_request = self.project.get_pr(self.data.pr_id)
                self._koji_build = (
                    KojiBuildTargetModel.get_last_successful_scratch_by_commit_target(
                        pull_request.head_commit, pull_request.target_branch
                    )
                )
        return self._koji_build

    @property
    def db_project_event(self) -> Optional[ProjectEventModel]:
        if not self._db_project_event:
            if self.koji_build:
                self._db_project_event = self.koji_build.get_project_event_model()
            else:
                self._db_project_event = self.data.db_project_event
        return self._db_project_event


class GetKojiBuildJobHelper(Protocol):
    @property
    @abstractmethod
    def koji_build_helper(self) -> KojiBuildJobHelper: ...


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
                db_project_event=self.data.db_project_event,
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
    task_id: int


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
    def num_of_branches(self): ...

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
                task_id=self._task_id,
            )
            self._branch_index += 1
            return koji_build_data
        raise StopIteration

    @property
    @abstractmethod
    def _nvr(self) -> str: ...

    @property
    @abstractmethod
    def _build_id(self) -> int: ...

    @property
    @abstractmethod
    def _dist_git_branch(self) -> str: ...

    @property
    @abstractmethod
    def _state(self) -> KojiBuildState: ...

    @property
    @abstractmethod
    def _task_id(self) -> int: ...


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
    def _task_id(self) -> int:
        return self.koji_build_event.task_id

    @property
    def num_of_branches(self):
        return 1  # just a branch in the event


class GetKojiBuildDataFromKojiBuildTagEventMixin(
    ConfigFromEventMixin,
    GetKojiBuildData,
):
    _koji_build_tag_event: Optional[koji.tag.Build] = None
    _sidetag: Optional[Sidetag] = None

    @property
    def koji_build_tag_event(self) -> koji.tag.Build:
        if not self._koji_build_tag_event:
            self._koji_build_tag_event = koji.tag.Build.from_event_dict(
                self.data.event_dict,
            )
        return self._koji_build_tag_event

    @property
    def sidetag(self) -> Optional[Sidetag]:
        if not self._sidetag:
            self._sidetag = SidetagHelper.get_sidetag_by_koji_name(
                self.koji_build_tag_event.tag_name,
            )
        return self._sidetag

    @property
    def _nvr(self) -> str:
        return self.koji_build_tag_event.nvr

    @property
    def _build_id(self) -> int:
        return self.koji_build_tag_event.build_id

    @property
    def _dist_git_branch(self) -> str:
        if self.sidetag.dist_git_branch == "main":
            # Koji doesn't recognize main, only rawhide
            return "rawhide"
        return self.sidetag.dist_git_branch

    @property
    def _state(self) -> KojiBuildState:
        return KojiBuildState.complete

    @property
    def _task_id(self) -> int:
        return self.koji_build_tag_event.task_id

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
    def koji_helper(self) -> KojiHelper:
        if not self._koji_helper:
            self._koji_helper = KojiHelper()
        return self._koji_helper

    def _get_latest_build(self) -> dict:
        if not (
            build := self.koji_helper.get_latest_candidate_build(
                self.project.repo,
                self._dist_git_branch,
            )
        ):
            raise PackitException(
                f"No build found for package={self.project.repo} "
                f"and branch={self._dist_git_branch}",
            )
        return build

    @property
    def build(self) -> dict:
        if not self._build:
            self._build = self._get_latest_build()
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

    @property
    def _task_id(self) -> int:
        return self.build["task_id"]


class GetKojiBuildDataFromKojiServiceMixin(
    ConfigFromEventMixin,
    GetKojiBuildDataFromKojiService,
):
    @property
    def _dist_git_branch(self) -> str:
        return self.project.get_pr(self.data.pr_id).target_branch

    @property
    def num_of_branches(self):
        return 1  # just a branch in the event


class GetKojiBuildDataFromKojiServiceMultipleBranches(
    GetKojiBuildDataFromKojiService,
    GetBranches,
):
    @property
    def _dist_git_branch(self) -> str:
        return self.branches[self._branch_index]

    @property
    def build(self):
        # call it every time since dist_git_branch reference can change
        return self._get_latest_build()

    @property
    def num_of_branches(self):
        return len(self.branches)


class GetCoprBuildEvent(Protocol):
    data: EventData

    @property
    @abstractmethod
    def copr_event(self) -> copr.CoprBuild: ...


class GetCoprBuildEventMixin(ConfigFromEventMixin, GetCoprBuildEvent):
    _copr_build_event: Optional[copr.CoprBuild] = None

    @property
    def copr_event(self):
        if not self._copr_build_event:
            self._copr_build_event = copr.CoprBuild.from_event_dict(
                self.data.event_dict,
            )
        return self._copr_build_event


class GetSRPMBuild(Protocol):
    @property
    @abstractmethod
    def build(self) -> Optional[SRPMBuildModel]: ...

    @property
    @abstractmethod
    def db_project_event(self) -> Optional[ProjectEventModel]: ...


class GetCoprSRPMBuildMixin(GetSRPMBuild, GetCoprBuildEventMixin):
    _build: Optional[Union[CoprBuildTargetModel, SRPMBuildModel]] = None
    _db_project_event: Optional[ProjectEventModel] = None

    @property
    def build(self):
        if not self._build:
            build_id = str(self.copr_event.build_id)
            if self.copr_event.chroot == COPR_SRPM_CHROOT:
                self._build = SRPMBuildModel.get_by_copr_build_id(build_id)
            else:
                self._build = CoprBuildTargetModel.get_by_build_id(
                    build_id,
                    self.copr_event.chroot,
                )
        return self._build

    @property
    def db_project_event(self) -> Optional[ProjectEventModel]:
        if not self._db_project_event:
            self._db_project_event = self.build.get_project_event_model()
        return self._db_project_event


class GetCoprBuild(Protocol):
    build_id: Optional[int] = None

    @property
    @abstractmethod
    def db_project_event(self) -> Optional[ProjectEventModel]: ...


class GetCoprBuildMixin(GetCoprBuild, ConfigFromEventMixin):
    _build: Optional[CoprBuildTargetModel] = None
    _db_project_event: Optional[ProjectEventModel] = None

    @property
    def db_project_event(self) -> Optional[ProjectEventModel]:
        if not self._db_project_event:
            # copr build end
            if self.build_id:
                build = CoprBuildTargetModel.get_by_id(self.build_id)
                self._db_project_event = build.get_project_event_model()
            # other events
            else:
                self._db_project_event = self.data.db_project_event
        return self._db_project_event


class GetCoprBuildJobHelper(Protocol):
    package_config: PackageConfig
    job_config: JobConfig
    celery_task: Optional[CeleryTask] = None
    pushgateway: Optional[Pushgateway] = None

    @property
    @abstractmethod
    def copr_build_helper(self) -> CoprBuildJobHelper: ...


class GetCoprBuildJobHelperMixin(Config, GetCoprBuildJobHelper):
    _copr_build_helper: Optional[CoprBuildJobHelper] = None

    @property
    def copr_build_helper(self) -> CoprBuildJobHelper:
        if not self._copr_build_helper:
            self._copr_build_helper = CoprBuildJobHelper(
                service_config=self.service_config,
                package_config=self.package_config,
                project=self.project,
                metadata=self.data,
                db_project_event=self.data.db_project_event,
                job_config=self.job_config,
                build_targets_override=self.data.build_targets_override,
                tests_targets_override=self.data.tests_targets_override,
                pushgateway=self.pushgateway,
                celery_task=self.celery_task,
            )
        return self._copr_build_helper


class GetCoprBuildJobHelperForIdMixin(
    GetCoprBuildJobHelper,
    GetCoprSRPMBuildMixin,
    ConfigFromEventMixin,
):
    _copr_build_helper: Optional[CoprBuildJobHelper] = None

    @property
    def copr_build_helper(self) -> CoprBuildJobHelper:
        # when reporting state of SRPM build built in Copr
        build_targets_override = (
            {
                (build.target, build.identifier)
                for build in CoprBuildTargetModel.get_all_by_build_id(
                    str(self.copr_event.build_id),
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
                db_project_event=self.db_project_event,
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
    def testing_farm_job_helper(self) -> TestingFarmJobHelper: ...


class GetTestingFarmJobHelperMixin(
    GetTestingFarmJobHelper,
    GetCoprBuildMixin,
    ConfigFromEventMixin,
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
                db_project_event=self.db_project_event,
                job_config=self.job_config,
                build_targets_override=self.data.build_targets_override,
                tests_targets_override=self.data.tests_targets_override,
                celery_task=self.celery_task,
            )
        return self._testing_farm_job_helper


class GetDownstreamTestingFarmJobHelper(Protocol):
    celery_task: Optional[CeleryTask] = None

    @property
    @abstractmethod
    def downstream_testing_farm_job_helper(self) -> DownstreamTestingFarmJobHelper: ...


class GetDownstreamTestingFarmJobHelperMixin(
    GetDownstreamTestingFarmJobHelper,
    GetKojiBuildFromTaskOrPullRequestMixin,
    ConfigFromEventMixin,
):
    _downstream_testing_farm_job_helper: Optional[DownstreamTestingFarmJobHelper] = None

    @property
    def downstream_testing_farm_job_helper(self) -> DownstreamTestingFarmJobHelper:
        if not self._downstream_testing_farm_job_helper:
            self._downstream_testing_farm_job_helper = DownstreamTestingFarmJobHelper(
                service_config=self.service_config,
                project=self.project,
                metadata=self.data,
                koji_build=self.koji_build,
                celery_task=self.celery_task,
            )
        return self._downstream_testing_farm_job_helper


class GetGithubCommentEvent(Protocol):
    @abstractmethod
    def is_comment_event(self) -> bool: ...

    @abstractmethod
    def is_copr_build_comment_event(self) -> bool: ...


class GetGithubCommentEventMixin(GetGithubCommentEvent, ConfigFromEventMixin):
    def is_comment_event(self) -> bool:
        return self.data.event_type in (
            github.pr.Comment.event_type(),
            gitlab.mr.Comment.event_type(),
            pagure.pr.Comment.event_type(),
        )

    def is_copr_build_comment_event(self) -> bool:
        return self.is_comment_event() and get_packit_commands_from_comment(
            self.data.event_dict.get("comment"),
            packit_comment_command_prefix=self.service_config.comment_command_prefix,
        )[0] in ("build", "copr-build")


class GetProjectToSync(Protocol):
    @property
    @abstractmethod
    def dg_repo_name(self) -> str: ...

    @property
    @abstractmethod
    def dg_branch(self) -> str: ...

    @property
    @abstractmethod
    def project_to_sync(self) -> Optional[ProjectToSync]: ...


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
        if self._project_to_sync is None and (
            project_to_sync := self.service_config.get_project_to_sync(
                dg_repo_name=self.dg_repo_name,
                dg_branch=self.dg_branch,
            )
        ):
            self._project_to_sync = project_to_sync
        return self._project_to_sync


class GetVMImageBuilder(Protocol):
    @property
    @abstractmethod
    def vm_image_builder(self): ...


class GetVMImageData(Protocol):
    @property
    @abstractmethod
    def build_id(self) -> str: ...

    @property
    @abstractmethod
    def chroot(self) -> str: ...

    @property
    @abstractmethod
    def identifier(self) -> str: ...

    @property
    @abstractmethod
    def owner(self) -> str: ...

    @property
    @abstractmethod
    def project_name(self) -> str: ...

    @property
    @abstractmethod
    def image_distribution(self) -> str: ...

    @property
    @abstractmethod
    def image_request(self) -> dict: ...

    @property
    @abstractmethod
    def image_customizations(self) -> dict: ...


class GetVMImageBuilderMixin(Config):
    _vm_image_builder: Optional[ImageBuilder] = None

    @property
    def vm_image_builder(self):
        if not self._vm_image_builder:
            self._vm_image_builder = ImageBuilder(
                self.service_config.redhat_api_refresh_token,
            )
        return self._vm_image_builder


class GetVMImageDataMixin(Config, GetCoprBuildJobHelper):
    job_config: JobConfig
    _copr_build: Optional[CoprBuildTargetModel] = None
    _copr_build_helper: Optional[CoprBuildJobHelper] = None

    @property
    def chroot(self) -> str:
        return self.job_config.copr_chroot

    @property
    def identifier(self) -> str:
        return self.job_config.identifier

    @property
    def owner(self) -> str:
        return self.job_config.owner or (self.copr_build.owner if self.copr_build else None)

    @property
    def project_name(self) -> str:
        return self.job_config.project or (
            self.copr_build.project_name if self.copr_build else None
        )

    @property
    def image_name(self) -> str:
        return f"{self.owner}/{self.project_name}/{self.data.pr_id}"

    @property
    def image_distribution(self) -> str:
        return self.job_config.image_distribution

    @property
    def image_request(self) -> dict:
        return self.job_config.image_request

    @property
    def image_customizations(self) -> dict:
        return self.job_config.image_customizations

    @property
    def copr_build(self) -> Optional[CoprBuildTargetModel]:
        if not self._copr_build:
            copr_builds = CoprBuildTargetModel.get_all_by(
                project_name=self.job_config.project or self.copr_build_helper.default_project_name,
                commit_sha=self.data.commit_sha,
                owner=self.job_config.owner or self.copr_build_helper.job_owner,
                target=self.job_config.copr_chroot,
                status=BuildStatus.success,
            )

            for copr_build in copr_builds:
                project_event_object = copr_build.get_project_event_object()
                # check whether the event trigger matches
                if project_event_object.id == self.data.db_project_object.id:
                    self._copr_build = copr_build
                    break
        return self._copr_build
