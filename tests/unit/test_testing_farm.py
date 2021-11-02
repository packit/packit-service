# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
from datetime import datetime

import pytest
from flexmock import flexmock
from packit.config import JobConfig, JobType, JobConfigTriggerType
from packit.config.job_config import JobMetadataConfig
from packit.local_project import LocalProject

import packit_service.service.urls as urls
from packit_service.config import PackageConfigGetter
from packit_service.models import TFTTestRunModel

# These names are definitely not nice, still they help with making classes
# whose names start with Testing* or Test* to become invisible for pytest,
# and so stop the test discovery warnings.
from packit_service.worker.events import (
    TestingFarmResultsEvent as TFResultsEvent,
)
from packit_service.models import JobTriggerModel, JobTriggerModelType
from packit_service.models import TestingFarmResult as TFResult

from packit_service.worker.build import copr_build as cb
from packit_service.worker.handlers import TestingFarmResultsHandler as TFResultsHandler
from packit_service.worker.handlers import TestingFarmHandler
from packit_service.worker.reporting import StatusReporter, BaseCommitStatus
from packit_service.worker.result import TaskResults
from packit_service.worker.testing_farm import (
    TestingFarmJobHelper as TFJobHelper,
)
from packit_service.constants import PG_COPR_BUILD_STATUS_SUCCESS
from packit.config.package_config import PackageConfig
from celery import Signature


@pytest.mark.parametrize(
    "tests_result,tests_summary,status_status,status_message",
    [
        pytest.param(
            TFResult.passed,
            "some summary",
            BaseCommitStatus.success,
            "some summary",
            id="passed_and_summary_provided",
        ),
        pytest.param(
            TFResult.passed,
            None,
            BaseCommitStatus.success,
            "Tests passed ...",
            id="passed_and_summary_not_provided",
        ),
        pytest.param(
            TFResult.failed,
            "some summary",
            BaseCommitStatus.failure,
            "some summary",
            id="failed_and_summary_provided",
        ),
        pytest.param(
            TFResult.failed,
            None,
            BaseCommitStatus.failure,
            "Tests failed ...",
            id="failed_and_summary_not_provided",
        ),
    ],
)
def test_testing_farm_response(
    tests_result,
    tests_summary,
    status_status,
    status_message,
):
    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo"
    ).and_return(
        flexmock(
            jobs=[
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                )
            ],
        )
    )
    config = flexmock(command_handler_work_dir=flexmock())
    flexmock(TFResultsHandler).should_receive("service_config").and_return(config)
    flexmock(TFResultsEvent).should_receive("db_trigger").and_return(None)
    config.should_receive("get_project").with_args(
        url="https://github.com/packit/ogr"
    ).and_return()
    created_dt = datetime.utcnow()
    event_dict = TFResultsEvent(
        pipeline_id="id",
        result=tests_result,
        compose=flexmock(),
        summary=tests_summary,
        log_url="some url",
        copr_build_id=flexmock(),
        copr_chroot="fedora-rawhide-x86_64",
        commit_sha=flexmock(),
        project_url="https://github.com/packit/ogr",
        created=created_dt,
    ).get_dict()
    test_farm_handler = TFResultsHandler(
        package_config=flexmock(), job_config=flexmock(), event=event_dict
    )
    flexmock(StatusReporter).should_receive("report").with_args(
        state=status_status,
        description=status_message,
        links_to_external_services={"Testing Farm": "some url"},
        url="https://dashboard.localhost/results/testing-farm/123",
        check_names="packit-stg/testing-farm-fedora-rawhide-x86_64",
    )

    urls.DASHBOARD_URL = "https://dashboard.localhost"
    tft_test_run_model = flexmock(
        id=123,
        submitted_time=datetime.now(),
        get_trigger_object=lambda: flexmock(
            id=12,
            job_config_trigger_type=JobConfigTriggerType.pull_request,
            job_trigger_model_type=JobTriggerModelType.pull_request,
        ),
    )
    tft_test_run_model.should_receive("set_status").with_args(
        tests_result, created=created_dt
    ).and_return().once()
    tft_test_run_model.should_receive("set_web_url").with_args(
        "some url"
    ).and_return().once()

    flexmock(TFTTestRunModel).should_receive("get_by_pipeline_id").and_return(
        tft_test_run_model
    )
    flexmock(JobTriggerModel).should_receive("get_or_create").and_return(
        flexmock(id=1, type=JobTriggerModelType.pull_request)
    )

    flexmock(LocalProject).should_receive("refresh_the_arguments").and_return(None)

    test_farm_handler.run()


