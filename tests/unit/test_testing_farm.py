# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
import re
from datetime import datetime, timezone

import pytest
from flexmock import flexmock
from packit.config import (
    CommonPackageConfig,
    JobConfig,
    JobConfigTriggerType,
    JobType,
    PackageConfig,
)
from packit.copr_helper import CoprHelper
from packit.local_project import LocalProject

import packit_service.models
import packit_service.service.urls as urls
from packit_service.config import ServiceConfig
from packit_service.events.event_data import (
    EventData,
)

# These names are definitely not nice, still they help with making classes
# whose names start with Testing* or Test* to become invisible for pytest,
# and so stop the test discovery warnings.
from packit_service.events.testing_farm import (
    Result as TFResultsEvent,
)
from packit_service.models import (
    BuildStatus,
    CoprBuildTargetModel,
    KojiBuildTargetModel,
    PipelineModel,
    ProjectEventModel,
    ProjectEventModelType,
    PullRequestModel,
    TestingFarmResult,
    TFTTestRunGroupModel,
    TFTTestRunTargetModel,
)
from packit_service.models import TestingFarmResult as TFResult
from packit_service.package_config_getter import PackageConfigGetter
from packit_service.worker.handlers import (
    DownstreamTestingFarmResultsHandler as DownstreamTFResultsHandler,
)
from packit_service.worker.handlers import TestingFarmHandler
from packit_service.worker.handlers import TestingFarmResultsHandler as TFResultsHandler
from packit_service.worker.helpers.testing_farm import (
    TestingFarmClient as TFClient,
)
from packit_service.worker.helpers.testing_farm import (
    TestingFarmJobHelper as TFJobHelper,
)
from packit_service.worker.reporting import BaseCommitStatus, StatusReporter
from packit_service.worker.result import TaskResults


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
    package_config = flexmock(
        jobs=[
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={"package": CommonPackageConfig()},
            ),
            JobConfig(
                type=JobType.tests,
                manual_trigger=True,
                trigger=JobConfigTriggerType.pull_request,
                packages={
                    "package": CommonPackageConfig(
                        identifier=None,
                        _targets=["fedora-rawhide"],
                    ),
                },
            ),
        ],
        packages={
            "package": {},
        },
    )
    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).and_return(package_config)
    config = flexmock(
        command_handler_work_dir=flexmock(),
        comment_command_prefix="/packit",
    )
    flexmock(TFResultsHandler).should_receive("service_config").and_return(config)
    flexmock(TFResultsEvent).should_receive("db_project_object").and_return(None)
    config.should_receive("get_project").with_args(
        url="https://github.com/packit/ogr",
    ).and_return(
        flexmock(
            service=flexmock(instance_url="https://github.com"),
            namespace="packit",
            repo="ogr",
        ),
    )
    config.should_receive("get_github_account_name").and_return("packit-as-a-service")
    created_dt = datetime.now(timezone.utc)
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
        package_config=package_config,
        job_config=JobConfig(
            type=JobType.tests,
            trigger=JobConfigTriggerType.pull_request,
            manual_trigger=True,
            packages={
                "package": CommonPackageConfig(
                    identifier=None,
                ),
            },
        ),
        event=event_dict,
    )
    flexmock(StatusReporter).should_receive("report").with_args(
        description=status_message,
        state=status_status,
        url="https://dashboard.localhost/jobs/testing-farm/123",
        check_names="testing-farm:fedora-rawhide-x86_64",
        markdown_content=None,
        links_to_external_services={"Testing Farm": "some url"},
        update_feedback_time=object,
    ).once()

    urls.DASHBOARD_URL = "https://dashboard.localhost"
    tft_test_run_model = (
        flexmock(
            id=123,
            submitted_time=datetime.now(),
            target="fedora-rawhide-x86_64",
            status=None,
        )
        .should_receive("get_project_event_model")
        .and_return(
            flexmock(id=123)
            .should_receive("get_project_event_object")
            .and_return(
                flexmock(
                    id=12,
                    job_config_trigger_type=JobConfigTriggerType.pull_request,
                    project_event_model_type=ProjectEventModelType.pull_request,
                    commit_sha="0000000000",
                ),
            )
            .mock(),
        )
        .mock()
    )
    tft_test_run_model.should_receive("set_status").with_args(
        tests_result,
        created=created_dt,
    ).and_return().once()
    tft_test_run_model.should_receive("set_web_url").with_args(
        "some url",
    ).and_return().once()

    flexmock(TFTTestRunTargetModel).should_receive("get_by_pipeline_id").and_return(
        tft_test_run_model,
    )
    flexmock(ProjectEventModel).should_receive("get_or_create").and_return(
        flexmock(id=1, type=ProjectEventModelType.pull_request),
    )

    flexmock(LocalProject).should_receive("refresh_the_arguments").and_return(None)

    test_farm_handler.run()


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
def test_downstream_testing_farm_response(
    tests_result,
    tests_summary,
    status_status,
    status_message,
):
    config = flexmock(
        command_handler_work_dir=flexmock(),
        comment_command_prefix="/packit",
    )
    flexmock(DownstreamTFResultsHandler).should_receive("service_config").and_return(config)
    flexmock(TFResultsEvent).should_receive("db_project_object").and_return(None)
    config.should_receive("get_project").with_args(
        url="https://src.fedoraproject.org/rpms/python-ogr",
    ).and_return(
        flexmock(
            service=flexmock(instance_url="https://src.fedoraproject.org"),
            namespace="rpms",
            repo="python-ogr",
            get_pr=lambda id: flexmock(head_commit="0000000000", target_branch="rawhide"),
        ),
    )
    # config.should_receive("get_github_account_name").and_return("packit-as-a-service")
    created_dt = datetime.now(timezone.utc)
    event_dict = TFResultsEvent(
        pipeline_id="id",
        result=tests_result,
        compose=flexmock(),
        summary=tests_summary,
        log_url="some url",
        copr_build_id=None,
        copr_chroot="fedora-rawhide",
        commit_sha=flexmock(),
        project_url="https://src.fedoraproject.org/rpms/python-ogr",
        created=created_dt,
    ).get_dict()
    test_farm_handler = DownstreamTFResultsHandler(
        package_config=None,
        job_config=None,
        event=event_dict,
    )
    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=status_status,
        description=status_message,
        url="https://dashboard.localhost/jobs/testing-farm/123",
        check_name="Packit - installability test(s)",
        target_branch="rawhide",
    ).once()

    urls.DASHBOARD_URL = "https://dashboard.localhost"
    tft_test_run_model = (
        flexmock(
            id=123,
            submitted_time=datetime.now(),
            target="fedora-rawhide",
            status=None,
            data={"fedora_ci_test": "installability"},
        )
        .should_receive("get_project_event_model")
        .and_return(
            flexmock(id=123)
            .should_receive("get_project_event_object")
            .and_return(
                flexmock(
                    id=12,
                    job_config_trigger_type=JobConfigTriggerType.pull_request,
                    project_event_model_type=ProjectEventModelType.pull_request,
                    commit_sha="0000000000",
                ),
            )
            .mock(),
        )
        .mock()
    )
    tft_test_run_model.should_receive("set_status").with_args(
        tests_result,
        created=created_dt,
    ).and_return().once()
    tft_test_run_model.should_receive("set_web_url").with_args(
        "some url",
    ).and_return().once()

    flexmock(TFTTestRunTargetModel).should_receive("get_by_pipeline_id").and_return(
        tft_test_run_model,
    )
    flexmock(ProjectEventModel).should_receive("get_or_create").and_return(
        flexmock(id=1, type=ProjectEventModelType.pull_request),
    )

    flexmock(LocalProject).should_receive("refresh_the_arguments").and_return(None)

    flexmock(KojiBuildTargetModel).should_receive(
        "get_last_successful_scratch_by_commit_target"
    ).with_args("0000000000", "rawhide").and_return(flexmock(target="rawhide"))

    test_farm_handler.run()


