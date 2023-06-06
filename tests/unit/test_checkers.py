# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import pytest
from flexmock import flexmock

from packit.config import (
    CommonPackageConfig,
    JobType,
    JobConfigTriggerType,
)
from packit_service.config import ServiceConfig
from packit_service.models import CoprBuildTargetModel
from packit_service.worker.checker.copr import (
    IsJobConfigTriggerMatching as IsJobConfigTriggerMatchingCopr,
)
from packit_service.worker.checker.koji import (
    IsJobConfigTriggerMatching as IsJobConfigTriggerMatchingKoji,
)
from packit_service.worker.checker.koji import (
    PermissionOnKoji,
)
from packit_service.worker.checker.testing_farm import (
    IsJobConfigTriggerMatching as IsJobConfigTriggerMatchingTF,
    IsIdentifierFromCommentMatching,
    IsLabelFromCommentMatching,
)
from packit_service.worker.checker.vm_image import (
    IsCoprBuildForChrootOk,
    HasAuthorWriteAccess,
)
from packit_service.worker.events import (
    PullRequestGithubEvent,
)
from packit_service.worker.events.event import EventData
from packit_service.worker.events.github import (
    PushGitHubEvent,
    PullRequestCommentGithubEvent,
)
from packit_service.worker.events.gitlab import MergeRequestGitlabEvent, PushGitlabEvent
from packit_service.worker.events.pagure import PushPagureEvent
from packit_service.worker.helpers.build.koji_build import KojiBuildJobHelper
from packit_service.worker.mixin import ConfigFromEventMixin


def construct_dict(event, action=None, git_ref="random-non-configured-branch"):
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
        get_pr=lambda pr_id: flexmock(target_branch="release"),
    )
    git_project.should_receive("can_merge_pr").and_return(can_merge_pr)
    flexmock(ConfigFromEventMixin).should_receive("project").and_return(git_project)

    db_project_event = flexmock(
        job_config_trigger_type=trigger, name=event["git_ref"], pr_id=1
    )
    flexmock(EventData).should_receive("db_project_event").and_return(db_project_event)

    if not success:
        flexmock(KojiBuildJobHelper).should_receive("report_status_to_all")

    checker = PermissionOnKoji(package_config, job_config, event)

    assert checker.pre_check() == success


@pytest.mark.parametrize(
    "checker_kls",
    (
        IsJobConfigTriggerMatchingKoji,
        IsJobConfigTriggerMatchingCopr,
        IsJobConfigTriggerMatchingTF,
    ),
)
@pytest.mark.parametrize(
    "success, event, trigger",
    (
        pytest.param(
            False,
            construct_dict(event=PushGitHubEvent.__name__),
            JobConfigTriggerType.commit,
            id="GitHub push to non-configured branch is ignored",
        ),
        pytest.param(
            False,
            construct_dict(event=PushGitlabEvent.__name__),
            JobConfigTriggerType.commit,
            id="GitLab push to non-configured branch is ignored",
        ),
        pytest.param(
            False,
            construct_dict(event=PushPagureEvent.__name__),
            JobConfigTriggerType.commit,
            id="Pagure push to non-configured branch is ignored",
        ),
        pytest.param(
            True,
            construct_dict(event=PushPagureEvent.__name__, git_ref="release"),
            JobConfigTriggerType.commit,
            id="Pagure push to configured branch is not ignored",
        ),
    ),
)
def test_branch_push_event_checker(success, event, trigger, checker_kls):
    package_config = flexmock(jobs=[])
    job_config = flexmock(
        type=JobType.upstream_koji_build,
        trigger=trigger,
        targets={"fedora-37"},
        branch="release",
    )

    git_project = flexmock(
        namespace="packit",
        repo="ogr",
        default_branch="main",
    )
    flexmock(ConfigFromEventMixin).should_receive("project").and_return(git_project)

    db_project_event = flexmock(job_config_trigger_type=trigger, name=event["git_ref"])
    flexmock(EventData).should_receive("db_project_event").and_return(db_project_event)

    checker = checker_kls(package_config, job_config, event)

    assert checker.pre_check() == success