@pytest.mark.parametrize(
    "chroot,distro,arch,use_internal_tf",
    [
        ("fedora-33-x86_64", "fedora-33", "x86_64", False),
        ("fedora-rawhide-aarch64", "fedora-rawhide", "aarch64", False),
        ("centos-stream-x86_64", "centos-stream", "x86_64", False),
        ("epel-6-x86_64", "centos-6", "x86_64", False),
        ("epel-7-x86_64", "centos-7", "x86_64", False),
        ("epel-8-x86_64", "centos-stream-8", "x86_64", False),
        ("fedora-33-x86_64", "fedora-33", "x86_64", True),
        ("fedora-rawhide-aarch64", "fedora-rawhide", "aarch64", True),
        ("centos-stream-x86_64", "centos-stream", "x86_64", True),
        ("epel-7-x86_64", "centos-7", "x86_64", True),
        ("epel-8-x86_64", "centos-8", "x86_64", True),
        ("oraclelinux-7-x86_64", "oraclelinux-7", "x86_64", True),
        ("oraclelinux-8-x86_64", "oraclelinux-8", "x86_64", True),
    ],
)
def test_chroot2distro_arch(chroot, distro, arch, use_internal_tf):
    job_helper = TFJobHelper(
        service_config=flexmock(
            testing_farm_api_url="xyz",
        ),
        package_config=flexmock(jobs=[]),
        project=flexmock(),
        metadata=flexmock(),
        db_trigger=flexmock(),
        job_config=JobConfig(
            type=JobType.tests,
            trigger=JobConfigTriggerType.pull_request,
            metadata=JobMetadataConfig(use_internal_tf=use_internal_tf),
        ),
    )
    job_helper = flexmock(job_helper)

    assert job_helper.chroot2distro_arch(chroot) == (distro, arch)


@pytest.mark.parametrize(
    "distro,compose,use_internal_tf",
    [
        ("fedora-33", "Fedora-33", False),
        ("fedora-rawhide", "Fedora-Rawhide", False),
        ("centos-stream-8", "CentOS-Stream-8", False),
        ("centos-stream", "CentOS-Stream-8", False),
        ("Centos-7", "CentOS-7", False),
        ("Centos-8", "CentOS-8", False),
        ("fedora-33", "Fedora-33-Updated", True),
        ("fedora-rawhide", "Fedora-Rawhide-Nightly", True),
        ("centos-stream-8", "RHEL-8.5.0-Nightly", True),
        ("centos-stream", "RHEL-8.5.0-Nightly", True),
        ("Centos-7", "CentOS-7-latest", True),
        ("Centos-8", "CentOS-8-latest", True),
        ("rhel-7", "RHEL-7-LatestReleased", True),
        ("rhel-8", "RHEL-8.5.0-Nightly", True),
        ("oraclelinux-7", "Oracle-Linux-7.9", True),
        ("oraclelinux-8", "Oracle-Linux-8.4", True),
    ],
)
def test_distro2compose(distro, compose, use_internal_tf):
    job_helper = TFJobHelper(
        service_config=flexmock(
            testing_farm_api_url="xyz",
        ),
        package_config=flexmock(jobs=[]),
        project=flexmock(),
        metadata=flexmock(),
        db_trigger=flexmock(),
        job_config=JobConfig(
            type=JobType.tests,
            trigger=JobConfigTriggerType.pull_request,
            metadata=JobMetadataConfig(use_internal_tf=use_internal_tf),
        ),
    )
    job_helper = flexmock(job_helper)

    response = flexmock(
        status_code=200, json=lambda: {"composes": [{"name": "Fedora-33"}]}
    )
    job_helper.should_receive("send_testing_farm_request").and_return(response).times(
        0 if use_internal_tf else 1
    )

    assert job_helper.distro2compose(distro, arch="x86_64") == compose


