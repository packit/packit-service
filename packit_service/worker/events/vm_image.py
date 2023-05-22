# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
from typing import Union, Optional

from packit_service.models import (
    VMImageBuildTargetModel,
    VMImageBuildStatus,
)
from packit_service.worker.events.event import (
    AbstractForgeIndependentEvent,
    AbstractProjectEventDbType,
)


class VMImageBuildResultEvent(AbstractForgeIndependentEvent):
    def __init__(
        self,
        build_id: str,
        copr_chroot: str,
        pr_id: int,
        actor: str,
        commit_sha: str,
        project_url: str,
        status: VMImageBuildStatus,
        message: str,
        created_at: Union[int, float, str],
    ):
        super().__init__(created_at, project_url, pr_id, actor)
        self.build_id = build_id
        self.copr_chroot = copr_chroot
        self.commit_sha = commit_sha
        self.status = status
        self.message = message

        self.topic = "vm-image-build-state-change"

    def get_db_trigger(self) -> Optional[AbstractProjectEventDbType]:
        model = VMImageBuildTargetModel.get_by_build_id(self.build_id)
        for run in model.runs:
            return run.get_project_event_object()
        return None
