# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import datetime

import requests
from copr.v3 import Client
from flexmock import flexmock

import packit_service.worker.build.babysit
from packit.config import PackageConfig, JobConfig, JobType, JobConfigTriggerType
from packit_service.models import (
    CoprBuildModel,
    JobTriggerModelType,
    TFTTestRunModel,
    TestingFarmResult,
)
from packit_service.worker.events import AbstractCoprBuildEvent, TestingFarmResultsEvent
from packit_service.worker.build.babysit import (
    check_copr_build,
    update_copr_builds,
    check_pending_copr_builds,
    check_pending_testing_farm_runs,
)
from packit_service.worker.handlers import (
    CoprBuildEndHandler,
    TestingFarmResultsHandler,
)


def test_check_copr_build_no_build():
    flexmock(CoprBuildModel).should_receive("get_all_by_build_id").with_args(
        1
    ).and_return([])
    assert check_copr_build(build_id=1)


def test_check_copr_build_not_ended():
    flexmock(CoprBuildModel).should_receive("get_all_by_build_id").with_args(
        1
    ).and_return([flexmock()])
    flexmock(Client).should_receive("create_from_config_file").and_return(
        flexmock(
            build_proxy=flexmock()
            .should_receive("get")
            .with_args(1)
            .and_return(flexmock(ended_on=False))
            .mock()
        )
    )
    assert not check_copr_build(build_id=1)


def test_check_copr_build_already_successful():
    flexmock(CoprBuildModel).should_receive("get_all_by_build_id").with_args(
        1
    ).and_return(
        [flexmock(status="success", build_submitted_time=datetime.datetime.utcnow())]
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


def test_check_copr_build_updated():
    flexmock(CoprBuildModel).should_receive("get_by_build_id").and_return()
    flexmock(CoprBuildModel).should_receive("get_all_by_build_id").with_args(
        1
    ).and_return(
        [
            flexmock(
                status="pending",
                build_submitted_time=datetime.datetime.utcnow(),
                target="the-target",
                owner="the-owner",
                project_name="the-project-name",
                commit_sha="123456",
                job_trigger=flexmock(type=JobTriggerModelType.pull_request),
                srpm_build=flexmock(url=None)
                .should_receive("set_url")
                .with_args("https://some.host/my.srpm")
                .mock(),
            )
            .should_receive("get_trigger_object")
            .and_return(
                flexmock(
                    project=flexmock(
                        repo_name="repo_name",
                        namespace="the-namespace",
                        project_url="https://github.com/the-namespace/repo_name",
                    ),
                    pr_id=5,
                    job_config_trigger_type=JobConfigTriggerType.pull_request,
                    job_trigger_model_type=JobTriggerModelType.pull_request,
                    id=123,
                )
            )
            .mock()
        ]
    )
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
    flexmock(AbstractCoprBuildEvent).should_receive("get_package_config").and_return(
        PackageConfig(
            jobs=[
                JobConfig(type=JobType.build, trigger=JobConfigTriggerType.pull_request)
            ]
        )
    )
    flexmock(CoprBuildEndHandler).should_receive("run").and_return().once()
    assert check_copr_build(build_id=1)


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
        status="pending",
        build_id=1,
        build_submitted_time=datetime.datetime.utcnow() - datetime.timedelta(weeks=2),
    )
    build.should_receive("set_status").with_args("error").once()

    flexmock(CoprBuildModel).should_receive("get_all_by_status").with_args(
        "pending"
    ).and_return([build])
    update_copr_builds(1, [build])


def test_check_pending_copr_builds_no_builds():
    flexmock(CoprBuildModel).should_receive("get_all_by_status").with_args(
        "pending"
    ).and_return([])
    flexmock(packit_service.worker.build.babysit).should_receive(
        "update_copr_builds"
    ).never()
    check_pending_copr_builds()


def test_check_pending_copr_builds():
    build1 = flexmock(status="pending", build_id=1)
    build2 = flexmock(status="pending", build_id=2)
    build3 = flexmock(status="pending", build_id=1)
    flexmock(CoprBuildModel).should_receive("get_all_by_status").with_args(
        "pending"
    ).and_return([build1, build2, build3])
    flexmock(packit_service.worker.build.babysit).should_receive(
        "update_copr_builds"
    ).with_args(1, [build1, build3]).once()
    flexmock(packit_service.worker.build.babysit).should_receive(
        "update_copr_builds"
    ).with_args(2, [build2]).once()
    check_pending_copr_builds()


def test_check_pending_testing_farm_runs_no_runs():
    flexmock(TFTTestRunModel).should_receive("get_all_by_status").with_args(
        TestingFarmResult.running
    ).and_return([])
    # No request should be performed
    flexmock(requests).should_receive("get").never()
    check_pending_testing_farm_runs()


def test_check_pending_testing_farm_runs():
    pipeline_id = 1
    run = (
        flexmock(
            pipeline_id=pipeline_id,
            submitted_time=datetime.datetime.utcnow(),
            commit_sha="123456",
            target="fedora-rawhide-x86_64",
            data={},
            job_trigger=flexmock(type=JobTriggerModelType.pull_request),
        )
        .should_receive("get_trigger_object")
        .and_return(
            flexmock(
                project=flexmock(
                    repo_name="repo_name",
                    namespace="the-namespace",
                    project_url="https://github.com/the-namespace/repo_name",
                ),
                pr_id=5,
                job_config_trigger_type=JobConfigTriggerType.pull_request,
                job_trigger_model_type=JobTriggerModelType.pull_request,
                id=123,
            )
        )
        .mock()
    )
    flexmock(TFTTestRunModel).should_receive("get_all_by_status").with_args(
        TestingFarmResult.running
    ).and_return([run]).once()
    flexmock(TFTTestRunModel).should_receive("get_by_pipeline_id").with_args(
        pipeline_id=pipeline_id
    ).and_return(run)
    url = "https://api.dev.testing-farm.io/v0.1/requests/1"
    flexmock(requests).should_receive("get").with_args(url).and_return(
        flexmock(
            json=lambda: {
                "id": pipeline_id,
                "state": TestingFarmResult.passed,
            },
            ok=lambda: True,
        )
    ).once()
    flexmock(TestingFarmResultsEvent).should_receive("get_package_config").and_return(
        PackageConfig(
            jobs=[
                JobConfig(type=JobType.tests, trigger=JobConfigTriggerType.pull_request)
            ]
        )
    )
    flexmock(TestingFarmResultsHandler).should_receive("run").and_return().once()
    check_pending_testing_farm_runs()


def test_check_pending_testing_farm_runs_timeout():
    run = flexmock(
        pipeline_id=1,
        status=TestingFarmResult.running,
        submitted_time=datetime.datetime.utcnow() - datetime.timedelta(weeks=2),
    )
    run.should_receive("set_status").with_args(TestingFarmResult.error).once()
    flexmock(TFTTestRunModel).should_receive("get_all_by_status").with_args(
        TestingFarmResult.running
    ).and_return([run]).once()
    check_pending_testing_farm_runs()
