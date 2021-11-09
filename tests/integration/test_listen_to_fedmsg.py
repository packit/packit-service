# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json
from datetime import datetime

import pytest
import requests
from celery.canvas import Signature
from flexmock import flexmock
from ogr.services.github import GithubProject
from ogr.utils import RequestResponse
from packit.config import JobConfig, JobType, JobConfigTriggerType
from packit.config.job_config import JobMetadataConfig
from packit.config.package_config import PackageConfig
from packit.local_project import LocalProject

import packit_service
from packit_service.config import PackageConfigGetter, ServiceConfig
from packit_service.models import (
    CoprBuildModel,
    TestingFarmResult,
    TFTTestRunModel,
    JobTriggerModelType,
    KojiBuildModel,
)
from packit_service.worker.events import AbstractCoprBuildEvent, KojiBuildEvent
import packit_service.service.urls as urls
from packit_service.service.urls import (
    get_copr_build_info_url,
    get_koji_build_info_url,
)
from packit_service.worker.build.copr_build import CoprBuildJobHelper
from packit_service.worker.handlers import CoprBuildEndHandler, TestingFarmHandler
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.reporting import StatusReporter, BaseCommitStatus
from packit_service.worker.tasks import (
    run_koji_build_report_handler,
    run_copr_build_end_handler,
    run_copr_build_start_handler,
    run_testing_farm_handler,
)
from packit_service.worker.testing_farm import TestingFarmJobHelper
from tests.conftest import copr_build_model
from tests.spellbook import DATA_DIR, first_dict_value, get_parameters_from_results

CHROOT = "fedora-rawhide-x86_64"
EXPECTED_BUILD_CHECK_NAME = f"packit-stg/rpm-build-{CHROOT}"
EXPECTED_TESTING_FARM_CHECK_NAME = f"packit-stg/testing-farm-{CHROOT}"

pytestmark = pytest.mark.usefixtures("mock_get_valid_build_targets")


@pytest.fixture
def mock_get_valid_build_targets():
    flexmock(packit_service.worker.build.copr_build).should_receive(
        "get_valid_build_targets"
    ).and_return(
        {
            "fedora-33-x86_64",
            "fedora-32-x86_64",
            "fedora-31-x86_64",
            "fedora-rawhide-x86_64",
        }
    )


@pytest.fixture(scope="module")
def copr_build_start():
    return json.loads((DATA_DIR / "fedmsg" / "copr_build_start.json").read_text())


@pytest.fixture(scope="module")
def copr_build_end():
    return json.loads((DATA_DIR / "fedmsg" / "copr_build_end.json").read_text())


@pytest.fixture(scope="module")
def koji_build_scratch_start():
    return json.loads(
        (DATA_DIR / "fedmsg" / "koji_build_scratch_start.json").read_text()
    )


@pytest.fixture(scope="module")
def koji_build_scratch_end():
    return json.loads((DATA_DIR / "fedmsg" / "koji_build_scratch_end.json").read_text())


@pytest.fixture(scope="module")
def pc_build_pr():
    return PackageConfig(
        jobs=[
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                metadata=JobMetadataConfig(_targets=["fedora-all"]),
            )
        ]
    )


@pytest.fixture(scope="module")
def pc_koji_build_pr():
    return PackageConfig(
        jobs=[
            JobConfig(
                type=JobType.production_build,
                trigger=JobConfigTriggerType.pull_request,
                metadata=JobMetadataConfig(_targets=["fedora-all"]),
            )
        ]
    )


@pytest.fixture(scope="module")
def pc_build_push():
    return PackageConfig(
        jobs=[
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.commit,
                metadata=JobMetadataConfig(_targets=["fedora-all"]),
            )
        ]
    )


@pytest.fixture(scope="module")
def pc_build_release():
    return PackageConfig(
        jobs=[
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.release,
                metadata=JobMetadataConfig(_targets=["fedora-all"]),
            )
        ]
    )


@pytest.fixture(scope="module")
def pc_tests():
    return PackageConfig(
        jobs=[
            JobConfig(
                type=JobType.tests,
                trigger=JobConfigTriggerType.pull_request,
                metadata=JobMetadataConfig(_targets=["fedora-all"]),
            )
        ]
    )