@pytest.mark.parametrize(
    "target,compose,use_internal_tf",
    [
        ("fedora-33-x86_64", "Fedora-33", False),
        ("fedora-33-aarch64", "Fedora-33", False),
        ("fedora-rawhide-x86_64", "Fedora-Rawhide", False),
        ("centos-stream-8-x86_64", "CentOS-Stream-8", False),
        ("centos-stream-x86_64", "CentOS-Stream-8", False),
        ("Centos-7-x86_64", "CentOS-7", False),
        ("Centos-8-x86_64", "CentOS-8", False),
        ("fedora-33-x86_64", "Fedora-33-Updated", True),
        ("fedora-rawhide-x86_64", "Fedora-Rawhide-Nightly", True),
        ("centos-stream-8-x86_64", "CentOS-Stream-8", True),
        ("centos-stream-x86_64", "CentOS-Stream-8", True),
        ("Centos-7-x86_64", "CentOS-7-latest", True),
        ("Centos-8-x86_64", "CentOS-8-latest", True),
        ("rhel-7-x86_64", "RHEL-7-LatestReleased", True),
        ("rhel-8-x86_64", "RHEL-8.5.0-Nightly", True),
        ("oraclelinux-7-x86_64", "Oracle-Linux-7.9", True),
        ("oraclelinux-8-x86_64", "Oracle-Linux-8.6", True),
        # Explicit compose name
        ("centos-7-latest-x86_64", "CentOS-7-latest", True),
        ("centos-8-latest-x86_64", "CentOS-8-latest", True),
        ("centos-8-Latest-x86_64", "CentOS-8-latest", True),
        ("centos-8.4-x86_64", "CentOS-8.4", True),
        # If target is present in the available composes, just return it
        ("RHEL-7.8-ZStream-x86_64", "RHEL-7.8-ZStream", True),
        ("RHEL-7.9-rhui-x86_64", "RHEL-7.9-rhui", True),
    ],
)
def test_distro2compose(target, compose, use_internal_tf):
    service_config = ServiceConfig.get_service_config()
    client = TFClient(
        api_url=service_config.testing_farm_api_url,
        token=service_config.testing_farm_secret,
        use_internal_tf=use_internal_tf,
    )
    client = flexmock(client)

    response = flexmock(status_code=200, json=lambda: {"composes": [{"name": compose}]})
    endpoint = "composes/redhat" if use_internal_tf else "composes/public"
    client.should_receive("send_testing_farm_request").with_args(
        endpoint=endpoint,
    ).and_return(response).once()

    assert client.distro2compose(target.rsplit("-", 1)[0]) == compose


@pytest.mark.parametrize(
    ("build_id,chroot,built_packages,packages_to_send"),
    [
        (
            "123456",
            "centos-stream-x86_64",
            None,
            None,
        ),
        (
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
            ["cool-project-0.1.0-2.el8.x86_64"],
        ),
        (
            "123456",
            "centos-stream-x86_64",
            [
                {
                    "arch": "x86_64",
                    "epoch": None,
                    "name": "cool-project",
                    "release": "2.el8",
                    "version": "0.1.0",
                },
            ],
            ["cool-project-0.1.0-2.el8.x86_64"],
        ),
    ],
)
def test_artifact(
    build_id,
    chroot,
    built_packages,
    packages_to_send,
):
    result = TFJobHelper._artifact(chroot, build_id, built_packages)

    artifact = {"id": f"{build_id}:{chroot}", "type": "fedora-copr-build"}

    if packages_to_send:
        artifact["packages"] = packages_to_send

    assert result == artifact


