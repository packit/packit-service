# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json

import pytest
from flexmock import flexmock
from ogr.services.pagure import PagureProject

from packit_service.config import PackageConfigGetter
from packit_service.worker.events.pagure import (
    PullRequestCommentPagureEvent,
    PullRequestFlagPagureEvent,
    PushPagureEvent,
)
from packit_service.worker.parser import Parser
from tests.spellbook import DATA_DIR


@pytest.fixture()
def pagure_pr_flag_updated():
    with open(DATA_DIR / "fedmsg" / "pagure_pr_flag_updated.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def distgit_commit():
    with open(DATA_DIR / "fedmsg" / "distgit_commit.json") as outfile:
        return json.load(outfile)


def test_parse_pagure_flag(pagure_pr_flag_updated):
    event_object = Parser.parse_event(pagure_pr_flag_updated)

    assert isinstance(event_object, PullRequestFlagPagureEvent)
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
    assert (
        event_object.pr_url
        == "https://src.fedoraproject.org/rpms/packit/pull-request/268"
    )
    assert event_object.pr_source_branch == "0.47.0-f36-update"
    assert event_object.project_name == "packit"
    assert event_object.project_namespace == "rpms"


def test_parse_pagure_pull_request_comment(pagure_pr_comment_added):
    event_object = Parser.parse_event(pagure_pr_comment_added)

    assert isinstance(event_object, PullRequestCommentPagureEvent)
    assert event_object.pr_id == 36
    assert event_object.base_repo_namespace == "rpms"
    assert event_object.base_repo_name == "python-teamcity-messages"
    assert event_object.base_repo_owner == "mmassari"
    assert event_object.base_ref is None
    assert event_object.target_repo == "python-teamcity-messages"
    assert event_object.commit_sha == "beaf90bcecc51968a46663f8d6f092bfdc92e682"
    assert event_object.user_login == "mmassari"
    assert event_object.comment == "/packit koji-build"
    assert (
        event_object.project_url
        == "https://src.fedoraproject.org/rpms/python-teamcity-messages"
    )

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
        flexmock(get_package_config_views=lambda: {}),
    ).once()
    flexmock(PagureProject).should_receive("get_web_url").and_return(
        "https://src.fedoraproject.org/rpms/python-teamcity-messages",
    )
    assert event_object.packages_config


def test_distgit_pagure_push(distgit_commit):
    event_object = Parser.parse_event(distgit_commit)

    assert isinstance(event_object, PushPagureEvent)
    assert event_object.repo_namespace == "rpms"
    assert event_object.repo_name == "buildah"
    assert event_object.commit_sha == "abcd"
    assert event_object.git_ref == "main"
    assert event_object.project_url == "https://src.fedoraproject.org/rpms/buildah"
