# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import pytest
from flexmock import flexmock
from ogr import PagureService
from ogr.abstract import AccessLevel, PRStatus
from ogr.services.github import GithubProject
from ogr.services.pagure import PagureProject
from packit.actions import ActionName
from packit.config import (
    CommonPackageConfig,
    JobConfig,
    JobConfigTriggerType,
    JobConfigView,
    JobType,
    PackageConfig,
)
from packit.config.commands import TestCommandConfig
from packit.config.requirements import LabelRequirementsConfig, RequirementsConfig
from packit.copr_helper import CoprHelper

from packit_service.config import ServiceConfig
from packit_service.events import (
    anitya,
    copr,
    github,
    gitlab,
    pagure,
)
from packit_service.events.event_data import EventData
from packit_service.models import CoprBuildTargetModel
from packit_service.worker.checker.bodhi import IsKojiBuildOwnerMatchingConfiguration
from packit_service.worker.checker.copr import (
    IsJobConfigTriggerMatching as IsJobConfigTriggerMatchingCopr,
)
from packit_service.worker.checker.copr import (
    IsPackageMatchingJobView,
)
from packit_service.worker.checker.distgit import (
    IsUpstreamTagMatchingConfig,
    LabelsOnDistgitPR,
)
from packit_service.worker.checker.helper import DistgitAccountsChecker
from packit_service.worker.checker.koji import (
    IsJobConfigTriggerMatching as IsJobConfigTriggerMatchingKoji,
)
from packit_service.worker.checker.koji import (
    PermissionOnKoji,
)
from packit_service.worker.checker.run_condition import IsRunConditionSatisfied
from packit_service.worker.checker.testing_farm import (
    IsIdentifierFromCommentMatching,
    IsLabelFromCommentMatching,
)
from packit_service.worker.checker.testing_farm import (
    IsJobConfigTriggerMatching as IsJobConfigTriggerMatchingTF,
)
from packit_service.worker.checker.vm_image import (
    HasAuthorWriteAccess,
    IsCoprBuildForChrootOk,
)
from packit_service.worker.helpers.build.koji_build import KojiBuildJobHelper
from packit_service.worker.mixin import ConfigFromEventMixin
from packit_service.worker.parser import Parser


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
            construct_dict(event=gitlab.mr.Action.event_type(), action="closed"),
            True,
            True,
            JobConfigTriggerType.pull_request,
            id="closed MRs are ignored",
        ),
        pytest.param(
            False,
            construct_dict(event=github.pr.Action.event_type()),
            True,
            False,
            JobConfigTriggerType.pull_request,
            id="Permissions on GitHub",
        ),
        pytest.param(
            False,
            construct_dict(event=gitlab.mr.Action.event_type()),
            True,
            False,
            JobConfigTriggerType.pull_request,
            id="Permissions on GitLab",
        ),
        pytest.param(
            False,
            construct_dict(event=gitlab.mr.Action.event_type()),
            False,
            True,
            JobConfigTriggerType.pull_request,
            id="Non-scratch builds are prohibited",
        ),
        pytest.param(
            True,
            construct_dict(event=github.pr.Action.event_type()),
            True,
            True,
            JobConfigTriggerType.pull_request,
            id="PR from GitHub shall pass",
        ),
        pytest.param(
            True,
            construct_dict(event=gitlab.mr.Action.event_type()),
            True,
            True,
            JobConfigTriggerType.pull_request,
            id="MR from GitLab shall pass",
        ),
    ),
)
def test_koji_permissions(success, event, is_scratch, can_merge_pr, trigger):
    event["pr_id"] = 1
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

    db_project_object = flexmock(
        job_config_trigger_type=trigger,
        name=event["git_ref"],
        pr_id=1,
    )
    flexmock(EventData).should_receive("db_project_event").and_return(
        flexmock().should_receive("get_project_event_object").and_return(db_project_object).mock(),
    )

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
            construct_dict(event=github.push.Commit.event_type()),
            JobConfigTriggerType.commit,
            id="GitHub push to non-configured branch is ignored",
        ),
        pytest.param(
            False,
            construct_dict(event=gitlab.push.Commit.event_type()),
            JobConfigTriggerType.commit,
            id="GitLab push to non-configured branch is ignored",
        ),
        pytest.param(
            False,
            construct_dict(event=pagure.push.Commit.event_type()),
            JobConfigTriggerType.commit,
            id="Pagure push to non-configured branch is ignored",
        ),
        pytest.param(
            True,
            construct_dict(event=pagure.push.Commit.event_type(), git_ref="release"),
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
        use_internal_tf=False,
    )

    git_project = flexmock(
        namespace="packit",
        repo="ogr",
        default_branch="main",
    )
    flexmock(ConfigFromEventMixin).should_receive("project").and_return(git_project)

    db_project_object = flexmock(job_config_trigger_type=trigger, name=event["git_ref"])
    flexmock(EventData).should_receive("db_project_event").and_return(
        flexmock().should_receive("get_project_event_object").and_return(db_project_object).mock(),
    )

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
            construct_dict(event=github.pr.Action.event_type()),
            JobConfigTriggerType.pull_request,
            id="GitHub PR target branch matches",
        ),
        pytest.param(
            "the-other-branch",
            False,
            construct_dict(event=github.pr.Action.event_type()),
            JobConfigTriggerType.pull_request,
            id="GitHub PR target branch does not match",
        ),
        pytest.param(
            "the-branch",
            True,
            construct_dict(event=gitlab.mr.Action.event_type()),
            JobConfigTriggerType.pull_request,
            id="GitLab PR target branch matches",
        ),
        pytest.param(
            "the-other-branch",
            False,
            construct_dict(event=gitlab.mr.Action.event_type()),
            JobConfigTriggerType.pull_request,
            id="GitLab PR target branch does not match",
        ),
    ),
)
def test_pr_event_checker(configured_branch, success, event, trigger, checker_kls):
    event["pr_id"] = 1
    package_config = flexmock(jobs=[])
    job_config = flexmock(
        type=JobType.upstream_koji_build,
        trigger=trigger,
        targets={"fedora-37"},
        branch=configured_branch,
        use_internal_tf=False,
    )

    git_project = flexmock(
        namespace="packit",
        repo="ogr",
        get_pr=lambda pr_id: flexmock(target_branch="the-branch"),
    )
    flexmock(ConfigFromEventMixin).should_receive("project").and_return(git_project)

    db_project_object = flexmock(job_config_trigger_type=trigger, pr_id=1)
    flexmock(EventData).should_receive("db_project_event").and_return(
        flexmock().should_receive("get_project_event_object").and_return(db_project_object).mock(),
    )

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
            "No successful Copr build found for project packit/packit-stg-packit-hello-world-None, "
            "commit 1 and chroot (target) fedora-36-x86_64",
            id="No copr build found for default packit repo, job config without Copr project info",
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
    flexmock(EventData).should_receive("_add_project_object_and_event").and_return()
    flexmock(CoprHelper).should_receive("copr_client").and_return(
        flexmock(config=flexmock().should_receive("get").and_return("packit").mock()),
    )

    checker = IsCoprBuildForChrootOk(
        package_config,
        job_config,
        {"event_type": github.pr.Comment.event_type(), "commit_sha": "1"},
    )
    checker.data._db_project_object = flexmock(id=1)
    checker.data._db_project_event = (
        flexmock()
        .should_receive("get_project_event_object")
        .and_return(flexmock(project_event_model_type="pull_request"))
        .mock()
    )
    checker._project = flexmock(
        service=flexmock(instance_url="packit-stg"),
        namespace="packit",
        repo="hello-world",
    )

    if error_msg:
        flexmock(checker).should_receive("report_pre_check_failure").with_args(
            error_msg,
        ).once()

    assert checker.pre_check() == success