@pytest.fixture(scope="module")
def copr_build_branch_push():
    return copr_build_model(
        job_config_trigger_type=JobConfigTriggerType.commit,
        job_trigger_model_type=JobTriggerModelType.branch_push,
        name="build-branch",
        task_accepted_time=datetime.now(),
    )


@pytest.fixture(scope="module")
def copr_build_release():
    return copr_build_model(
        job_config_trigger_type=JobConfigTriggerType.release,
        job_trigger_model_type=JobTriggerModelType.release,
        tag_name="v1.0.1",
        commit_hash="0011223344",
        task_accepted_time=datetime.now(),
    )


@pytest.mark.parametrize(
    "pc_comment_pr_succ,pr_comment_called",
    (
        (True, True),
        (False, False),
    ),
)
def test_copr_build_end(
    copr_build_end,
    pc_build_pr,
    copr_build_pr,
    pc_comment_pr_succ,
    pr_comment_called,
):
    pr = flexmock(source_project=flexmock())
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    pc_build_pr.jobs[0].notifications.pull_request.successful_build = pc_comment_pr_succ
    flexmock(AbstractCoprBuildEvent).should_receive("get_package_config").and_return(
        pc_build_pr
    )
    flexmock(CoprBuildEndHandler).should_receive(
        "was_last_packit_comment_with_congratulation"
    ).and_return(False)
    if pr_comment_called:
        pr.should_receive("comment")
    else:
        pr.should_receive("comment").never()
    flexmock(CoprBuildModel).should_receive("get_by_build_id").and_return(copr_build_pr)
    copr_build_pr.should_call("set_status").with_args("success").once()
    copr_build_pr.should_receive("set_end_time").once()

    url = get_copr_build_info_url(1)
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)
    # check if packit-service set correct PR status
    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.success,
        description="RPMs were built successfully.",
        url=url,
        check_names=CoprBuildJobHelper.get_build_check(copr_build_end["chroot"]),
    ).once()

    # no test job defined => testing farm should be skipped
    flexmock(TestingFarmJobHelper).should_receive("run_testing_farm").times(0)
    flexmock(Signature).should_receive("apply_async").once()

    # fix SRPM url since it touches multiple classes

    (
        flexmock(CoprBuildJobHelper)
        .should_receive("get_build")
        .with_args(1044215)
        .and_return(flexmock(source_package={"url": "https://my.host/my.srpm"}))
        .at_least()
        .once()
    )
    flexmock(copr_build_pr._srpm_build_for_mocking).should_receive("set_url").with_args(
        "https://my.host/my.srpm"
    ).mock()

    flexmock(Pushgateway).should_receive("push").once().and_return()

    processing_results = SteveJobs().process_message(copr_build_end)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)

    flexmock(CoprBuildJobHelper).should_receive("get_built_packages").and_return([])

    run_copr_build_end_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )


def test_copr_build_end_push(copr_build_end, pc_build_push, copr_build_branch_push):
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("get_pr").and_return(
        # we cannot comment for branch push events
        flexmock(source_project=flexmock())
        .should_receive("comment")
        .never()
    )
    flexmock(AbstractCoprBuildEvent).should_receive("get_package_config").and_return(
        pc_build_push
    )
    flexmock(CoprBuildEndHandler).should_receive(
        "was_last_packit_comment_with_congratulation"
    ).and_return(False)

    flexmock(CoprBuildModel).should_receive("get_by_build_id").and_return(
        copr_build_branch_push
    )

    copr_build_branch_push.should_receive("set_status").with_args("success")
    copr_build_branch_push.should_receive("set_end_time").once()
    url = get_copr_build_info_url(1)
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)
    # check if packit-service set correct PR status
    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.success,
        description="RPMs were built successfully.",
        url=url,
        check_names=CoprBuildJobHelper.get_build_check(copr_build_end["chroot"]),
    ).once()

    # skip testing farm
    flexmock(CoprBuildJobHelper).should_receive("job_tests").and_return(None)
    flexmock(Signature).should_receive("apply_async").once()

    # skip SRPM url since it touches multiple classes
    flexmock(CoprBuildEndHandler).should_receive("set_srpm_url").and_return(None)

    flexmock(Pushgateway).should_receive("push").once().and_return()

    processing_results = SteveJobs().process_message(copr_build_end)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)

    flexmock(CoprBuildJobHelper).should_receive("get_built_packages").and_return([])

    run_copr_build_end_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )


