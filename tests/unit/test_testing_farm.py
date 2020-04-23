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
from packit_service.config import PackageConfigGetterForGithub
from packit_service.models import TFTTestRunModel

# These names are definately not nice, still they help with making classes
# whose names start with Testing* or Test* to become invisible for pytest,
# and so stop the test discovery warnings.
from packit_service.service.events import (
    TestingFarmResultsEvent as TFResultsEvent,
    TestingFarmResult as TFResult,
    TestResult as TResult,
)
from packit_service.worker.handlers import TestingFarmResultsHandler as TFResultsHandler
from packit_service.worker.reporting import StatusReporter
from packit_service.worker.testing_farm import TestingFarmJobHelper as TFJobHelper


@pytest.mark.parametrize(
    "tests_result,tests_message,tests_tests,status_status,status_message",
    [
        pytest.param(
            TFResult.passed,
            "some message",
            [
                TResult(
                    name="/install/copr-build",
                    result=TFResult.passed,
                    log_url="some specific url",
                )
            ],
            CommitStatus.success,
            "Installation passed",
            id="only_instalation_passed",
        ),
        pytest.param(
            TFResult.failed,
            "some message",
            [
                TResult(
                    name="/install/copr-build",
                    result=TFResult.failed,
                    log_url="some specific url",
                )
            ],
            CommitStatus.failure,
            "Installation failed",
            id="only_instalation_failed",
        ),
        pytest.param(
            TFResult.passed,
            "some message",
            [
                TResult(
                    name="/something/different",
                    result=TFResult.passed,
                    log_url="some specific url",
                )
            ],
            CommitStatus.success,
            "some message",
            id="only_instalation_not_provided_passed",
        ),
        pytest.param(
            TFResult.failed,
            "some message",
            [
                TResult(
                    name="/something/different",
                    result=TFResult.failed,
                    log_url="some specific url",
                )
            ],
            CommitStatus.failure,
            "some message",
            id="only_instalation_not_provided_failed",
        ),
        pytest.param(
            TFResult.passed,
            "some message",
            [
                TResult(
                    name="/install/copr-build",
                    result=TFResult.passed,
                    log_url="some specific url",
                ),
                TResult(
                    name="/different/test",
                    result=TFResult.passed,
                    log_url="some specific url",
                ),
            ],
            CommitStatus.success,
            "some message",
            id="only_instalation_mutliple_results_passed",
        ),
        pytest.param(
            TFResult.failed,
            "some message",
            [
                TResult(
                    name="/install/copr-build",
                    result=TFResult.failed,
                    log_url="some specific url",
                ),
                TResult(
                    name="/different/test",
                    result=TFResult.passed,
                    log_url="some specific url",
                ),
            ],
            CommitStatus.failure,
            "some message",
            id="only_instalation_mutliple_results_failed",
        ),
        pytest.param(
            TFResult.failed,
            "some message",
            [
                TResult(
                    name="/install/copr-build",
                    result=TFResult.passed,
                    log_url="some specific url",
                ),
                TResult(
                    name="/different/test",
                    result=TFResult.failed,
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
    flexmock(PackageConfigGetterForGithub).should_receive(
        "get_package_config_from_repo"
    ).and_return(
        flexmock(
            jobs=[
                JobConfig(
                    type=JobType.copr_build, trigger=JobConfigTriggerType.pull_request,
                )
            ],
        )
    )
    test_farm_handler = TFResultsHandler(
        config=flexmock(command_handler_work_dir=flexmock()),
        job_config=flexmock(),
        event=TFResultsEvent(
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
            project_url="https://github.com/packit-service/ogr",
            commit_sha=flexmock(),
        ),
    )
    flexmock(StatusReporter).should_receive("report").with_args(
        state=status_status,
        description=status_message,
        url="some url",
        check_names="packit-stg/testing-farm-fedora-rawhide-x86_64",
    )

    tft_test_run_model = flexmock()
    tft_test_run_model.should_receive("set_status").with_args(
        tests_result
    ).and_return().once()
    tft_test_run_model.should_receive("set_web_url").with_args(
        "some url"
    ).and_return().once()

    flexmock(TFTTestRunModel).should_receive("get_by_pipeline_id").and_return(
        tft_test_run_model
    )

    flexmock(LocalProject).should_receive("refresh_the_arguments").and_return(None)
    test_farm_handler.run()


@pytest.mark.parametrize(
    (
        "tf_token,"
        "ps_deployment,"
        "repo,"
        "namespace,"
        "commit_sha,"
        "project_url,"
        "git_ref,"
        "copr_owner,"
        "copr_project,"
        "pipeline_id,"
        "chroot"
    ),
    [
        (
            "very-secret",
            "test",
            "packit",
            "packit-service",
            "feb41e5",
            "https://github.com/packit-service/packit",
            "master",
            "me",
            "cool-project",
            "9daccabb-4bfa-4f2d-b7cb-96471dbff607",
            "centos-stream-x86_64",
        ),
    ],
)
def test_trigger_payload(
    tf_token,
    ps_deployment,
    repo,
    namespace,
    commit_sha,
    project_url,
    git_ref,
    copr_owner,
    copr_project,
    pipeline_id,
    chroot,
):
    # Soo many things are happening in a single constructor!!!!
    config = flexmock(
        testing_farm_secret=tf_token,
        deployment=ps_deployment,
        command_handler_work_dir="/tmp",
    )
    package_config = flexmock(jobs=[])
    project = flexmock(
        repo=repo,
        namespace=namespace,
        service="GitHub",
        get_git_urls=lambda: {"git": f"{project_url}.git"},
    )
    event = flexmock(
        commit_sha=commit_sha, project_url=project_url, git_ref=git_ref, pr_id=None
    )

    job_helper = TFJobHelper(config, package_config, project, event)
    job_helper = flexmock(job_helper)

    job_helper.should_receive("job_owner").and_return(copr_owner)
    job_helper.should_receive("job_project").and_return(copr_project)
    payload = job_helper._trigger_payload(pipeline_id, chroot)

    assert payload["pipeline"]["id"] == pipeline_id
    assert payload["api"]["token"] == tf_token
    assert "packit.dev/api" in payload["response-url"]
    assert payload["artifact"] == {
        "repo-name": repo,
        "repo-namespace": namespace,
        "copr-repo-name": f"{copr_owner}/{copr_project}",
        "copr-chroot": chroot,
        "commit-sha": commit_sha,
        "git-url": project_url,
        "git-ref": git_ref,
    }