@pytest.mark.parametrize(
    ("compose", "composes", "result"),
    [
        ("Fedora-Cloud-Base-39", {re.compile("Fedora-Cloud-Base-.+")}, True),
        ("Fedora-Cloud-Base-", {re.compile("Fedora-Cloud-Base-.+")}, False),
        ("debezium-tf1", {re.compile("debezium-tf.*")}, True),
        ("Fedora 38", {re.compile("Fedora 38")}, True),
        ("Fedora 3", {re.compile("Fedora 38")}, False),
    ],
)
def test_is_compose_matching(compose, composes, result):
    assert TFClient.is_compose_matching(compose, composes) is result


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
        "tag_name,"
        "project_url,"
        "git_ref,"
        "copr_owner,"
        "copr_project,"
        "build_id,"
        "chroot,"
        "distro,"
        "compose,"
        "arch,"
        "artifacts,"
        "tmt_plan,"
        "tf_post_install_script,"
        "tf_extra_params,"
        "copr_rpms,"
        "comment,"
        "expected_envs"
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
            "1.0",
            "https://github.com/source/packit",
            "master",
            "me",
            "cool-project",
            "123456",
            "centos-stream-x86_64",
            "centos-stream",
            "Fedora-Rawhide",
            "x86_64",
            [{"id": "123456:centos-stream-x86_64", "type": "fedora-copr-build"}],
            None,
            None,
            None,
            None,
            None,
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
            "1.0",
            "https://github.com/source/packit",
            "master",
            "me",
            "cool-project",
            "123456",
            "centos-stream-x86_64",
            "centos-stream",
            "Fedora-Rawhide",
            "x86_64",
            [{"id": "123456:centos-stream-x86_64", "type": "fedora-copr-build"}],
            None,
            None,
            None,
            None,
            None,
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
            "1.0",
            "https://github.com/source/packit",
            "master",
            "me",
            "cool-project",
            "123456",
            "centos-stream-x86_64",
            "centos-stream",
            "Fedora-Rawhide",
            "x86_64",
            [{"id": "123456:centos-stream-x86_64", "type": "fedora-copr-build"}],
            None,
            None,
            None,
            None,
            None,
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
            "1.0",
            "https://github.com/source/packit",
            "master",
            "me",
            "cool-project",
            "123456",
            "centos-stream-x86_64",
            "centos-stream",
            "Fedora-Rawhide",
            "x86_64",
            [
                {
                    "id": "123456:centos-stream-x86_64",
                    "type": "fedora-copr-build",
                    "packages": ["cool-project-0:0.1.0-2.el8.x86_64"],
                },
            ],
            None,
            None,
            None,
            "cool-project-0:0.1.0-2.el8.x86_64",
            None,
            None,
        ),
        # Test tmt_plan and tf_post_install_script
        (
            "https://api.dev.testing-farm.io/v0.1/",
            "very-secret",
            "internal-very-secret",  # internal TF configured
            True,  # internal TF enabled in the config
            "test",
            "packit",
            "packit-service",
            "feb41e5",
            "1.0",
            "https://github.com/source/packit",
            "master",
            "me",
            "cool-project",
            "123456",
            "centos-stream-x86_64",
            "centos-stream",
            "Fedora-Rawhide",
            "x86_64",
            [{"id": "123456:centos-stream-x86_64", "type": "fedora-copr-build"}],
            "^packit",
            "echo 'hi packit'",
            None,
            None,
            None,
            None,
        ),
        # Testing built_packages for more builds (additional build from other PR)
        (
            "https://api.dev.testing-farm.io/v0.1/",
            "very-secret",
            "internal-very-secret",  # internal TF configured
            True,  # internal TF enabled in the config
            "test",
            "packit",
            "packit-service",
            "feb41e5",
            "1.0",
            "https://github.com/source/packit",
            "master",
            "me",
            "cool-project",
            "123456",
            "centos-stream-x86_64",
            "centos-stream",
            "Fedora-Rawhide",
            "x86_64",
            [
                {
                    "id": "123456:centos-stream-x86_64",
                    "type": "fedora-copr-build",
                },
                {
                    "id": "54321:centos-stream-x86_64",
                    "type": "fedora-copr-build",
                    "packages": ["not-cool-project-0:0.1.0-2.el8.x86_64"],
                },
            ],
            None,
            None,
            None,
            "not-cool-project-0:0.1.0-2.el8.x86_64",
            None,
            None,
        ),
        # Testing built_packages for more builds (additional build from other PR) and more packages
        (
            "https://api.dev.testing-farm.io/v0.1/",
            "very-secret",
            "internal-very-secret",  # internal TF configured
            True,  # internal TF enabled in the config
            "test",
            "packit",
            "packit-service",
            "feb41e5",
            "1.0",
            "https://github.com/source/packit",
            "master",
            "me",
            "cool-project",
            "123456",
            "centos-stream-x86_64",
            "centos-stream",
            "Fedora-Rawhide",
            "x86_64",
            [
                {
                    "id": "123456:centos-stream-x86_64",
                    "type": "fedora-copr-build",
                    "packages": [
                        "cool-project-0:0.1.0-2.el8.x86_64",
                        "cool-project-2-0:0.1.0-2.el8.x86_64",
                    ],
                },
                {
                    "id": "54321:centos-stream-x86_64",
                    "type": "fedora-copr-build",
                    "packages": [
                        "not-cool-project-0:0.1.0-2.el8.x86_64",
                        "not-cool-project-2-0:0.1.0-2.el8.x86_64",
                    ],
                },
            ],
            None,
            None,
            None,
            "cool-project-0:0.1.0-2.el8.x86_64 cool-project-2-0:0.1.0-2.el8.x86_64 "
            "not-cool-project-0:0.1.0-2.el8.x86_64 not-cool-project-2-0:0.1.0-2.el8.x86_64",
            None,
            None,
        ),
        # Test that API key and notifications is not overriden by extra-params
        (
            "https://api.dev.testing-farm.io/v0.1/",
            "very-secret",
            "",  # without internal TF configured
            False,
            "test",
            "packit",
            "packit-service",
            "feb41e5",
            "1.0",
            "https://github.com/source/packit",
            "master",
            "me",
            "cool-project",
            "123456",
            "centos-stream-x86_64",
            "centos-stream",
            "Fedora-Rawhide",
            "x86_64",
            [{"id": "123456:centos-stream-x86_64", "type": "fedora-copr-build"}],
            None,
            None,
            {
                "api_key": "foo",
                "notification": {"webhook": {"url": "https://malicious.net"}},
            },
            None,
            None,
            None,
        ),
        # Test that comment env vars are loaded properly to TF payload
        (
            "https://api.dev.testing-farm.io/v0.1/",
            "very-secret",
            "",  # without internal TF configured
            False,
            "test",
            "packit",
            "packit-service",
            "feb41e5",
            "1.0",
            "https://github.com/source/packit",
            "master",
            "me",
            "cool-project",
            "123456",
            "centos-stream-x86_64",
            "centos-stream",
            "Fedora-Rawhide",
            "x86_64",
            [{"id": "123456:centos-stream-x86_64", "type": "fedora-copr-build"}],
            None,
            None,
            {
                "api_key": "foo",
                "notification": {"webhook": {"url": "https://malicious.net"}},
            },
            None,
            "/packit test --labels suite1 --env IP_FAMILY=ipv6 --env INSTALL_TYPE=bundle",
            {"IP_FAMILY": "ipv6", "INSTALL_TYPE": "bundle"},
        ),
        # Test that comment env vars has the highest priority
        (
            "https://api.dev.testing-farm.io/v0.1/",
            "very-secret",
            "",  # without internal TF configured
            False,
            "test",
            "packit",
            "packit-service",
            "feb41e5",
            "1.0",
            "https://github.com/source/packit",
            "master",
            "me",
            "cool-project",
            "123456",
            "centos-stream-x86_64",
            "centos-stream",
            "Fedora-Rawhide",
            "x86_64",
            [{"id": "123456:centos-stream-x86_64", "type": "fedora-copr-build"}],
            None,
            None,
            {
                "api_key": "foo",
                "notification": {"webhook": {"url": "https://malicious.net"}},
            },
            None,
            "/packit test --labels suite1 --env IP_FAMILY=ipv6 --env MY_ENV_VARIABLE=my-value2",
            {"IP_FAMILY": "ipv6", "MY_ENV_VARIABLE": "my-value2"},
        ),
        # Test unseting the env variable
        (
            "https://api.dev.testing-farm.io/v0.1/",
            "very-secret",
            "",  # without internal TF configured
            False,
            "test",
            "packit",
            "packit-service",
            "feb41e5",
            "1.0",
            "https://github.com/source/packit",
            "master",
            "me",
            "cool-project",
            "123456",
            "centos-stream-x86_64",
            "centos-stream",
            "Fedora-Rawhide",
            "x86_64",
            [{"id": "123456:centos-stream-x86_64", "type": "fedora-copr-build"}],
            None,
            None,
            {
                "api_key": "foo",
                "notification": {"webhook": {"url": "https://malicious.net"}},
            },
            None,
            "/packit test --labels suite1 --env IP_FAMILY=ipv6 --env MY_ENV_VARIABLE=",
            {"IP_FAMILY": "ipv6", "MY_ENV_VARIABLE": ""},
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
    tag_name,
    project_url,
    git_ref,
    copr_owner,
    copr_project,
    build_id,
    chroot,
    distro,
    compose,
    arch,
    artifacts,
    tmt_plan,
    tf_post_install_script,
    tf_extra_params,
    copr_rpms,
    comment,
    expected_envs,
):
    service_config = ServiceConfig.get_service_config()
    service_config.testing_farm_api_url = tf_api
    service_config.testing_farm_secret = tf_token
    service_config.internal_testing_farm_secret = internal_tf_token
    service_config.deployment = ps_deployment
    service_config.comment_command_prefix = "/packit"

    package_config = flexmock(jobs=[])
    pr = flexmock(
        source_project=flexmock(get_web_url=lambda: "https://github.com/source/packit"),
        target_project=flexmock(get_web_url=lambda: "https://github.com/packit/packit"),
        head_commit=commit_sha,
        target_branch_head_commit="abcdefgh",
        source_branch="the-source-branch",
        target_branch="the-target-branch",
    )
    project = flexmock(
        repo=repo,
        namespace=namespace,
        service="GitHub",
        get_git_urls=lambda: {"git": f"{project_url}.git"},
        get_pr=lambda id_: pr,
        full_repo_name=f"{namespace}/{repo}",
    )
    metadata = flexmock(
        trigger=flexmock(),
        commit_sha=commit_sha,
        tag_name=tag_name,
        git_ref=git_ref,
        project_url=project_url,
        pr_id=123,
        event_dict={"comment": comment},
    )
    db_project_object = flexmock()

    job_helper = TFJobHelper(
        service_config=service_config,
        package_config=package_config,
        project=project,
        metadata=metadata,
        db_project_event=flexmock()
        .should_receive("get_project_event_object")
        .and_return(db_project_object)
        .mock(),
        job_config=JobConfig(
            type=JobType.tests,
            trigger=JobConfigTriggerType.pull_request,
            packages={
                "package": CommonPackageConfig(
                    use_internal_tf=use_internal_tf,
                    tmt_plan=tmt_plan,
                    tf_post_install_script=tf_post_install_script,
                    tf_extra_params=tf_extra_params,
                ),
            },
        ),
    )
    # Add custom env var to job_config
    job_helper.job_config.env = {"MY_ENV_VARIABLE": "my-value"}

    token_to_use = internal_tf_token if use_internal_tf else tf_token
    assert job_helper.tft_client._token == token_to_use

    job_helper = flexmock(job_helper)

    job_helper.should_receive("job_owner").and_return(copr_owner)
    job_helper.should_receive("job_project").and_return(copr_project)

    # URLs shortened for clarity
    log_url = "https://copr-be.cloud.fedoraproject.org/results/.../builder-live.log"
    srpm_url = f"https://download.copr.fedorainfracloud.org/results/.../{repo}-0.1-1.src.rpm"
    copr_build = flexmock(
        id=build_id,
        built_packages=[
            {
                "name": repo,
                "version": "0.1",
                "release": "1",
                "arch": "noarch",
                "epoch": "0",
            },
        ],
        build_logs_url=log_url,
        owner="builder",
        project_name="some_package",
    )
    copr_build.should_receive("get_srpm_build").and_return(flexmock(url=srpm_url))

    payload = job_helper._payload(
        target=chroot,
        compose=compose,
        artifacts=artifacts,
        build=copr_build,
    )

    expected_test = {
        "url": project_url,
        "ref": commit_sha,
        "merge_sha": "abcdefgh",
        "path": ".",
    }
    if tmt_plan:
        expected_test["name"] = tmt_plan

    assert payload["test"]["tmt"] == expected_test

    expected_environments = [
        {
            "arch": arch,
            "os": {"compose": compose},
            "artifacts": artifacts,
            "tmt": {
                "context": {
                    "distro": distro,
                    "arch": arch,
                    "trigger": "commit",
                    "initiator": "packit",
                },
            },
            "variables": {
                "PACKIT_BUILD_LOG_URL": log_url,
                "PACKIT_COMMIT_SHA": commit_sha,
                "PACKIT_TAG_NAME": tag_name,
                "PACKIT_FULL_REPO_NAME": f"{namespace}/{repo}",
                "PACKIT_PACKAGE_NVR": f"{repo}-0.1-1",
                "PACKIT_SOURCE_BRANCH": "the-source-branch",
                "PACKIT_SOURCE_SHA": "feb41e5",
                "PACKIT_SOURCE_URL": "https://github.com/source/packit",
                "PACKIT_SRPM_URL": srpm_url,
                "PACKIT_TARGET_BRANCH": "the-target-branch",
                "PACKIT_TARGET_SHA": "abcdefgh",
                "PACKIT_TARGET_URL": "https://github.com/packit/packit",
                "PACKIT_PR_ID": 123,
                "PACKIT_COPR_PROJECT": "builder/some_package",
                "MY_ENV_VARIABLE": "my-value",
            },
        },
    ]
    if copr_rpms:
        expected_environments[0]["variables"]["PACKIT_COPR_RPMS"] = copr_rpms

    if tf_post_install_script:
        expected_environments[0]["settings"] = {
            "provisioning": {"post_install_script": tf_post_install_script},
        }

    if comment is not None:
        expected_environments[0]["variables"].update(expected_envs)
        # If MY_ENV_VARIABLE="" then it should be unset from payload and removed from expected envs
        if expected_envs.get("MY_ENV_VARIABLE") == "":
            expected_environments[0]["variables"].pop("MY_ENV_VARIABLE")

    assert payload["environments"] == expected_environments
    assert payload["notification"]["webhook"]["url"].endswith("/testing-farm/results")
    if tf_extra_params:
        assert payload["notification"]["webhook"]["url"] != "https://malicious.net"


