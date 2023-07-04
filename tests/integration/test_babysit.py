# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import datetime

import pytest
import requests
from copr.v3 import Client, CoprNoResultException
from flexmock import flexmock

import packit_service.worker.helpers.build.babysit
from packit.config import (
    CommonPackageConfig,
    JobConfig,
    JobConfigTriggerType,
    JobType,
    PackageConfig,
)
from packit.copr_helper import CoprHelper
from packit_service.models import (
    CoprBuildTargetModel,
    ProjectEventModelType,
    TFTTestRunTargetModel,
    TestingFarmResult,
    BuildStatus,
)
from packit_service.worker.events import AbstractCoprBuildEvent, TestingFarmResultsEvent
from packit_service.worker.helpers.build.babysit import (
    check_copr_build,
    update_copr_builds,
    check_pending_copr_builds,
    check_pending_testing_farm_runs,
)
from packit_service.worker.handlers import (
    CoprBuildEndHandler,
    CoprBuildStartHandler,
    TestingFarmResultsHandler,
)


def test_check_copr_build_no_build():
    flexmock(CoprBuildTargetModel).should_receive("get_all_by_build_id").with_args(
        1
    ).and_return([])
    assert check_copr_build(build_id=1)


def test_check_copr_build_not_started():
    flexmock(CoprBuildTargetModel).should_receive("get_all_by_build_id").with_args(
        1
    ).and_return([flexmock()])
    flexmock(Client).should_receive("create_from_config_file").and_return(
        flexmock(
            build_proxy=flexmock()
            .should_receive("get")
            .with_args(1)
            .and_return(flexmock(ended_on=False, started_on=False))
            .mock()
        )
    )
    assert not check_copr_build(build_id=1)


def test_check_copr_build_already_successful():
    flexmock(CoprBuildTargetModel).should_receive("get_all_by_build_id").with_args(
        1
    ).and_return(
        [
            flexmock(
                status=BuildStatus.success,
                build_submitted_time=datetime.datetime.utcnow(),
            )
        ]
    )
    flexmock(Client).should_receive("create_from_config_file").and_return(
        flexmock(
            build_proxy=flexmock()
            .should_receive("get")
            .with_args(1)
            .and_return(flexmock(ended_on="timestamp", state="completed"))
            .mock()
        )
    )
    assert check_copr_build(build_id=1)


@pytest.mark.parametrize(
    "build_status, build_ended_on",
    [
        (BuildStatus.pending, "timestamp"),
        (BuildStatus.pending, None),
        (BuildStatus.waiting_for_srpm, None),
    ],
)
def test_check_copr_build_updated(build_status, build_ended_on):
    db_build = (
        flexmock(
            build_id="55",
            status=build_status,
            build_submitted_time=datetime.datetime.utcnow(),
            target="the-target",
            owner="the-owner",
            project_name="the-namespace-repo_name-5",
            commit_sha="123456",
            project_event=flexmock(type=ProjectEventModelType.pull_request),
            srpm_build=flexmock(url=None)
            .should_receive("set_url")
            .with_args("https://some.host/my.srpm")
            .mock(),
        )
        .should_receive("get_project_event_object")
        .and_return(
            flexmock(
                project=flexmock(
                    repo_name="repo_name",
                    namespace="the-namespace",
                    project_url="https://github.com/the-namespace/repo_name",
                ),
                pr_id=5,
                job_config_trigger_type=JobConfigTriggerType.pull_request,
                project_event_model_type=ProjectEventModelType.pull_request,
                id=123,
            )
        )
        .mock()
        .should_receive("get_project_event_model")
        .and_return(flexmock())
        .mock()
    )
    flexmock(CoprHelper).should_receive("get_copr_client").and_return(
        Client(config={"username": "the-owner", "copr_url": "https://dummy.url"})
    )
    flexmock(CoprBuildTargetModel).should_receive("get_by_build_id").and_return(
        db_build
    )
    flexmock(CoprBuildTargetModel).should_receive("get_all_by_build_id").with_args(
        1
    ).and_return([db_build])
    flexmock(Client).should_receive("create_from_config_file").and_return(
        flexmock(
            build_proxy=flexmock()
            .should_receive("get")
            .with_args(1)
            .and_return(
                flexmock(
                    ended_on=build_ended_on,
                    started_on="timestamp",
                    state="completed",
                    source_package={
                        "name": "source_package_name",
                        "url": "https://some.host/my.srpm",
                    },
                )
            )
            .mock(),
            build_chroot_proxy=flexmock()
            .should_receive("get")
            .with_args(1, "the-target")
            .and_return(flexmock(ended_on="timestamp", state="succeeded"))
            .mock(),
        )
    )
    flexmock(AbstractCoprBuildEvent).should_receive("get_packages_config").and_return(
        PackageConfig(
            jobs=[
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                )
            ],
            packages={"package": CommonPackageConfig()},
        )
    )
    flexmock(CoprBuildEndHandler).should_receive("run_job").and_return().once()
    assert check_copr_build(build_id=1) is bool(build_ended_on)


