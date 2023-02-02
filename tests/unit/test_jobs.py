# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from typing import Type

import copy
import celery
import pytest
from flexmock import flexmock

from packit.config import (
    CommonPackageConfig,
    JobConfig,
    JobConfigTriggerType,
    JobType,
    PackageConfig,
)

from packit_service.config import ServiceConfig
from packit_service.constants import COMMENT_REACTION
from packit_service.worker.events import (
    CoprBuildEndEvent,
    CoprBuildStartEvent,
    IssueCommentEvent,
    KojiTaskEvent,
    MergeRequestCommentGitlabEvent,
    MergeRequestGitlabEvent,
    PullRequestCommentGithubEvent,
    PullRequestCommentPagureEvent,
    PullRequestGithubEvent,
    PushGitHubEvent,
    PushGitlabEvent,
    PushPagureEvent,
    ReleaseEvent,
    TestingFarmResultsEvent,
    CheckRerunCommitEvent,
    CheckRerunPullRequestEvent,
    CheckRerunReleaseEvent,
)
from packit_service.worker.events.koji import KojiBuildEvent
from packit_service.worker.handlers import (
    CoprBuildEndHandler,
    CoprBuildStartHandler,
    JobHandler,
    TestingFarmHandler,
    TestingFarmResultsHandler,
    CoprBuildHandler,
    KojiBuildHandler,
    KojiTaskReportHandler,
    ProposeDownstreamHandler,
)
from packit_service.worker.handlers.bodhi import CreateBodhiUpdateHandler
from packit_service.worker.handlers.distgit import DownstreamKojiBuildHandler
from packit_service.worker.handlers.koji import KojiBuildReportHandler
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.result import TaskResults