def test_copr_build_end_release(copr_build_end, pc_build_release, copr_build_release):
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("get_pr").and_return(
        # we cannot comment for branch push events
        flexmock(source_project=flexmock())
        .should_receive("comment")
        .never()
        .mock()
    )
    flexmock(AbstractCoprBuildEvent).should_receive("get_package_config").and_return(
        pc_build_release
    )
    flexmock(CoprBuildEndHandler).should_receive(
        "was_last_packit_comment_with_congratulation"
    ).and_return(False)

    flexmock(CoprBuildModel).should_receive("get_by_build_id").and_return(
        copr_build_release
    )
    copr_build_release.should_receive("set_status").with_args("success")
    copr_build_release.should_receive("set_end_time").once()
    url = get_copr_build_info_url(1)
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)
    # check if packit-service set correct PR status
    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.success,
        description="RPMs were built successfully.",
        url=url,
        check_names=CoprBuildJobHelper.get_build_check(copr_build_end["chroot"]),
    ).once()

    # skip testing farm
    flexmock(CoprBuildJobHelper).should_receive("job_tests").and_return(None)
    flexmock(Signature).should_receive("apply_async").once()

    # skip SRPM url since it touches multiple classes
    flexmock(CoprBuildEndHandler).should_receive("set_srpm_url").and_return(None)

    flexmock(Pushgateway).should_receive("push").once().and_return()

    processing_results = SteveJobs().process_message(copr_build_end)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)

    flexmock(CoprBuildJobHelper).should_receive("get_built_packages").and_return([])

    run_copr_build_end_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )


