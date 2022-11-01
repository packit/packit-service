# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import pytest
from flexmock import flexmock

from packit.config.job_config import JobType, JobConfigTriggerType
from packit_service.worker.checker.koji import PermissionOnKoji
from packit_service.worker.events import (
    PullRequestGithubEvent,
)
from packit_service.worker.events.event import EventData
from packit_service.worker.events.github import PushGitHubEvent
from packit_service.worker.events.gitlab import MergeRequestGitlabEvent, PushGitlabEvent
from packit_service.worker.events.pagure import PushPagureEvent
from packit_service.worker.helpers.build.koji_build import KojiBuildJobHelper
from packit_service.worker.mixin import ConfigMixin


def construct_dict(event, action=None, git_ref=None):
    return {
        "event_type": event,
        "actor": "bfu",
        "project_url": "some_url",
        "git_ref": git_ref,
        "action": action,
    }


@pytest.mark.parametrize(
    "success, event, is_scratch, can_merge_pr, trigger",
    (
        pytest.param(
            False,
            construct_dict(event=MergeRequestGitlabEvent.__name__, action="closed"),
            True,
            True,
            JobConfigTriggerType.pull_request,
            id="closed MRs are ignored",
        ),
        pytest.param(
            False,
            construct_dict(event=PushGitHubEvent.__name__),
            True,
            None,
            JobConfigTriggerType.commit,
            id="GitHub push to non-configured branch is ignored",
        ),
        pytest.param(
            False,
            construct_dict(event=PushGitlabEvent.__name__),
            True,
            None,
            JobConfigTriggerType.commit,
            id="GitLab push to non-configured branch is ignored",
        ),
        pytest.param(
            False,
            construct_dict(event=PushPagureEvent.__name__),
            True,
            None,
            JobConfigTriggerType.commit,
            id="Pagure push to non-configured branch is ignored",
        ),
        pytest.param(
            True,
            construct_dict(event=PushPagureEvent.__name__, git_ref="release"),
            True,
            None,
            JobConfigTriggerType.commit,
            id="Pagure push to configured branch is not ignored",
        ),
        pytest.param(
            False,
            construct_dict(event=PullRequestGithubEvent.__name__),
            True,
            False,
            JobConfigTriggerType.pull_request,
            id="Permissions on GitHub",
        ),
        pytest.param(
            False,
            construct_dict(event=MergeRequestGitlabEvent.__name__),
            True,
            False,
            JobConfigTriggerType.pull_request,
            id="Permissions on GitLab",
        ),
        pytest.param(
            False,
            construct_dict(event=MergeRequestGitlabEvent.__name__),
            False,
            True,
            JobConfigTriggerType.pull_request,
            id="Non-scratch builds are prohibited",
        ),
        pytest.param(
            True,
            construct_dict(event=PullRequestGithubEvent.__name__),
            True,
            True,
            JobConfigTriggerType.pull_request,
            id="PR from GitHub shall pass",
        ),
        pytest.param(
            True,
            construct_dict(event=MergeRequestGitlabEvent.__name__),
            True,
            True,
            JobConfigTriggerType.pull_request,
            id="MR from GitLab shall pass",
        ),
    ),
)
def test_koji_permissions(success, event, is_scratch, can_merge_pr, trigger):
    package_config = flexmock(jobs=[])
    job_config = flexmock(
        type=JobType.upstream_koji_build,
        scratch=is_scratch,
        trigger=trigger,
        targets={"fedora-37"},
        branch="release",
    )

    git_project = flexmock(
        namespace="packit",
        repo="ogr",
        default_branch="main",
    )
    git_project.should_receive("can_merge_pr").and_return(can_merge_pr)
    flexmock(ConfigMixin).should_receive("project").and_return(git_project)

    db_trigger = flexmock(job_config_trigger_type=trigger)
    flexmock(EventData).should_receive("db_trigger").and_return(db_trigger)

    if not success:
        flexmock(KojiBuildJobHelper).should_receive("report_status_to_all")

    checker = PermissionOnKoji(package_config, job_config, event)

    assert checker.pre_check() == success
