# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json

import pytest
from flexmock import flexmock
from ogr.services.pagure import PagureProject

from packit_service.events.enums import PullRequestAction, PullRequestCommentAction
from packit_service.events.pagure import (
    pr,
    push,
)
from packit_service.package_config_getter import PackageConfigGetter
from packit_service.worker.parser import Parser
from tests.spellbook import DATA_DIR


@pytest.fixture()
def pagure_pr_flag_updated():
    with open(DATA_DIR / "fedmsg" / "pagure_pr_flag_updated.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def pagure_pr_new():
    with open(DATA_DIR / "fedmsg" / "pagure_pr_new.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def pagure_pr_updated():
    with open(DATA_DIR / "fedmsg" / "pagure_pr_updated.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def pagure_pr_rebased():
    with open(DATA_DIR / "fedmsg" / "pagure_pr_rebased.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def distgit_commit():
    with open(DATA_DIR / "fedmsg" / "distgit_commit.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def pagure_pr_new_no_fork():
    with open(DATA_DIR / "fedmsg" / "pagure_pr_new_no_fork.json") as outfile:
        return json.load(outfile)


def test_parse_pagure_flag(pagure_pr_flag_updated):
    event_object = Parser.parse_event(pagure_pr_flag_updated)

    assert isinstance(event_object, pr.Flag)
    assert event_object.project_url == "https://src.fedoraproject.org/rpms/packit"
    assert event_object.pr_id == 268

    assert event_object.username == "Zuul"
    assert event_object.comment == "Jobs result is success"
    assert event_object.status == "success"
    assert event_object.date_updated == 1646754511
    assert (
        event_object.url
        == "https://fedora.softwarefactory-project.io/zuul/buildset/66ec2c23c78446afa2fd993"
    )
    assert event_object.commit_sha == "c69960e6f562c90905435fec824fcae952abfad6"
    assert event_object.pr_url == "https://src.fedoraproject.org/rpms/packit/pull-request/268"
    assert event_object.pr_source_branch == "0.47.0-f36-update"
    assert event_object.project_name == "packit"
    assert event_object.project_namespace == "rpms"


def test_parse_pagure_pull_request_comment(pagure_pr_comment_added):
    event_object = Parser.parse_event(pagure_pr_comment_added)

    assert isinstance(event_object, pr.Comment)
    assert event_object.action == PullRequestCommentAction.created
    assert event_object.pr_id == 36
    assert event_object.base_repo_namespace == "rpms"
    assert event_object.base_repo_name == "python-teamcity-messages"
    assert event_object.base_repo_owner == "mmassari"
    assert event_object.base_ref is None
    assert event_object.target_repo == "python-teamcity-messages"
    assert event_object.project_url == "https://src.fedoraproject.org/rpms/python-teamcity-messages"
    assert (
        event_object.source_project_url
        == "https://src.fedoraproject.org/rpms/python-teamcity-messages"
    )
    assert event_object.user_login == "mmassari"
    assert event_object.comment == "/packit koji-build"
    assert event_object.comment_id == 110401
    assert event_object.commit_sha == "beaf90bcecc51968a46663f8d6f092bfdc92e682"

    assert isinstance(event_object.project, PagureProject)
    assert event_object.project.full_repo_name == "rpms/python-teamcity-messages"
    assert isinstance(event_object.base_project, PagureProject)
    assert event_object.base_project.full_repo_name == "rpms/python-teamcity-messages"

    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=None,
        project=event_object.project,
        reference="rawhide",
        pr_id=None,
        fail_when_missing=False,
    ).and_return(
        flexmock(get_package_config_views=dict),
    ).once()
    flexmock(PagureProject).should_receive("get_web_url").and_return(
        "https://src.fedoraproject.org/rpms/python-teamcity-messages",
    )
    assert event_object.packages_config


def test_distgit_pagure_push(distgit_commit):
    event_object = Parser.parse_event(distgit_commit)

    assert isinstance(event_object, push.Commit)
    assert event_object.repo_namespace == "rpms"
    assert event_object.repo_name == "buildah"
    assert event_object.commit_sha == "abcd"
    assert event_object.git_ref == "main"
    assert event_object.project_url == "https://src.fedoraproject.org/rpms/buildah"


def test_parse_pagure_pull_request_new(pagure_pr_new):
    event_object = Parser.parse_event(pagure_pr_new)

    assert isinstance(event_object, pr.Action)
    assert event_object.action == PullRequestAction.opened
    assert event_object.pr_id == 2
    assert event_object.base_repo_namespace == "rpms"
    assert event_object.base_repo_name == "optee_os"
    assert event_object.base_repo_owner == "zbyszek"
    assert event_object.base_ref is None
    assert event_object.target_repo == "optee_os"
    assert event_object.project_url == "https://src.fedoraproject.org/rpms/optee_os"
    assert (
        event_object.source_project_url
        == "https://src.fedoraproject.org/fork/zbyszek/rpms/optee_os"
    )
    assert event_object.commit_sha == "889f07af35d27bbcaf9c535c17a63b974aa42ee3"
    assert event_object.user_login == "zbyszek"
    assert event_object.target_branch == "rawhide"

    assert isinstance(event_object.project, PagureProject)
    assert event_object.project.full_repo_name == "rpms/optee_os"
    assert isinstance(event_object.base_project, PagureProject)
    assert event_object.base_project.full_repo_name == "fork/zbyszek/rpms/optee_os"

    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=event_object.base_project,
        project=event_object.project,
        reference="889f07af35d27bbcaf9c535c17a63b974aa42ee3",
        pr_id=2,
        fail_when_missing=False,
    ).and_return(
        flexmock(get_package_config_views=dict),
    ).once()
    flexmock(PagureProject).should_receive("get_web_url").and_return(
        "https://src.fedoraproject.org/rpms/optee_os",
    )
    assert event_object.packages_config


def test_parse_pagure_pull_request_new_no_fork(pagure_pr_new_no_fork):
    event_object = Parser.parse_event(pagure_pr_new_no_fork)

    assert isinstance(event_object, pr.Action)
    assert event_object.action == PullRequestAction.opened
    assert event_object.pr_id == 150
    assert event_object.base_repo_namespace is None
    assert event_object.base_repo_name == "fedora-kiwi-descriptions"
    assert event_object.base_repo_owner == "ngompa"
    assert event_object.base_ref is None
    assert event_object.target_repo == "fedora-kiwi-descriptions"
    assert event_object.project_url == "https://pagure.io/fedora-kiwi-descriptions"
    assert event_object.source_project_url == "https://pagure.io/fedora-kiwi-descriptions"
    assert event_object.commit_sha == "914e61919e3a3a82c0ef7b6bd3a73f74e2de36e7"
    assert event_object.user_login == "ngompa"
    assert event_object.target_branch == "rawhide"


def test_parse_pagure_pull_request_updated(pagure_pr_updated):
    event_object = Parser.parse_event(pagure_pr_updated)

    assert isinstance(event_object, pr.Action)
    assert event_object.action == PullRequestAction.synchronize
    assert event_object.pr_id == 32
    assert event_object.base_repo_namespace == "rpms"
    assert event_object.base_repo_name == "marshalparser"
    assert event_object.base_repo_owner == "lbalhar"
    assert event_object.base_ref is None
    assert event_object.target_repo == "marshalparser"
    assert event_object.commit_sha == "f2f041328d629719c5ff31a08e800638d5df497f"
    assert event_object.user_login == "pagure"
    assert event_object.project_url == "https://src.fedoraproject.org/rpms/marshalparser"

    assert isinstance(event_object.project, PagureProject)
    assert event_object.project.full_repo_name == "rpms/marshalparser"
    assert isinstance(event_object.base_project, PagureProject)
    assert event_object.base_project.full_repo_name == "fork/lbalhar/rpms/marshalparser"

    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=event_object.base_project,
        project=event_object.project,
        reference="f2f041328d629719c5ff31a08e800638d5df497f",
        pr_id=32,
        fail_when_missing=False,
    ).and_return(
        flexmock(get_package_config_views=dict),
    ).once()
    flexmock(PagureProject).should_receive("get_web_url").and_return(
        "https://src.fedoraproject.org/rpms/marshalparser",
    )
    assert event_object.packages_config


def test_parse_pagure_pull_request_rebased(pagure_pr_rebased):
    event_object = Parser.parse_event(pagure_pr_rebased)

    assert isinstance(event_object, pr.Action)
    assert event_object.action == PullRequestAction.synchronize
    assert event_object.pr_id == 6
    assert event_object.base_repo_namespace == "rpms"
    assert event_object.base_repo_name == "ftp"
    assert event_object.base_repo_owner == "omejzlik"
    assert event_object.base_ref is None
    assert event_object.target_repo == "ftp"
    assert event_object.commit_sha == "196f3c99b21d75bf441331e1a82fb76d243e82d5"
    assert event_object.user_login == "pagure"
    assert event_object.project_url == "https://src.fedoraproject.org/rpms/ftp"

    assert isinstance(event_object.project, PagureProject)
    assert event_object.project.full_repo_name == "rpms/ftp"
    assert isinstance(event_object.base_project, PagureProject)
    assert event_object.base_project.full_repo_name == "fork/omejzlik/rpms/ftp"

    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=event_object.base_project,
        project=event_object.project,
        reference="196f3c99b21d75bf441331e1a82fb76d243e82d5",
        pr_id=6,
        fail_when_missing=False,
    ).and_return(
        flexmock(get_package_config_views=dict),
    ).once()
    flexmock(PagureProject).should_receive("get_web_url").and_return(
        "https://src.fedoraproject.org/rpms/ftp",
    )
    assert event_object.packages_config