def test_copr_build_end_testing_farm(copr_build_end, copr_build_pr):
    tft_api_url = "https://api.dev.testing-farm.io/v0.1/"
    service_config = ServiceConfig(
        testing_farm_api_url=tft_api_url, testing_farm_secret="secret token"
    )
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(
        service_config
    )
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("get_pr").and_return(
        flexmock(
            source_project=flexmock(get_web_url=lambda: "https://github.com/foo/bar")
        )
        .should_receive("comment")
        .mock()
    )
    urls.DASHBOARD_URL = "https://dashboard.localhost"

    config = PackageConfig(
        jobs=[
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                metadata=JobMetadataConfig(
                    _targets=["fedora-rawhide"],
                    owner="some-owner",
                    project="some-project",
                ),
            ),
            JobConfig(
                type=JobType.tests,
                trigger=JobConfigTriggerType.pull_request,
                metadata=JobMetadataConfig(_targets=["fedora-rawhide"]),
            ),
        ]
    )

    flexmock(AbstractCoprBuildEvent).should_receive("get_package_config").and_return(
        config
    )
    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo"
    ).and_return(config)
    flexmock(CoprBuildEndHandler).should_receive(
        "was_last_packit_comment_with_congratulation"
    ).and_return(False)

    flexmock(LocalProject).should_receive("refresh_the_arguments").and_return(None)

    flexmock(CoprBuildModel).should_receive("get_by_build_id").and_return(copr_build_pr)
    flexmock(CoprBuildModel).should_receive("get_by_id").and_return(copr_build_pr)
    copr_build_pr.should_call("set_status").with_args("success").once()
    copr_build_pr.should_receive("set_end_time").once()
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)
    # check if packit-service set correct PR status
    url = get_copr_build_info_url(1)
    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.success,
        description="RPMs were built successfully.",
        url=url,
        check_names=EXPECTED_BUILD_CHECK_NAME,
    ).once()

    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.pending,
        description="RPMs were built successfully.",
        url=url,
        check_names=EXPECTED_TESTING_FARM_CHECK_NAME,
    ).once()

    payload = {
        "api_key": "secret token",
        "test": {
            "fmf": {
                "url": "https://github.com/foo/bar",
                "ref": "0011223344",
            },
        },
        "environments": [
            {
                "arch": "x86_64",
                "os": {"compose": "Fedora-Rawhide"},
                "tmt": {
                    "context": {
                        "distro": "fedora-rawhide",
                        "arch": "x86_64",
                        "trigger": "commit",
                    }
                },
                "artifacts": [
                    {
                        "id": "1:fedora-rawhide-x86_64",
                        "type": "fedora-copr-build",
                    },
                ],
            }
        ],
        "notification": {
            "webhook": {
                "url": "https://stg.packit.dev/api/testing-farm/results",
                "token": "secret token",
            }
        },
    }

    flexmock(TestingFarmJobHelper).should_receive("is_fmf_configured").and_return(True)
    flexmock(TestingFarmJobHelper).should_receive("distro2compose").with_args(
        "fedora-rawhide", "x86_64"
    ).and_return("Fedora-Rawhide")

    pipeline_id = "5e8079d8-f181-41cf-af96-28e99774eb68"
    flexmock(TestingFarmJobHelper).should_receive(
        "send_testing_farm_request"
    ).with_args(endpoint="requests", method="POST", data=payload).and_return(
        RequestResponse(
            status_code=200,
            ok=True,
            content=json.dumps({"id": pipeline_id}).encode(),
            json={"id": pipeline_id},
        )
    )

    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.running,
        description="Build succeeded. Submitting the tests ...",
        check_names=EXPECTED_TESTING_FARM_CHECK_NAME,
        url="",
    ).once()

    flexmock(GithubProject).should_receive("get_web_url").and_return(
        "https://github.com/foo/bar"
    )

    tft_test_run_model = flexmock(id=5)
    flexmock(TFTTestRunModel).should_receive("create").with_args(
        pipeline_id=pipeline_id,
        commit_sha="0011223344",
        status=TestingFarmResult.new,
        target="fedora-rawhide-x86_64",
        web_url=None,
        run_model=copr_build_pr.runs[0],
        data={"base_project_url": "https://github.com/foo/bar"},
    ).and_return(tft_test_run_model)

    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.running,
        description="Tests have been submitted ...",
        url="https://dashboard.localhost/results/testing-farm/5",
        check_names=EXPECTED_TESTING_FARM_CHECK_NAME,
    ).once()
    flexmock(Signature).should_receive("apply_async").twice()

    # skip SRPM url since it touches multiple classes
    flexmock(CoprBuildEndHandler).should_receive("set_srpm_url").and_return(None)

    flexmock(Pushgateway).should_receive("push").twice().and_return()

    processing_results = SteveJobs().process_message(copr_build_end)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)

    flexmock(CoprBuildJobHelper).should_receive("get_built_packages").and_return([])

    run_copr_build_end_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    flexmock(TestingFarmHandler).should_receive("db_trigger").and_return(
        copr_build_pr.get_trigger_object()
    )

    flexmock(CoprBuildModel).should_receive(
        "get_all_by_owner_project_target_commit"
    ).and_return([copr_build_pr])

    run_testing_farm_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
        chroot="fedora-rawhide-x86_64",
        build_id=1,
    )


