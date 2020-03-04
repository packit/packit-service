# MIT License
#
# Copyright (c) 2018-2019 Red Hat, Inc.

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
import json

import pytest
import requests
from copr.v3 import Client
from flexmock import flexmock
from ogr.abstract import CommitStatus
from ogr.services.github import GithubProject
from ogr.utils import RequestResponse
from packit.config import JobConfig, JobType, JobConfigTriggerType
from packit.config.package_config import PackageConfig
from packit.copr_helper import CoprHelper
from packit.local_project import LocalProject

from packit_service.models import CoprBuild
from packit_service.service.events import CoprBuildEvent
from packit_service.service.urls import get_log_url
from packit_service.worker.build.copr_build import CoprBuildJobHelper
from packit_service.worker.copr_db import CoprBuildDB
from packit_service.worker.handlers import CoprBuildEndHandler, GithubTestingFarmHandler
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.reporting import StatusReporter
from packit_service.worker.testing_farm import TestingFarmJobHelper
from tests.spellbook import DATA_DIR

CHROOT = "fedora-rawhide-x86_64"
EXPECTED_BUILD_CHECK_NAME = f"packit-stg/rpm-build-{CHROOT}"
EXPECTED_TESTING_FARM_CHECK_NAME = f"packit-stg/testing-farm-{CHROOT}"


@pytest.fixture()
def copr_build_start():
    return json.loads((DATA_DIR / "fedmsg" / "copr_build_start.json").read_text())


@pytest.fixture()
def copr_build_end():
    return json.loads((DATA_DIR / "fedmsg" / "copr_build_end.json").read_text())


@pytest.fixture()
def pc_build():
    return PackageConfig(
        jobs=[
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                metadata={"targets": ["fedora-all"]},
            )
        ]
    )


@pytest.fixture()
def pc_tests():
    return PackageConfig(
        jobs=[
            JobConfig(
                type=JobType.tests,
                trigger=JobConfigTriggerType.pull_request,
                metadata={"targets": ["fedora-all"]},
            )
        ]
    )


@pytest.mark.parametrize(
    "pc_comment_pr_succ,pr_comment_called", ((True, True), (False, False),)
)
def test_copr_build_end(
    copr_build_end, pc_build, copr_build, pc_comment_pr_succ, pr_comment_called
):
    steve = SteveJobs()
    flexmock(SteveJobs, _is_private=False)
    flexmock(CoprHelper).should_receive("get_copr_client").and_return(
        Client(
            config={
                "copr_url": "https://copr.fedorainfracloud.org",
                "username": "some-owner",
            }
        )
    )
    flexmock(CoprBuildJobHelper).should_receive("copr_build_model").and_return(
        flexmock()
    )
    pc_build.notifications.pull_request.successful_build = pc_comment_pr_succ
    flexmock(CoprBuildEvent).should_receive("get_package_config").and_return(pc_build)
    flexmock(CoprBuildEndHandler).should_receive(
        "was_last_build_successful"
    ).and_return(False)
    if pr_comment_called:
        flexmock(GithubProject).should_receive("pr_comment")
    else:
        flexmock(GithubProject).should_receive("pr_comment").never()

    flexmock(CoprBuild).should_receive("get_by_build_id").and_return(copr_build)
    flexmock(CoprBuild).should_receive("set_status").with_args("success")
    flexmock(CoprBuildDB).should_receive("get_build").and_return(
        {
            "commit_sha": "XXXXX",
            "pr_id": 24,
            "repo_name": "hello-world",
            "repo_namespace": "packit-service",
            "ref": "XXXX",
            "https_url": "https://github.com/packit-service/hello-world",
        }
    )

    url = get_log_url(1)
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)
    # check if packit-service set correct PR status
    flexmock(StatusReporter).should_receive("report").with_args(
        state=CommitStatus.success,
        description="RPMs were built successfully.",
        url=url,
        check_names=CoprBuildJobHelper.get_build_check(copr_build_end["chroot"]),
    ).once()

    # skip testing farm
    flexmock(CoprBuildJobHelper).should_receive("job_tests").and_return(None)

    steve.process_message(copr_build_end)


