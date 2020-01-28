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
from ogr.services.github import GithubProject
from ogr.utils import RequestResponse
from packit.config import JobConfig, JobType, JobTriggerType
from packit.copr_helper import CoprHelper
from packit.local_project import LocalProject

from packit_service.constants import PACKIT_STG_CHECK, PACKIT_STG_TESTING_FARM_CHECK
from packit_service.service.events import CoprBuildEvent
from packit_service.worker.copr_build import CoprBuildJobHelper
from packit_service.worker.copr_db import CoprBuildDB
from packit_service.worker.fedmsg_handlers import CoprBuildEndHandler
from packit_service.worker.github_handlers import GithubTestingFarmHandler
from packit_service.worker.handler import BuildStatusReporter, PRCheckName
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.testing_farm import TestingFarmJobHelper
from tests.spellbook import DATA_DIR


@pytest.fixture()
def copr_build_start():
    return json.loads((DATA_DIR / "fedmsg" / "copr_build_start.json").read_text())


@pytest.fixture()
def copr_build_end():
    return json.loads((DATA_DIR / "fedmsg" / "copr_build_end.json").read_text())


def test_copr_build_end(copr_build_end):
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
    flexmock(CoprBuildEvent).should_receive("get_package_config").and_return(
        flexmock(
            jobs=[
                JobConfig(
                    job=JobType.copr_build,
                    trigger=JobTriggerType.pull_request,
                    metadata={"targets": ["fedora-all"]},
                )
            ]
        )
    )
    flexmock(PRCheckName).should_receive("get_build_check").and_return(PACKIT_STG_CHECK)
    flexmock(CoprBuildEndHandler).should_receive(
        "was_last_build_successful"
    ).and_return(False)
    flexmock(GithubProject).should_receive("pr_comment")

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

    url = (
        f"https://copr-be.cloud.fedoraproject.org/results/"
        f"packit/packit-service-hello-world-24-stg/fedora-rawhide-x86_64/"
        f"01044215-hello/builder-live.log"
    )
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)
    # check if packit-service set correct PR status
    flexmock(BuildStatusReporter).should_receive("report").with_args(
        state="success",
        description="RPMs were built successfully.",
        url=url,
        check_names=PACKIT_STG_CHECK,
    ).once()

    # skip testing farm
    flexmock(CoprBuildJobHelper).should_receive("job_tests").and_return(None)

    steve.process_message(copr_build_end)