def test_copr_build_is_package_matching_job_view():
    jobs = [
        JobConfigView(
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={"package-a": CommonPackageConfig()},
            ),
            "package-a",
        ),
    ]

    flexmock(copr.CoprBuild).should_receive("from_event_dict").and_return(
        flexmock(build_id=123),
    )

    checker = IsPackageMatchingJobView(
        flexmock(),
        jobs[0],
        {"pkg": "package"},
    )
    checker._build = (
        flexmock().should_receive("get_package_name").and_return("package-b").once().mock()
    )

    assert not checker.pre_check()


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
    fake_package_config_job_config_project_db_trigger,
    has_write_access,
    result,
):
    package_config, job_config, _, _ = fake_package_config_job_config_project_db_trigger

    actor = "maja"
    project_url = "just an url"
    checker = HasAuthorWriteAccess(
        package_config,
        job_config,
        {
            "event_type": github.pr.Comment.event_type(),
            "actor": actor,
            "project_url": project_url,
        },
    )

    flexmock(ServiceConfig).should_receive("get_project").with_args(
        url=project_url,
    ).and_return(
        flexmock(repo="repo", namespace="ns")
        .should_receive("has_write_access")
        .with_args(user=actor)
        .and_return(has_write_access)
        .mock(),
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
        event=github.push.Commit.event_type(),
        git_ref="gh-readonly-queue/main/pr-767-0203dd99c3d003cbfd912cec946cc5b46f695b10",
    )

    git_project = flexmock(
        namespace="packit",
        repo="ogr",
    )
    git_project.should_receive("can_merge_pr").and_return(True)
    flexmock(ConfigFromEventMixin).should_receive("project").and_return(git_project)

    db_project_object = flexmock(
        job_config_trigger_type=JobConfigTriggerType.commit,
        name="gh-readonly-queue/main/pr-767-0203dd99c3d003cbfd912cec946cc5b46f695b10",
    )
    flexmock(EventData).should_receive("db_project_event").and_return(
        flexmock().should_receive("get_project_event_object").and_return(db_project_object).mock(),
    )

    checker = IsJobConfigTriggerMatchingKoji(package_config, job_config, event)

    assert checker.pre_check()