def test_copr_build_end_testing_farm(copr_build_end, copr_build):
    steve = SteveJobs()
    flexmock(SteveJobs, _is_private=False)
    flexmock(CoprHelper).should_receive("get_copr_client").and_return(
        Client(
            config={
                "copr_url": "https://copr.fedorainfracloud.org",
                "username": "some-owner",
            }
        )
    )
    flexmock(CoprBuildJobHelper).should_receive("copr_build_model").and_return(
        flexmock()
    )
    config = PackageConfig(
        jobs=[
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                metadata={"targets": ["fedora-rawhide"]},
            ),
            JobConfig(
                type=JobType.tests,
                trigger=JobConfigTriggerType.pull_request,
                metadata={"targets": ["fedora-rawhide"]},
            ),
        ]
    )

    flexmock(CoprBuildEvent).should_receive("get_package_config").and_return(config)
    flexmock(GithubTestingFarmHandler).should_receive(
        "get_package_config_from_repo"
    ).and_return(config)
    flexmock(CoprBuildEndHandler).should_receive(
        "was_last_build_successful"
    ).and_return(False)
    flexmock(GithubProject).should_receive("pr_comment")

    flexmock(LocalProject).should_receive("refresh_the_arguments").and_return(None)

    flexmock(CoprBuild).should_receive("get_by_build_id").and_return(copr_build)
    flexmock(CoprBuild).should_receive("set_status").with_args("success")
    flexmock(CoprBuildDB).should_receive("get_build").and_return(
        {
            "commit_sha": "XXXXX",
            "pr_id": 24,
            "repo_name": "hello-world",
            "repo_namespace": "packit-service",
            "ref": "XXXX",
            "https_url": "https://github.com/packit-service/hello-world",
        }
    )

    url = "https://localhost:5000/copr-build/1/logs"
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)
    # check if packit-service set correct PR status
    flexmock(StatusReporter).should_receive("report").with_args(
        state=CommitStatus.success,
        description="RPMs were built successfully.",
        url=url,
        check_names=EXPECTED_BUILD_CHECK_NAME,
    ).once()

    flexmock(StatusReporter).should_receive("report").with_args(
        state=CommitStatus.pending,
        description="RPMs were built successfully.",
        url=url,
        check_names=EXPECTED_TESTING_FARM_CHECK_NAME,
    ).once()

    flexmock(TestingFarmJobHelper).should_receive(
        "send_testing_farm_request"
    ).and_return(
        RequestResponse(
            status_code=200,
            ok=True,
            content='{"url": "some-url"}'.encode(),
            json={"url": "some-url"},
        )
    )

    flexmock(StatusReporter).should_receive("report").with_args(
        state=CommitStatus.pending,
        description="Build succeeded. Submitting the tests ...",
        check_names=EXPECTED_TESTING_FARM_CHECK_NAME,
        url="",
    ).once()
    flexmock(StatusReporter).should_receive("report").with_args(
        state=CommitStatus.pending,
        description="Tests are running ...",
        url="some-url",
        check_names=EXPECTED_TESTING_FARM_CHECK_NAME,
    ).once()

    steve.process_message(copr_build_end)


def test_copr_build_end_failed_testing_farm(copr_build_end, copr_build):
    steve = SteveJobs()
    flexmock(SteveJobs, _is_private=False)
    flexmock(CoprHelper).should_receive("get_copr_client").and_return(
        Client(
            config={
                "copr_url": "https://copr.fedorainfracloud.org",
                "username": "some-owner",
            }
        )
    )
    flexmock(CoprBuildJobHelper).should_receive("copr_build_model").and_return(
        flexmock()
    )
    config = PackageConfig(
        jobs=[
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                metadata={"targets": ["fedora-rawhide"]},
            ),
            JobConfig(
                type=JobType.tests,
                trigger=JobConfigTriggerType.pull_request,
                metadata={"targets": ["fedora-rawhide"]},
            ),
        ]
    )

    flexmock(CoprBuildEvent).should_receive("get_package_config").and_return(config)
    flexmock(GithubTestingFarmHandler).should_receive(
        "get_package_config_from_repo"
    ).and_return(config)
    flexmock(CoprBuildEndHandler).should_receive(
        "was_last_build_successful"
    ).and_return(False)
    flexmock(GithubProject).should_receive("pr_comment")

    flexmock(LocalProject).should_receive("refresh_the_arguments").and_return(None)

    flexmock(CoprBuild).should_receive("get_by_build_id").and_return(copr_build)
    flexmock(CoprBuild).should_receive("set_status").with_args("success")
    flexmock(CoprBuildDB).should_receive("get_build").and_return(
        {
            "commit_sha": "XXXXX",
            "pr_id": 24,
            "repo_name": "hello-world",
            "repo_namespace": "packit-service",
            "ref": "XXXX",
            "https_url": "https://github.com/packit-service/hello-world",
        }
    )

    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)
    # check if packit-service set correct PR status
    flexmock(StatusReporter).should_receive("report").with_args(
        state=CommitStatus.success,
        description="RPMs were built successfully.",
        url="https://localhost:5000/copr-build/1/logs",
        check_names=EXPECTED_BUILD_CHECK_NAME,
    ).once()

    flexmock(StatusReporter).should_receive("report").with_args(
        state=CommitStatus.pending,
        description="RPMs were built successfully.",
        url="https://localhost:5000/copr-build/1/logs",
        check_names=EXPECTED_TESTING_FARM_CHECK_NAME,
    ).once()

    flexmock(TestingFarmJobHelper).should_receive(
        "send_testing_farm_request"
    ).and_return(
        RequestResponse(
            status_code=400,
            ok=False,
            content='{"message": "some error"}'.encode(),
            json={"message": "some error"},
        )
    )

    flexmock(StatusReporter).should_receive("report").with_args(
        state=CommitStatus.pending,
        description="Build succeeded. Submitting the tests ...",
        check_names=EXPECTED_TESTING_FARM_CHECK_NAME,
        url="",
    ).once()
    flexmock(StatusReporter).should_receive("report").with_args(
        state=CommitStatus.failure,
        description="some error",
        check_names=EXPECTED_TESTING_FARM_CHECK_NAME,
        url="",
    ).once()

    steve.process_message(copr_build_end)


