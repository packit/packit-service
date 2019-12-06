import json

import pytest
from flexmock import flexmock
from ogr.abstract import GitProject, GitService
from packit.api import PackitAPI
from packit.config import PackageConfig, JobConfig, JobType, JobTriggerType
from packit.exceptions import FailedCreateSRPM

from packit_service.config import ServiceConfig
from packit_service.service.models import CoprBuild
from packit_service.worker import sentry_integration
from packit_service.worker.copr_build import CoprBuildHandler
from packit_service.worker.copr_db import CoprBuildDB
from packit_service.worker.handler import BuildStatusReporter
from packit_service.worker.parser import Parser
from tests.spellbook import DATA_DIR


@pytest.fixture()
def pull_request():
    with open(DATA_DIR / "webhooks" / "github_pr_event.json", "r") as outfile:
        return json.load(outfile)


class FakeCoprBuildModel:
    build_id = 0

    def save(self):
        pass

    def add_build(self):
        pass


def build_handler(metadata=None, trigger=None, jobs=None):
    if not metadata:
        metadata = {
            "owner": "nobody",
            "targets": [
                "fedora-29-x86_64",
                "fedora-30-x86_64",
                "fedora-31-x86_64",
                "fedora-rawhide-x86_64",
            ],
        }
    jobs = jobs or []
    jobs.append(
        JobConfig(
            job=JobType.copr_build,
            trigger=trigger or JobTriggerType.pull_request,
            metadata=metadata,
        )
    )
    pkg_conf = PackageConfig(jobs=jobs, downstream_package_name="dummy")
    event = Parser.parse_pr_event(pull_request())
    handler = CoprBuildHandler(
        config=ServiceConfig(),
        package_config=pkg_conf,
        project=GitProject("", GitService(), ""),
        event=event,
    )
    handler._api = PackitAPI(ServiceConfig, pkg_conf)
    return handler


def test_copr_build_check_names():
    metadata = {"owner": "nobody", "targets": ["bright-future-x86_64"]}
    handler = build_handler(metadata)
    flexmock(BuildStatusReporter).should_receive("report").with_args(
        state="pending",
        description="SRPM build has just started...",
        check_names="packit-stg/srpm-build",
    ).and_return()
    flexmock(BuildStatusReporter).should_receive("report").with_args(
        state="pending",
        description="RPM build is waiting for successful SPRM build",
        check_names=["packit-stg/rpm-build-bright-future-x86_64"],
    ).and_return()
    flexmock(BuildStatusReporter).should_receive("report").with_args(
        state="success",
        description="SRPM was built successfully.",
        check_names="packit-stg/srpm-build",
    ).and_return()
    flexmock(BuildStatusReporter).should_receive("report").with_args(
        state="pending",
        description="RPM build has just started...",
        check_names=["packit-stg/rpm-build-bright-future-x86_64"],
        url="https://copr.fedorainfracloud.org/coprs/nobody/--342-stg/build/1/",
    ).and_return()

    flexmock(GitProject).should_receive("set_commit_status").and_return().never()
    flexmock(CoprBuild).should_receive("create").and_return(FakeCoprBuildModel())
    flexmock(CoprBuildDB).should_receive("add_build").and_return().once()
    flexmock(PackitAPI).should_receive("run_copr_build").and_return(1, None)
    assert handler.run_copr_build()["success"]


def test_copr_build_success_set_test_check():
    # status is set for:
    #  - SRPM build has just started...
    #  - SRPM was built successfully.
    # for every build-target (4) when:
    #  - RPM build is waiting for successful SPRM build
    #  - RPM build has just started...
    # for every test-target (2) when:
    #  - Waiting for a successful RPM build
    test_job = JobConfig(
        job=JobType.tests, trigger=JobTriggerType.pull_request, metadata={}
    )
    handler = build_handler(jobs=[test_job])
    flexmock(GitProject).should_receive("set_commit_status").and_return().times(12)
    flexmock(CoprBuild).should_receive("create").and_return(FakeCoprBuildModel())
    flexmock(CoprBuildDB).should_receive("add_build").and_return().once()
    flexmock(PackitAPI).should_receive("run_copr_build").and_return(1, None).once()
    print(handler.run_copr_build())


def test_copr_build_success():
    # status is set for:
    #  - srpm build pending and success (2)
    # forevery target (4) when:
    #  - build in progress 4
    #  - build finished 4
    handler = build_handler()
    flexmock(GitProject).should_receive("set_commit_status").and_return().times(10)
    flexmock(CoprBuild).should_receive("create").and_return(FakeCoprBuildModel())
    flexmock(CoprBuildDB).should_receive("add_build").and_return().once()
    flexmock(PackitAPI).should_receive("run_copr_build").and_return(1, None).once()
    assert handler.run_copr_build()["success"]


def test_copr_build_fails_in_packit():
    # status is set for:
    #  - SRPM build has just started...
    #  - 'packit-stg/srpm-build': some error
    #  - 'packit-stg/rpm-build': RPM build failed. No tests will be run.
    # for every build-target (4) when:
    #  - RPM build is waiting for succesfull SPRM build
    handler = build_handler()
    flexmock(GitProject).should_receive("set_commit_status").and_return().times(7)
    flexmock(CoprBuild).should_receive("create").and_return(FakeCoprBuildModel())
    flexmock(sentry_integration).should_receive("send_to_sentry").and_return().once()
    flexmock(CoprBuildDB).should_receive("add_build").never()
    flexmock(PackitAPI).should_receive("run_copr_build").and_raise(
        FailedCreateSRPM, "some error"
    )
    assert not handler.run_copr_build()["success"]


def test_copr_build_no_targets():
    metadata = {"owner": "nobody"}
    handler = build_handler(metadata)
    flexmock(GitProject).should_receive("pr_comment").with_args(
        342, "'targets' value is required in packit config for copr_build job"
    ).and_return().once()
    flexmock(GitProject).should_receive("set_commit_status").never()
    flexmock(CoprBuildDB).should_receive("add_build").never()
    flexmock(PackitAPI).should_receive("run_copr_build").never()
    assert not handler.run_copr_build()["success"]