@pytest.mark.parametrize(
    ("payload", "params", "result"),
    [
        (
            {"foo": "bar", "bar": "baz"},
            {"foo": "baz"},
            {"foo": "baz", "bar": "baz"},
        ),
        (
            [{"tmt": {"context": {"how": "full"}}}],
            [{"pool": "new-pool"}],
            [{"pool": "new-pool", "tmt": {"context": {"how": "full"}}}],
        ),
        (
            [{"tmt": {"context": {"how": "full"}}}],
            [{"tmt": {"context": {"how": "not-full", "foo": "bar"}}}],
            [{"tmt": {"context": {"how": "not-full", "foo": "bar"}}}],
        ),
        (
            [{"tmt": {"context": {"how": "full"}}}],
            [
                {},
                {"pool": "new-pool"},
            ],
            [{"tmt": {"context": {"how": "full"}}}],
        ),
        (
            {
                "environments": [
                    {
                        "arch": "x86_64",
                        "artifacts": [
                            {
                                "id": "123:fedora-37",
                                "type": "fedora-copr-build",
                            },
                        ],
                    },
                ],
            },
            {
                "environments": [
                    {
                        "artifacts": [
                            {
                                "type": "repository",
                                "id": "123:fedora-37",
                                "packages": "some-nvr",
                            },
                        ],
                        "settings": {
                            "provisioning": {"tags": {"BusinessUnit": "sst_upgrades"}},
                        },
                    },
                ],
            },
            {
                "environments": [
                    {
                        "arch": "x86_64",
                        "artifacts": [
                            {
                                "id": "123:fedora-37",
                                "type": "fedora-copr-build",
                            },
                            {
                                "type": "repository",
                                "id": "123:fedora-37",
                                "packages": "some-nvr",
                            },
                        ],
                        "settings": {
                            "provisioning": {"tags": {"BusinessUnit": "sst_upgrades"}},
                        },
                    },
                ],
            },
        ),
    ],
)
def test_merge_payload_with_extra_params(payload, params, result):
    TFJobHelper._merge_payload_with_extra_params(payload, params)
    assert payload == result


def test_merge_extra_params():
    tf_settings = {"provisioning": {"tags": {"BusinessUnit": "sst_upgrades"}}}

    service_config = flexmock(
        testing_farm_api_url="API URL",
        testing_farm_secret="secret token",
        deployment="prod",
        comment_command_prefix="/packit-dev",
    )
    package_config = flexmock()
    project = flexmock(full_repo_name="test/merge")
    metadata = flexmock(
        commit_sha="0000000",
        pr_id=None,
        tag_name=None,
        event_dict={"comment": ""},
    )
    db_project_event = (
        flexmock().should_receive("get_project_event_object").and_return(flexmock()).mock()
    )
    job_config = flexmock(
        fmf_url="https://github.com/fmf/",
        fmf_ref="main",
        fmf_path="/",
        tmt_plan=None,
        upstream_package_name="test",
        upstream_project_url="https://github.com/test",
        downstream_package_name="test",
        downstream_project_url="https://src.fedoraproject.org/test",
        use_internal_tf=False,
        tf_post_install_script=(
            "#!/bin/sh\nsudo sed -i s/.*ssh-rsa/ssh-rsa/ /root/.ssh/authorized_keys"
        ),
        tf_extra_params={
            "environments": [
                {"tmt": {"context": {"distro": "rhel-7.9"}}, "settings": tf_settings},
            ],
        },
    )
    helper = TFJobHelper(
        service_config,
        package_config,
        project,
        metadata,
        db_project_event,
        job_config,
    )

    payload = helper._payload("rhel-7.9", "rhel-7.9")
    assert (
        payload["environments"][0]["settings"]["provisioning"]["tags"]["BusinessUnit"]
        == "sst_upgrades"
        and payload["environments"][0]["settings"]["provisioning"]["post_install_script"]
        == "#!/bin/sh\nsudo sed -i s/.*ssh-rsa/ssh-rsa/ /root/.ssh/authorized_keys"
    )


