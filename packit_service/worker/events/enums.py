# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from enum import Enum


class PullRequestAction(Enum):
    opened = "opened"
    reopened = "reopened"
    synchronize = "synchronize"


class GitlabEventAction(Enum):
    opened = "opened"  # from state
    closed = "closed"  # from state
    reopen = "reopen"  # from action
    update = "update"  # from action


class PullRequestCommentAction(Enum):
    created = "created"
    edited = "edited"


class IssueCommentAction(Enum):
    created = "created"
    edited = "edited"


class FedmsgTopic(Enum):
    dist_git_push = "org.fedoraproject.prod.git.receive"
    copr_build_finished = "org.fedoraproject.prod.copr.build.end"
    copr_build_started = "org.fedoraproject.prod.copr.build.start"