@pytest.mark.parametrize(
    "event_cls,db_trigger,jobs,result",
    [
        # Single job defined:
        pytest.param(
            PullRequestGithubEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler},
            id="config=build_for_pr&pull_request&PullRequestGithubEvent",
        ),
        pytest.param(
            PullRequestGithubEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler},
            id="config=copr_build_for_pr&pull_request&PullRequestGithubEvent",
        ),
        pytest.param(
            PullRequestGithubEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {TestingFarmHandler},
            id="config=test_for_pr&pull_request&PullRequestGithubEvent",
        ),
        # Not matching event:
        pytest.param(
            PullRequestGithubEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            set(),
            id="config=build_for_commit&pull_request&PullRequestGithubEvent",
        ),
        # Matching events:
        pytest.param(
            MergeRequestGitlabEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler},
            id="config=build_for_pr&pull_request&MergeRequestGitlabEvent",
        ),
        pytest.param(
            PushGitHubEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler},
            id="config=build_for_push&commit&PushGitHubEvent",
        ),
        pytest.param(
            PushGitlabEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler},
            id="config=build_for_push&commit&PushGitlabEvent",
        ),
        pytest.param(
            ReleaseEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.release),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler},
            id="config=build_for_release&release&ReleaseEvent",
        ),
        # Copr results for build:
        pytest.param(
            CoprBuildStartEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildStartHandler},
            id="config=build_for_pr&pull_request&CoprBuildStartEvent",
        ),
        pytest.param(
            CoprBuildEndEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildEndHandler},
            id="config=build_for_pr&pull_request&CoprBuildEndEvent",
        ),
        # Copr results for test:
        pytest.param(
            CoprBuildStartEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildStartHandler},
            id="config=build_for_pr&pull_request&CoprBuildStartEvent",
        ),
        pytest.param(
            CoprBuildEndEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildEndHandler},
            id="config=test_for_pr&pull_request&CoprBuildEndEvent",
        ),
        # Test results:
        pytest.param(
            TestingFarmResultsEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {TestingFarmResultsHandler},
            id="config=test_for_pr&pull_request&TestingFarmResultsEvent",
        ),
        # Koji:
        pytest.param(
            PullRequestGithubEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.production_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {KojiBuildHandler},
            id="config=production_build_for_pr&pull_request&PullRequestGithubEvent",
        ),
        pytest.param(
            MergeRequestGitlabEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.production_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {KojiBuildHandler},
            id="config=production_build_for_pr&pull_request&MergeRequestGitlabEvent",
        ),
        pytest.param(
            PushGitHubEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.production_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {KojiBuildHandler},
            id="config=production_build_for_commit&commit&PushGitHubEvent",
        ),
        pytest.param(
            PushGitlabEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.production_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {KojiBuildHandler},
            id="config=production_build_for_commit&commit&PushGitlabEvent",
        ),
        pytest.param(
            ReleaseEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.release),
            [
                JobConfig(
                    type=JobType.production_build,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {KojiBuildHandler},
            id="config=production_build_for_release&commit&ReleaseEvent",
        ),
        pytest.param(
            KojiTaskEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.production_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {KojiTaskReportHandler},
            id="config=production_build_for_pr&pull_request&KojiBuildEvent",
        ),
        # Build and test:
        pytest.param(
            PullRequestGithubEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler, TestingFarmHandler},
            id="config=build_for_pr+test_for_pr&pull_request&PullRequestGithubEvent",
        ),
        pytest.param(
            CoprBuildStartEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildStartHandler},
            id="config=build_for_pr+test_for_pr&pull_request&CoprBuildStartEvent",
        ),
        pytest.param(
            CoprBuildEndEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildEndHandler},
            id="config=build_for_pr+test_for_pr&pull_request&CoprBuildEndEvent",
        ),
        pytest.param(
            TestingFarmResultsEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {TestingFarmResultsHandler},
            id="config=build_for_pr+test_for_pr&pull_request&TestingFarmResultsEvent",
        ),
        # Multiple triggers for copr-build:
        pytest.param(
            PullRequestGithubEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler},
            id="config=build_for_pr+build_for_commit+build_for_release"
            "&pull_request&PullRequestGithubEvent",
        ),
        pytest.param(
            PushGitHubEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler},
            id="config=build_for_pr+build_for_commit+build_for_release&commit&PushGitHubEvent",
        ),
        pytest.param(
            ReleaseEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.release),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler},
            id="config=build_for_pr+build_for_commit+build_for_release&release&ReleaseEvent",
        ),
        # No matching job for multiple triggers:
        pytest.param(
            ReleaseEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.release),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            set(),
            id="config=build_for_pr+build_for_commit&release&ReleaseEvent",
        ),
        # multiple events for build but test only for push:
        pytest.param(
            PullRequestGithubEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler, TestingFarmHandler},
            id="config=build_for_pr+test_for_pr+build_for_commit+build_for_release"
            "&pull_request&PullRequestGithubEvent",
        ),
        pytest.param(
            PullRequestGithubEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            skip_build=True,
                        )
                    },
                ),
            ],
            {TestingFarmHandler},
            id="config=test_for_pr_skip_build" "&pull_request&PullRequestGithubEvent",
        ),
        pytest.param(
            PullRequestGithubEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            skip_build=True,
                        )
                    },
                ),
            ],
            {CoprBuildHandler, TestingFarmHandler},
            id="config=copr_build_for_pr+test_for_pr_skip_build"
            "&pull_request&PullRequestGithubEvent",
        ),
        pytest.param(
            PushGitHubEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler},
            id="config=build_for_pr+test_for_pr+build_for_commit+build_for_release"
            "&commit&PushGitHubEvent",
        ),
        pytest.param(
            CoprBuildStartEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildStartHandler},
            id="config=build_for_pr+test_for_pr+build_for_commit+build_for_release"
            "&pull_request&CoprBuildStartEvent",
        ),
        pytest.param(
            CoprBuildEndEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildEndHandler},
            id="config=build_for_pr+test_for_pr+build_for_commit+build_for_release"
            "&pull_request&CoprBuildEndEvent",
        ),
        pytest.param(
            TestingFarmResultsEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {TestingFarmResultsHandler},
            id="config=build_for_pr+test_for_pr+build_for_commit+build_for_release"
            "&pull_request&TestingFarmResultsEvent",
        ),
        pytest.param(
            TestingFarmResultsEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            set(),
            id="config=build_for_pr+test_for_pr+build_for_commit+build_for_release"
            "&commit&TestingFarmResultsEvent",
        ),
        # build for commit and release, test only for push:
        pytest.param(
            PullRequestGithubEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {TestingFarmHandler},
            id="config=test_for_pr+build_for_commit+build_for_release"
            "&pull_request&PullRequestGithubEvent",
        ),
        pytest.param(
            PushGitHubEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler},
            id="config=test_for_pr+build_for_commit+build_for_release"
            "&commit&PushGitHubEvent",
        ),
        pytest.param(
            CoprBuildStartEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildStartHandler},
            id="config=test_for_pr+build_for_commit+build_for_release"
            "&pull_request&CoprBuildStartEvent",
        ),
        pytest.param(
            CoprBuildEndEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildEndHandler},
            id="config=test_for_pr+build_for_commit+build_for_release"
            "&pull_request&CoprBuildEndEvent",
        ),
        pytest.param(
            TestingFarmResultsEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {TestingFarmResultsHandler},
            id="config=test_for_pr+build_for_commit+build_for_release"
            "&pull_request&TestingFarmResultsEvent",
        ),
        pytest.param(
            TestingFarmResultsEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            set(),
            id="config=test_for_pr+build_for_commit+build_for_release"
            "&commit&TestingFarmResultsEvent",
        ),
        # copr and koji build combination
        pytest.param(
            PullRequestGithubEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.production_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler, KojiBuildHandler},
            id="config=build_for_pr+production_build_for_pr"
            "&pull_request&PullRequestGithubEvent",
        ),
        pytest.param(
            CoprBuildStartEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.production_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildStartHandler},
            id="config=build_for_pr+production_build_for_pr"
            "&pull_request&CoprBuildStartEvent",
        ),
        pytest.param(
            KojiTaskEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.production_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {KojiTaskReportHandler},
            id="config=build_for_pr+production_build_for_pr"
            "&pull_request&KojiBuildEvent",
        ),
        pytest.param(
            PushPagureEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.koji_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {DownstreamKojiBuildHandler},
            id="config=koji_build_for_commit&commit&DownstreamKojiBuildHandler",
        ),
        pytest.param(
            KojiBuildEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.koji_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {KojiBuildReportHandler},
            id="config=koji_build_for_commit&build&DownstreamKojiBuildHandler",
        ),
        pytest.param(
            KojiBuildEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.bodhi_update,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CreateBodhiUpdateHandler, KojiBuildReportHandler},
            id="config=bodhi_update_for_commit&commit&CreateBodhiUpdateHandler",
        ),
    ],
)
def test_get_handlers_for_event(event_cls, db_trigger, jobs, result):
    # We are using isinstance for matching event to handlers
    # and flexmock can't do this for us so we need a subclass to test it.
    # (And real event classes have a lot of __init__ arguments.)
    class Event(event_cls):
        def __init__(self):
            pass

        @property
        def db_trigger(self):
            return db_trigger

        @property
        def package_config(self):
            return flexmock(jobs=jobs)

    event = Event()
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(
        ServiceConfig(packit_comment_command_prefix="/packit")
    )

    event_handlers = set(SteveJobs(event).get_handlers_for_event())
    assert event_handlers == result


