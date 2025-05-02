# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json
from datetime import datetime, timedelta

import pytest
from flexmock import flexmock
from ogr.services.github import GithubProject

from packit_service.events.copr import End, Start
from packit_service.events.enums import FedmsgTopic
from packit_service.models import CoprBuildTargetModel, get_most_recent_targets
from packit_service.package_config_getter import PackageConfigGetter
from packit_service.worker.parser import Parser
from tests.spellbook import DATA_DIR


@pytest.fixture(scope="module")
def copr_build_results_start():
    with open(DATA_DIR / "fedmsg" / "copr_build_start.json") as outfile:
        return json.load(outfile)


@pytest.fixture(scope="module")
def copr_build_results_end():
    with open(DATA_DIR / "fedmsg" / "copr_build_end.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def copr_models():
    time = datetime(2000, 4, 28, 14, 9, 33, 860293)
    latest_time = datetime.utcnow()
    copr = flexmock(CoprBuildTargetModel).new_instances().mock()
    copr.build_id = "1"
    copr.build_submitted_time = time
    copr.target = "target"

    another_copr = flexmock(CoprBuildTargetModel).new_instances().mock()
    another_copr.build_id = "2"
    another_copr.build_submitted_time = latest_time
    another_copr.target = "target"

    yield [copr, another_copr]


@pytest.mark.parametrize("build_id", (1044215, "1044215"))
def test_parse_copr_build_event_start(
    copr_build_results_start,
    copr_build_pr,
    build_id,
):
    flexmock(CoprBuildTargetModel).should_receive("get_by_build_id").and_return(
        copr_build_pr,
    )

    event_object = Parser.parse_event(copr_build_results_start)

    assert isinstance(event_object, Start)
    assert event_object.topic == FedmsgTopic.copr_build_started
    assert event_object.build_id == int(build_id)
    assert event_object.chroot == "fedora-rawhide-x86_64"
    assert event_object.status == 3
    assert event_object.owner == "packit"
    assert event_object.project_name == "packit-service-hello-world-24"
    assert event_object.project_url == "https://github.com/packit-service/hello-world"
    assert event_object.base_repo_name == "hello-world"
    assert event_object.base_repo_namespace == "packit-service"
    assert event_object.pkg == "hello"

    assert isinstance(event_object.project, GithubProject)
    assert event_object.project.full_repo_name == "packit-service/hello-world"

    assert (
        not event_object.base_project  # With Github app, we cannot work with fork repo
    )

    assert event_object.get_copr_build_logs_url() == (
        "https://download.copr.fedorainfracloud.org/results/packit/"
        "packit-service-hello-world-24/fedora-rawhide-x86_64/01044215-hello/builder-live.log"
    )

    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=None,
        project=event_object.project,
        pr_id=24,
        reference="0011223344",
        fail_when_missing=False,
    ).and_return(
        flexmock(get_package_config_views=dict),
    ).once()

    assert event_object.packages_config


def test_parse_copr_build_event_end(copr_build_results_end, copr_build_pr):
    flexmock(CoprBuildTargetModel).should_receive("get_by_build_id").and_return(
        copr_build_pr,
    )

    event_object = Parser.parse_event(copr_build_results_end)

    assert isinstance(event_object, End)
    assert event_object.topic == FedmsgTopic.copr_build_finished
    assert event_object.build_id == 1044215
    assert event_object.chroot == "fedora-rawhide-x86_64"
    assert event_object.status == 1
    assert event_object.owner == "packit"
    assert event_object.project_name == "packit-service-hello-world-24"
    assert event_object.base_repo_name == "hello-world"
    assert event_object.base_repo_namespace == "packit-service"
    assert event_object.pkg == "hello"
    assert event_object.git_ref == "0011223344"

    flexmock(GithubProject).should_receive("get_pr").with_args(pr_id=123).and_return(
        flexmock(author="the-fork"),
    )
    assert isinstance(event_object.project, GithubProject)
    assert event_object.project.full_repo_name == "packit-service/hello-world"

    assert (
        not event_object.base_project  # With Github app, we cannot work with fork repo
    )

    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).with_args(
        base_project=None,
        project=event_object.project,
        pr_id=24,
        reference="0011223344",
        fail_when_missing=False,
    ).and_return(
        flexmock(get_package_config_views=dict),
    ).once()

    assert event_object.packages_config


def test_get_most_recent_targets(copr_models, tf_models):
    latest_copr_models = get_most_recent_targets(copr_models)
    assert len(latest_copr_models) == 1
    assert datetime.utcnow() - latest_copr_models[0].build_submitted_time < timedelta(
        seconds=2,
    )

    latest_tf_models = get_most_recent_targets(tf_models)
    assert len(latest_tf_models) == 1
    assert datetime.utcnow() - latest_tf_models[0].submitted_time < timedelta(seconds=2)