def test_merge_extra_params_with_install():
    tf_settings = {"provisioning": {"tags": {"BusinessUnit": "sst_upgrades"}}}

    service_config = flexmock(
        testing_farm_secret="secret token",
        deployment="prod",
        comment_command_prefix="/packit-dev",
    )
    package_config = flexmock()
    project = flexmock(full_repo_name="test/merge")
    metadata = flexmock(
        commit_sha="0000000",
        pr_id=None,
        tag_name=None,
        event_dict={"comment": ""},
    )
    db_project_event = (
        flexmock().should_receive("get_project_event_object").and_return(flexmock()).mock()
    )
    flexmock(CoprBuildTargetModel).should_receive("get_by_build_id").with_args(1234).and_return(
        flexmock(
            owner="packit",
            project_name="packit",
        )
    )
    job_config = flexmock(
        fmf_url="https://github.com/fmf/",
        fmf_ref="main",
        fmf_path="/",
        tmt_plan=None,
        upstream_package_name="test",
        upstream_project_url="https://github.com/test",
        downstream_package_name="test",
        downstream_project_url="https://src.fedoraproject.org/test",
        use_internal_tf=False,
        tf_extra_params={
            "environments": [
                {"tmt": {"context": {"distro": "rhel-7.9"}}, "settings": tf_settings},
            ],
        },
    )
    helper = TFJobHelper(
        service_config,
        package_config,
        project,
        metadata,
        db_project_event,
        job_config,
    )

    payload = helper._payload_install_test(1234, "rhel-7.9", "rhel-7.9")
    assert (
        payload["environments"][0]["settings"]["provisioning"]["tags"]["BusinessUnit"]
        == "sst_upgrades"
    )


@pytest.mark.parametrize(
    (
        "fmf_url",
        "fmf_ref",
        "fmf_path",
        "result_url",
        "result_ref",
        "result_path",
        "merge_pr_in_ci",
    ),
    [
        (  # custom tests and specified ref
            "https://github.com/mmuzila/test",
            "main",
            None,
            "https://github.com/mmuzila/test",
            "main",
            ".",
            True,
        ),
        (  # defaulting to the tests in repo, also merging
            None,
            None,
            None,
            "https://github.com/packit/packit",
            "feb41e5",
            ".",
            True,
        ),
        (  # specifying only ref and merging
            None,
            "main",
            None,
            "https://github.com/packit/packit",
            "feb41e5",
            ".",
            True,
        ),
        (  # specifying custom repo with tests, no ref
            "https://github.com/mmuzila/test",
            None,
            None,
            "https://github.com/mmuzila/test",
            None,
            ".",
            True,
        ),
        (  # specifying custom fmf path
            None,
            None,
            "custom/path",
            "https://github.com/packit/packit",
            "feb41e5",
            "custom/path",
            True,
        ),
        (  # paths are sanitized
            None,
            None,
            "./custom/path/",
            "https://github.com/packit/packit",
            "feb41e5",
            "custom/path",
            True,
        ),
        (  # defaulting to the tests in repo, no merging
            None,
            None,
            None,
            "https://github.com/packit/packit",
            "feb41e5",
            ".",
            False,
        ),
    ],
)
def test_test_repo(
    fmf_url,
    fmf_ref,
    fmf_path,
    result_url,
    result_ref,
    result_path,
    merge_pr_in_ci,
):
    tf_api = "https://api.dev.testing-farm.io/v0.1/"
    tf_token = "very-secret"
    ps_deployment = "test"
    repo = "packit"
    source_project_url = "https://github.com/packit/packit"
    git_ref = "master"
    namespace = "packit-service"
    commit_sha = "feb41e5"
    tag_name = None
    copr_owner = "me"
    copr_project = "cool-project"
    chroot = "centos-stream-x86_64"
    compose = "Fedora-Rawhide"

    service_config = ServiceConfig.get_service_config()
    service_config.testing_farm_api_url = tf_api
    service_config.testing_farm_secret = tf_token
    service_config.deployment = ps_deployment
    service_config.comment_command_prefix = "/packit-dev"

    package_config = flexmock(jobs=[])
    pr = flexmock(
        source_project=flexmock(
            get_web_url=lambda: source_project_url,
        ),
        target_project=flexmock(get_web_url=lambda: "https://github.com/target/bar"),
        head_commit=commit_sha,
        target_branch_head_commit="abcdefgh",
        source_branch="the-source-branch",
        target_branch="the-target-branch",
    )
    project = flexmock(
        repo=repo,
        namespace=namespace,
        service="GitHub",
        get_git_urls=lambda: {"git": f"{source_project_url}.git"},
        get_pr=lambda id_: pr,
        full_repo_name=f"{namespace}/{repo}",
    )
    metadata = flexmock(
        trigger=flexmock(),
        commit_sha=commit_sha,
        tag_name=tag_name,
        git_ref=git_ref,
        project_url=source_project_url,
        pr_id=123,
        event_dict={"comment": ""},
    )
    db_project_object = flexmock()

    job_helper = TFJobHelper(
        service_config=service_config,
        package_config=package_config,
        project=project,
        metadata=metadata,
        db_project_event=flexmock()
        .should_receive("get_project_event_object")
        .and_return(db_project_object)
        .mock(),
        job_config=JobConfig(
            type=JobType.tests,
            trigger=JobConfigTriggerType.pull_request,
            packages={
                "package": CommonPackageConfig(
                    fmf_url=fmf_url,
                    fmf_ref=fmf_ref,
                    fmf_path=fmf_path,
                    merge_pr_in_ci=merge_pr_in_ci,
                ),
            },
        ),
    )
    job_helper = flexmock(job_helper)

    job_helper.should_receive("job_owner").and_return(copr_owner)
    job_helper.should_receive("job_project").and_return(copr_project)

    flexmock(TFClient).should_receive("distro2compose").and_return(compose)

    build_id = 1
    # URLs shortened for clarity
    log_url = "https://copr-be.cloud.fedoraproject.org/results/.../builder-live.log"
    srpm_url = f"https://download.copr.fedorainfracloud.org/results/.../{repo}-0.1-1.src.rpm"
    copr_build = flexmock(
        id=build_id,
        built_packages=[
            {
                "name": repo,
                "version": "0.1",
                "release": "1",
                "arch": "noarch",
                "epoch": "0",
            },
        ],
        build_logs_url=log_url,
        owner="mf",
        project_name="tree",
    )
    copr_build.should_receive("get_srpm_build").and_return(flexmock(url=srpm_url))

    payload = job_helper._payload(chroot, compose=compose, build=copr_build)
    assert payload.get("test")
    assert payload["test"].get("tmt")
    assert payload["test"]["tmt"].get("url") == result_url
    assert payload["test"]["tmt"].get("ref") == result_ref
    assert payload["test"]["tmt"].get("path") == result_path

    # if custom fmf tests are not defined or we're not merging, we don't pass the
    # merge SHA
    merge_sha_should_be_none = fmf_url or not merge_pr_in_ci
    assert (merge_sha_should_be_none and payload["test"]["tmt"].get("merge_sha") is None) or (
        not merge_sha_should_be_none and payload["test"]["tmt"].get("merge_sha") == "abcdefgh"
    )


