# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from ..abstract.comment import Commit


class Comment(Commit):
    @classmethod
    def event_type(cls) -> str:
        return "github.commit.Comment"