@pytest.mark.parametrize(
    "event_cls, comment, packit_comment_command_prefix, db_trigger, jobs, result",
    [
        pytest.param(
            PullRequestCommentGithubEvent,
            "",
            "/packit",
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            set(),
            id="config=build_for_pr&pull_request&PullRequestCommentGithubEvent"
            "&empty_comment",
        ),
        pytest.param(
            PullRequestCommentGithubEvent,
            "",
            "/packit-stg",
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            set(),
            id="config=build_for_pr&pull_request&PullRequestCommentGithubEvent"
            "&empty_comment&stg",
        ),
        pytest.param(
            PullRequestCommentGithubEvent,
            "/packit build",
            "/packit",
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler},
            id="config=build_for_pr&pull_request&PullRequestCommentGithubEvent"
            "&packit_build",
        ),
        pytest.param(
            PullRequestCommentGithubEvent,
            "/packit build",
            "/packit",
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler},
            id="config=copr_build_for_pr&pull_request&PullRequestCommentGithubEvent"
            "&packit_build",
        ),
        pytest.param(
            PullRequestCommentGithubEvent,
            "/packit copr-build",
            "/packit",
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler},
            id="config=build_for_pr&pull_request&PullRequestCommentGithubEvent"
            "&packit_copr-build",
        ),
        pytest.param(
            PullRequestCommentGithubEvent,
            "/packit build",
            "/packit",
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {TestingFarmHandler},
            id="config=test_for_pr&pull_request&PullRequestCommentGithubEvent"
            "&packit_build",
        ),
        pytest.param(
            PullRequestCommentGithubEvent,
            "/packit test",
            "/packit",
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {TestingFarmHandler},
            id="config=test_for_pr&pull_request&PullRequestCommentGithubEvent"
            "&packit_test",
        ),
        pytest.param(
            PullRequestCommentGithubEvent,
            "/packit production-build",
            "/packit",
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.production_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {KojiBuildHandler},
            id="config=production_build_for_pr&pull_request&PullRequestCommentGithubEvent"
            "&packit_production-build",
        ),
        pytest.param(
            PullRequestCommentGithubEvent,
            "/packit build",
            "/packit",
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            skip_build=True,
                        )
                    },
                ),
            ],
            {TestingFarmHandler},
            id="config=test_for_pr_skip_build&pull_request&PullRequestCommentGithubEvent"
            "&packit_build",
        ),
        pytest.param(
            PullRequestCommentGithubEvent,
            "/packit test",
            "/packit",
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            skip_build=True,
                        )
                    },
                ),
            ],
            {TestingFarmHandler},
            id="config=test_for_pr_skip_build&pull_request&PullRequestCommentGithubEvent"
            "&packit_test",
        ),
        pytest.param(
            PullRequestCommentGithubEvent,
            "/packit build",
            "/packit-stg",
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            set(),
            id="config=build_for_pr&pull_request&PullRequestCommentGithubEvent"
            "&packit_build&stg",
        ),
        pytest.param(
            PullRequestCommentGithubEvent,
            "/packit-stg build",
            "/packit-stg",
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler},
            id="config=build_for_pr&pull_request&PullRequestCommentGithubEvent"
            "&packit_stg_build&stg",
        ),
    ],
)
def test_get_handlers_for_comment_event(
    event_cls, comment, packit_comment_command_prefix, db_trigger, jobs, result
):
    # We are using isinstance for matching event to handlers
    # and flexmock can't do this for us so we need a subclass to test it.
    # (And real event classes have a lot of __init__ arguments.)
    class Event(event_cls):
        def __init__(self):
            self.comment = comment

        @property
        def db_trigger(self):
            return db_trigger

        @property
        def package_config(self):
            return flexmock(jobs=jobs)

    flexmock(ServiceConfig).should_receive("get_service_config").and_return(
        ServiceConfig(comment_command_prefix=packit_comment_command_prefix)
    )

    event = Event()
    if result:
        comment_object = flexmock()
        event._comment_object = comment_object
        flexmock(comment_object).should_receive("add_reaction").with_args(
            COMMENT_REACTION
        ).once()

    event_handlers = set(SteveJobs(event).get_handlers_for_event())
    assert event_handlers == result


