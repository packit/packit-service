# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from enum import Enum, auto
from typing import Dict, Union

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


MAP_TO_COMMIT_STATUS: Dict[BaseCommitStatus, CommitStatus] = {
    BaseCommitStatus.pending: CommitStatus.pending,
    BaseCommitStatus.running: CommitStatus.running,
    BaseCommitStatus.failure: CommitStatus.failure,
    BaseCommitStatus.neutral: CommitStatus.error,
    BaseCommitStatus.success: CommitStatus.success,
    BaseCommitStatus.error: CommitStatus.error,
}

MAP_TO_CHECK_RUN: Dict[
    BaseCommitStatus, Union[GithubCheckRunResult, GithubCheckRunStatus]
] = {
    BaseCommitStatus.pending: GithubCheckRunStatus.queued,
    BaseCommitStatus.running: GithubCheckRunStatus.in_progress,
    BaseCommitStatus.failure: GithubCheckRunResult.failure,
    BaseCommitStatus.neutral: GithubCheckRunResult.neutral,
    BaseCommitStatus.success: GithubCheckRunResult.success,
    BaseCommitStatus.error: GithubCheckRunResult.failure,
}