def test_copr_build_end_failed_testing_farm_no_json(copr_build_end, copr_build):
    steve = SteveJobs()
    flexmock(SteveJobs, _is_private=False)
    flexmock(CoprHelper).should_receive("get_copr_client").and_return(
        Client(
            config={
                "copr_url": "https://copr.fedorainfracloud.org",
                "username": "some-owner",
            }
        )
    )
    flexmock(CoprBuildJobHelper).should_receive("copr_build_model").and_return(
        flexmock()
    )
    config = PackageConfig(
        jobs=[
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                metadata={"targets": ["fedora-rawhide"]},
            ),
            JobConfig(
                type=JobType.tests,
                trigger=JobConfigTriggerType.pull_request,
                metadata={"targets": ["fedora-rawhide"]},
            ),
        ]
    )

    flexmock(CoprBuildEvent).should_receive("get_package_config").and_return(config)
    flexmock(GithubTestingFarmHandler).should_receive(
        "get_package_config_from_repo"
    ).and_return(config)
    flexmock(CoprBuildEndHandler).should_receive(
        "was_last_build_successful"
    ).and_return(False)
    flexmock(GithubProject).should_receive("pr_comment")

    flexmock(LocalProject).should_receive("refresh_the_arguments").and_return(None)

    flexmock(CoprBuild).should_receive("get_by_build_id").and_return(copr_build)
    flexmock(CoprBuild).should_receive("set_status").with_args("success")
    flexmock(CoprBuildDB).should_receive("get_build").and_return(
        {
            "commit_sha": "XXXXX",
            "pr_id": 24,
            "repo_name": "hello-world",
            "repo_namespace": "packit-service",
            "ref": "XXXX",
            "https_url": "https://github.com/packit-service/hello-world",
        }
    )

    url = get_log_url(1)
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)
    # check if packit-service set correct PR status
    flexmock(StatusReporter).should_receive("report").with_args(
        state=CommitStatus.success,
        description="RPMs were built successfully.",
        url=url,
        check_names=EXPECTED_BUILD_CHECK_NAME,
    ).once()

    flexmock(StatusReporter).should_receive("report").with_args(
        state=CommitStatus.pending,
        description="RPMs were built successfully.",
        url=url,
        check_names=EXPECTED_TESTING_FARM_CHECK_NAME,
    ).once()

    flexmock(TestingFarmJobHelper).should_receive(
        "send_testing_farm_request"
    ).and_return(
        RequestResponse(
            status_code=400,
            ok=False,
            content="some text error".encode(),
            reason="some text error",
            json=None,
        )
    )

    flexmock(CoprBuild).should_receive("set_status").with_args("failure")
    flexmock(StatusReporter).should_receive("report").with_args(
        state=CommitStatus.pending,
        description="Build succeeded. Submitting the tests ...",
        check_names=EXPECTED_TESTING_FARM_CHECK_NAME,
        url="",
    ).once()
    flexmock(StatusReporter).should_receive("report").with_args(
        state=CommitStatus.failure,
        description="Failed to submit tests: some text error",
        check_names=EXPECTED_TESTING_FARM_CHECK_NAME,
        url="",
    ).once()

    steve.process_message(copr_build_end)