def test_copr_build_end_failed_testing_farm(copr_build_end, copr_build_pr):
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("get_pr").and_return(
        flexmock(source_project=flexmock(get_web_url=lambda: "abc"))
        .should_receive("comment")
        .mock()
    )

    config = PackageConfig(
        jobs=[
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                metadata=JobMetadataConfig(
                    _targets=["fedora-rawhide"],
                    owner="some-owner",
                    project="some-project",
                ),
            ),
            JobConfig(
                type=JobType.tests,
                trigger=JobConfigTriggerType.pull_request,
                metadata=JobMetadataConfig(_targets=["fedora-rawhide"]),
            ),
        ]
    )

    flexmock(AbstractCoprBuildEvent).should_receive("get_package_config").and_return(
        config
    )
    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo"
    ).and_return(config)
    flexmock(CoprBuildEndHandler).should_receive(
        "was_last_packit_comment_with_congratulation"
    ).and_return(False)

    flexmock(LocalProject).should_receive("refresh_the_arguments").and_return(None)

    flexmock(CoprBuildModel).should_receive("get_by_build_id").and_return(copr_build_pr)
    flexmock(CoprBuildModel).should_receive("get_by_id").and_return(copr_build_pr)
    copr_build_pr.should_call("set_status").with_args("success").once()
    copr_build_pr.should_receive("set_end_time").once()
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)
    # check if packit-service set correct PR status
    url = get_copr_build_info_url(1)
    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.success,
        description="RPMs were built successfully.",
        url=url,
        check_names=EXPECTED_BUILD_CHECK_NAME,
    ).once()

    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.pending,
        description="RPMs were built successfully.",
        url=url,
        check_names=EXPECTED_TESTING_FARM_CHECK_NAME,
    ).once()

    flexmock(TestingFarmJobHelper).should_receive("is_fmf_configured").and_return(True)
    flexmock(TestingFarmJobHelper).should_receive(
        "send_testing_farm_request"
    ).and_return(
        RequestResponse(
            status_code=400,
            ok=False,
            content=b'{"errors": "some error"}',
            json={"errors": "some error"},
        )
    )

    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.running,
        description="Build succeeded. Submitting the tests ...",
        check_names=EXPECTED_TESTING_FARM_CHECK_NAME,
        url="",
    ).once()
    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.failure,
        description="some error",
        check_names=EXPECTED_TESTING_FARM_CHECK_NAME,
        url="",
    ).once()

    flexmock(Signature).should_receive("apply_async").twice()
    flexmock(Pushgateway).should_receive("push").twice().and_return()

    # skip SRPM url since it touches multiple classes
    flexmock(CoprBuildEndHandler).should_receive("set_srpm_url").and_return(None)

    processing_results = SteveJobs().process_message(copr_build_end)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)

    flexmock(CoprBuildJobHelper).should_receive("get_built_packages").and_return([])

    run_copr_build_end_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    flexmock(TestingFarmHandler).should_receive("db_trigger").and_return(
        copr_build_pr.get_trigger_object()
    )

    run_testing_farm_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
        chroot="fedora-rawhide-x86_64",
        build_id=flexmock(),
    )


def test_copr_build_end_failed_testing_farm_no_json(copr_build_end, copr_build_pr):
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("get_pr").and_return(
        flexmock(source_project=flexmock(get_web_url=lambda: "abc"))
        .should_receive("comment")
        .mock()
    )

    config = PackageConfig(
        jobs=[
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                metadata=JobMetadataConfig(
                    _targets=["fedora-rawhide"],
                    owner="some-owner",
                    project="some-project",
                ),
            ),
            JobConfig(
                type=JobType.tests,
                trigger=JobConfigTriggerType.pull_request,
                metadata=JobMetadataConfig(_targets=["fedora-rawhide"]),
            ),
        ]
    )

    flexmock(AbstractCoprBuildEvent).should_receive("get_package_config").and_return(
        config
    )
    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo"
    ).and_return(config)
    flexmock(CoprBuildEndHandler).should_receive(
        "was_last_packit_comment_with_congratulation"
    ).and_return(False)

    flexmock(LocalProject).should_receive("refresh_the_arguments").and_return(None)

    flexmock(CoprBuildModel).should_receive("get_by_build_id").and_return(copr_build_pr)
    flexmock(CoprBuildModel).should_receive("get_by_id").and_return(copr_build_pr)
    copr_build_pr.should_call("set_status").with_args("success").once()
    copr_build_pr.should_receive("set_end_time").once()
    url = get_copr_build_info_url(1)
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)
    # check if packit-service set correct PR status
    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.success,
        description="RPMs were built successfully.",
        url=url,
        check_names=EXPECTED_BUILD_CHECK_NAME,
    ).once()

    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.pending,
        description="RPMs were built successfully.",
        url=url,
        check_names=EXPECTED_TESTING_FARM_CHECK_NAME,
    ).once()

    flexmock(TestingFarmJobHelper).should_receive("is_fmf_configured").and_return(True)
    flexmock(TestingFarmJobHelper).should_receive(
        "send_testing_farm_request"
    ).and_return(
        RequestResponse(
            status_code=400,
            ok=False,
            content=b"some text error",
            reason="some text error",
            json=None,
        )
    )

    flexmock(CoprBuildModel).should_receive("set_status").with_args("failure")
    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.running,
        description="Build succeeded. Submitting the tests ...",
        check_names=EXPECTED_TESTING_FARM_CHECK_NAME,
        url="",
    ).once()
    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.failure,
        description="Failed to submit tests: some text error",
        check_names=EXPECTED_TESTING_FARM_CHECK_NAME,
        url="",
    ).once()

    flexmock(Signature).should_receive("apply_async").twice()
    flexmock(Pushgateway).should_receive("push").twice().and_return()

    # skip SRPM url since it touches multiple classes
    flexmock(CoprBuildEndHandler).should_receive("set_srpm_url").and_return(None)

    processing_results = SteveJobs().process_message(copr_build_end)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)

    flexmock(CoprBuildJobHelper).should_receive("get_built_packages").and_return([])

    run_copr_build_end_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    flexmock(TestingFarmHandler).should_receive("db_trigger").and_return(
        copr_build_pr.get_trigger_object()
    )

    run_testing_farm_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
        chroot="fedora-rawhide-x86_64",
        build_id=flexmock(),
    )