def test_check_copr_build_waiting_started():
    db_build = (
        flexmock(
            build_id="55",
            status=BuildStatus.waiting_for_srpm,
            build_submitted_time=datetime.datetime.utcnow(),
            target="the-target",
            owner="the-owner",
            project_name="the-namespace-repo_name-5",
            commit_sha="123456",
            project_event=flexmock(type=ProjectEventModelType.pull_request),
            build_start_time=None,
            srpm_build=flexmock(url=None)
            .should_receive("set_url")
            .with_args("https://some.host/my.srpm")
            .mock(),
        )
        .should_receive("get_project_event_object")
        .and_return(
            flexmock(
                project=flexmock(
                    repo_name="repo_name",
                    namespace="the-namespace",
                    project_url="https://github.com/the-namespace/repo_name",
                ),
                pr_id=5,
                job_config_trigger_type=JobConfigTriggerType.pull_request,
                project_event_model_type=ProjectEventModelType.pull_request,
                id=123,
            )
        )
        .mock()
        .should_receive("get_project_event_model")
        .and_return(flexmock())
        .mock()
    )
    flexmock(CoprHelper).should_receive("get_copr_client").and_return(
        Client(config={"username": "the-owner", "copr_url": "https://dummy.url"})
    )
    flexmock(CoprBuildTargetModel).should_receive("get_by_build_id").and_return(
        db_build
    )
    flexmock(CoprBuildTargetModel).should_receive("get_all_by_build_id").with_args(
        1
    ).and_return([db_build])
    flexmock(Client).should_receive("create_from_config_file").and_return(
        flexmock(
            build_proxy=flexmock()
            .should_receive("get")
            .with_args(1)
            .and_return(
                flexmock(
                    started_on="timestamp",
                    ended_on=None,
                    state="completed",
                    source_package={
                        "name": "source_package_name",
                        "url": "https://some.host/my.srpm",
                    },
                )
            )
            .mock(),
            build_chroot_proxy=flexmock()
            .should_receive("get")
            .with_args(1, "the-target")
            .and_return(
                flexmock(started_on="timestamp", ended_on=None, state="succeeded")
            )
            .mock(),
        )
    )
    flexmock(AbstractCoprBuildEvent).should_receive("get_packages_config").and_return(
        PackageConfig(
            jobs=[
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                )
            ],
            packages={"package": CommonPackageConfig()},
        )
    )
    flexmock(CoprBuildStartHandler).should_receive("run_job").and_return().once()
    assert not check_copr_build(build_id=1)


def test_check_copr_build_waiting_already_started():
    db_build = (
        flexmock(
            build_id="55",
            status=BuildStatus.waiting_for_srpm,
            build_submitted_time=datetime.datetime.utcnow(),
            target="the-target",
            owner="the-owner",
            project_name="the-namespace-repo_name-5",
            commit_sha="123456",
            project_event=flexmock(type=ProjectEventModelType.pull_request),
            build_start_time="some timestamp",
            srpm_build=flexmock(url=None)
            .should_receive("set_url")
            .with_args("https://some.host/my.srpm")
            .mock(),
        )
        .should_receive("get_project_event_object")
        .and_return(
            flexmock(
                project=flexmock(
                    repo_name="repo_name",
                    namespace="the-namespace",
                    project_url="https://github.com/the-namespace/repo_name",
                ),
                pr_id=5,
                job_config_trigger_type=JobConfigTriggerType.pull_request,
                project_event_model_type=ProjectEventModelType.pull_request,
                id=123,
            )
        )
        .mock()
        .should_receive("get_project_event_model")
        .and_return(flexmock())
        .mock()
    )
    flexmock(CoprHelper).should_receive("get_copr_client").and_return(
        Client(config={"username": "the-owner", "copr_url": "https://dummy.url"})
    )
    flexmock(CoprBuildTargetModel).should_receive("get_by_build_id").and_return(
        db_build
    )
    flexmock(CoprBuildTargetModel).should_receive("get_all_by_build_id").with_args(
        1
    ).and_return([db_build])
    flexmock(Client).should_receive("create_from_config_file").and_return(
        flexmock(
            build_proxy=flexmock()
            .should_receive("get")
            .with_args(1)
            .and_return(
                flexmock(
                    started_on="timestamp",
                    ended_on=None,
                    state="completed",
                    source_package={
                        "name": "source_package_name",
                        "url": "https://some.host/my.srpm",
                    },
                )
            )
            .mock(),
            build_chroot_proxy=flexmock()
            .should_receive("get")
            .with_args(1, "the-target")
            .and_return(
                flexmock(started_on="timestamp", ended_on=None, state="succeeded")
            )
            .mock(),
        )
    )
    flexmock(AbstractCoprBuildEvent).should_receive("get_packages_config").and_return(
        PackageConfig(
            jobs=[
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                )
            ],
            packages={"package": CommonPackageConfig()},
        )
    )
    flexmock(CoprBuildStartHandler).should_receive("run_job").and_return().never()
    assert not check_copr_build(build_id=1)