@pytest.mark.parametrize(
    "event_cls,check_name_job,db_trigger,job_identifier,jobs,result",
    [
        pytest.param(
            CheckRerunPullRequestEvent,
            "rpm-build",
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            None,
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler},
            id="config=build_for_pr&pull_request&CheckRerunPullRequestEvent",
        ),
        pytest.param(
            CheckRerunPullRequestEvent,
            "rpm-build",
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            "the-identifier",
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            identifier="the-identifier",
                        )
                    },
                ),
            ],
            {CoprBuildHandler},
            id="config=build_for_pr&pull_request&CheckRerunPullRequestEvent"
            "&identifier_match",
        ),
        pytest.param(
            CheckRerunPullRequestEvent,
            "rpm-build",
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            None,
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            identifier="the-identifier",
                        )
                    },
                ),
            ],
            set(),
            id="config=build_for_pr&pull_request&CheckRerunPullRequestEvent"
            "&identifier_not_in_event",
        ),
        pytest.param(
            CheckRerunPullRequestEvent,
            "rpm-build",
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            "the-identifier",
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            set(),
            id="config=build_for_pr&pull_request&CheckRerunPullRequestEvent"
            "&identifier_not_in_config",
        ),
        pytest.param(
            CheckRerunPullRequestEvent,
            "testing-farm",
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            None,
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {TestingFarmHandler},
            id="config=tests_for_pr&pull_request&CheckRerunPullRequestEvent",
        ),
        pytest.param(
            CheckRerunPullRequestEvent,
            "production-build",
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            None,
            [
                JobConfig(
                    type=JobType.production_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {KojiBuildHandler},
            id="config=production_build_for_pr&pull_request&CheckRerunPullRequestEvent",
        ),
        pytest.param(
            CheckRerunCommitEvent,
            "rpm-build",
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            None,
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler},
            id="config=build_for_pr&pull_request&CheckRerunCommitEvent",
        ),
        pytest.param(
            CheckRerunCommitEvent,
            "testing-farm",
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            None,
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {TestingFarmHandler},
            id="config=tests_for_pr&pull_request&CheckRerunCommitEvent",
        ),
        pytest.param(
            CheckRerunReleaseEvent,
            "production-build",
            flexmock(job_config_trigger_type=JobConfigTriggerType.release),
            None,
            [
                JobConfig(
                    type=JobType.production_build,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {KojiBuildHandler},
            id="config=production_build_for_release&pull_request&CheckRerunCommitEvent",
        ),
        pytest.param(
            CheckRerunReleaseEvent,
            "production-build",
            flexmock(job_config_trigger_type=JobConfigTriggerType.release),
            None,
            [
                JobConfig(
                    type=JobType.production_build,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {KojiBuildHandler},
            id="config=production_build_for_release&pull_request&CheckRerunCommitEvent",
        ),
    ],
)
def test_get_handlers_for_check_rerun_event(
    event_cls, check_name_job, job_identifier, db_trigger, jobs, result
):
    # We are using isinstance for matching event to handlers
    # and flexmock can't do this for us so we need a subclass to test it.
    # (And real event classes have a lot of __init__ arguments.)
    class Event(event_cls):
        def __init__(self):
            self.check_name_job = check_name_job
            self.job_identifier = job_identifier

        @property
        def db_trigger(self):
            return db_trigger

        @property
        def package_config(self):
            return flexmock(jobs=jobs)

    flexmock(ServiceConfig).should_receive("get_service_config").and_return(
        ServiceConfig(packit_comment_command_prefix="/packit")
    )
    event = Event()

    event_handlers = set(SteveJobs(event).get_handlers_for_event())
    assert event_handlers == result


@pytest.mark.parametrize(
    "handler_kls,event_cls,db_trigger,jobs,result_job_config",
    [
        # Basic copr build:
        pytest.param(
            CoprBuildHandler,
            PullRequestGithubEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                )
            ],
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                )
            ],
            id="build_for_pr&CoprBuildHandler&PullRequestGithubEvent",
        ),
        pytest.param(
            CoprBuildStartHandler,
            CoprBuildStartEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                )
            ],
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                )
            ],
            id="build_for_pr&CoprBuildStartHandler&CoprBuildStartEvent",
        ),
        pytest.param(
            CoprBuildEndHandler,
            CoprBuildEndEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                )
            ],
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                )
            ],
            id="build_for_pr&CoprBuildEndHandler&CoprBuildEndEvent",
        ),
        # Test only for pr:
        pytest.param(
            CoprBuildHandler,
            PullRequestGithubEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                )
            ],
            [],
            id="tests_for_pr&CoprBuildHandler&PullRequestGithubEvent",
        ),
        # Both test and build for pr:
        pytest.param(
            CoprBuildHandler,
            PullRequestGithubEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                )
            ],
            id="build_for_pr+tests_for_pr&CoprBuildHandler&PullRequestGithubEvent",
        ),
        pytest.param(
            CoprBuildHandler,
            PullRequestGithubEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                # Reverse order:
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                )
            ],
            id="test_for_pr+build_for_pr&CoprBuildHandler&PullRequestGithubEvent",
        ),
        # Multiple builds for pr:
        pytest.param(
            CoprBuildHandler,
            PullRequestGithubEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project2",
                        )
                    },
                ),
            ],
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project2",
                        )
                    },
                ),
            ],
            id="build_for_pr_twice&CoprBuildHandler&PullRequestGithubEvent",
        ),
        # Multiple triggers for build:
        pytest.param(
            CoprBuildHandler,
            PullRequestGithubEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            project="project2",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            project="project3",
                        )
                    },
                ),
            ],
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        )
                    },
                )
            ],
            id="build_for_pr+build_for_commit+build_for_release"
            "&CoprBuildHandler&PullRequestGithubEvent",
        ),
        pytest.param(
            CoprBuildHandler,
            PushGitHubEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            project="project2",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            project="project3",
                        )
                    },
                ),
            ],
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            project="project2",
                        )
                    },
                ),
            ],
            id="build_for_pr+build_for_commit+build_for_release"
            "&CoprBuildHandler&PushGitHubEvent",
        ),
        pytest.param(
            CoprBuildHandler,
            ReleaseEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.release),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            project="project2",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            project="project3",
                        )
                    },
                ),
            ],
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            project="project3",
                        )
                    },
                ),
            ],
            id="build_for_pr+build_for_commit+build_for_release"
            "&CoprBuildHandler&ReleaseEvent",
        ),
        # Build for commit and release, test for pr
        pytest.param(
            CoprBuildHandler,
            PullRequestGithubEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            project="project2",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            project="project3",
                        )
                    },
                ),
            ],
            [],
            id="tests_for_pr+build_for_commit+build_for_release"
            "&CoprBuildHandler&PullRequestGithubEvent",
        ),
        pytest.param(
            CoprBuildHandler,
            PushGitHubEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            project="project2",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            project="project3",
                        )
                    },
                ),
            ],
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            project="project2",
                        )
                    },
                ),
            ],
            id="tests_for_pr+build_for_commit+build_for_release"
            "&CoprBuildHandler&PushGitHubEvent",
        ),
        pytest.param(
            CoprBuildHandler,
            ReleaseEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.release),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            project="project2",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            project="project3",
                        )
                    },
                ),
            ],
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            project="project3",
                        )
                    },
                ),
            ],
            id="tests_for_pr+build_for_commit+build_for_release"
            "&CoprBuildHandler&ReleaseEvent",
        ),
        pytest.param(
            CoprBuildStartHandler,
            CoprBuildStartEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            project="project2",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            project="project3",
                        )
                    },
                ),
            ],
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        )
                    },
                ),
            ],
            id="tests_for_pr+build_for_commit+build_for_release"
            "&CoprBuildStartHandler&CoprBuildStartEvent",
        ),
        pytest.param(
            CoprBuildEndHandler,
            CoprBuildEndEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            project="project2",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            project="project3",
                        )
                    },
                ),
            ],
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        )
                    },
                ),
            ],
            id="tests_for_pr+build_for_commit+build_for_release"
            "&CoprBuildEndHandler&CoprBuildEndEvent",
        ),
        pytest.param(
            TestingFarmResultsHandler,
            TestingFarmResultsEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            project="project2",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            project="project3",
                        )
                    },
                ),
            ],
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        )
                    },
                ),
            ],
            id="tests_for_pr+build_for_commit+build_for_release"
            "&TestingFarmResultsHandler&TestingFarmResultsEvent",
        ),
        # Build for pr, commit and release, test for pr
        pytest.param(
            CoprBuildHandler,
            PullRequestGithubEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project0",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            project="project2",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            project="project3",
                        )
                    },
                ),
            ],
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project0",
                        )
                    },
                ),
            ],
            id="build_for_pr+tests_for_pr+build_for_commit+build_for_release"
            "&CoprBuildHandler&PullRequestGithubEvent",
        ),
        pytest.param(
            CoprBuildHandler,
            PushGitHubEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project0",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            project="project2",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            project="project3",
                        )
                    },
                ),
            ],
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            project="project2",
                        )
                    },
                ),
            ],
            id="build_for_pr+tests_for_pr+build_for_commit+build_for_release"
            "&CoprBuildHandler&PushGitHubEvent",
        ),
        pytest.param(
            CoprBuildHandler,
            ReleaseEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.release),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project0",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            project="project2",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            project="project3",
                        )
                    },
                ),
            ],
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            project="project3",
                        )
                    },
                ),
            ],
            id="build_for_pr+tests_for_pr+build_for_commit+build_for_release"
            "&CoprBuildHandler&ReleaseEvent",
        ),
        pytest.param(
            CoprBuildStartHandler,
            CoprBuildStartEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project0",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            project="project2",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            project="project3",
                        )
                    },
                ),
            ],
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project0",
                        )
                    },
                ),
            ],
            id="build_for_pr+tests_for_pr+build_for_commit+build_for_release"
            "&CoprBuildStartHandler&CoprBuildStartEvent",
        ),
        pytest.param(
            CoprBuildEndHandler,
            CoprBuildEndEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project0",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            project="project2",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            project="project3",
                        )
                    },
                ),
            ],
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project0",
                        )
                    },
                ),
            ],
            id="build_for_pr+tests_for_pr+build_for_commit+build_for_release"
            "&CoprBuildEndHandler&CoprBuildEndEvent",
        ),
        pytest.param(
            TestingFarmResultsHandler,
            TestingFarmResultsEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project0",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            project="project2",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            project="project3",
                        )
                    },
                ),
            ],
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        )
                    },
                ),
            ],
            id="build_for_pr+tests_for_pr+build_for_commit+build_for_release"
            "&TestingFarmResultsHandler&TestingFarmResultsEvent",
        ),
        # copr build and koji build:
        pytest.param(
            CoprBuildHandler,
            PullRequestGithubEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.production_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                )
            ],
            id="build_for_pr+production_build_for_pr&CoprBuildHandler&PullRequestGithubEvent",
        ),
        pytest.param(
            KojiBuildHandler,
            PullRequestGithubEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.production_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [
                JobConfig(
                    type=JobType.production_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                )
            ],
            id="build_for_pr+production_build_for_pr&KojiBuildHandler&PullRequestGithubEvent",
        ),
        pytest.param(
            KojiTaskReportHandler,
            KojiTaskEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.production_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [
                JobConfig(
                    type=JobType.production_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                )
            ],
            id="build_for_pr+production_build_for_pr&KojiBuildReportHandler&KojiBuildEvent",
        ),
        # comments:
        pytest.param(
            CoprBuildHandler,
            PullRequestCommentGithubEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                )
            ],
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                )
            ],
            id="build_for_pr&CoprBuildHandler&PullRequestCommentGithubEvent",
        ),
        pytest.param(
            CoprBuildHandler,
            MergeRequestCommentGitlabEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                )
            ],
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                )
            ],
            id="build_for_pr&CoprBuildHandler&MergeRequestCommentGitlabEvent",
        ),
        pytest.param(
            CoprBuildHandler,
            PullRequestCommentPagureEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                )
            ],
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                )
            ],
            id="build_for_pr&CoprBuildHandler&PullRequestCommentPagureEvent",
        ),
        # Build comment for test defined:
        pytest.param(
            CoprBuildHandler,
            PullRequestCommentGithubEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                )
            ],
            [],
            id="tests_for_pr&CoprBuildHandler&PullRequestCommentGithubEvent",
        ),
        # Testing farm on comment:
        pytest.param(
            TestingFarmHandler,
            PullRequestCommentGithubEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                )
            ],
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                )
            ],
            id="tests_for_pr&TestingFarmHandler&PullRequestCommentGithubEvent",
        ),
        # Propose update retrigger:
        pytest.param(
            ProposeDownstreamHandler,
            IssueCommentEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.release),
            [
                JobConfig(
                    type=JobType.propose_downstream,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                )
            ],
            [
                JobConfig(
                    type=JobType.propose_downstream,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                )
            ],
            id="propose_downstream_for_release&TestingFarmHandler&PullRequestCommentGithubEvent",
        ),
        pytest.param(
            DownstreamKojiBuildHandler,
            PushPagureEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.koji_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                )
            ],
            [
                JobConfig(
                    type=JobType.koji_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                )
            ],
            id="koji_build_for_commit&DownstreamKojiBuildHandler&PushPagureEvent",
        ),
        pytest.param(
            KojiBuildReportHandler,
            KojiBuildEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.koji_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                )
            ],
            [
                JobConfig(
                    type=JobType.koji_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                )
            ],
            id="koji_build_for_commit&KojiBuildReportHandler&KojiBuildEvent",
        ),
        pytest.param(
            CreateBodhiUpdateHandler,
            KojiBuildEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.bodhi_update,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                )
            ],
            [
                JobConfig(
                    type=JobType.bodhi_update,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                )
            ],
            id="bodhi_update_for_commit&CreateBodhiUpdateHandler&KojiBuildEvent",
        ),
        pytest.param(
            KojiBuildReportHandler,
            KojiBuildEvent,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.bodhi_update,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                )
            ],
            [
                JobConfig(
                    type=JobType.bodhi_update,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                )
            ],
            id="bodhi_update_for_commit&KojiBuildReportHandler&KojiBuildEvent",
        ),
    ],
)
def test_get_config_for_handler_kls(
    handler_kls: Type[JobHandler], event_cls, db_trigger, jobs, result_job_config
):
    class Event(event_cls):  # type: ignore
        def __init__(self):
            pass

        @property
        def db_trigger(self):
            return db_trigger

        @property
        def package_config(self):
            return flexmock(jobs=jobs)

    event = Event()

    job_config = SteveJobs(event).get_config_for_handler_kls(
        handler_kls=handler_kls,
    )
    assert job_config == result_job_config


