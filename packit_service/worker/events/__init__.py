# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from packit_service.worker.events.comment import (
    AbstractCommentEvent,
    AbstractIssueCommentEvent,
    AbstractPRCommentEvent,
)
from packit_service.worker.events.copr import (
    AbstractCoprBuildEvent,
    CoprBuildEndEvent,
    CoprBuildStartEvent,
)
from packit_service.worker.events.event import (
    AbstractForgeIndependentEvent,
    Event,
    EventData,
)
from packit_service.worker.events.github import (
    AbstractGithubEvent,
    CheckRerunCommitEvent,
    CheckRerunEvent,
    CheckRerunPullRequestEvent,
    CheckRerunReleaseEvent,
    InstallationEvent,
    IssueCommentEvent,
    PullRequestCommentGithubEvent,
    PullRequestGithubEvent,
    PushGitHubEvent,
    ReleaseEvent,
)
from packit_service.worker.events.gitlab import (
    IssueCommentGitlabEvent,
    MergeRequestCommentGitlabEvent,
    MergeRequestGitlabEvent,
    PipelineGitlabEvent,
    PushGitlabEvent,
    ReleaseGitlabEvent,
    TagPushGitlabEvent,
)
from packit_service.worker.events.koji import KojiTaskEvent
from packit_service.worker.events.open_scan_hub import (
    OpenScanHubTaskFinishedEvent,
    OpenScanHubTaskStartedEvent,
)
from packit_service.worker.events.pagure import (
    AbstractPagureEvent,
    PullRequestCommentPagureEvent,
    PullRequestFlagPagureEvent,
    PullRequestPagureEvent,
    PushPagureEvent,
)
from packit_service.worker.events.testing_farm import TestingFarmResultsEvent
from packit_service.worker.events.vm_image import VMImageBuildResultEvent

__all__ = [
    Event.__name__,
    EventData.__name__,
    AbstractCoprBuildEvent.__name__,
    CoprBuildStartEvent.__name__,
    CoprBuildEndEvent.__name__,
    AbstractGithubEvent.__name__,
    PushGitHubEvent.__name__,
    PullRequestGithubEvent.__name__,
    PullRequestCommentGithubEvent.__name__,
    IssueCommentEvent.__name__,
    InstallationEvent.__name__,
    ReleaseEvent.__name__,
    PushGitlabEvent.__name__,
    MergeRequestGitlabEvent.__name__,
    MergeRequestCommentGitlabEvent.__name__,
    IssueCommentGitlabEvent.__name__,
    KojiTaskEvent.__name__,
    AbstractPagureEvent.__name__,
    PushPagureEvent.__name__,
    PullRequestCommentPagureEvent.__name__,
    PullRequestPagureEvent.__name__,
    TestingFarmResultsEvent.__name__,
    VMImageBuildResultEvent.__name__,
    PipelineGitlabEvent.__name__,
    CheckRerunCommitEvent.__name__,
    CheckRerunPullRequestEvent.__name__,
    CheckRerunReleaseEvent.__name__,
    CheckRerunEvent.__name__,
    AbstractCommentEvent.__name__,
    AbstractPRCommentEvent.__name__,
    AbstractIssueCommentEvent.__name__,
    AbstractForgeIndependentEvent.__name__,
    ReleaseGitlabEvent.__name__,
    TagPushGitlabEvent.__name__,
    PullRequestFlagPagureEvent.__name__,
    OpenScanHubTaskFinishedEvent.__name__,
    OpenScanHubTaskStartedEvent.__name__,
]