def test_check_copr_build_not_exists():
    flexmock(Client).should_receive("create_from_config_file").and_return(
        flexmock(
            build_proxy=flexmock()
            .should_receive("get")
            .with_args(1)
            .and_raise(CoprNoResultException, "Build 1 does not exist")
            .mock()
        )
    )
    builds = []
    for i in range(2):
        builds.append(flexmock(status=BuildStatus.pending, build_id=1))
        builds[i].should_receive("set_status").with_args(BuildStatus.error).once()
    flexmock(CoprBuildTargetModel).should_receive("get_all_by_status").with_args(
        BuildStatus.pending
    ).and_return(builds)
    check_pending_copr_builds()


def test_check_update_copr_builds_timeout():
    flexmock(Client).should_receive("create_from_config_file").and_return(
        flexmock(
            build_proxy=flexmock()
            .should_receive("get")
            .with_args(1)
            .and_return(
                flexmock(
                    ended_on=True,
                    state="completed",
                    source_package={
                        "name": "source_package_name",
                        "url": "https://some.host/my.srpm",
                    },
                )
            )
            .mock(),
            build_chroot_proxy=flexmock()
            .should_receive("get")
            .with_args(1, "the-target")
            .and_return(flexmock(ended_on="timestamp", state="succeeded"))
            .mock(),
        )
    )
    build = flexmock(
        status=BuildStatus.pending,
        build_id="1",
        build_submitted_time=datetime.datetime.utcnow() - datetime.timedelta(weeks=2),
    )
    build.should_receive("set_status").with_args(BuildStatus.error).once()

    flexmock(CoprBuildTargetModel).should_receive("get_all_by_status").with_args(
        BuildStatus.pending
    ).and_return([build])
    update_copr_builds(1, [build])


def test_check_pending_copr_builds_no_builds():
    flexmock(CoprBuildTargetModel).should_receive("get_all_by_status").with_args(
        BuildStatus.pending
    ).and_return([])
    flexmock(packit_service.worker.helpers.build.babysit).should_receive(
        "update_copr_builds"
    ).never()
    check_pending_copr_builds()


def test_check_pending_copr_builds():
    build1 = flexmock(status=BuildStatus.pending, build_id="1")
    build2 = flexmock(status=BuildStatus.pending, build_id="2")
    build3 = flexmock(status=BuildStatus.pending, build_id="1")
    flexmock(CoprBuildTargetModel).should_receive("get_all_by_status").with_args(
        BuildStatus.pending
    ).and_return([build1, build2, build3])
    flexmock(packit_service.worker.helpers.build.babysit).should_receive(
        "update_copr_builds"
    ).with_args(1, [build1, build3]).once()
    flexmock(packit_service.worker.helpers.build.babysit).should_receive(
        "update_copr_builds"
    ).with_args(2, [build2]).once()
    check_pending_copr_builds()


def test_check_pending_testing_farm_runs_no_runs():
    flexmock(TFTTestRunTargetModel).should_receive("get_all_by_status").with_args(
        TestingFarmResult.new, TestingFarmResult.queued, TestingFarmResult.running
    ).and_return([])
    # No request should be performed
    flexmock(requests).should_receive("get").never()
    check_pending_testing_farm_runs()


