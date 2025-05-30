# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from logging import getLogger
from typing import Optional

from packit.config import JobConfigTriggerType

from packit_service.models import ProjectEventModel

from ..event import use_for_job_config_trigger
from .abstract import AnityaUpdate

logger = getLogger(__name__)


# the decorator is needed in case the DB project event is not created (not valid arguments)
# but we still want to report from pre_check of the PullFromUpstreamHandler
@use_for_job_config_trigger(trigger_type=JobConfigTriggerType.release)
class NewHotness(AnityaUpdate):
    def __init__(
        self,
        package_name: str,
        version: str,
        distgit_project_url: str,
        bug_id: int,
        anitya_project_id: int,
        anitya_project_name: str,
    ):
        super().__init__(
            package_name=package_name,
            distgit_project_url=distgit_project_url,
            anitya_project_id=anitya_project_id,
            anitya_project_name=anitya_project_name,
        )
        self._version = version
        self.bug_id = bug_id

    @classmethod
    def event_type(cls) -> str:
        return "anitya.NewHotness"

    @property
    def version(self) -> str:
        return self._version

    @classmethod
    def from_event_dict(cls, event: dict) -> "NewHotness":
        return cls(
            package_name=event.get("package_name"),
            version=event.get("version"),
            distgit_project_url=event.get("distgit_project_url"),
            bug_id=event.get("bug_id"),
            anitya_project_id=event.get("anitya_project_id"),
            anitya_project_name=event.get("anitya_project_name"),
        )


# TODO: Uncomment once it is possible to deduce the version for the sync-release
# action.
# @use_for_job_config_trigger(trigger_type=JobConfigTriggerType.release)
class VersionUpdate(AnityaUpdate):
    def __init__(
        self,
        package_name: str,
        versions: list[str],
        distgit_project_url: str,
        anitya_project_id: int,
        anitya_project_name: str,
    ):
        super().__init__(
            package_name=package_name,
            distgit_project_url=distgit_project_url,
            anitya_project_id=anitya_project_id,
            anitya_project_name=anitya_project_name,
        )

        self._versions = versions

    @classmethod
    def event_type(cls) -> str:
        return "anitya.VersionUpdate"

    @property
    def version(self) -> Optional[str]:
        # we will decide the version just when syncing release
        # (for the particular branch etc.),
        # until that we work with all the new versions
        return None

    def _add_release_and_event(self):
        if not self._db_project_object or not self._db_project_event:
            (
                self._db_project_object,
                self._db_project_event,
            ) = ProjectEventModel.add_anitya_multiple_versions_event(
                versions=self._versions,
                project_name=self.anitya_project_name,
                project_id=self.anitya_project_id,
                package=self.package_name,
            )