def test_get_request_details():
    request_id = "123abc"
    request = {
        "id": request_id,
        "environments_requested": [
            {"arch": "x86_64", "os": {"compose": "Fedora-Rawhide"}},
        ],
        "result": {"overall": "passed", "summary": "all ok"},
    }
    request_response = flexmock(status_code=200)
    request_response.should_receive("json").and_return(request)
    flexmock(
        TFClient,
        send_testing_farm_request=request_response,
    )
    details = TFClient.get_request_details(request_id)
    assert details == request


@pytest.mark.parametrize(
    ("copr_build", "wait_for_build"),
    [
        (
            flexmock(
                commit_sha="1111111111111111111111111111111111111111",
                status=BuildStatus.success,
                group_of_targets=flexmock(runs=[flexmock(test_run_group=None)]),
            ),
            False,
        ),
        (
            flexmock(
                id=1,
                commit_sha="1111111111111111111111111111111111111111",
                status=BuildStatus.pending,
                group_of_targets=flexmock(runs=[flexmock(test_run_group=None)]),
            ),
            True,
        ),
        (
            flexmock(
                id=1,
                status=BuildStatus.success,
                # run_testing_farm should not be called for the existing target
                group_of_targets=flexmock(
                    runs=[
                        flexmock(
                            test_run_group=flexmock(
                                grouped_targets=[
                                    flexmock(
                                        target="foo",
                                        status=TestingFarmResult.new,
                                        copr_builds=[
                                            flexmock(status=BuildStatus.success),
                                        ],
                                    ),
                                ],
                            ),
                        ),
                    ],
                ),
            ),
            False,
        ),
    ],
)
def test_trigger_build(copr_build, wait_for_build):
    valid_commit_sha = "1111111111111111111111111111111111111111"

    package_config = PackageConfig(packages={"package": CommonPackageConfig()})
    job_config = JobConfig(
        type=JobType.tests,
        trigger=JobConfigTriggerType.pull_request,
        packages={
            "package": CommonPackageConfig(
                spec_source_id=1,
            ),
        },
    )
    job_config._files_to_sync_used = False
    package_config.jobs = [job_config]
    package_config.spec_source_id = 1

    event = {
        "event_type": "CoprBuildEndEvent",
        "commit_sha": valid_commit_sha,
        "targets_override": ["target-x86_64"],
    }

    flexmock(TFJobHelper).should_receive("get_latest_copr_build").and_return(copr_build)

    if copr_build and copr_build.status == BuildStatus.success:
        flexmock(TFJobHelper).should_receive("run_testing_farm").and_return(
            TaskResults(success=True, details={}),
        ).twice()
    targets = {"target-x86_64", "another-target-x86_64"}
    tests = [
        flexmock(
            copr_builds=[
                flexmock(
                    id=1,
                    status=copr_build.status if copr_build else BuildStatus.pending,
                ),
            ],
            target=target,
            status=TestingFarmResult.new,
        )
        for target in targets
    ]
    flexmock(TFTTestRunTargetModel).should_receive("create").and_return(
        *tests,
    ).one_by_one()
    flexmock(PipelineModel).should_receive("create").and_return(flexmock())
    flexmock(TFTTestRunGroupModel).should_receive("create").and_return(
        flexmock(grouped_targets=tests),
    )

    if wait_for_build:
        for target in targets:
            flexmock(TFJobHelper).should_receive(
                "report_status_to_tests_for_test_target",
            ).with_args(
                state=BaseCommitStatus.pending,
                description="The latest build has not finished yet, "
                "waiting until it finishes before running tests for it.",
                target=target,
                url="https://dashboard.localhost/jobs/copr/1",
            )

    flexmock(CoprHelper).should_receive("get_valid_build_targets").and_return(targets)

    tf_handler = TestingFarmHandler(
        package_config,
        job_config,
        event,
        celery_task=flexmock(request=flexmock(retries=0)),
    )
    flexmock(tf_handler).should_receive("project").and_return(
        flexmock().should_receive("get_web_url").and_return("https://foo.bar").mock(),
    )
    tf_handler._db_project_object = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        project_event_model_type=ProjectEventModelType.pull_request,
        id=11,
    )
    tf_handler.run()


def test_trigger_build_manual_tests_dont_report():
    copr_build = flexmock(
        id=1,
        commit_sha="1111111111111111111111111111111111111111",
        status=BuildStatus.pending,
        group_of_targets=flexmock(runs=[flexmock(test_run_group=None)]),
    )
    valid_commit_sha = "1111111111111111111111111111111111111111"

    package_config = PackageConfig(packages={"package": CommonPackageConfig()})
    job_config = JobConfig(
        type=JobType.tests,
        trigger=JobConfigTriggerType.pull_request,
        manual_trigger=True,
        packages={
            "package": CommonPackageConfig(
                spec_source_id=1,
            ),
        },
    )
    job_config._files_to_sync_used = False
    package_config.jobs = [job_config]
    package_config.spec_source_id = 1

    event = {
        "event_type": "CoprBuildEndEvent",
        "commit_sha": valid_commit_sha,
        "targets_override": ["target-x86_64"],
    }

    flexmock(TFJobHelper).should_receive("get_latest_copr_build").and_return(copr_build)

    targets = {"target-x86_64", "another-target-x86_64"}
    tests = [
        flexmock(
            copr_builds=[
                flexmock(
                    id=1,
                    status=copr_build.status if copr_build else BuildStatus.pending,
                ),
            ],
            target=target,
            status=TestingFarmResult.new,
        )
        for target in targets
    ]
    flexmock(TFTTestRunTargetModel).should_receive("create").and_return(
        *tests,
    ).one_by_one()
    flexmock(PipelineModel).should_receive("create").and_return(flexmock())
    flexmock(TFTTestRunGroupModel).should_receive("create").and_return(
        flexmock(grouped_targets=tests),
    )

    for target in targets:
        flexmock(TFJobHelper).should_receive(
            "report_status_to_tests_for_test_target",
        ).with_args(
            state=BaseCommitStatus.neutral,
            description="The latest build has not finished yet. "
            "Please retrigger the tests once it has finished.",
            target=target,
            url="https://dashboard.localhost/jobs/copr/1",
        )

    flexmock(CoprHelper).should_receive("get_valid_build_targets").and_return(targets)

    tf_handler = TestingFarmHandler(
        package_config,
        job_config,
        event,
        celery_task=flexmock(request=flexmock(retries=0)),
    )
    flexmock(tf_handler).should_receive("project").and_return(
        flexmock().should_receive("get_web_url").and_return("https://foo.bar").mock(),
    )
    tf_handler._db_project_object = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        project_event_model_type=ProjectEventModelType.pull_request,
        id=11,
    )
    tf_handler.run()


@pytest.mark.parametrize(
    ("job_fmf_url", "job_use_target_repo_for_fmf_url", "pr_id", "fmf_url"),
    [
        # custom set URL
        ("https://custom.xyz/mf/fmf/", False, None, "https://custom.xyz/mf/fmf/"),
        # PR, from fork
        (None, False, 42, "https://github.com/mf/packit"),
        # if from branch
        (None, False, None, "https://github.com/packit/packit"),
        (None, True, 42, "https://github.com/packit/packit"),
    ],
)
def test_fmf_url(job_fmf_url, job_use_target_repo_for_fmf_url, pr_id, fmf_url):
    job_config = JobConfig(
        trigger=JobConfigTriggerType.pull_request,
        type=JobType.tests,
        packages={
            "package": CommonPackageConfig(
                fmf_url=job_fmf_url, use_target_repo_for_fmf_url=job_use_target_repo_for_fmf_url
            ),
        },
    )
    metadata = flexmock(pr_id=pr_id)

    git_project = flexmock()
    if job_fmf_url is not None:
        git_project.should_receive("get_pr").never()
    elif pr_id is not None:
        git_project.should_receive("get_pr").with_args(pr_id).and_return(
            flexmock(
                source_project=flexmock()
                .should_receive("get_web_url")
                .and_return("https://github.com/mf/packit")
                .mock(),
            ),
        )
        if job_use_target_repo_for_fmf_url:
            git_project.should_receive("get_web_url").and_return(
                "https://github.com/packit/packit",
            ).once()
    else:
        git_project.should_receive("get_web_url").and_return(
            "https://github.com/packit/packit",
        ).once()

    helper = TFJobHelper(
        service_config=flexmock(comment_command_prefix="/packit-dev"),
        package_config=flexmock(jobs=[]),
        project=git_project,
        metadata=metadata,
        db_project_event=flexmock()
        .should_receive("get_project_event_object")
        .and_return(flexmock())
        .mock(),
        job_config=job_config,
    )

    assert helper.fmf_url == fmf_url