@pytest.mark.parametrize(
    "event_kls,comment,packit_comment_command_prefix,result",
    [
        pytest.param(
            PullRequestCommentGithubEvent,
            "/packit build",
            "/packit",
            {CoprBuildHandler, TestingFarmHandler},
        ),
        pytest.param(
            PullRequestCommentGithubEvent,
            "/packit-stg build",
            "/packit-stg",
            {CoprBuildHandler, TestingFarmHandler},
        ),
        pytest.param(
            PullRequestCommentGithubEvent,
            "/packit test",
            "/packit",
            {TestingFarmHandler},
        ),
        pytest.param(
            PullRequestCommentGithubEvent,
            "/packit-stg test",
            "/packit-stg",
            {TestingFarmHandler},
        ),
        pytest.param(
            PullRequestCommentGithubEvent,
            "/packit propose-downstream",
            "/packit",
            {ProposeDownstreamHandler},
        ),
        pytest.param(
            PullRequestCommentGithubEvent,
            "/packit-stg propose-downstream",
            "/packit-stg",
            {ProposeDownstreamHandler},
        ),
        pytest.param(
            PullRequestCommentGithubEvent,
            "/packit production-build",
            "/packit",
            {KojiBuildHandler},
        ),
        pytest.param(
            PullRequestCommentGithubEvent,
            "/packit-stg production-build",
            "/packit-stg",
            {KojiBuildHandler},
        ),
    ],
)
def test_get_handlers_triggered_by_comment(
    event_kls, comment, packit_comment_command_prefix, result
):
    class Event(event_kls):
        def __init__(self):
            self.comment = comment

    event = Event()
    comment_object = flexmock()
    event._comment_object = comment_object
    flexmock(comment_object).should_receive("add_reaction").with_args(
        COMMENT_REACTION
    ).once()

    flexmock(ServiceConfig).should_receive("get_service_config").and_return(
        ServiceConfig(comment_command_prefix=packit_comment_command_prefix)
    )
    event_handlers = SteveJobs(event).get_handlers_for_comment_and_rerun_event()
    assert event_handlers == result