@pytest.mark.parametrize(
    "checker_kls",
    (
        IsJobConfigTriggerMatchingKoji,
        IsJobConfigTriggerMatchingCopr,
        IsJobConfigTriggerMatchingTF,
    ),
)
@pytest.mark.parametrize(
    "configured_branch, success, event, trigger",
    (
        pytest.param(
            "the-branch",
            True,
            construct_dict(event=PullRequestGithubEvent.__name__),
            JobConfigTriggerType.pull_request,
            id="GitHub PR target branch matches",
        ),
        pytest.param(
            "the-other-branch",
            False,
            construct_dict(event=PullRequestGithubEvent.__name__),
            JobConfigTriggerType.pull_request,
            id="GitHub PR target branch does not match",
        ),
        pytest.param(
            "the-branch",
            True,
            construct_dict(event=MergeRequestGitlabEvent.__name__),
            JobConfigTriggerType.pull_request,
            id="GitLab PR target branch matches",
        ),
        pytest.param(
            "the-other-branch",
            False,
            construct_dict(event=MergeRequestGitlabEvent.__name__),
            JobConfigTriggerType.pull_request,
            id="GitLab PR target branch does not match",
        ),
    ),
)
def test_pr_event_checker(configured_branch, success, event, trigger, checker_kls):
    package_config = flexmock(jobs=[])
    job_config = flexmock(
        type=JobType.upstream_koji_build,
        trigger=trigger,
        targets={"fedora-37"},
        branch=configured_branch,
    )

    git_project = flexmock(
        namespace="packit",
        repo="ogr",
        get_pr=lambda pr_id: flexmock(target_branch="the-branch"),
    )
    flexmock(ConfigFromEventMixin).should_receive("project").and_return(git_project)

    db_project_event = flexmock(job_config_trigger_type=trigger, pr_id=1)
    flexmock(EventData).should_receive("db_project_event").and_return(db_project_event)

    checker = checker_kls(package_config, job_config, event)

    assert checker.pre_check() == success


@pytest.mark.parametrize(
    "success, project_name, owner, copr_builds, error_msg",
    (
        pytest.param(
            True,
            "knx-stack",
            "mmassari",
            [
                flexmock(
                    project_name="knx-stack",
                    owner="mmassari",
                    target="fedora-36-x86_64",
                    status="success",
                    get_project_event_object=lambda: flexmock(id=1),
                ),
            ],
            None,
            id="A successful Copr build for project found",
        ),
        pytest.param(
            False,
            "knx-stack",
            "mmassari",
            [],
            "No successful Copr build found for project mmassari/knx-stack, "
            "commit 1 and chroot (target) fedora-36-x86_64",
            id="No copr build found",
        ),
        pytest.param(
            False,
            None,
            None,
            [],
            "No successful Copr build found for "
            "commit 1 and chroot (target) fedora-36-x86_64",
            id="No copr build found, job config without Copr project info",
        ),
    ),
)
def test_vm_image_is_copr_build_ok_for_chroot(
    fake_package_config_job_config_project_db_trigger,
    success,
    project_name,
    owner,
    copr_builds,
    error_msg,
):
    package_config, job_config, _, _ = fake_package_config_job_config_project_db_trigger
    job_config.project = project_name
    job_config.owner = owner

    flexmock(CoprBuildTargetModel).should_receive("get_all_by").and_return(copr_builds)

    checker = IsCoprBuildForChrootOk(
        package_config,
        job_config,
        {"event_type": PullRequestCommentGithubEvent.__name__, "commit_sha": "1"},
    )
    checker.data._db_project_event = flexmock(id=1)

    if error_msg:
        flexmock(checker).should_receive("report_pre_check_failure").with_args(
            error_msg
        ).once()

    assert checker.pre_check() == success


@pytest.mark.parametrize(
    "has_write_access, result",
    (
        pytest.param(
            True,
            True,
            id="Author has write access",
        ),
        pytest.param(
            False,
            False,
            id="Author has not write access",
        ),
    ),
)
def test_vm_image_has_author_write_access(
    fake_package_config_job_config_project_db_trigger, has_write_access, result
):
    package_config, job_config, _, _ = fake_package_config_job_config_project_db_trigger

    actor = "maja"
    project_url = "just an url"
    checker = HasAuthorWriteAccess(
        package_config,
        job_config,
        {
            "event_type": PullRequestCommentGithubEvent.__name__,
            "actor": actor,
            "project_url": project_url,
        },
    )

    flexmock(ServiceConfig).should_receive("get_project").with_args(
        url=project_url
    ).and_return(
        flexmock(repo="repo", namespace="ns")
        .should_receive("has_write_access")
        .with_args(user=actor)
        .and_return(has_write_access)
        .mock()
    )

    if not has_write_access:
        flexmock(checker).should_receive("report_pre_check_failure").once()

    assert checker.pre_check() == result


