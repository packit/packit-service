# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json
import pytest
from flexmock import flexmock

from ogr.services.pagure import PagureProject
from packit_service.config import PackageConfigGetter
from packit_service.models import (
    ProjectEventModel,
    ProjectReleaseModel,
    ProjectEventModelType,
)
from packit_service.worker.events.new_hotness import NewHotnessUpdateEvent
from packit_service.worker.parser import Parser
from tests.spellbook import DATA_DIR


@pytest.fixture()
def new_hotness_update():
    with open(DATA_DIR / "fedmsg" / "new_hotness_update.json") as outfile:
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
        "get_package_config_from_repo"
    ).with_args(
        base_project=None,
        project=event_object.project,
        pr_id=None,
        reference=None,
        fail_when_missing=False,
    ).and_return(
        flexmock(
            upstream_project_url=upstream_project_url,
            upstream_tag_template=upstream_tag_template,
            get_packages_config=lambda: flexmock(),
        )
    ).once()

    flexmock(ProjectEventModel).should_receive("get_or_create").with_args(
        type=ProjectEventModelType.release, event_id="123", commit_sha=None
    ).and_return(flexmock())
    flexmock(ProjectReleaseModel).should_receive("get_or_create").with_args(
        tag_name=tag_name,
        namespace=repo_namespace,
        repo_name=repo_name,
        project_url=upstream_project_url,
        commit_hash=None,
    ).and_return(
        flexmock(project_event_model_type=ProjectEventModelType.release, id="123")
    )

    assert isinstance(event_object, NewHotnessUpdateEvent)
    assert isinstance(event_object.project, PagureProject)
    assert event_object.package_name == "redis"
    assert event_object.release_monitoring_project_id == 4181
    assert event_object.repo_namespace == repo_namespace
    assert event_object.repo_name == repo_name
    assert (
        event_object.distgit_project_url == "https://src.fedoraproject.org/rpms/redis"
    )
    assert event_object.tag_name == tag_name
    assert event_object.packages_config

    if create_db_trigger:
        assert event_object.db_project_object