@pytest.mark.parametrize(
    "event_kls,check_name_job,result",
    [
        pytest.param(
            CheckRerunPullRequestEvent,
            "rpm-build",
            {CoprBuildHandler},
        ),
        pytest.param(
            CheckRerunPullRequestEvent,
            "testing-farm",
            {TestingFarmHandler},
        ),
        pytest.param(
            CheckRerunPullRequestEvent,
            "production-build",
            {KojiBuildHandler},
        ),
    ],
)
def test_get_handlers_triggered_by_check_rerun(event_kls, check_name_job, result):
    class Event(event_kls):
        def __init__(self):
            self.check_name_job = check_name_job

    flexmock(ServiceConfig).should_receive("get_service_config").and_return(
        ServiceConfig(packit_comment_command_prefix="/packit")
    )
    event = Event()
    event_handlers = SteveJobs(event).get_handlers_for_comment_and_rerun_event()
    assert event_handlers == result


@pytest.mark.parametrize(
    "event_kls,handler,allowed_handlers",
    [
        pytest.param(
            PullRequestCommentGithubEvent,
            CoprBuildHandler,
            {CoprBuildHandler, KojiBuildHandler},
        ),
        pytest.param(
            CheckRerunPullRequestEvent,
            KojiBuildHandler,
            {CoprBuildHandler, KojiBuildHandler},
        ),
        pytest.param(
            ReleaseEvent,
            ProposeDownstreamHandler,
            {KojiBuildHandler, ProposeDownstreamHandler},
        ),
    ],
)
def test_handler_matches_to_job(event_kls, handler: Type[JobHandler], allowed_handlers):
    class Event(event_kls):  # type: ignore
        def __init__(self):
            pass

    event = Event()
    assert SteveJobs(event).is_handler_matching_the_event(handler, allowed_handlers)