@pytest.mark.parametrize(
    "comment, result",
    (
        pytest.param(
            "/packit test --identifier my-id-1",
            True,
            id="Matching identifier specified",
        ),
        pytest.param(
            "/packit test --id my-id-1",
            True,
            id="Matching identifier specified",
        ),
        pytest.param(
            "/packit test -i my-id-1",
            True,
            id="Matching identifier specified",
        ),
        pytest.param(
            "/packit test",
            True,
            id="No identifier specified",
        ),
        pytest.param(
            "/packit test --identifier my-id-2",
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
        use_internal_tf=False,
        packages={"package": CommonPackageConfig()},
        identifier="my-id-1",
        test_command=TestCommandConfig(default_labels=None, default_identifier=None),
    )

    event = {
        "event_type": github.pr.Comment.event_type(),
        "comment": comment,
    }

    git_project = flexmock(
        namespace="packit",
        repo="ogr",
    )
    flexmock(ConfigFromEventMixin).should_receive("project").and_return(git_project)

    db_project_object = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        pr_id=1,
    )
    flexmock(EventData).should_receive("db_project_event").and_return(
        flexmock().should_receive("get_project_event_object").and_return(db_project_object).mock(),
    )

    checker = IsIdentifierFromCommentMatching(
        package_config=package_config,
        job_config=job_config,
        event=event,
    )

    assert checker.pre_check() == result


@pytest.mark.parametrize(
    "comment, default_identifier, job_identifier, result",
    (
        pytest.param(
            "/packit test --identifier my-id2",
            "id1",
            "id1",
            False,
            id="Identifier specified in comment",
        ),
        pytest.param(
            "/packit test",
            None,
            "id1",
            True,
            id="No identifier specified, no default identifier",
        ),
        pytest.param(
            "/packit test",
            "id1",
            "id1",
            True,
            id="No identifier specified, default identifier matching",
        ),
        pytest.param(
            "/packit test",
            "id1",
            "id2",
            False,
            id="No identifier specified, default identifier not matching",
        ),
        pytest.param(
            "/packit test",
            "id1",
            None,
            False,
            id="No identifier specified, default identifier not matching (job without label)",
        ),
    ),
)
def test_tf_comment_default_identifier(
    comment,
    default_identifier,
    job_identifier,
    result,
):
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
        use_internal_tf=False,
        packages={"package": CommonPackageConfig()},
        identifier=job_identifier,
        test_command=TestCommandConfig(
            default_labels=None,
            default_identifier=default_identifier,
        ),
    )

    event = {
        "event_type": github.pr.Comment.event_type(),
        "comment": comment,
    }

    git_project = flexmock(
        namespace="packit",
        repo="ogr",
    )
    flexmock(ConfigFromEventMixin).should_receive("project").and_return(git_project)

    db_project_object = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        pr_id=1,
    )
    flexmock(EventData).should_receive("db_project_event").and_return(
        flexmock().should_receive("get_project_event_object").and_return(db_project_object).mock(),
    )

    checker = IsIdentifierFromCommentMatching(
        package_config=package_config,
        job_config=job_config,
        event=event,
    )
    assert checker.pre_check() == result