def test_copr_build_end_testing_farm(copr_build_end):
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
    config = flexmock(
        jobs=[
            JobConfig(
                job=JobType.copr_build,
                trigger=JobTriggerType.pull_request,
                metadata={"targets": ["fedora-rawhide"]},
            ),
            JobConfig(
                job=JobType.tests,
                trigger=JobTriggerType.pull_request,
                metadata={"targets": ["fedora-rawhide"]},
            ),
        ]
    )

    flexmock(CoprBuildEvent).should_receive("get_package_config").and_return(config)
    flexmock(GithubTestingFarmHandler).should_receive(
        "get_package_config_from_repo"
    ).and_return(config)
    flexmock(PRCheckName).should_receive("get_build_check").and_return(PACKIT_STG_CHECK)
    flexmock(CoprBuildEndHandler).should_receive(
        "was_last_build_successful"
    ).and_return(False)
    flexmock(GithubProject).should_receive("pr_comment")

    flexmock(LocalProject).should_receive("refresh_the_arguments").and_return(None)

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

    url = (
        f"https://copr-be.cloud.fedoraproject.org/results/"
        f"packit/packit-service-hello-world-24-stg/fedora-rawhide-x86_64/"
        f"01044215-hello/builder-live.log"
    )
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)
    # check if packit-service set correct PR status
    flexmock(BuildStatusReporter).should_receive("report").with_args(
        state="success",
        description="RPMs were built successfully.",
        url=url,
        check_names=PACKIT_STG_CHECK,
    ).once()

    flexmock(BuildStatusReporter).should_receive("report").with_args(
        state="success",
        description="RPMs were built successfully.",
        url=url,
        check_names=f"{PACKIT_STG_TESTING_FARM_CHECK}-fedora-rawhide-x86_64",
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

    flexmock(BuildStatusReporter).should_receive("report").with_args(
        state="pending",
        description="Build succeeded. Submitting the tests ...",
        check_names=f"{PACKIT_STG_TESTING_FARM_CHECK}-fedora-rawhide-x86_64",
    ).once()
    flexmock(BuildStatusReporter).should_receive("report").with_args(
        state="pending",
        description="Tests are running ...",
        url="some-url",
        check_names=f"{PACKIT_STG_TESTING_FARM_CHECK}-fedora-rawhide-x86_64",
    ).once()

    steve.process_message(copr_build_end)


def test_copr_build_end_failed_testing_farm(copr_build_end):
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
    config = flexmock(
        jobs=[
            JobConfig(
                job=JobType.copr_build,
                trigger=JobTriggerType.pull_request,
                metadata={"targets": ["fedora-rawhide"]},
            ),
            JobConfig(
                job=JobType.tests,
                trigger=JobTriggerType.pull_request,
                metadata={"targets": ["fedora-rawhide"]},
            ),
        ]
    )

    flexmock(CoprBuildEvent).should_receive("get_package_config").and_return(config)
    flexmock(GithubTestingFarmHandler).should_receive(
        "get_package_config_from_repo"
    ).and_return(config)
    flexmock(PRCheckName).should_receive("get_build_check").and_return(PACKIT_STG_CHECK)
    flexmock(CoprBuildEndHandler).should_receive(
        "was_last_build_successful"
    ).and_return(False)
    flexmock(GithubProject).should_receive("pr_comment")

    flexmock(LocalProject).should_receive("refresh_the_arguments").and_return(None)

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

    url = (
        f"https://copr-be.cloud.fedoraproject.org/results/"
        f"packit/packit-service-hello-world-24-stg/fedora-rawhide-x86_64/"
        f"01044215-hello/builder-live.log"
    )
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)
    # check if packit-service set correct PR status
    flexmock(BuildStatusReporter).should_receive("report").with_args(
        state="success",
        description="RPMs were built successfully.",
        url=url,
        check_names=PACKIT_STG_CHECK,
    ).once()

    flexmock(BuildStatusReporter).should_receive("report").with_args(
        state="success",
        description="RPMs were built successfully.",
        url=url,
        check_names=f"{PACKIT_STG_TESTING_FARM_CHECK}-fedora-rawhide-x86_64",
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

    flexmock(BuildStatusReporter).should_receive("report").with_args(
        state="pending",
        description="Build succeeded. Submitting the tests ...",
        check_names=f"{PACKIT_STG_TESTING_FARM_CHECK}-fedora-rawhide-x86_64",
    ).once()
    flexmock(BuildStatusReporter).should_receive("report").with_args(
        state="failure",
        description="some error",
        check_names=f"{PACKIT_STG_TESTING_FARM_CHECK}-fedora-rawhide-x86_64",
    ).once()

    steve.process_message(copr_build_end)


def test_copr_build_end_failed_testing_farm_no_json(copr_build_end):
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
    config = flexmock(
        jobs=[
            JobConfig(
                job=JobType.copr_build,
                trigger=JobTriggerType.pull_request,
                metadata={"targets": ["fedora-rawhide"]},
            ),
            JobConfig(
                job=JobType.tests,
                trigger=JobTriggerType.pull_request,
                metadata={"targets": ["fedora-rawhide"]},
            ),
        ]
    )

    flexmock(CoprBuildEvent).should_receive("get_package_config").and_return(config)
    flexmock(GithubTestingFarmHandler).should_receive(
        "get_package_config_from_repo"
    ).and_return(config)
    flexmock(PRCheckName).should_receive("get_build_check").and_return(PACKIT_STG_CHECK)
    flexmock(CoprBuildEndHandler).should_receive(
        "was_last_build_successful"
    ).and_return(False)
    flexmock(GithubProject).should_receive("pr_comment")

    flexmock(LocalProject).should_receive("refresh_the_arguments").and_return(None)

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

    url = (
        f"https://copr-be.cloud.fedoraproject.org/results/"
        f"packit/packit-service-hello-world-24-stg/fedora-rawhide-x86_64/"
        f"01044215-hello/builder-live.log"
    )
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)
    # check if packit-service set correct PR status
    flexmock(BuildStatusReporter).should_receive("report").with_args(
        state="success",
        description="RPMs were built successfully.",
        url=url,
        check_names=PACKIT_STG_CHECK,
    ).once()

    flexmock(BuildStatusReporter).should_receive("report").with_args(
        state="success",
        description="RPMs were built successfully.",
        url=url,
        check_names=f"{PACKIT_STG_TESTING_FARM_CHECK}-fedora-rawhide-x86_64",
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

    flexmock(BuildStatusReporter).should_receive("report").with_args(
        state="pending",
        description="Build succeeded. Submitting the tests ...",
        check_names=f"{PACKIT_STG_TESTING_FARM_CHECK}-fedora-rawhide-x86_64",
    ).once()
    flexmock(BuildStatusReporter).should_receive("report").with_args(
        state="failure",
        description="Failed to submit tests: some text error",
        check_names=f"{PACKIT_STG_TESTING_FARM_CHECK}-fedora-rawhide-x86_64",
    ).once()

    steve.process_message(copr_build_end)


def test_copr_build_start(copr_build_start):
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
    flexmock(CoprBuildEvent).should_receive("get_package_config").and_return(
        flexmock(
            jobs=[
                JobConfig(
                    job=JobType.copr_build,
                    trigger=JobTriggerType.pull_request,
                    metadata={"targets": ["fedora-all"]},
                )
            ]
        )
    )
    flexmock(PRCheckName).should_receive("get_build_check").and_return(PACKIT_STG_CHECK)

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

    url = (
        f"https://copr-be.cloud.fedoraproject.org/results/"
        f"packit/packit-service-hello-world-24-stg/fedora-rawhide-x86_64/"
        f"01044215-hello/builder-live.log"
    )
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)

    # check if packit-service set correct PR status
    flexmock(BuildStatusReporter).should_receive("report").with_args(
        state="pending",
        description="RPM build has started...",
        url=url,
        check_names=PACKIT_STG_CHECK,
    ).once()

    steve.process_message(copr_build_start)


