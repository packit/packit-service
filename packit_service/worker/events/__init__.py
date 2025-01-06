# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from packit_service.worker.events.abstract import (
    ForgeIndependent as AbstractForgeIndependentEvent,
)
from packit_service.worker.events.abstract import (
    Result as AbstractResultEvent,
)
from packit_service.worker.events.anitya.base import (
    NewHotness as NewHotnessUpdateEvent,
)
from packit_service.worker.events.anitya.base import (
    VersionUpdate as AnityaVersionUpdateEvent,
)
from packit_service.worker.events.comment import (
    AbstractCommentEvent,
    AbstractIssueCommentEvent,
    AbstractPRCommentEvent,
)
from packit_service.worker.events.copr import (
    CoprBuild as AbstractCoprBuildEvent,
)
from packit_service.worker.events.copr import (
    End as CoprBuildEndEvent,
)
from packit_service.worker.events.copr import (
    Start as CoprBuildStartEvent,
)
from packit_service.worker.events.event import (
    Event,
    EventData,
)
from packit_service.worker.events.github.abstract import (
    GithubEvent as AbstractGithubEvent,
)
from packit_service.worker.events.github.check import (
    Commit as CheckRerunCommitEvent,
)
from packit_service.worker.events.github.check import (
    PullRequest as CheckRerunPullRequestEvent,
)
from packit_service.worker.events.github.check import (
    Release as CheckRerunReleaseEvent,
)
from packit_service.worker.events.github.check import (
    Rerun as CheckRerunEvent,
)
from packit_service.worker.events.github.installation import (
    Installation as InstallationEvent,
)
from packit_service.worker.events.github.issue import Comment as IssueCommentEvent
from packit_service.worker.events.github.pr import (
    Comment as PullRequestCommentGithubEvent,
)
from packit_service.worker.events.github.pr import (
    Synchronize as PullRequestGithubEvent,
)
from packit_service.worker.events.github.push import Push as PushGitHubEvent
from packit_service.worker.events.github.release import Release as ReleaseEvent
from packit_service.worker.events.gitlab.commit import (
    Comment as CommitCommentGitlabEvent,
)
from packit_service.worker.events.gitlab.issue import (
    Comment as IssueCommentGitlabEvent,
)
from packit_service.worker.events.gitlab.mr import (
    Comment as MergeRequestCommentGitlabEvent,
)
from packit_service.worker.events.gitlab.mr import (
    Synchronize as MergeRequestGitlabEvent,
)
from packit_service.worker.events.gitlab.pipeline import (
    Pipeline as PipelineGitlabEvent,
)
from packit_service.worker.events.gitlab.push import (
    Push as PushGitlabEvent,
)
from packit_service.worker.events.gitlab.push import (
    TagPush as TagPushGitlabEvent,
)
from packit_service.worker.events.gitlab.release import (
    Release as ReleaseGitlabEvent,
)
from packit_service.worker.events.koji.base import Task as KojiTaskEvent
from packit_service.worker.events.openscanhub.task import (
    Finished as OpenScanHubTaskFinishedEvent,
)
from packit_service.worker.events.openscanhub.task import (
    Started as OpenScanHubTaskStartedEvent,
)
from packit_service.worker.events.pagure.abstract import PagureEvent as AbstractPagureEvent
from packit_service.worker.events.pagure.pr import (
    Comment as PullRequestCommentPagureEvent,
)
from packit_service.worker.events.pagure.pr import (
    Flag as PullRequestFlagPagureEvent,
)
from packit_service.worker.events.pagure.pr import (
    Synchronize as PullRequestPagureEvent,
)
from packit_service.worker.events.pagure.push import Push as PushPagureEvent
from packit_service.worker.events.testing_farm import Result as TestingFarmResultsEvent
from packit_service.worker.events.vm_image import Result as VMImageBuildResultEvent

__all__ = [
    Event.__name__,
    EventData.__name__,
    # Copr
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
    IssueCommentGitlabEvent.__name__,
    KojiTaskEvent.__name__,
    AbstractPagureEvent.__name__,
    PushPagureEvent.__name__,
    PullRequestCommentPagureEvent.__name__,
    PullRequestPagureEvent.__name__,
    PullRequestFlagPagureEvent.__name__,
    TestingFarmResultsEvent.__name__,
    VMImageBuildResultEvent.__name__,
    CheckRerunCommitEvent.__name__,
    CheckRerunPullRequestEvent.__name__,
    CheckRerunReleaseEvent.__name__,
    CheckRerunEvent.__name__,
    AbstractCommentEvent.__name__,
    AbstractPRCommentEvent.__name__,
    AbstractIssueCommentEvent.__name__,
    OpenScanHubTaskFinishedEvent.__name__,
    OpenScanHubTaskStartedEvent.__name__,
    # GitLab events
    ReleaseGitlabEvent.__name__,
    TagPushGitlabEvent.__name__,
    PipelineGitlabEvent.__name__,
    PushGitlabEvent.__name__,
    MergeRequestGitlabEvent.__name__,
    MergeRequestCommentGitlabEvent.__name__,
    CommitCommentGitlabEvent.__name__,
    # Anitya events
    NewHotnessUpdateEvent.__name__,
    AnityaVersionUpdateEvent.__name__,
    # Abstracts
    AbstractForgeIndependentEvent.__name__,
    AbstractResultEvent.__name__,
]