@pytest.mark.parametrize(
    "comment, result",
    (
        pytest.param(
            "/packit test --labels label1,label2",
            True,
            id="Matching label specified",
        ),
        pytest.param(
            "/packit test",
            True,
            id="No labels specified",
        ),
        pytest.param(
            "/packit test --labels random-label1,random-label2",
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
        use_internal_tf=False,
        packages={"package": CommonPackageConfig()},
        identifier="my-id-1",
        labels=["label1", "label3"],
        test_command=TestCommandConfig(default_labels=None, default_identifier=None),
    )

    event = {
        "event_type": github.pr.Comment.event_type(),
        "comment": comment,
    }

    git_project = flexmock(
        namespace="packit",
        repo="ogr",
    )
    flexmock(ConfigFromEventMixin).should_receive("project").and_return(git_project)

    db_project_object = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        pr_id=1,
    )
    flexmock(EventData).should_receive("db_project_event").and_return(
        flexmock().should_receive("get_project_event_object").and_return(db_project_object).mock(),
    )

    checker = IsLabelFromCommentMatching(
        package_config=package_config,
        job_config=job_config,
        event=event,
    )

    assert checker.pre_check() == result


@pytest.mark.parametrize(
    "comment, default_labels, job_labels, result",
    (
        pytest.param(
            "/packit test --labels label1,label2",
            ["label3"],
            ["label3"],
            False,
            id="Labels specified in comment",
        ),
        pytest.param(
            "/packit test",
            None,
            ["label1"],
            True,
            id="No labels specified, no default labels",
        ),
        pytest.param(
            "/packit test",
            ["label2"],
            ["label1", "label2"],
            True,
            id="No labels specified, default labels matching",
        ),
        pytest.param(
            "/packit test",
            ["label3"],
            ["label1", "label2"],
            False,
            id="No labels specified, default labels not matching",
        ),
        pytest.param(
            "/packit test",
            ["label3"],
            [],
            False,
            id="No labels specified, default labels not matching (job without label)",
        ),
    ),
)
def test_tf_comment_default_labels(comment, default_labels, job_labels, result):
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
        use_internal_tf=False,
        packages={"package": CommonPackageConfig()},
        identifier="my-id-1",
        labels=job_labels,
        test_command=TestCommandConfig(
            default_labels=default_labels,
            default_identifier=None,
        ),
    )

    event = {
        "event_type": github.pr.Comment.event_type(),
        "comment": comment,
    }

    git_project = flexmock(
        namespace="packit",
        repo="ogr",
    )
    flexmock(ConfigFromEventMixin).should_receive("project").and_return(git_project)

    db_project_object = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        pr_id=1,
    )
    flexmock(EventData).should_receive("db_project_event").and_return(
        flexmock().should_receive("get_project_event_object").and_return(db_project_object).mock(),
    )

    checker = IsLabelFromCommentMatching(
        package_config=package_config,
        job_config=job_config,
        event=event,
    )

    assert checker.pre_check() == result


# Test covers the regression from #2155 when labels are specified in the comment
# for retriggering TF and either:
# * there are no labels specified in the job config, or
# * there are multiple test jobs definition from which some don't have any
#   labels set
@pytest.mark.parametrize(
    "comment, result",
    (
        pytest.param(
            "/packit test",
            True,
            id="No labels specified, none in config: should pass",
        ),
        pytest.param(
            "/packit test --labels should_fail,should_fail_hard",
            False,
            id="Labels specified, none in config: should fail",
        ),
    ),
)
def test_tf_comment_labels_none_in_config(comment, result):
    package_config = flexmock(jobs=[])
    job_config = flexmock(
        type=JobType.tests,
        trigger=JobConfigTriggerType.pull_request,
        targets={"fedora-37"},
        skip_build=True,
        manual_trigger=True,
        use_internal_tf=False,
        packages={"package": CommonPackageConfig()},
        labels=None,
        identifier="my-id-1",
        test_command=TestCommandConfig(default_labels=None, default_identifier=None),
    )

    event = {
        "event_type": github.pr.Comment.event_type(),
        "comment": comment,
    }

    git_project = flexmock(
        namespace="packit",
        repo="ogr",
    )
    flexmock(ConfigFromEventMixin).should_receive("project").and_return(git_project)

    db_project_object = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        pr_id=1,
    )
    flexmock(EventData).should_receive("db_project_event").and_return(
        flexmock().should_receive("get_project_event_object").and_return(db_project_object).mock(),
    )

    checker = IsLabelFromCommentMatching(
        package_config=package_config,
        job_config=job_config,
        event=event,
    )

    assert checker.pre_check() == result