def test_copr_build_just_tests_defined(copr_build_start):
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
    flexmock(CoprBuildEvent).should_receive("get_package_config").and_return(
        flexmock(
            jobs=[
                JobConfig(
                    job=JobType.tests,
                    trigger=JobTriggerType.pull_request,
                    metadata={"targets": ["fedora-all"]},
                )
            ]
        )
    )
    flexmock(PRCheckName).should_receive("get_build_check").and_return(PACKIT_STG_CHECK)
    flexmock(PRCheckName).should_receive("get_testing_farm_check").and_return(
        PACKIT_STG_TESTING_FARM_CHECK
    )

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

    url = (
        f"https://copr-be.cloud.fedoraproject.org/results/"
        f"packit/packit-service-hello-world-24-stg/fedora-rawhide-x86_64/"
        f"01044215-hello/builder-live.log"
    )
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)

    # check if packit-service set correct PR status
    flexmock(BuildStatusReporter).should_receive("report").with_args(
        state="pending",
        description="RPM build has started...",
        url=url,
        check_names=PACKIT_STG_CHECK,
    ).never()

    flexmock(BuildStatusReporter).should_receive("report").with_args(
        state="pending",
        description="RPM build has started...",
        url=url,
        check_names=PACKIT_STG_TESTING_FARM_CHECK,
    ).once()

    steve.process_message(copr_build_start)


def test_copr_build_not_comment_on_success(copr_build_end):
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
    flexmock(CoprBuildEvent).should_receive("get_package_config").and_return(
        flexmock(
            jobs=[
                JobConfig(
                    job=JobType.copr_build,
                    trigger=JobTriggerType.pull_request,
                    metadata={"targets": ["fedora-all"]},
                )
            ]
        )
    )
    flexmock(PRCheckName).should_receive("get_build_check").and_return(PACKIT_STG_CHECK)

    flexmock(CoprBuildEndHandler).should_receive(
        "was_last_build_successful"
    ).and_return(True)
    flexmock(GithubProject).should_receive("pr_comment").never()

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

    url = (
        f"https://copr-be.cloud.fedoraproject.org/results/"
        f"packit/packit-service-hello-world-24-stg/fedora-rawhide-x86_64/"
        f"01044215-hello/builder-live.log"
    )
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)

    # check if packit-service set correct PR status
    flexmock(BuildStatusReporter).should_receive("report").with_args(
        state="success",
        description="RPMs were built successfully.",
        url=url,
        check_names=PACKIT_STG_CHECK,
    ).once()

    # skip testing farm
    flexmock(CoprBuildJobHelper).should_receive("job_tests").and_return(None)

    steve.process_message(copr_build_end)