@pytest.mark.parametrize(
    "distro,arch,compose,use_internal_tf",
    [
        ("fedora-33", "x86_64", "Fedora-33", False),
        ("fedora-33", "aarch64", "Fedora-33-aarch64", False),
    ],
)
def test_distro2compose_for_aarch64(distro, arch, compose, use_internal_tf):
    job_helper = TFJobHelper(
        service_config=flexmock(
            testing_farm_api_url="xyz",
        ),
        package_config=flexmock(jobs=[]),
        project=flexmock(),
        metadata=flexmock(),
        db_trigger=flexmock(),
        job_config=JobConfig(
            type=JobType.tests,
            trigger=JobConfigTriggerType.pull_request,
            metadata=JobMetadataConfig(use_internal_tf=use_internal_tf),
        ),
    )
    job_helper = flexmock(job_helper)

    response = flexmock(
        status_code=200, json=lambda: {"composes": [{"name": "Fedora-33"}]}
    )
    job_helper.should_receive("send_testing_farm_request").and_return(response).times(
        0 if use_internal_tf else 1
    )

    assert job_helper.distro2compose(distro, arch=arch) == compose


@pytest.mark.parametrize(
    (
        "tf_api,"
        "tf_token,"
        "internal_tf_token,"
        "use_internal_tf,"
        "ps_deployment,"
        "repo,"
        "namespace,"
        "commit_sha,"
        "project_url,"
        "git_ref,"
        "copr_owner,"
        "copr_project,"
        "build_id,"
        "chroot,"
        "built_packages,"
        "distro,"
        "compose,"
        "arch,"
        "packages_to_send"
    ),
    [
        (
            "https://api.dev.testing-farm.io/v0.1/",
            "very-secret",
            "",  # without internal TF configured
            False,
            "test",
            "packit",
            "packit-service",
            "feb41e5",
            "https://github.com/packit/packit",
            "master",
            "me",
            "cool-project",
            "123456",
            "centos-stream-x86_64",
            None,
            "centos-stream",
            "Fedora-Rawhide",
            "x86_64",
            None,
        ),
        (
            "https://api.dev.testing-farm.io/v0.1/",
            "very-secret",
            "internal-very-secret",  # internal TF configured
            False,  # internal TF disabled in the config
            "test",
            "packit",
            "packit-service",
            "feb41e5",
            "https://github.com/packit/packit",
            "master",
            "me",
            "cool-project",
            "123456",
            "centos-stream-x86_64",
            None,
            "centos-stream",
            "Fedora-Rawhide",
            "x86_64",
            None,
        ),
        (
            "https://api.dev.testing-farm.io/v0.1/",
            "very-secret",
            "internal-very-secret",  # internal TF configured
            True,  # internal TF enabled in the config
            "test",
            "packit",
            "packit-service",
            "feb41e5",
            "https://github.com/packit/packit",
            "master",
            "me",
            "cool-project",
            "123456",
            "centos-stream-x86_64",
            None,
            "centos-stream",
            "Fedora-Rawhide",
            "x86_64",
            None,
        ),
        # Testing built_packages
        (
            "https://api.dev.testing-farm.io/v0.1/",
            "very-secret",
            "internal-very-secret",  # internal TF configured
            True,  # internal TF enabled in the config
            "test",
            "packit",
            "packit-service",
            "feb41e5",
            "https://github.com/packit/packit",
            "master",
            "me",
            "cool-project",
            "123456",
            "centos-stream-x86_64",
            [
                {
                    "arch": "x86_64",
                    "epoch": 0,
                    "name": "cool-project",
                    "release": "2.el8",
                    "version": "0.1.0",
                },
                {
                    "arch": "src",
                    "epoch": 0,
                    "name": "cool-project",
                    "release": "2.el8",
                    "version": "0.1.0",
                },
            ],
            "centos-stream",
            "Fedora-Rawhide",
            "x86_64",
            ["cool-project-0:0.1.0-2.el8.x86_64"],
        ),
    ],
)
def test_payload(
    tf_api,
    tf_token,
    internal_tf_token,
    use_internal_tf,
    ps_deployment,
    repo,
    namespace,
    commit_sha,
    project_url,
    git_ref,
    copr_owner,
    copr_project,
    build_id,
    chroot,
    built_packages,
    distro,
    compose,
    arch,
    packages_to_send,
):
    # Soo many things are happening in a single constructor!!!!
    config = flexmock(
        testing_farm_api_url=tf_api,
        testing_farm_secret=tf_token,
        internal_testing_farm_secret=internal_tf_token,
        deployment=ps_deployment,
        command_handler_work_dir="/tmp",
    )
    package_config = flexmock(jobs=[])
    pr = flexmock(source_project=flexmock(get_web_url=lambda: project_url))
    project = flexmock(
        repo=repo,
        namespace=namespace,
        service="GitHub",
        get_git_urls=lambda: {"git": f"{project_url}.git"},
        get_pr=lambda id_: pr,
    )
    metadata = flexmock(
        trigger=flexmock(),
        commit_sha=commit_sha,
        git_ref=git_ref,
        project_url=project_url,
        pr_id=123,
    )
    db_trigger = flexmock()

    job_helper = TFJobHelper(
        service_config=config,
        package_config=package_config,
        project=project,
        metadata=metadata,
        db_trigger=db_trigger,
        job_config=JobConfig(
            type=JobType.tests,
            trigger=JobConfigTriggerType.pull_request,
            metadata=JobMetadataConfig(use_internal_tf=use_internal_tf),
        ),
    )

    token_to_use = internal_tf_token if use_internal_tf else tf_token
    assert job_helper.tft_token == token_to_use

    job_helper = flexmock(job_helper)

    job_helper.should_receive("job_owner").and_return(copr_owner)
    job_helper.should_receive("job_project").and_return(copr_project)
    job_helper.should_receive("distro2compose").and_return(compose)
    payload = job_helper._payload(build_id, chroot, built_packages)

    assert payload["api_key"] == token_to_use
    assert payload["test"]["fmf"] == {
        "url": project_url,
        "ref": commit_sha,
    }

    artifact = {"id": f"{build_id}:{chroot}", "type": "fedora-copr-build"}

    if packages_to_send:
        artifact["packages"] = packages_to_send

    assert payload["environments"] == [
        {
            "arch": arch,
            "os": {"compose": compose},
            "artifacts": [artifact],
            "tmt": {"context": {"distro": distro, "arch": arch, "trigger": "commit"}},
        }
    ]
    assert payload["notification"]["webhook"]["url"].endswith("/testing-farm/results")