@pytest.mark.parametrize(
    "upstream_tag_include, upstream_tag_exclude, result",
    (
        pytest.param(
            None,
            None,
            True,
        ),
        pytest.param(
            None,
            r"^.+\.2\..+",
            True,
        ),
        pytest.param(
            None,
            r"^.+\.1\..+",
            False,
        ),
        pytest.param(
            r"^.+\.2\..+",
            None,
            False,
        ),
        pytest.param(
            r"^.+\.1\..+",
            None,
            True,
        ),
        pytest.param(
            r"^.+\.1\..+",
            r"^2\..+",
            False,
        ),
    ),
)
def test_sync_release_matching_tag(upstream_tag_include, upstream_tag_exclude, result):
    package_config = flexmock(jobs=[])
    job_config = flexmock(
        type=JobType.pull_from_upstream,
        trigger=JobConfigTriggerType.release,
        targets={"fedora-37"},
        upstream_tag_include=upstream_tag_include,
        upstream_tag_exclude=upstream_tag_exclude,
    )
    git_project = flexmock(
        namespace="packit",
        repo="ogr",
    )
    flexmock(ConfigFromEventMixin).should_receive("project").and_return(git_project)

    db_project_event = flexmock(
        job_config_trigger_type=JobConfigTriggerType.release,
        pr_id=1,
    )
    flexmock(EventData).should_receive("db_project_event").and_return(db_project_event)

    checker = IsUpstreamTagMatchingConfig(
        package_config=package_config,
        job_config=job_config,
        event={"tag_name": "2.1.1"},
    )

    assert checker.pre_check() == result


@pytest.mark.parametrize(
    "account, allowed_pr_authors, should_pass",
    (
        ("direct-account", ["all_admins", "direct-account"], True),
        ("admin-1", ["all_admins"], True),
        ("admin-2", ["all_admins"], False),
        ("group-account-1", ["all_admins", "@copr"], True),
        ("group-account-2", ["all_admins", "@copr"], False),
    ),
)
def test_koji_check_allowed_accounts(
    distgit_push_event,
    account,
    allowed_pr_authors,
    should_pass,
):
    flexmock(PagureProject).should_receive("get_users_with_given_access").with_args(
        [AccessLevel.maintain],
    ).and_return({"admin-1"})
    flexmock(PagureService).should_receive("get_group").with_args("copr").and_return(
        flexmock(members={"group-account-1"}),
    )

    assert (
        DistgitAccountsChecker(
            distgit_push_event.project,
            allowed_pr_authors,
            account,
        ).check_allowed_accounts()
        == should_pass
    )


@pytest.mark.parametrize(
    "pr_labels,labels_present,labels_absent,should_pass",
    (
        ([], [], [], True),
        ([flexmock(name="allowed-1")], [], ["skip-ci"], True),
        ([flexmock(name="allowed-1")], ["allowed-1"], ["skip-ci"], True),
        ([flexmock(name="allowed-1")], ["allowed-1"], ["skip-ci"], True),
        (
            [flexmock(name="allowed-1"), flexmock(name="skip-ci")],
            ["allowed-1"],
            ["skip-ci"],
            False,
        ),
    ),
)
def test_labels_on_distgit_pr(
    distgit_push_event,
    pr_labels,
    labels_present,
    labels_absent,
    should_pass,
):
    jobs = [
        JobConfig(
            type=JobType.koji_build,
            trigger=JobConfigTriggerType.commit,
            packages={
                "package": CommonPackageConfig(
                    dist_git_branches=["f36"],
                    require=RequirementsConfig(
                        LabelRequirementsConfig(
                            absent=labels_absent,
                            present=labels_present,
                        ),
                    ),
                ),
            },
        ),
    ]

    package_config = PackageConfig(
        jobs=jobs,
        packages={"package": CommonPackageConfig()},
    )
    job_config = jobs[0]

    flexmock(PagureProject).should_receive("get_pr").and_return(
        flexmock(
            id=5,
            head_commit="ad0c308af91da45cf40b253cd82f07f63ea9cbbf",
            status=PRStatus.open,
            labels=pr_labels,
            target_branch="f36",
        ),
    )

    checker = LabelsOnDistgitPR(
        package_config,
        job_config,
        distgit_push_event.get_dict(),
    )
    assert checker.pre_check() == should_pass


