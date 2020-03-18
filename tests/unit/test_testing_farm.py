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
import pytest
from flexmock import flexmock
from ogr.abstract import CommitStatus
from packit.config import JobConfig, JobType, JobConfigTriggerType
from packit.local_project import LocalProject

from packit_service.service.events import (
    TestingFarmResultsEvent,
    TestingFarmResult,
    TestResult,
)
from packit_service.worker.handlers import TestingFarmResultsHandler
from packit_service.worker.reporting import StatusReporter


@pytest.mark.parametrize(
    "tests_result,tests_message,tests_tests,status_status,status_message",
    [
        pytest.param(
            TestingFarmResult.passed,
            "some message",
            [
                TestResult(
                    name="/install/copr-build",
                    result=TestingFarmResult.passed,
                    log_url="some specific url",
                )
            ],
            CommitStatus.success,
            "Installation passed",
            id="only_instalation_passed",
        ),
        pytest.param(
            TestingFarmResult.failed,
            "some message",
            [
                TestResult(
                    name="/install/copr-build",
                    result=TestingFarmResult.failed,
                    log_url="some specific url",
                )
            ],
            CommitStatus.failure,
            "Installation failed",
            id="only_instalation_failed",
        ),
        pytest.param(
            TestingFarmResult.passed,
            "some message",
            [
                TestResult(
                    name="/something/different",
                    result=TestingFarmResult.passed,
                    log_url="some specific url",
                )
            ],
            CommitStatus.success,
            "some message",
            id="only_instalation_not_provided_passed",
        ),
        pytest.param(
            TestingFarmResult.failed,
            "some message",
            [
                TestResult(
                    name="/something/different",
                    result=TestingFarmResult.failed,
                    log_url="some specific url",
                )
            ],
            CommitStatus.failure,
            "some message",
            id="only_instalation_not_provided_failed",
        ),
        pytest.param(
            TestingFarmResult.passed,
            "some message",
            [
                TestResult(
                    name="/install/copr-build",
                    result=TestingFarmResult.passed,
                    log_url="some specific url",
                ),
                TestResult(
                    name="/different/test",
                    result=TestingFarmResult.passed,
                    log_url="some specific url",
                ),
            ],
            CommitStatus.success,
            "some message",
            id="only_instalation_mutliple_results_passed",
        ),
        pytest.param(
            TestingFarmResult.failed,
            "some message",
            [
                TestResult(
                    name="/install/copr-build",
                    result=TestingFarmResult.failed,
                    log_url="some specific url",
                ),
                TestResult(
                    name="/different/test",
                    result=TestingFarmResult.passed,
                    log_url="some specific url",
                ),
            ],
            CommitStatus.failure,
            "some message",
            id="only_instalation_mutliple_results_failed",
        ),
        pytest.param(
            TestingFarmResult.failed,
            "some message",
            [
                TestResult(
                    name="/install/copr-build",
                    result=TestingFarmResult.passed,
                    log_url="some specific url",
                ),
                TestResult(
                    name="/different/test",
                    result=TestingFarmResult.failed,
                    log_url="some specific url",
                ),
            ],
            CommitStatus.failure,
            "some message",
            id="only_instalation_mutliple_results_failed_different",
        ),
    ],
)
def test_testing_farm_response(
    tests_result, tests_message, tests_tests, status_status, status_message
):
    flexmock(TestingFarmResultsHandler).should_receive(
        "get_package_config_from_repo"
    ).and_return(
        flexmock(
            jobs=[
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata={},
                )
            ],
        )
    )
    test_farm_handler = TestingFarmResultsHandler(
        config=flexmock(command_handler_work_dir=flexmock()),
        job_config=flexmock(),
        event=TestingFarmResultsEvent(
            pipeline_id="id",
            result=tests_result,
            environment=flexmock(),
            message=tests_message,
            log_url="some url",
            copr_repo_name=flexmock(),
            copr_chroot="fedora-rawhide-x86_64",
            tests=tests_tests,
            repo_namespace=flexmock(),
            repo_name=flexmock(),
            git_ref=flexmock(),
            https_url=flexmock(),
            commit_sha=flexmock(),
        ),
    )
    flexmock(StatusReporter).should_receive("report").with_args(
        state=status_status,
        description=status_message,
        url="some url",
        check_names="packit-stg/testing-farm-fedora-rawhide-x86_64",
    )

    flexmock(LocalProject).should_receive("refresh_the_arguments").and_return(None)
    test_farm_handler.run()
