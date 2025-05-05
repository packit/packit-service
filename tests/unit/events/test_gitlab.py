# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json

import pytest
from flexmock import flexmock
from ogr.services.gitlab import GitlabProject

from packit_service.events import abstract
from packit_service.events.gitlab import (
    enums,
    issue,
    mr,
    pipeline,
    push,
    release,
)
from packit_service.models import PullRequestModel
from packit_service.package_config_getter import PackageConfigGetter
from packit_service.worker.parser import Parser
from tests.spellbook import DATA_DIR

PATH_TO_GITLAB_WEBHOOKS = DATA_DIR / "webhooks" / "gitlab"


@pytest.fixture()
def merge_request():
    with open(PATH_TO_GITLAB_WEBHOOKS / "mr_event.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def merge_request_update():
    with open(PATH_TO_GITLAB_WEBHOOKS / "mr_update_event.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def merge_request_closed():
    with open(PATH_TO_GITLAB_WEBHOOKS / "mr_closed.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def gitlab_push():
    with open(
        PATH_TO_GITLAB_WEBHOOKS / "push_with_one_commit.json",
    ) as outfile:
        return json.load(outfile)


@pytest.fixture()
def gitlab_push_many_commits():
    with open(
        PATH_TO_GITLAB_WEBHOOKS / "push_with_many_commits.json",
    ) as outfile:
        return json.load(outfile)


@pytest.fixture()
def gitlab_issue_comment():
    with open(PATH_TO_GITLAB_WEBHOOKS / "issue_comment.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def gitlab_mr_comment():
    with open(PATH_TO_GITLAB_WEBHOOKS / "mr_comment.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def gitlab_mr_pipeline():
    with open(PATH_TO_GITLAB_WEBHOOKS / "mr_pipeline.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def gitlab_tag_push():
    with open(PATH_TO_GITLAB_WEBHOOKS / "tag_push.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def gitlab_release():
    with open(PATH_TO_GITLAB_WEBHOOKS / "release.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def gitlab_commit_comment():
    with open(PATH_TO_GITLAB_WEBHOOKS / "commit_comment.json") as outfile:
        return json.load(outfile)


def test_parse_gitlab_release(gitlab_release):
    event_object = Parser.parse_event(gitlab_release)

    flexmock(GitlabProject).should_receive("get_sha_from_tag").and_return("123456")

    assert isinstance(event_object, release.Release)
    assert event_object.repo_namespace == "fedora/src"
    assert event_object.repo_name == "python-teamcity-messages"
    assert event_object.tag_name == "v1.32"
    assert event_object.project_url == "https://gitlab.com/fedora/src/python-teamcity-messages"
    assert event_object.commit_sha == "6147b3de219ecdda30ba727cf74a0414ca1e618a"
    assert event_object.get_dict()


def test_parse_gitlab_tag_push(gitlab_tag_push):
    event_object = Parser.parse_event(gitlab_tag_push)

    assert isinstance(event_object, push.Tag)
    assert event_object.repo_namespace == "fedora/src"
    assert event_object.repo_name == "python-teamcity-messages"
    assert event_object.commit_sha == "6147b3de219ecdda30ba727cf74a0414ca1e618a"
    assert event_object.actor == "mmassari1"
    assert event_object.git_ref == "v1.32"
    assert event_object.title == "1.32"
    assert event_object.message == "1.32\n"
    assert event_object.project_url == "https://gitlab.com/fedora/src/python-teamcity-messages"
    assert event_object.get_dict()


def test_parse_mr(merge_request):
    event_object = Parser.parse_event(merge_request)

    assert isinstance(event_object, mr.Action)
    assert event_object.action == enums.Action.opened
    assert event_object.object_id == 58759529
    assert event_object.identifier == "1"
    assert event_object.source_repo_namespace == "testing/packit"
    assert event_object.source_repo_name == "hello-there"
    assert event_object.source_repo_branch == "test"
    assert event_object.commit_sha == "1f6a716aa7a618a9ffe56970d77177d99d100022"
    assert event_object.target_repo_namespace == "testing/packit"
    assert event_object.target_repo_name == "hello-there"
    assert event_object.target_repo_branch == "master"
    assert event_object.project_url == "https://gitlab.com/testing/packit/hello-there"

    assert isinstance(event_object.project, GitlabProject)
    assert event_object.project.full_repo_name == "testing/packit/hello-there"
    assert event_object.base_project.full_repo_name == "testing/packit/hello-there"

    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=event_object.base_project,
        project=event_object.project,
        pr_id=1,
        reference="1f6a716aa7a618a9ffe56970d77177d99d100022",
        fail_when_missing=False,
    ).and_return(
        flexmock(get_package_config_views=dict),
    ).once()
    assert event_object.packages_config


def test_parse_mr_action(merge_request_update):
    event_object = Parser.parse_event(merge_request_update)
    assert isinstance(event_object, mr.Action)
    assert event_object.action == enums.Action.update
    assert event_object.commit_sha == "45e272a57335e4e308f3176df6e9226a9e7805a9"
    assert event_object.commit_sha_before == "94ccba9f986629e24b432c11d9c7fd20bb2ea51d"
    assert event_object.identifier == "2"

    assert isinstance(event_object.project, GitlabProject)
    assert event_object.project.full_repo_name == "testing/packit/hello-there"
    assert event_object.base_project.full_repo_name == "testing/packit/hello-there"

    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=event_object.base_project,
        project=event_object.project,
        pr_id=2,
        reference="45e272a57335e4e308f3176df6e9226a9e7805a9",
        fail_when_missing=False,
    ).and_return(
        flexmock(get_package_config_views=dict),
    ).once()
    assert event_object.packages_config


def test_parse_mr_closed(merge_request_closed):
    event_object = Parser.parse_event(merge_request_closed)
    assert isinstance(event_object, mr.Action)
    assert event_object.action == enums.Action.closed


def test_parse_mr_comment(gitlab_mr_comment):
    event_object = Parser.parse_event(gitlab_mr_comment)

    assert isinstance(event_object, mr.Comment)
    assert event_object.action == enums.Action.opened
    assert event_object.pr_id == 2
    assert event_object.source_repo_namespace == "testing/packit"
    assert event_object.source_repo_name == "hello-there"
    assert event_object.target_repo_namespace == "testing/packit"
    assert event_object.target_repo_name == "hello-there"
    assert event_object.project_url == "https://gitlab.com/testing/packit/hello-there"
    assert event_object.actor == "shreyaspapi"
    assert event_object.comment == "must be reopened"
    assert event_object.commit_sha == "45e272a57335e4e308f3176df6e9226a9e7805a9"

    assert isinstance(event_object.project, GitlabProject)
    assert event_object.project.full_repo_name == "testing/packit/hello-there"

    assert isinstance(event_object.project, GitlabProject)
    assert event_object.base_project.full_repo_name == "testing/packit/hello-there"

    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=event_object.base_project,
        project=event_object.project,
        pr_id=2,
        reference="45e272a57335e4e308f3176df6e9226a9e7805a9",
        fail_when_missing=False,
    ).and_return(
        flexmock(get_package_config_views=dict),
    ).once()
    assert event_object.packages_config


def test_parse_commit_comment(gitlab_commit_comment):
    event_object = Parser.parse_event(gitlab_commit_comment)

    assert isinstance(event_object, abstract.comment.Commit)
    assert event_object.repo_namespace == "gitlabhq"
    assert event_object.repo_name == "gitlab-test"
    assert event_object.project_url == "http://gitlab.com/gitlabhq/gitlab-test"
    assert event_object.actor == "root"
    assert event_object.comment == "test commit comment"
    assert event_object.commit_sha == "cfe32cf61b73a0d5e9f13e774abde7ff789b1660"

    assert isinstance(event_object.project, GitlabProject)
    assert event_object.project.full_repo_name == "gitlabhq/gitlab-test"

    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=None,
        project=event_object.project,
        reference="cfe32cf61b73a0d5e9f13e774abde7ff789b1660",
        pr_id=None,
        fail_when_missing=False,
    ).and_return(
        flexmock(get_package_config_views=dict),
    ).once()
    assert event_object.packages_config


def test_parse_gitlab_issue_comment(gitlab_issue_comment):
    event_object = Parser.parse_event(gitlab_issue_comment)

    assert isinstance(event_object, issue.Comment)
    assert event_object.action == enums.Action.opened
    assert event_object.issue_id == 1
    assert event_object.repo_namespace == "testing/packit"
    assert event_object.repo_name == "hello-there"

    assert event_object.project_url == "https://gitlab.com/testing/packit/hello-there"
    assert event_object.actor == "shreyaspapi"
    assert event_object.comment == "testing comment"

    assert isinstance(event_object.project, GitlabProject)
    assert event_object.project.full_repo_name == "testing/packit/hello-there"

    flexmock(event_object.project).should_receive("get_releases").and_return(
        [flexmock(tag_name="0.5.0")],
    )
    flexmock(event_object.project, get_sha_from_tag=lambda tag_name: "123456")
    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=event_object.base_project,
        project=event_object.project,
        pr_id=None,
        reference="123456",
        fail_when_missing=False,
    ).and_return(
        flexmock(get_package_config_views=dict),
    ).once()
    assert event_object.packages_config


def test_parse_gitlab_push(gitlab_push):
    event_object = Parser.parse_event(gitlab_push)

    assert isinstance(event_object, push.Commit)
    assert event_object.repo_namespace == "testing/packit"
    assert event_object.repo_name == "hello-there"
    assert event_object.commit_sha == "cb2859505e101785097e082529dced35bbee0c8f"
    assert event_object.commit_sha_before == "0e27f070efa4bef2a7c0168f07a0ac36ef90d8cb"
    assert event_object.project_url == "https://gitlab.com/testing/packit/hello-there"
    assert event_object.git_ref == "test2"

    assert isinstance(event_object.project, GitlabProject)
    assert event_object.project.full_repo_name == "testing/packit/hello-there"
    assert not event_object.base_project

    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=event_object.base_project,
        project=event_object.project,
        pr_id=None,
        reference="cb2859505e101785097e082529dced35bbee0c8f",
        fail_when_missing=False,
    ).and_return(
        flexmock(get_package_config_views=dict),
    ).once()
    assert event_object.packages_config


def test_parse_gitlab_push_many_commits(gitlab_push_many_commits):
    event_object = Parser.parse_event(gitlab_push_many_commits)

    assert isinstance(event_object, push.Commit)
    assert event_object.repo_namespace == "packit-service/rpms"
    assert event_object.repo_name == "open-vm-tools"
    assert event_object.commit_sha == "15af92227f9e965b392e85ba2f08a41a5aeb278a"
    assert event_object.commit_sha_before == "8c349949521e5c3fcd5c1811d1acbc5a752b385e"
    assert event_object.project_url == "https://gitlab.com/packit-service/rpms/open-vm-tools"
    assert event_object.git_ref == "c9s"

    assert isinstance(event_object.project, GitlabProject)
    assert event_object.project.full_repo_name == "packit-service/rpms/open-vm-tools"
    assert not event_object.base_project

    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=event_object.base_project,
        project=event_object.project,
        pr_id=None,
        reference="15af92227f9e965b392e85ba2f08a41a5aeb278a",
        fail_when_missing=False,
    ).and_return(
        flexmock(get_package_config_views=dict),
    ).once()
    assert event_object.packages_config


def test_parse_gitlab_pipeline(gitlab_mr_pipeline):
    event_object = Parser.parse_event(gitlab_mr_pipeline)

    assert isinstance(event_object, pipeline.Pipeline)
    assert event_object.project_url == "https://gitlab.com/redhat/centos-stream/rpms/luksmeta"
    assert event_object.project_name == "luksmeta"
    assert event_object.pipeline_id == 384095584
    assert event_object.git_ref == "9-c9s-src-5"
    assert event_object.status == "failed"
    assert event_object.detailed_status == "failed"
    assert event_object.source == "merge_request_event"
    assert event_object.commit_sha == "ee58e259da263ecb4c1f0129be7aef8cfd4dedd6"
    assert (
        event_object.merge_request_url
        == "https://gitlab.com/redhat/centos-stream/rpms/luksmeta/-/merge_requests/4"
    )

    flexmock(PullRequestModel).should_receive("get_or_create").and_return(flexmock())
    # assert event_object.db_project_object
    assert isinstance(event_object.project, GitlabProject)
    assert event_object.project.full_repo_name == "redhat/centos-stream/rpms/luksmeta"
    assert not event_object.base_project

    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=event_object.base_project,
        project=event_object.project,
        pr_id=None,
        reference="ee58e259da263ecb4c1f0129be7aef8cfd4dedd6",
        fail_when_missing=False,
    ).and_return(
        flexmock(get_package_config_views=dict),
    ).once()
    assert event_object.packages_config