def test_copr_build_start(copr_build_start, pc_build_pr, copr_build_pr):
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("get_pr").and_return(
        flexmock(source_project=flexmock())
    )
    flexmock(AbstractCoprBuildEvent).should_receive("get_package_config").and_return(
        pc_build_pr
    )
    flexmock(CoprBuildJobHelper).should_receive("get_build_check").and_return(
        EXPECTED_BUILD_CHECK_NAME
    )

    flexmock(CoprBuildModel).should_receive("get_by_build_id").and_return(copr_build_pr)
    url = get_copr_build_info_url(1)
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)

    copr_build_pr.should_receive("set_start_time").once()
    copr_build_pr.should_call("set_status").with_args("pending").once()
    copr_build_pr.should_receive("set_build_logs_url")

    # check if packit-service set correct PR status
    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.running,
        description="RPM build is in progress...",
        url=url,
        check_names=EXPECTED_BUILD_CHECK_NAME,
    ).once()

    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").once().and_return()

    processing_results = SteveJobs().process_message(copr_build_start)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)

    run_copr_build_start_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )


def test_copr_build_just_tests_defined(copr_build_start, pc_tests, copr_build_pr):
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("get_pr").and_return(
        flexmock(source_project=flexmock())
    )
    flexmock(AbstractCoprBuildEvent).should_receive("get_package_config").and_return(
        pc_tests
    )
    flexmock(TestingFarmJobHelper).should_receive("get_build_check").and_return(
        EXPECTED_BUILD_CHECK_NAME
    )
    flexmock(TestingFarmJobHelper).should_receive("get_test_check").and_return(
        EXPECTED_TESTING_FARM_CHECK_NAME
    )

    flexmock(CoprBuildModel).should_receive("get_by_build_id").and_return(copr_build_pr)
    url = get_copr_build_info_url(1)
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)
    copr_build_pr.should_receive("set_start_time").once()
    copr_build_pr.should_call("set_status").with_args("pending").once()
    copr_build_pr.should_receive("set_build_logs_url")

    # check if packit-service sets the correct PR status
    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.running,
        description="RPM build is in progress...",
        url=url,
        check_names=EXPECTED_BUILD_CHECK_NAME,
    ).never()

    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.running,
        description="RPM build is in progress...",
        url=url,
        check_names=TestingFarmJobHelper.get_test_check(copr_build_start["chroot"]),
    ).once()
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").once().and_return()

    processing_results = SteveJobs().process_message(copr_build_start)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)

    run_copr_build_start_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )


