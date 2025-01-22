# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from packit_service.worker.events.abstract.comment import Commit


class Comment(Commit):
    @classmethod
    def event_type(cls) -> str:
        return "gitlab.commit.Comment"