def test_get_additional_builds():
    job_config = JobConfig(
        trigger=JobConfigTriggerType.pull_request,
        type=JobType.tests,
        packages={
            "package": CommonPackageConfig(
                _targets=["test-target", "another-test-target"],
            ),
        },
    )
    metadata = flexmock(
        event_dict={"comment": "/packit-dev test my-namespace/my-repo#10"},
    )

    git_project = flexmock()

    helper = TFJobHelper(
        service_config=flexmock(comment_command_prefix="/packit-dev"),
        package_config=flexmock(jobs=[]),
        project=git_project,
        metadata=metadata,
        db_project_event=flexmock()
        .should_receive("get_project_event_object")
        .and_return(flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request))
        .mock(),
        job_config=job_config,
    )
    additional_copr_build = flexmock(
        target="test-target",
    )
    pr = flexmock(id=16, job_config_trigger_type=JobConfigTriggerType.pull_request)
    pr.should_receive("get_copr_builds").and_return([additional_copr_build])

    flexmock(PullRequestModel).should_receive("get").with_args(
        pr_id=10,
        namespace="my-namespace",
        repo_name="my-repo",
        project_url="https://github.com/my-namespace/my-repo",
    ).and_return(pr)

    flexmock(CoprHelper).should_receive("get_valid_build_targets").and_return(
        {"test-target", "another-test-target"},
    )

    flexmock(packit_service.worker.helpers.testing_farm).should_receive(
        "filter_most_recent_target_models_by_status",
    ).with_args(
        models=[additional_copr_build],
        statuses_to_filter_with=[BuildStatus.success],
    ).and_return(
        {additional_copr_build},
    ).once()

    additional_copr_builds = helper.get_copr_builds_from_other_pr()

    assert additional_copr_builds.get("test-target") == additional_copr_build
    assert additional_copr_builds.get("another-test-target") is None


def test_get_additional_builds_pr_not_in_db():
    job_config = JobConfig(
        trigger=JobConfigTriggerType.pull_request,
        type=JobType.tests,
        packages={
            "package": CommonPackageConfig(
                _targets=["test-target", "another-test-target"],
            ),
        },
    )
    metadata = flexmock(
        event_dict={"comment": "/packit-dev test my-namespace/my-repo#10"},
    )

    git_project = flexmock()

    helper = TFJobHelper(
        service_config=flexmock(comment_command_prefix="/packit-dev"),
        package_config=flexmock(jobs=[]),
        project=git_project,
        metadata=metadata,
        db_project_event=flexmock()
        .should_receive("get_project_event_object")
        .and_return(flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request))
        .mock(),
        job_config=job_config,
    )

    flexmock(PullRequestModel).should_receive("get").with_args(
        pr_id=10,
        namespace="my-namespace",
        repo_name="my-repo",
        project_url="https://github.com/my-namespace/my-repo",
    ).and_return()

    additional_copr_builds = helper.get_copr_builds_from_other_pr()

    assert additional_copr_builds is None


def test_get_additional_builds_builds_not_in_db():
    job_config = JobConfig(
        trigger=JobConfigTriggerType.pull_request,
        type=JobType.tests,
        packages={
            "package": CommonPackageConfig(
                _targets=["test-target", "another-test-target"],
            ),
        },
    )
    metadata = flexmock(
        event_dict={"comment": "/packit-dev test my-namespace/my-repo#10"},
    )

    git_project = flexmock()

    helper = TFJobHelper(
        service_config=flexmock(comment_command_prefix="/packit-dev"),
        package_config=flexmock(jobs=[]),
        project=git_project,
        metadata=metadata,
        db_project_event=flexmock()
        .should_receive("get_project_event_object")
        .and_return(flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request))
        .mock(),
        job_config=job_config,
    )

    flexmock(PullRequestModel).should_receive("get").with_args(
        pr_id=10,
        namespace="my-namespace",
        repo_name="my-repo",
        project_url="https://github.com/my-namespace/my-repo",
    ).and_return(
        flexmock(id=16, job_config_trigger_type=JobConfigTriggerType.pull_request)
        .should_receive("get_copr_builds")
        .and_return([])
        .mock(),
    )
    flexmock(CoprHelper).should_receive("get_valid_build_targets").and_return(
        {"test-target", "another-test-target"},
    )
    additional_copr_builds = helper.get_copr_builds_from_other_pr()

    assert additional_copr_builds is None


def test_get_additional_builds_wrong_format():
    job_config = JobConfig(
        trigger=JobConfigTriggerType.pull_request,
        type=JobType.tests,
        packages={
            "package": CommonPackageConfig(
                _targets=["test-target", "another-test-target"],
            ),
        },
    )
    metadata = flexmock(
        event_dict={"comment": "/packit-dev test my/namespace/my-repo#10"},
    )

    git_project = flexmock()

    helper = TFJobHelper(
        service_config=flexmock(comment_command_prefix="/packit-dev"),
        package_config=flexmock(jobs=[]),
        project=git_project,
        metadata=metadata,
        db_project_event=flexmock()
        .should_receive("get_project_event_object")
        .and_return(flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request))
        .mock(),
        job_config=job_config,
    )

    additional_copr_builds = helper.get_copr_builds_from_other_pr()

    assert additional_copr_builds is None