@pytest.mark.parametrize(
    "allowed_builders,owner,should_pass",
    (
        (["packit"], "packit", True),
        (["packit"], "another-account", False),
        (["packit", "another-account"], "another-account", True),
        (["packit", "another-account"], "packit", True),
    ),
)
def test_allowed_builders_for_bodhi(
    koji_build_completed_event,
    allowed_builders,
    owner,
    should_pass,
):
    koji_build_completed_event.owner = owner
    jobs = [
        JobConfig(
            type=JobType.bodhi_update,
            trigger=JobConfigTriggerType.commit,
            packages={
                "package": CommonPackageConfig(
                    dist_git_branches=["f36"],
                    allowed_builders=allowed_builders,
                ),
            },
        ),
    ]

    package_config = PackageConfig(
        jobs=jobs,
        packages={"package": CommonPackageConfig()},
    )
    job_config = jobs[0]

    checker = IsKojiBuildOwnerMatchingConfiguration(
        package_config,
        job_config,
        koji_build_completed_event.get_dict(),
    )
    assert checker.pre_check() == should_pass


def test_allowed_builders_for_bodhi_alias(
    koji_build_completed_event,
):
    koji_build_completed_event.owner = "owner"
    jobs = [
        JobConfig(
            type=JobType.bodhi_update,
            trigger=JobConfigTriggerType.commit,
            packages={
                "package": CommonPackageConfig(
                    dist_git_branches=["f36"],
                    allowed_builders=["all_admins"],
                ),
            },
        ),
    ]

    flexmock(PagureProject).should_receive("get_users_with_given_access").and_return(
        ["owner"],
    )

    package_config = PackageConfig(
        jobs=jobs,
        packages={"package": CommonPackageConfig()},
    )
    job_config = jobs[0]

    checker = IsKojiBuildOwnerMatchingConfiguration(
        package_config,
        job_config,
        koji_build_completed_event.get_dict(),
    )
    assert checker.pre_check()