@pytest.mark.parametrize(
    ("fmf_url," "fmf_ref," "result_url," "result_ref"),
    [
        (
            "https://github.com/mmuzila/test",
            "main",
            "https://github.com/mmuzila/test",
            "main",
        ),
        (
            None,
            None,
            "https://github.com/packit/packit",
            "feb41e5",
        ),
        (
            None,
            "main",
            "https://github.com/packit/packit",
            "feb41e5",
        ),
        (
            "https://github.com/mmuzila/test",
            None,
            "https://github.com/mmuzila/test",
            None,
        ),
    ],
)
def test_test_repo(fmf_url, fmf_ref, result_url, result_ref):
    tf_api = "https://api.dev.testing-farm.io/v0.1/"
    tf_token = "very-secret"
    ps_deployment = "test"
    repo = "packit"
    project_url = "https://github.com/packit/packit"
    git_ref = "master"
    namespace = "packit-service"
    commit_sha = "feb41e5"
    copr_owner = "me"
    copr_project = "cool-project"
    build_id = "123456"
    chroot = "centos-stream-x86_64"
    compose = "Fedora-Rawhide"

    config = flexmock(
        testing_farm_api_url=tf_api,
        testing_farm_secret=tf_token,
        deployment=ps_deployment,
        command_handler_work_dir="/tmp",
    )
    package_config = flexmock(jobs=[])
    pr = flexmock(source_project=flexmock(get_web_url=lambda: project_url))
    project = flexmock(
        repo=repo,
        namespace=namespace,
        service="GitHub",
        get_git_urls=lambda: {"git": f"{project_url}.git"},
        get_pr=lambda id_: pr,
    )
    metadata = flexmock(
        trigger=flexmock(),
        commit_sha=commit_sha,
        git_ref=git_ref,
        project_url=project_url,
        pr_id=123,
    )
    db_trigger = flexmock()

    job_helper = TFJobHelper(
        service_config=config,
        package_config=package_config,
        project=project,
        metadata=metadata,
        db_trigger=db_trigger,
        job_config=JobConfig(
            type=JobType.tests,
            trigger=JobConfigTriggerType.pull_request,
            metadata=JobMetadataConfig(fmf_url=fmf_url, fmf_ref=fmf_ref),
        ),
    )
    job_helper = flexmock(job_helper)

    job_helper.should_receive("job_owner").and_return(copr_owner)
    job_helper.should_receive("job_project").and_return(copr_project)
    job_helper.should_receive("distro2compose").and_return(compose)

    payload = job_helper._payload(build_id, chroot, None)
    assert payload.get("test")
    assert payload["test"].get("fmf")
    assert payload["test"]["fmf"].get("url") == result_url
    assert payload["test"]["fmf"].get("ref") == result_ref