def test_koji_branch_merge_queue():
    """
    Check that specifying regex for GitHub merge queue temporary branch where the
    CI must be green passes the check.
    """
    package_config = flexmock(jobs=[])
    job_config = flexmock(
        type=JobType.upstream_koji_build,
        scratch=True,
        trigger=JobConfigTriggerType.commit,
        targets={"fedora-37"},
        branch="gh-readonly-queue/.*",
    )

    event = construct_dict(
        event=PushGitHubEvent.__name__,
        git_ref="gh-readonly-queue/main/pr-767-0203dd99c3d003cbfd912cec946cc5b46f695b10",
    )

    git_project = flexmock(
        namespace="packit",
        repo="ogr",
    )
    git_project.should_receive("can_merge_pr").and_return(True)
    flexmock(ConfigFromEventMixin).should_receive("project").and_return(git_project)

    db_project_event = flexmock(
        job_config_trigger_type=JobConfigTriggerType.commit,
        name="gh-readonly-queue/main/pr-767-0203dd99c3d003cbfd912cec946cc5b46f695b10",
    )
    flexmock(EventData).should_receive("db_project_event").and_return(db_project_event)

    checker = IsJobConfigTriggerMatchingKoji(package_config, job_config, event)

    assert checker.pre_check()


@pytest.mark.parametrize(
    "comment, result",
    (
        pytest.param(
            "/packit-dev test --identifier my-id-1",
            True,
            id="Matching identifier specified",
        ),
        pytest.param(
            "/packit-dev test",
            True,
            id="No identifier specified",
        ),
        pytest.param(
            "/packit-dev test --identifier my-id-2",
            False,
            id="Non-matching identifier specified",
        ),
    ),
)
def test_tf_comment_identifier(comment, result):
    """
    Check that Testing Farm checker for comment attributes works properly.
    """
    package_config = flexmock(jobs=[])
    job_config = flexmock(
        type=JobType.tests,
        trigger=JobConfigTriggerType.pull_request,
        targets={"fedora-37"},
        skip_build=True,
        manual_trigger=True,
        packages={"package": CommonPackageConfig()},
        identifier="my-id-1",
    )

    event = {
        "event_type": PullRequestCommentGithubEvent.__name__,
        "comment": comment,
    }

    git_project = flexmock(
        namespace="packit",
        repo="ogr",
    )
    flexmock(ConfigFromEventMixin).should_receive("project").and_return(git_project)

    db_project_event = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        pr_id=1,
    )
    flexmock(EventData).should_receive("db_project_event").and_return(db_project_event)

    checker = IsIdentifierFromCommentMatching(
        package_config=package_config, job_config=job_config, event=event
    )

    assert checker.pre_check() == result


@pytest.mark.parametrize(
    "comment, result",
    (
        pytest.param(
            "/packit-dev test --labels label1,label2",
            True,
            id="Matching label specified",
        ),
        pytest.param(
            "/packit-dev test",
            True,
            id="No labels specified",
        ),
        pytest.param(
            "/packit-dev test --labels random-label1,random-label2",
            False,
            id="Non-matching label specified",
        ),
    ),
)
def test_tf_comment_labels(comment, result):
    """
    Check that Testing Farm checker for comment attributes works properly.
    """
    package_config = flexmock(jobs=[])
    job_config = flexmock(
        type=JobType.tests,
        trigger=JobConfigTriggerType.pull_request,
        targets={"fedora-37"},
        skip_build=True,
        manual_trigger=True,
        packages={"package": CommonPackageConfig()},
        identifier="my-id-1",
        labels=["label1", "label3"],
    )

    event = {
        "event_type": PullRequestCommentGithubEvent.__name__,
        "comment": comment,
    }

    git_project = flexmock(
        namespace="packit",
        repo="ogr",
    )
    flexmock(ConfigFromEventMixin).should_receive("project").and_return(git_project)

    db_project_event = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        pr_id=1,
    )
    flexmock(EventData).should_receive("db_project_event").and_return(db_project_event)

    checker = IsLabelFromCommentMatching(
        package_config=package_config, job_config=job_config, event=event
    )

    assert checker.pre_check() == result
