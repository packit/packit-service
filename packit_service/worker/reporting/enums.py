# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from enum import Enum, auto
from typing import Union

from ogr.abstract import CommitStatus
from ogr.services.github.check_run import (
    GithubCheckRunResult,
    GithubCheckRunStatus,
)


class DuplicateCheckMode(Enum):
    """Enum of possible behaviour for handling duplicates when commenting."""

    # Do not check for duplicates
    do_not_check = auto()
    # Check only last comment from us for duplicate
    check_last_comment = auto()
    # Check the whole comment list for duplicate
    check_all_comments = auto()


class BaseCommitStatus(Enum):
    failure = "failure"
    neutral = "neutral"
    success = "success"
    pending = "pending"
    running = "running"
    error = "error"
    canceled = "canceled"


MAP_TO_COMMIT_STATUS: dict[BaseCommitStatus, CommitStatus] = {
    BaseCommitStatus.pending: CommitStatus.pending,
    BaseCommitStatus.running: CommitStatus.running,
    BaseCommitStatus.failure: CommitStatus.failure,
    BaseCommitStatus.neutral: CommitStatus.error,
    BaseCommitStatus.success: CommitStatus.success,
    BaseCommitStatus.error: CommitStatus.error,
    BaseCommitStatus.canceled: CommitStatus.canceled,
}

MAP_TO_CHECK_RUN: dict[
    BaseCommitStatus,
    Union[GithubCheckRunResult, GithubCheckRunStatus],
] = {
    BaseCommitStatus.pending: GithubCheckRunStatus.queued,
    BaseCommitStatus.running: GithubCheckRunStatus.in_progress,
    BaseCommitStatus.failure: GithubCheckRunResult.failure,
    BaseCommitStatus.neutral: GithubCheckRunResult.neutral,
    BaseCommitStatus.success: GithubCheckRunResult.success,
    BaseCommitStatus.error: GithubCheckRunResult.failure,
    BaseCommitStatus.canceled: GithubCheckRunResult.cancelled,
}