def test_copr_build_not_comment_on_success(copr_build_end, pc_build_pr, copr_build_pr):
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("get_pr").and_return(
        flexmock(source_project=flexmock()).should_receive("comment").never()
    )
    flexmock(AbstractCoprBuildEvent).should_receive("get_package_config").and_return(
        pc_build_pr
    )
    flexmock(CoprBuildJobHelper).should_receive("get_build_check").and_return(
        EXPECTED_BUILD_CHECK_NAME
    )

    flexmock(CoprBuildEndHandler).should_receive(
        "was_last_packit_comment_with_congratulation"
    ).and_return(True)

    flexmock(CoprBuildModel).should_receive("get_by_build_id").and_return(copr_build_pr)
    copr_build_pr.should_call("set_status").with_args("success").once()
    copr_build_pr.should_receive("set_end_time").once()
    url = get_copr_build_info_url(1)
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)

    # check if packit-service set correct PR status
    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.success,
        description="RPMs were built successfully.",
        url=url,
        check_names=CoprBuildJobHelper.get_build_check(copr_build_end["chroot"]),
    ).once()

    # skip testing farm
    flexmock(CoprBuildJobHelper).should_receive("get_built_packages").and_return([])
    flexmock(CoprBuildJobHelper).should_receive("job_tests").and_return(None)
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").once().and_return()

    # skip SRPM url since it touches multiple classes
    flexmock(CoprBuildEndHandler).should_receive("set_srpm_url").and_return(None)

    processing_results = SteveJobs().process_message(copr_build_end)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)

    run_copr_build_end_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )


def test_koji_build_start(koji_build_scratch_start, pc_koji_build_pr, koji_build_pr):
    koji_build_pr.target = "rawhide"
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(KojiBuildEvent).should_receive("get_package_config").and_return(
        pc_koji_build_pr
    )

    flexmock(KojiBuildModel).should_receive("get_by_build_id").and_return(koji_build_pr)
    url = get_koji_build_info_url(1)
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)

    koji_build_pr.should_receive("set_build_start_time").once()
    koji_build_pr.should_receive("set_build_finished_time").with_args(None).once()
    koji_build_pr.should_receive("set_status").with_args("pending").once()
    koji_build_pr.should_receive("set_build_logs_url")
    koji_build_pr.should_receive("set_web_url")

    # check if packit-service set correct PR status
    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.running,
        description="RPM build is in progress...",
        url=url,
        check_names="packit-stg/production-build-rawhide",
    ).once()
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").once().and_return()

    processing_results = SteveJobs().process_message(koji_build_scratch_start)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)

    results = run_koji_build_report_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]


def test_koji_build_start_build_not_found(koji_build_scratch_start):
    flexmock(KojiBuildModel).should_receive("get_by_build_id").and_return(None)

    # check if packit-service set correct PR status
    flexmock(StatusReporter).should_receive("report").never()

    processing_results = SteveJobs().process_message(koji_build_scratch_start)

    assert len(processing_results) == 1
    assert processing_results[0]["success"]
    assert (
        "No packit config found in the repository."
        == processing_results[0]["details"]["msg"]
    )


def test_koji_build_end(koji_build_scratch_end, pc_koji_build_pr, koji_build_pr):
    koji_build_pr.target = "rawhide"
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(KojiBuildEvent).should_receive("get_package_config").and_return(
        pc_koji_build_pr
    )

    flexmock(KojiBuildModel).should_receive("get_by_build_id").and_return(koji_build_pr)
    url = get_koji_build_info_url(1)
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)

    koji_build_pr.should_receive("set_build_start_time").once()
    koji_build_pr.should_receive("set_build_finished_time").once()
    koji_build_pr.should_receive("set_status").with_args("success").once()
    koji_build_pr.should_receive("set_build_logs_url")
    koji_build_pr.should_receive("set_web_url")

    # check if packit-service set correct PR status
    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.success,
        description="RPMs were built successfully.",
        url=url,
        check_names="packit-stg/production-build-rawhide",
    ).once()
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").once().and_return()

    processing_results = SteveJobs().process_message(koji_build_scratch_end)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)

    results = run_koji_build_report_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]
