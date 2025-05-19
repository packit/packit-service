# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json

import pytest
from flexmock import flexmock
from ogr.services.gitlab import GitlabProject
from ogr.services.pagure import PagureProject

from packit_service.events.anitya import NewHotness, VersionUpdate
from packit_service.models import (
    ProjectEventModel,
    ProjectEventModelType,
    ProjectReleaseModel,
)
from packit_service.package_config_getter import PackageConfigGetter
from packit_service.worker.parser import Parser
from tests.spellbook import DATA_DIR


@pytest.fixture()
def new_hotness_update():
    with open(DATA_DIR / "fedmsg" / "new_hotness_update.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def anitya_version_update():
    with open(DATA_DIR / "fedmsg" / "anitya_version_update.json") as outfile:
        return json.load(outfile)


@pytest.mark.parametrize(
    "upstream_project_url, upstream_tag_template, create_db_trigger, "
    "tag_name, repo_namespace, repo_name",
    [
        (
            "https://github.com/redis-namespace/redis",
            None,
            True,
            "7.0.3",
            "redis-namespace",
            "redis",
        ),
        (
            "https://github.com/redis-namespace/redis",
            "no-version-tag",
            True,
            "no-version-tag",
            "redis-namespace",
            "redis",
        ),
        (
            "https://github.com/redis-namespace/redis",
            "v{version}",
            True,
            "v7.0.3",
            "redis-namespace",
            "redis",
        ),
        (
            "https://github.com/redis-namespace",
            None,
            False,
            "7.0.3",
            None,
            "redis-namespace",
        ),
        (
            "https://github.com/redis-namespace/another-level/redis",
            None,
            True,
            "7.0.3",
            "redis-namespace/another-level",
            "redis",
        ),
    ],
)
def test_parse_new_hotness_update(
    new_hotness_update,
    upstream_project_url,
    upstream_tag_template,
    create_db_trigger,
    tag_name,
    repo_namespace,
    repo_name,
):
    event_object = Parser.parse_event(new_hotness_update)

    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=None,
        project=event_object.project,
        pr_id=None,
        reference=None,
        fail_when_missing=False,
    ).and_return(
        flexmock(
            upstream_project_url=upstream_project_url,
            upstream_package_name="upstream_package",
            upstream_tag_template=upstream_tag_template,
            get_packages_config=lambda: flexmock(),
        ),
    ).once()

    flexmock(ProjectEventModel).should_receive("get_or_create").with_args(
        type=ProjectEventModelType.release,
        event_id="123",
        commit_sha=None,
    ).and_return(flexmock())
    flexmock(ProjectReleaseModel).should_receive("get_or_create").with_args(
        tag_name=tag_name,
        namespace=repo_namespace,
        repo_name=repo_name,
        project_url=upstream_project_url,
        commit_hash=None,
    ).and_return(
        flexmock(project_event_model_type=ProjectEventModelType.release, id="123"),
    )

    assert isinstance(event_object, NewHotness)
    assert isinstance(event_object.project, PagureProject)
    assert event_object.package_name == "redis"
    assert event_object.anitya_project_id == 4181
    assert event_object.repo_namespace == repo_namespace
    assert event_object.repo_name == repo_name
    assert event_object.distgit_project_url == "https://src.fedoraproject.org/rpms/redis"
    assert event_object.tag_name == tag_name
    assert event_object.packages_config

    if create_db_trigger:
        assert event_object.db_project_object


# [NOTE] doesn't exist in CentOS… I've added CentOS entry to the event payload
# and also faked the potential package config with the “would be” data
def test_parse_anitya_version_update(anitya_version_update):
    event = Parser.parse_event(anitya_version_update)

    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=None,
        project=event.project,
        pr_id=None,
        reference=None,
        fail_when_missing=False,
    ).and_return(
        flexmock(
            upstream_project_url="https://github.com/vemel/mypy_boto3",
            upstream_tag_template="{version}",
            get_packages_config=lambda: flexmock(),
        ),
    ).once()

    flexmock(ProjectEventModel).should_receive("get_or_create").with_args(
        type=ProjectEventModelType.release,
        event_id="123",
        commit_sha=None,
    ).and_return(flexmock())

    assert isinstance(event, VersionUpdate)
    assert isinstance(event.project, GitlabProject)
    assert event.package_name == "python3-mypy-boto3"
    assert event.anitya_project_id == 40221
    assert event.repo_namespace == "vemel"
    assert event.repo_name == "mypy_boto3"
    assert (
        event.distgit_project_url
        == "https://gitlab.com/redhat/centos-stream/rpms/python3-mypy-boto3"
    )
    assert event._versions == ["1.33.0", "1.33.1"]
