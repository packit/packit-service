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
from packit.config import JobConfig, JobType, JobTriggerType
from packit.copr_helper import CoprHelper

from packit_service.constants import PACKIT_STG_CHECK
from packit_service.service.events import CoprBuildEvent
from packit_service.worker.copr_build import CoprBuildJobHelper
from packit_service.worker.copr_db import CoprBuildDB
from packit_service.worker.fedmsg_handlers import CoprBuildEndHandler
from packit_service.worker.handler import BuildStatusReporter, PRCheckName
from packit_service.worker.jobs import SteveJobs
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
                    metadata={},
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
        f"01044215-hello/builder-live.log.gz"
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
                    metadata={},
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
        f"01044215-hello/builder-live.log.gz"
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
                    metadata={},
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
        f"01044215-hello/builder-live.log.gz"
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