@pytest.mark.parametrize(
    "event_kls,handler,allowed_handlers",
    [
        pytest.param(
            PushPagureEvent,
            CoprBuildHandler,
            {DownstreamKojiBuildHandler},
        ),
        pytest.param(
            CheckRerunPullRequestEvent,
            KojiBuildHandler,
            {CoprBuildHandler},
        ),
    ],
)
def test_handler_doesnt_match_to_job(
    event_kls, handler: Type[JobHandler], allowed_handlers
):
    class Event(event_kls):  # type: ignore
        def __init__(self):
            pass

    event = Event()
    assert not SteveJobs(event).is_handler_matching_the_event(handler, allowed_handlers)


@pytest.mark.parametrize(
    "event_kls,job_config_trigger_type,jobs,result,kwargs",
    [
        pytest.param(
            PullRequestCommentGithubEvent,
            JobConfigTriggerType.pull_request,
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.propose_downstream,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {},
        ),
        pytest.param(
            PullRequestCommentGithubEvent,
            JobConfigTriggerType.release,
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.propose_downstream,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [
                JobConfig(
                    type=JobType.propose_downstream,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {},
        ),
        pytest.param(
            PullRequestCommentGithubEvent,
            JobConfigTriggerType.release,
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.propose_downstream,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.propose_downstream,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [
                JobConfig(
                    type=JobType.propose_downstream,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {},
        ),
        pytest.param(
            PullRequestCommentGithubEvent,
            JobConfigTriggerType.release,
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [],
            {},
        ),
        pytest.param(
            PullRequestCommentPagureEvent,
            JobConfigTriggerType.pull_request,
            [
                JobConfig(
                    type=JobType.koji_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [
                JobConfig(
                    type=JobType.koji_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {},
        ),
        pytest.param(
            PullRequestCommentPagureEvent,
            JobConfigTriggerType.pull_request,
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.koji_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.koji_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {},
        ),
        pytest.param(
            CheckRerunPullRequestEvent,
            JobConfigTriggerType.pull_request,
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            identifier="first",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            identifier="second",
                        )
                    },
                ),
            ],
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            identifier="first",
                        )
                    },
                ),
            ],
            {"job_identifier": "first"},
        ),
        pytest.param(
            PullRequestCommentPagureEvent,
            JobConfigTriggerType.pull_request,
            [
                JobConfig(
                    type=JobType.bodhi_update,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            identifier="first",
                        )
                    },
                ),
            ],
            [
                JobConfig(
                    type=JobType.bodhi_update,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            identifier="first",
                        )
                    },
                ),
            ],
            {},
        ),
        pytest.param(
            IssueCommentEvent,
            JobConfigTriggerType.release,
            [
                JobConfig(
                    type=JobType.koji_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [
                JobConfig(
                    type=JobType.koji_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {"issue_id": 1},
        ),
        pytest.param(
            IssueCommentEvent,
            JobConfigTriggerType.release,
            [
                JobConfig(
                    type=JobType.bodhi_update,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [
                JobConfig(
                    type=JobType.bodhi_update,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {"issue_id": 1},
        ),
    ],
)
def test_get_jobs_matching_trigger(
    event_kls, job_config_trigger_type, jobs, result, kwargs
):
    class Event(event_kls):
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

        @property
        def job_config_trigger_type(self):
            return job_config_trigger_type

        @property
        def package_config(self):
            return flexmock(jobs=jobs)

    event = Event(**kwargs)
    assert result == SteveJobs(event).get_jobs_matching_event()


@pytest.mark.parametrize(
    "event_kls,jobs,handler_kls,tasks_created,identifier",
    [
        (
            TestingFarmResultsEvent,
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            identifier="foo",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            identifier="bar",
                        )
                    },
                ),
            ],
            TestingFarmResultsHandler,
            1,
            "foo",
        ),
        (
            TestingFarmResultsEvent,
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            TestingFarmResultsHandler,
            2,
            None,
        ),
    ],
)
def test_create_tasks_tf_identifier(
    event_kls, jobs, handler_kls, tasks_created, identifier
):
    class Event(event_kls):
        def __init__(self):
            self._db_trigger = None

        @property
        def package_config(self):
            return flexmock(jobs=jobs)

        def get_dict(self, *args, **kwargs):
            return {"identifier": identifier}

        @property
        def actor(self):
            return None

    event = Event()
    # Ignore remote reporting
    flexmock(SteveJobs, report_task_accepted=lambda handler_kls, job_config: None)
    # We are testing the number of tasks, the exact signatures are not important
    flexmock(handler_kls).should_receive("get_signature").and_return(None)
    flexmock(TaskResults, create_from=lambda *args, **kwargs: object())
    flexmock(celery).should_receive("group").with_args(
        tasks_created * [None]
    ).and_return(flexmock().should_receive("apply_async").mock())
    assert tasks_created == len(SteveJobs(event).create_tasks(jobs, handler_kls))


def test_monorepo_jobs_matching_event():
    python_teamcity_messages = CommonPackageConfig(
        patch_generation_ignore_paths=[],
        specfile_path="python-teamcity-messages.spec",
        upstream_ref=None,
        files_to_sync=[
            {
                "dest": "python-teamcity-messages.spec",
                "mkpath": False,
                "filters": [],
                "delete": False,
                "src": ["python-teamcity-messages.spec"],
            },
            {
                "dest": ".packit.yaml",
                "mkpath": False,
                "filters": [],
                "delete": False,
                "src": [".packit.yaml"],
            },
        ],
        paths=["."],
        dist_git_base_url="https://src.fedoraproject.org/",
        upstream_package_name="teamcity-messages",
        archive_root_dir_template="{upstream_pkg_name}-{version}",
        upstream_project_url="https://github.com/majamassarini/teamcity-messages",
        config_file_path=".packit.yaml",
        patch_generation_patch_id_digits=4,
        dist_git_namespace="rpms",
        downstream_package_name="python-teamcity-messages",
        upstream_tag_template="v{version}",
    )
    python_teamcity_messages_double = copy.deepcopy(python_teamcity_messages)
    python_teamcity_messages_double.downstream_package_name = "a double"

    packages = {
        "python-teamcity-messages": copy.deepcopy(python_teamcity_messages),
        "python-teamcity-messages-double": python_teamcity_messages_double,
    }
    jobs = [
        JobConfig(
            type=JobType.propose_downstream,
            trigger=JobConfigTriggerType.release,
            skip_build=False,
            packages={
                "python-teamcity-messages": python_teamcity_messages,
                "python-teamcity-messages-double": python_teamcity_messages_double,
            },
        ),
        JobConfig(
            type=JobType.propose_downstream,
            trigger=JobConfigTriggerType.release,
            skip_build=False,
            packages={
                "python-teamcity-messages": python_teamcity_messages,
            },
        ),
        JobConfig(
            type=JobType.propose_downstream,
            trigger=JobConfigTriggerType.release,
            skip_build=False,
            packages={
                "python-teamcity-messages-double": python_teamcity_messages_double
            },
        ),
    ]
    packages_config = PackageConfig(packages=packages, jobs=jobs)

    class Event(ReleaseEvent):
        def __init__(self):
            self._package_config_searched = None
            self._package_config = packages_config
            self._project = flexmock()
            self._base_project = flexmock()
            self._pr_id = flexmock()
            self._commit_sha = flexmock()
            self.fail_when_config_file_missing = True

        @property
        def job_config_trigger_type(self):
            return JobConfigTriggerType.release

    event = Event()
    steve = SteveJobs(event)
    handlers = steve.get_handlers_for_event()
    assert handlers

    for handler in handlers:
        job_configs = steve.get_config_for_handler_kls(handler)
        assert job_configs

        original_ref = 0
        double_ref = 0
        for job_config in job_configs:
            if job_config.downstream_package_name == "a double":
                double_ref += 1
            else:
                original_ref += 1

        assert original_ref == 2
        assert double_ref == 2