@pytest.mark.parametrize(
    "clone_repos",
    (False, True),
)
@pytest.mark.parametrize(
    "command,should_pass",
    (
        (None, True),
        ("true", True),
        ("false", False),
    ),
)
@pytest.mark.parametrize(
    "job_type,job_trigger,event_type",
    (
        (
            JobType.pull_from_upstream,
            JobConfigTriggerType.release,
            "pull_from_upstream_trigger_anitya",
        ),
        (
            JobType.pull_from_upstream,
            JobConfigTriggerType.release,
            "pull_from_upstream_retrigger_pr_comment",
        ),
        (JobType.koji_build, JobConfigTriggerType.commit, "koji_build_trigger_push"),
        (JobType.koji_build, JobConfigTriggerType.commit, "koji_build_retrigger_pr_comment"),
        #        (
        #            JobType.koji_build,
        #            JobConfigTriggerType.commit,
        #            "koji_build_retrigger_issue_comment",
        #        ),
        (
            JobType.koji_build,
            JobConfigTriggerType.koji_build,
            "koji_build_trigger_koji_build_tagging",
        ),
        (
            JobType.bodhi_update,
            JobConfigTriggerType.commit,
            "bodhi_update_trigger_koji_build_success",
        ),
        (JobType.bodhi_update, JobConfigTriggerType.commit, "bodhi_update_retrigger_pr_comment"),
        #        (
        #            JobType.bodhi_update,
        #            JobConfigTriggerType.commit,
        #            "bodhi_update_retrigger_issue_comment",
        #        ),
        (
            JobType.bodhi_update,
            JobConfigTriggerType.koji_build,
            "bodhi_update_trigger_koji_build_tagging",
        ),
        (JobType.copr_build, JobConfigTriggerType.pull_request, "copr_build_trigger_pr_creation"),
        (JobType.copr_build, JobConfigTriggerType.pull_request, "copr_build_trigger_pr_push"),
    ),
)
def test_run_condition(
    job_type,
    job_trigger,
    event_type,
    clone_repos,
    command,
    should_pass,
    new_hotness_update,
    distgit_push_event,
    pagure_pr_comment_added,
    koji_build_tagged,
    koji_build_completed_event,
    github_pr_event,
    github_push_event,
):
    jobs = [
        JobConfig(
            type=job_type,
            trigger=job_trigger,
            packages={
                "package": CommonPackageConfig(
                    downstream_package_name="package",
                    specfile_path="package.spec",
                    clone_repos_before_run_condition=clone_repos,
                ),
            },
        ),
    ]

    package_config = PackageConfig(
        jobs=jobs,
        packages={"package": CommonPackageConfig()},
    )
    job_config = jobs[0]

    if command is not None:
        job_config.actions = {
            ActionName.run_condition: [command],
        }

    if event_type == "pull_from_upstream_trigger_anitya":
        flexmock(anitya.update.NewHotness).should_receive("_add_release_and_event").and_return()
        event = Parser.parse_new_hotness_update_event(new_hotness_update)
        git_ref = "7.0.3"
    elif event_type == "koji_build_trigger_push":
        event = distgit_push_event
        git_ref = "ad0c308af91da45cf40b253cd82f07f63ea9cbbf"
    elif event_type in (
        "pull_from_upstream_retrigger_pr_comment",
        "koji_build_retrigger_pr_comment",
        "bodhi_update_retrigger_pr_comment",
    ):
        event = Parser.parse_pagure_pull_request_comment_event(pagure_pr_comment_added)
        git_ref = "beaf90bcecc51968a46663f8d6f092bfdc92e682"
    #    elif event_type in (
    #        "koji_build_retrigger_issue_comment",
    #        "bodhi_update_retrigger_issue_comment",
    #    ):
    elif event_type in (
        "koji_build_trigger_koji_build_tagging",
        "bodhi_update_trigger_koji_build_tagging",
    ):
        event = Parser.parse_koji_build_tag_event(koji_build_tagged)
        git_ref = "HEAD"
    elif event_type == "bodhi_update_trigger_koji_build_success":
        event = koji_build_completed_event
        git_ref = "e029dd5250dde9a37a2cdddb6d822d973b09e5da"
    elif event_type == "copr_build_trigger_pr_creation":
        event = github_pr_event
        git_ref = "528b803be6f93e19ca4130bf4976f2800a3004c4"
    elif event_type == "copr_build_trigger_pr_push":
        event = github_push_event
        git_ref = "04885ff850b0fa0e206cd09db73565703d48f99b"

    checker = IsRunConditionSatisfied(
        package_config,
        job_config,
        event.get_dict(),
    )

    flexmock(PagureProject).should_receive("get_pr").with_args(36).and_return(
        flexmock(source_project=checker.project)
    )
    flexmock(GithubProject).should_receive("get_pr").with_args(342).and_return(
        flexmock(source_project=checker.project)
    )

    if clone_repos:
        checker.packit_api._up = flexmock(
            local_project=flexmock(working_dir="cloned-distgit-repo")
            .should_receive("checkout_release")
            .with_args(git_ref)
            .mock(),
            actions_handler=checker.actions_handler,
            get_current_version=lambda: "1.2.3",
            clean_working_dir=lambda: None,
        )
        checker.packit_api._dg = flexmock(
            local_project=flexmock(working_dir="cloned-distgit-repo")
            .should_receive("checkout_release")
            .with_args(git_ref)
            .mock(),
            specfile=flexmock(expanded_version="1.2.3"),
        )
    else:
        spec_file_content = (
            "Name: package\nVersion: 1.2.3\n"
            "Release: 1\nSummary: package\n"
            "License: MIT\n%description\npackage\n"
        )
        flexmock(PagureProject).should_receive("get_file_content").with_args(
            path="package.spec", ref=git_ref
        ).and_return(spec_file_content)
        flexmock(GithubProject).should_receive("get_file_content").with_args(
            path="package.spec", ref=git_ref
        ).and_return(spec_file_content)

    assert checker.pre_check() == should_pass