@pytest.mark.parametrize(
    "created",
    (
        # I don't think it matters that this is evaluated before running the test
        datetime.datetime.utcnow(),
        None,
    ),
)
def test_check_pending_testing_farm_runs(created):
    pipeline_id = 1
    run = (
        flexmock(
            pipeline_id=pipeline_id,
            submitted_time=created,
            commit_sha="123456",
            target="fedora-rawhide-x86_64",
            data={},
            project_event=flexmock(type=ProjectEventModelType.pull_request),
            identifier=None,
        )
        .should_receive("get_project_event_object")
        .and_return(
            flexmock(
                project=flexmock(
                    repo_name="repo_name",
                    namespace="the-namespace",
                    project_url="https://github.com/the-namespace/repo_name",
                ),
                pr_id=5,
                job_config_trigger_type=JobConfigTriggerType.pull_request,
                project_event_model_type=ProjectEventModelType.pull_request,
                id=123,
            )
        )
        .mock()
    )
    flexmock(TFTTestRunTargetModel).should_receive("get_all_by_status").with_args(
        TestingFarmResult.new, TestingFarmResult.queued, TestingFarmResult.running
    ).and_return([run]).once()
    flexmock(TFTTestRunTargetModel).should_receive("get_by_pipeline_id").with_args(
        pipeline_id=pipeline_id
    ).and_return(run)
    url = "https://api.dev.testing-farm.io/v0.1/requests/1"
    flexmock(requests).should_receive("get").with_args(url).and_return(
        flexmock(
            json=lambda: {
                "id": pipeline_id,
                "state": TestingFarmResult.passed,
                "created": "2021-11-01 17:22:36.061250",
            },
            ok=lambda: True,
        )
    ).once()
    flexmock(TestingFarmResultsEvent).should_receive("get_packages_config").and_return(
        PackageConfig(
            jobs=[
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                )
            ],
            packages={"package": CommonPackageConfig()},
        )
    )
    flexmock(TestingFarmResultsHandler).should_receive("run_job").and_return().once()
    check_pending_testing_farm_runs()


@pytest.mark.parametrize(
    "status",
    [TestingFarmResult.new, TestingFarmResult.queued, TestingFarmResult.running],
)
def test_check_pending_testing_farm_runs_timeout(status):
    run = flexmock(
        pipeline_id=1,
        status=status,
        submitted_time=datetime.datetime.utcnow() - datetime.timedelta(weeks=2),
    )
    run.should_receive("set_status").with_args(TestingFarmResult.error).once()
    flexmock(TFTTestRunTargetModel).should_receive("get_all_by_status").with_args(
        TestingFarmResult.new, TestingFarmResult.queued, TestingFarmResult.running
    ).and_return([run]).once()
    check_pending_testing_farm_runs()


@pytest.mark.parametrize(
    "identifier",
    [None, "first", "second"],
)
def test_check_pending_testing_farm_runs_identifiers(identifier):
    pipeline_id = 1
    run = (
        flexmock(
            pipeline_id=pipeline_id,
            submitted_time=datetime.datetime.utcnow(),
            commit_sha="123456",
            target="fedora-rawhide-x86_64",
            data={},
            project_event=flexmock(type=ProjectEventModelType.pull_request),
            identifier=identifier,
        )
        .should_receive("get_project_event_object")
        .and_return(
            flexmock(
                project=flexmock(
                    repo_name="repo_name",
                    namespace="the-namespace",
                    project_url="https://github.com/the-namespace/repo_name",
                ),
                pr_id=5,
                job_config_trigger_type=JobConfigTriggerType.pull_request,
                project_event_model_type=ProjectEventModelType.pull_request,
                id=123,
            )
        )
        .mock()
    )
    flexmock(TFTTestRunTargetModel).should_receive("get_all_by_status").with_args(
        TestingFarmResult.new, TestingFarmResult.queued, TestingFarmResult.running
    ).and_return([run]).once()
    flexmock(TFTTestRunTargetModel).should_receive("get_by_pipeline_id").with_args(
        pipeline_id=pipeline_id
    ).and_return(run)
    url = "https://api.dev.testing-farm.io/v0.1/requests/1"
    flexmock(requests).should_receive("get").with_args(url).and_return(
        flexmock(
            json=lambda: {
                "id": pipeline_id,
                "state": TestingFarmResult.passed,
                "created": "2021-11-01 17:22:36.061250",
            },
            ok=lambda: True,
        )
    ).once()
    flexmock(TestingFarmResultsEvent).should_receive("get_packages_config").and_return(
        PackageConfig(
            jobs=[
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            identifier="first",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            identifier="second",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            packages={"package": CommonPackageConfig()},
        )
    )
    flexmock(TestingFarmResultsHandler).should_receive("run_job").and_return().once()
    check_pending_testing_farm_runs()
