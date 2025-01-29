# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from typing import Optional

from packit.config import JobConfigTriggerType
from packit.utils.koji_helper import KojiHelper

from ..event import (
    use_for_job_config_trigger,
)
from .abstract import KojiEvent

logger = logging.getLogger(__name__)


@use_for_job_config_trigger(trigger_type=JobConfigTriggerType.koji_build)
class Build(KojiEvent):
    """Represents an event of tagging a Koji build.

    Docs: https://fedora-fedmsg.readthedocs.io/en/latest/topics.html#buildsys-tag
    """

    _koji_helper: Optional[KojiHelper] = None

    def __init__(
        self,
        build_id: int,
        tag_id: int,
        tag_name: str,
        project_url: str,
        package_name: str,
        epoch: str,
        version: str,
        release: str,
        owner: str,
    ):
        task_id = None
        if info := self.koji_helper.get_build_info(build_id):
            task_id = info.get("task_id")

        super().__init__(task_id=task_id)

        self.build_id = build_id
        self.tag_id = tag_id
        self.tag_name = tag_name
        self.project_url = project_url
        self.package_name = package_name
        self.epoch = epoch
        self.version = version
        self.release = release
        self.owner = owner

    @classmethod
    def event_type(cls) -> str:
        return "koji.tag.Build"

    @property
    def koji_helper(self) -> KojiHelper:
        if not self._koji_helper:
            self._koji_helper = KojiHelper()
        return self._koji_helper

    @property
    def commit_sha(self) -> Optional[str]:  # type:ignore
        return None

    @property
    def nvr(self) -> str:
        return f"{self.package_name}-{self.version}-{self.release}"

    @classmethod
    def from_event_dict(cls, event: dict) -> "Build":
        return Build(
            build_id=event.get("build_id"),
            tag_id=event.get("tag_id"),
            tag_name=event.get("tag_name"),
            project_url=event.get("project_url"),
            package_name=event.get("package_name"),
            epoch=event.get("epoch"),
            version=event.get("version"),
            release=event.get("release"),
            owner=event.get("owner"),
        )

    def get_non_serializable_attributes(self):
        return [*super().get_non_serializable_attributes(), "_koji_helper"]