def test_get_request_details():
    request_id = "123abc"
    request = {
        "id": request_id,
        "environments_requested": [
            {"arch": "x86_64", "os": {"compose": "Fedora-Rawhide"}}
        ],
        "result": {"overall": "passed", "summary": "all ok"},
    }
    request_response = flexmock(status_code=200)
    request_response.should_receive("json").and_return(request)
    flexmock(
        TFJobHelper,
        send_testing_farm_request=request_response,
        job_build=None,
    )
    details = TFJobHelper.get_request_details(request_id)
    assert details == request


@pytest.mark.parametrize(
    ("copr_build," "run_new_build"),
    [
        (
            None,
            True,
        ),
        (
            flexmock(
                commit_sha="1111111111111111111111111111111111111111",
                status=PG_COPR_BUILD_STATUS_SUCCESS,
            ),
            False,
        ),
    ],
)
def test_trigger_build(copr_build, run_new_build):

    valid_commit_sha = "1111111111111111111111111111111111111111"

    package_config = PackageConfig()
    package_config.jobs = []
    package_config.spec_source_id = 1
    job_config = flexmock()
    job_config.type = JobType.build
    job_config.spec_source_id = 1

    event = {
        "event_type": "CoprBuileEndEvent",
        "commit_sha": valid_commit_sha,
    }

    flexmock(TFJobHelper).should_receive("get_latest_copr_build").and_return(copr_build)

    if run_new_build:
        flexmock(TFJobHelper, job_owner="owner", job_project="project")
        flexmock(Signature).should_receive("apply_async").once()
    else:
        flexmock(TFJobHelper).should_receive("run_testing_farm").and_return(
            TaskResults(success=True, details={})
        )

    flexmock(cb).should_receive("get_valid_build_targets").and_return(
        {"target", "another-target"}
    )

    tf_handler = TestingFarmHandler(package_config, job_config, event, "target")
    tf_handler.run()