def test_copr_build_start(copr_build_start, pc_build, copr_build):
    steve = SteveJobs()
    flexmock(SteveJobs, _is_private=False)
    flexmock(CoprHelper).should_receive("get_copr_client").and_return(
        Client(
            config={
                "copr_url": "https://copr.fedorainfracloud.org",
                "username": "some-owner",
            }
        )
    )
    flexmock(CoprBuildJobHelper).should_receive("copr_build_model").and_return(
        flexmock()
    )
    flexmock(CoprBuildEvent).should_receive("get_package_config").and_return(pc_build)
    flexmock(CoprBuildJobHelper).should_receive("get_build_check").and_return(
        EXPECTED_BUILD_CHECK_NAME
    )

    flexmock(CoprBuild).should_receive("get_by_build_id").and_return(copr_build)
    flexmock(CoprBuildDB).should_receive("get_build").and_return(
        {
            "commit_sha": "XXXXX",
            "pr_id": 24,
            "repo_name": "hello-world",
            "repo_namespace": "packit-service",
            "ref": "XXXX",
            "https_url": "https://github.com/packit-service/hello-world",
        }
    )

    url = get_log_url(1)
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)

    flexmock(CoprBuild).should_receive("set_status").with_args("pending").once()
    flexmock(CoprBuild).should_receive("set_build_logs_url")
    # check if packit-service set correct PR status
    flexmock(StatusReporter).should_receive("report").with_args(
        state=CommitStatus.pending,
        description="RPM build has started...",
        url=url,
        check_names=EXPECTED_BUILD_CHECK_NAME,
    ).once()

    steve.process_message(copr_build_start)


def test_copr_build_just_tests_defined(copr_build_start, pc_tests, copr_build):
    steve = SteveJobs()
    flexmock(SteveJobs, _is_private=False)
    flexmock(CoprHelper).should_receive("get_copr_client").and_return(
        Client(
            config={
                "copr_url": "https://copr.fedorainfracloud.org",
                "username": "some-owner",
            }
        )
    )
    flexmock(CoprBuildJobHelper).should_receive("copr_build_model").and_return(
        flexmock()
    )
    flexmock(CoprBuildEvent).should_receive("get_package_config").and_return(pc_tests)
    flexmock(TestingFarmJobHelper).should_receive("get_build_check").and_return(
        EXPECTED_BUILD_CHECK_NAME
    )
    flexmock(TestingFarmJobHelper).should_receive("get_test_check").and_return(
        EXPECTED_TESTING_FARM_CHECK_NAME
    )

    flexmock(CoprBuild).should_receive("get_by_build_id").and_return(copr_build)
    flexmock(CoprBuildDB).should_receive("get_build").and_return(
        {
            "commit_sha": "XXXXX",
            "pr_id": 24,
            "repo_name": "hello-world",
            "repo_namespace": "packit-service",
            "ref": "XXXX",
            "https_url": "https://github.com/packit-service/hello-world",
        }
    )

    url = get_log_url(1)
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)

    flexmock(CoprBuild).should_receive("set_status").with_args("pending")
    flexmock(CoprBuild).should_receive("set_build_logs_url")
    # check if packit-service sets the correct PR status
    flexmock(StatusReporter).should_receive("report").with_args(
        state=CommitStatus.pending,
        description="RPM build has started...",
        url=url,
        check_names=EXPECTED_BUILD_CHECK_NAME,
    ).never()

    flexmock(StatusReporter).should_receive("report").with_args(
        state=CommitStatus.pending,
        description="RPM build has started...",
        url=url,
        check_names=TestingFarmJobHelper.get_test_check(copr_build_start["chroot"]),
    ).once()

    steve.process_message(copr_build_start)


def test_copr_build_not_comment_on_success(copr_build_end, pc_build, copr_build):
    steve = SteveJobs()
    flexmock(SteveJobs, _is_private=False)
    flexmock(CoprHelper).should_receive("get_copr_client").and_return(
        Client(
            config={
                "copr_url": "https://copr.fedorainfracloud.org",
                "username": "some-owner",
            }
        )
    )
    flexmock(CoprBuildJobHelper).should_receive("copr_build_model").and_return(
        flexmock()
    )
    flexmock(CoprBuildEvent).should_receive("get_package_config").and_return(pc_build)
    flexmock(CoprBuildJobHelper).should_receive("get_build_check").and_return(
        EXPECTED_BUILD_CHECK_NAME
    )

    flexmock(CoprBuildEndHandler).should_receive(
        "was_last_build_successful"
    ).and_return(True)
    flexmock(GithubProject).should_receive("pr_comment").never()

    flexmock(CoprBuild).should_receive("get_by_build_id").and_return(copr_build)
    flexmock(CoprBuild).should_receive("set_status").with_args("success")
    flexmock(CoprBuildDB).should_receive("get_build").and_return(
        {
            "commit_sha": "XXXXX",
            "pr_id": 24,
            "repo_name": "hello-world",
            "repo_namespace": "packit-service",
            "ref": "XXXX",
            "https_url": "https://github.com/packit-service/hello-world",
        }
    )

    url = get_log_url(1)
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)

    # check if packit-service set correct PR status
    flexmock(StatusReporter).should_receive("report").with_args(
        state=CommitStatus.success,
        description="RPMs were built successfully.",
        url=url,
        check_names=CoprBuildJobHelper.get_build_check(copr_build_end["chroot"]),
    ).once()

    # skip testing farm
    flexmock(CoprBuildJobHelper).should_receive("job_tests").and_return(None)

    steve.process_message(copr_build_end)
