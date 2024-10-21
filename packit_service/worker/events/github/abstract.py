# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from typing import Optional

from packit_service.worker.events.event import AbstractForgeIndependentEvent


class GithubEvent(AbstractForgeIndependentEvent):
    def __init__(self, project_url: str, pr_id: Optional[int] = None, **kwargs):
        super().__init__(pr_id=pr_id)
        self.project_url: str = project_url
        self.git_ref: Optional[str] = None  # git ref that can be 'git checkout'-ed
        self.identifier: Optional[str] = (
            None  # will be shown to users -- e.g. in logs or in the copr-project name
        )
