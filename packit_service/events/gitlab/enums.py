# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from enum import Enum


class Action(Enum):
    opened = "opened"  # from state
    closed = "closed"  # from state
    reopen = "reopen"  # from action
    update = "update"  # from action