@pytest.mark.parametrize(
    ("chroot,build,additional_build,result"),
    [
        (
            "centos-stream-x86_64",
            flexmock(
                build_id="123456",
                built_packages=[
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
            ),
            flexmock(
                build_id="54321",
                built_packages=[
                    {
                        "arch": "x86_64",
                        "epoch": 0,
                        "name": "not-cool-project",
                        "release": "2.el8",
                        "version": "0.1.0",
                    },
                    {
                        "arch": "src",
                        "epoch": 0,
                        "name": "not-cool-project",
                        "release": "2.el8",
                        "version": "0.1.0",
                    },
                ],
            ),
            [
                {
                    "id": "123456:centos-stream-x86_64",
                    "type": "fedora-copr-build",
                    "packages": ["cool-project-0.1.0-2.el8.x86_64"],
                },
                {
                    "id": "54321:centos-stream-x86_64",
                    "type": "fedora-copr-build",
                    "packages": ["not-cool-project-0.1.0-2.el8.x86_64"],
                },
            ],
        ),
        (
            "centos-stream-x86_64",
            flexmock(
                build_id="123456",
                built_packages=[
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
            ),
            None,
            [
                {
                    "id": "123456:centos-stream-x86_64",
                    "type": "fedora-copr-build",
                    "packages": ["cool-project-0.1.0-2.el8.x86_64"],
                },
            ],
        ),
    ],
)
def test_get_artifacts(chroot, build, additional_build, result):
    job_config = JobConfig(
        trigger=JobConfigTriggerType.pull_request,
        type=JobType.tests,
        packages={
            "package": CommonPackageConfig(
                _targets=["test-target", "another-test-target"],
            ),
        },
    )
    metadata = flexmock(
        event_dict={"comment": "/packit-dev test my/namespace/my-repo#10"},
    )

    git_project = flexmock()

    helper = TFJobHelper(
        service_config=flexmock(comment_command_prefix="/packit-dev"),
        package_config=flexmock(jobs=[]),
        project=git_project,
        metadata=metadata,
        db_project_event=flexmock()
        .should_receive("get_project_event_object")
        .and_return(flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request))
        .mock(),
        job_config=job_config,
    )

    artifacts = helper._get_artifacts(
        chroot=chroot,
        build=build,
        additional_build=additional_build,
    )

    assert artifacts == result


@pytest.mark.parametrize(
    "jobs,event,should_pass",
    [
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            use_internal_tf=True,
                        ),
                    },
                ),
            ],
            {"event_type": "github.pr.Action", "commit_sha": "abcdef"},
            False,
            id="one_internal_test_job",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            identifier="public",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            use_internal_tf=True,
                        ),
                    },
                ),
            ],
            {"event_type": "github.pr.Action", "commit_sha": "abcdef"},
            False,
            id="multiple_test_jobs_build_required",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            identifier="public",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    skip_build=True,
                    packages={
                        "package": CommonPackageConfig(
                            use_internal_tf=True,
                        ),
                    },
                ),
            ],
            {"event_type": "github.pr.Action", "commit_sha": "abcdef"},
            True,
            id="multiple_test_jobs_build_required_internal_job_skip_build",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                    manual_trigger=False,
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            identifier="public",
                        ),
                    },
                    manual_trigger=False,
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    skip_build=True,
                    manual_trigger=True,
                    packages={
                        "package": CommonPackageConfig(
                            use_internal_tf=True,
                        ),
                    },
                ),
            ],
            {"event_type": "github.pr.Action", "commit_sha": "abcdef"},
            True,
            id="multiple_test_jobs_build_required_internal_job_skip_build_manual_trigger",
        ),
    ],
)
def test_check_if_actor_can_run_job_and_report(jobs, event, should_pass):
    package_config = PackageConfig(packages={"package": CommonPackageConfig()})
    package_config.jobs = jobs

    db_project_object = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        id=123,
        project_event_model_type=ProjectEventModelType.pull_request,
    )
    flexmock(ProjectEventModel).should_receive("get_or_create").with_args(
        type=ProjectEventModelType.pull_request,
        event_id=123,
        commit_sha="abcdef",
    ).and_return(
        flexmock().should_receive("get_project_event_object").and_return(db_project_object).mock(),
    )
    flexmock(PullRequestModel).should_receive("get_or_create").and_return(
        db_project_object,
    )

    gh_project = flexmock(namespace="n", repo="r")
    gh_project.should_receive("can_merge_pr").with_args("actor").and_return(False)
    flexmock(EventData).should_receive("get_project").and_return(gh_project)
    flexmock(ServiceConfig).should_receive("get_project").and_return(gh_project)

    if not should_pass:
        flexmock(TFJobHelper).should_receive("report_status_to_tests").once()

    event.update({"actor": "actor", "project_url": "url"})

    assert TestingFarmHandler.pre_check(package_config, jobs[0], event) == should_pass


@pytest.mark.parametrize(
    "target,use_internal_tf,supported",
    [
        ("distro-aarch64", True, True),
        ("distro-x86_64", True, True),
        ("distro-aarch64", False, True),
        ("distro-x86_64", False, True),
        ("distro-ppc64le", True, True),
        ("distro-s390x", True, True),
        ("distro-ppc64le", False, False),
        ("distro-s390x", False, False),
    ],
)
def test_is_supported_architecture(target, use_internal_tf, supported):
    service_config = ServiceConfig.get_service_config()
    client = TFClient(
        api_url=service_config.testing_farm_api_url,
        token=service_config.testing_farm_secret,
        use_internal_tf=use_internal_tf,
    )
    if not supported:
        flexmock(TFJobHelper).should_receive("report_status_to_tests_for_test_target")

    assert client.is_supported_architecture(target.rsplit("-", 1)[1]) == supported


@pytest.mark.parametrize(
    "comment,expected_identifier,expected_labels,expected_pr_arg,expected_envs",
    [
        (
            "/packit-dev test --identifier my-id-1 --labels label1,label2 namespace-1/repo-1#33",
            "my-id-1",
            ["label1", "label2"],
            "namespace-1/repo-1#33",
            None,
        ),
        (
            "/packit-dev test namespace-2/repo-2#36 --identifier my-id-2",
            "my-id-2",
            None,
            "namespace-2/repo-2#36",
            None,
        ),
        (
            "/packit-dev test namespace-2/repo-2#36 --labels label1 --identifier my-id-2",
            "my-id-2",
            ["label1"],
            "namespace-2/repo-2#36",
            None,
        ),
        (
            "/packit-dev test namespace-2/repo-2#36 --labels label1 --id my-id-2",
            "my-id-2",
            ["label1"],
            "namespace-2/repo-2#36",
            None,
        ),
        (
            "/packit-dev test namespace-2/repo-2#36 --labels label1 -i my-id-2",
            "my-id-2",
            ["label1"],
            "namespace-2/repo-2#36",
            None,
        ),
        (
            "/packit-dev test namespace-2/repo-2#36 --labels label1 -i my-id-2 "
            "--env IP_FAMILY=ipv6",
            "my-id-2",
            ["label1"],
            "namespace-2/repo-2#36",
            {"IP_FAMILY": "ipv6"},
        ),
        (
            "/packit-dev test namespace-2/repo-2#36 --env INSTALL_TYPE=bundle --labels label1"
            " -i my-id-2 --env IP_FAMILY=ipv6",
            "my-id-2",
            ["label1"],
            "namespace-2/repo-2#36",
            {"INSTALL_TYPE": "bundle", "IP_FAMILY": "ipv6"},
        ),
        (
            "/packit-dev test namespace-2/repo-2#36 --env INSTALL_TYPE= --labels label1"
            " -i my-id-2 --env IP_FAMILY=ipv6",
            "my-id-2",
            ["label1"],
            "namespace-2/repo-2#36",
            {"IP_FAMILY": "ipv6", "INSTALL_TYPE": ""},
        ),
    ],
)
def test_parse_comment_arguments(
    comment: str,
    expected_identifier: str,
    expected_labels: list[str],
    expected_pr_arg: str,
    expected_envs: dict[str, str],
):
    job_config = JobConfig(
        trigger=JobConfigTriggerType.pull_request,
        type=JobType.tests,
        packages={
            "package": CommonPackageConfig(
                _targets=["test-target", "another-test-target"],
            ),
        },
    )
    metadata = flexmock(event_dict={"comment": comment})

    git_project = flexmock()

    helper = TFJobHelper(
        service_config=flexmock(comment_command_prefix="/packit-dev"),
        package_config=flexmock(jobs=[]),
        project=git_project,
        metadata=metadata,
        db_project_event=flexmock()
        .should_receive("get_project_event_object")
        .and_return(flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request))
        .mock(),
        job_config=job_config,
    )

    assert helper.comment_arguments.pr_argument == expected_pr_arg
    assert helper.comment_arguments.identifier == expected_identifier
    assert helper.comment_arguments.labels == expected_labels
    assert helper.comment_arguments.envs == expected_envs
