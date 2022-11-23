# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
from datetime import datetime, timezone

import pytest
from celery import Signature
from flexmock import flexmock

import packit_service.models
import packit_service.service.urls as urls
from packit.config import (
    CommonPackageConfig,
    JobConfig,
    JobConfigTriggerType,
    JobType,
    PackageConfig,
)
from packit.local_project import LocalProject
from packit_service.config import PackageConfigGetter, ServiceConfig
from packit_service.models import JobTriggerModel, JobTriggerModelType, BuildStatus
from packit_service.models import (
    TFTTestRunTargetModel,
    PullRequestModel,
)
from packit_service.models import TestingFarmResult as TFResult

# These names are definitely not nice, still they help with making classes
# whose names start with Testing* or Test* to become invisible for pytest,
# and so stop the test discovery warnings.
from packit_service.worker.checker.testing_farm import CanActorRunJob
from packit_service.worker.events import (
    TestingFarmResultsEvent as TFResultsEvent,
    EventData,
)
from packit_service.worker.handlers import TestingFarmHandler
from packit_service.worker.handlers import TestingFarmResultsHandler as TFResultsHandler
from packit_service.worker.helpers.build import copr_build as cb
from packit_service.worker.helpers.testing_farm import (
    TestingFarmJobHelper as TFJobHelper,
)
from packit_service.worker.reporting import StatusReporter, BaseCommitStatus
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
    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo"
    ).and_return(
        flexmock(
            jobs=[
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
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
        package_config=flexmock(),
        job_config=flexmock(identifier=None),
        event=event_dict,
    )
    flexmock(StatusReporter).should_receive("report").with_args(
        state=status_status,
        description=status_message,
        links_to_external_services={"Testing Farm": "some url"},
        url="https://dashboard.localhost/results/testing-farm/123",
        check_names="testing-farm:fedora-rawhide-x86_64",
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
        target="fedora-rawhide-x86_64",
    )
    tft_test_run_model.should_receive("set_status").with_args(
        tests_result, created=created_dt
    ).and_return().once()
    tft_test_run_model.should_receive("set_web_url").with_args(
        "some url"
    ).and_return().once()

    flexmock(TFTTestRunTargetModel).should_receive("get_by_pipeline_id").and_return(
        tft_test_run_model
    )
    flexmock(JobTriggerModel).should_receive("get_or_create").and_return(
        flexmock(id=1, type=JobTriggerModelType.pull_request)
    )

    flexmock(LocalProject).should_receive("refresh_the_arguments").and_return(None)

    test_farm_handler.run()


@pytest.mark.parametrize(
    "target,compose,use_internal_tf",
    [
        ("fedora-33-x86_64", "Fedora-33", False),
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
        ("RHEL-7.8-ZStream", "RHEL-7.8-ZStream", True),
        ("RHEL-7.8-ZStream-x86_64", "RHEL-7.8-ZStream", True),
        ("RHEL-7.9-rhui", "RHEL-7.9-rhui", True),
    ],
)
def test_distro2compose(target, compose, use_internal_tf):
    job_helper = TFJobHelper(
        service_config=ServiceConfig.get_service_config(),
        package_config=flexmock(jobs=[]),
        project=flexmock(),
        metadata=flexmock(),
        db_trigger=flexmock(),
        job_config=JobConfig(
            type=JobType.tests,
            trigger=JobConfigTriggerType.pull_request,
            packages={
                "package": CommonPackageConfig(
                    use_internal_tf=use_internal_tf,
                )
            },
        ),
    )
    job_helper = flexmock(job_helper)

    response = flexmock(status_code=200, json=lambda: {"composes": [{"name": compose}]})
    endpoint = "composes/redhat" if use_internal_tf else "composes/public"
    job_helper.should_receive("send_testing_farm_request").with_args(
        endpoint=endpoint
    ).and_return(response).once()

    assert job_helper.distro2compose(target) == compose


@pytest.mark.parametrize(
    "target,compose,use_internal_tf",
    [
        ("fedora-33-x86_64", "Fedora-33", False),
        ("fedora-33-aarch64", "Fedora-33-aarch64", False),
    ],
)
def test_distro2compose_for_aarch64(target, compose, use_internal_tf):
    job_helper = TFJobHelper(
        service_config=ServiceConfig.get_service_config(),
        package_config=flexmock(jobs=[]),
        project=flexmock(),
        metadata=flexmock(),
        db_trigger=flexmock(),
        job_config=JobConfig(
            type=JobType.tests,
            trigger=JobConfigTriggerType.pull_request,
            packages={
                "package": CommonPackageConfig(
                    use_internal_tf=use_internal_tf,
                )
            },
        ),
    )
    job_helper = flexmock(job_helper)

    response = flexmock(status_code=200, json=lambda: {"composes": [{"name": compose}]})
    job_helper.should_receive("send_testing_farm_request").with_args(
        endpoint="composes/public"
    ).and_return(response).once()

    assert job_helper.distro2compose(target) == compose


@pytest.mark.parametrize(
    ("build_id," "chroot," "built_packages," "packages_to_send"),
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
        "distro,"
        "compose,"
        "arch,"
        "artifacts,"
        "tmt_plan,"
        "tf_post_install_script,"
        "copr_rpms"
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
                }
            ],
            None,
            None,
            "cool-project-0:0.1.0-2.el8.x86_64",
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
            "not-cool-project-0:0.1.0-2.el8.x86_64",
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
            "cool-project-0:0.1.0-2.el8.x86_64 cool-project-2-0:0.1.0-2.el8.x86_64 "
            "not-cool-project-0:0.1.0-2.el8.x86_64 not-cool-project-2-0:0.1.0-2.el8.x86_64",
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
    distro,
    compose,
    arch,
    artifacts,
    tmt_plan,
    tf_post_install_script,
    copr_rpms,
):
    service_config = ServiceConfig.get_service_config()
    service_config.testing_farm_api_url = tf_api
    service_config.testing_farm_secret = tf_token
    service_config.internal_testing_farm_secret = internal_tf_token
    service_config.deployment = ps_deployment

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
        git_ref=git_ref,
        project_url=project_url,
        pr_id=123,
    )
    db_trigger = flexmock()

    job_helper = TFJobHelper(
        service_config=service_config,
        package_config=package_config,
        project=project,
        metadata=metadata,
        db_trigger=db_trigger,
        job_config=JobConfig(
            type=JobType.tests,
            trigger=JobConfigTriggerType.pull_request,
            packages={
                "package": CommonPackageConfig(
                    use_internal_tf=use_internal_tf,
                    tmt_plan=tmt_plan,
                    tf_post_install_script=tf_post_install_script,
                )
            },
        ),
    )

    token_to_use = internal_tf_token if use_internal_tf else tf_token
    assert job_helper.tft_token == token_to_use

    job_helper = flexmock(job_helper)

    job_helper.should_receive("job_owner").and_return(copr_owner)
    job_helper.should_receive("job_project").and_return(copr_project)

    # URLs shortened for clarity
    log_url = "https://copr-be.cloud.fedoraproject.org/results/.../builder-live.log"
    srpm_url = (
        f"https://download.copr.fedorainfracloud.org/results/.../{repo}-0.1-1.src.rpm"
    )
    copr_build = flexmock(
        id=build_id,
        built_packages=[
            {
                "name": repo,
                "version": "0.1",
                "release": "1",
                "arch": "noarch",
                "epoch": "0",
            }
        ],
        build_logs_url=log_url,
        owner="builder",
        project_name="some_package",
    )
    copr_build.should_receive("get_srpm_build").and_return(flexmock(url=srpm_url))

    payload = job_helper._payload(
        target=chroot, compose=compose, artifacts=artifacts, build=copr_build
    )

    assert payload["api_key"] == token_to_use

    expected_test = {
        "url": project_url,
        "ref": commit_sha,
    }
    if tmt_plan:
        expected_test["name"] = tmt_plan

    assert payload["test"]["fmf"] == expected_test

    expected_environments = [
        {
            "arch": arch,
            "os": {"compose": compose},
            "artifacts": artifacts,
            "tmt": {"context": {"distro": distro, "arch": arch, "trigger": "commit"}},
            "variables": {
                "PACKIT_BUILD_LOG_URL": log_url,
                "PACKIT_COMMIT_SHA": commit_sha,
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
            },
        }
    ]
    if copr_rpms:
        expected_environments[0]["variables"]["PACKIT_COPR_RPMS"] = copr_rpms

    if tf_post_install_script:
        expected_environments[0]["settings"] = {
            "provisioning": {"post_install_script": tf_post_install_script}
        }

    assert payload["environments"] == expected_environments
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
    source_project_url = "https://github.com/packit/packit"
    git_ref = "master"
    namespace = "packit-service"
    commit_sha = "feb41e5"
    copr_owner = "me"
    copr_project = "cool-project"
    chroot = "centos-stream-x86_64"
    compose = "Fedora-Rawhide"

    service_config = ServiceConfig.get_service_config()
    service_config.testing_farm_api_url = tf_api
    service_config.testing_farm_secret = tf_token
    service_config.deployment = ps_deployment

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
        git_ref=git_ref,
        project_url=source_project_url,
        pr_id=123,
    )
    db_trigger = flexmock()

    job_helper = TFJobHelper(
        service_config=service_config,
        package_config=package_config,
        project=project,
        metadata=metadata,
        db_trigger=db_trigger,
        job_config=JobConfig(
            type=JobType.tests,
            trigger=JobConfigTriggerType.pull_request,
            packages={
                "package": CommonPackageConfig(
                    fmf_url=fmf_url,
                    fmf_ref=fmf_ref,
                )
            },
        ),
    )
    job_helper = flexmock(job_helper)

    job_helper.should_receive("job_owner").and_return(copr_owner)
    job_helper.should_receive("job_project").and_return(copr_project)
    job_helper.should_receive("distro2compose").and_return(compose)

    build_id = 1
    # URLs shortened for clarity
    log_url = "https://copr-be.cloud.fedoraproject.org/results/.../builder-live.log"
    srpm_url = (
        f"https://download.copr.fedorainfracloud.org/results/.../{repo}-0.1-1.src.rpm"
    )
    copr_build = flexmock(
        id=build_id,
        built_packages=[
            {
                "name": repo,
                "version": "0.1",
                "release": "1",
                "arch": "noarch",
                "epoch": "0",
            }
        ],
        build_logs_url=log_url,
        owner="mf",
        project_name="tree",
    )
    copr_build.should_receive("get_srpm_build").and_return(flexmock(url=srpm_url))

    payload = job_helper._payload(chroot, compose=compose, build=copr_build)
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
    )
    details = TFJobHelper.get_request_details(request_id)
    assert details == request


@pytest.mark.parametrize(
    ("copr_build", "run_new_build", "wait_for_build"),
    [
        (None, True, False),
        (
            flexmock(
                commit_sha="1111111111111111111111111111111111111111",
                status=BuildStatus.success,
            ),
            False,
            False,
        ),
        (
            flexmock(
                id=1,
                commit_sha="1111111111111111111111111111111111111111",
                status=BuildStatus.pending,
            ),
            False,
            True,
        ),
    ],
)
def test_trigger_build(copr_build, run_new_build, wait_for_build):
    valid_commit_sha = "1111111111111111111111111111111111111111"

    package_config = PackageConfig(packages={"package": CommonPackageConfig()})
    job_config = JobConfig(
        type=JobType.tests,
        trigger=JobConfigTriggerType.pull_request,
        packages={
            "package": CommonPackageConfig(
                spec_source_id=1,
            )
        },
    )
    job_config._files_to_sync_used = False
    package_config.jobs = [job_config]
    package_config.spec_source_id = 1

    event = {
        "event_type": "CoprBuileEndEvent",
        "commit_sha": valid_commit_sha,
        "targets_override": ["target-x86_64"],
    }

    flexmock(TFJobHelper).should_receive("get_latest_copr_build").and_return(copr_build)

    if run_new_build:
        flexmock(TFJobHelper, job_owner="owner", job_project="project")
        flexmock(TFJobHelper).should_receive("report_status_to_tests_for_chroot")
        flexmock(Signature).should_receive("apply_async").once()
    else:
        flexmock(TFJobHelper).should_receive("run_testing_farm").and_return(
            TaskResults(success=True, details={})
        )
    targets = {"target-x86_64", "another-target-x86_64"}
    if wait_for_build:
        for target in targets:
            flexmock(TFJobHelper).should_receive(
                "report_status_to_tests_for_test_target"
            ).with_args(
                state=BaseCommitStatus.pending,
                description="The latest build has not finished yet, "
                "waiting until it finishes before running tests for it.",
                target=target,
                url="https://dashboard.localhost/results/copr-builds/1",
            )

    flexmock(cb).should_receive("get_valid_build_targets").and_return(targets)

    tf_handler = TestingFarmHandler(
        package_config,
        job_config,
        event,
        celery_task=flexmock(request=flexmock(retries=0)),
    )
    tf_handler._db_trigger = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request
    )
    tf_handler.run()


@pytest.mark.parametrize(
    ("job_fmf_url", "pr_id", "fmf_url"),
    [
        # custom set URL
        ("https://custom.xyz/mf/fmf/", None, "https://custom.xyz/mf/fmf/"),
        # PR, from fork
        (None, 42, "https://github.com/mf/packit"),
        # if from branch
        (None, None, "https://github.com/packit/packit"),
    ],
)
def test_fmf_url(job_fmf_url, pr_id, fmf_url):
    job_config = JobConfig(
        trigger=JobConfigTriggerType.pull_request,
        type=JobType.tests,
        packages={
            "package": CommonPackageConfig(
                fmf_url=job_fmf_url,
            )
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
                .and_return(fmf_url)
                .mock()
            )
        )
    else:
        git_project.should_receive("get_web_url").and_return(
            "https://github.com/packit/packit"
        ).once()

    helper = TFJobHelper(
        service_config=flexmock(comment_command_prefix="/packit-dev"),
        package_config=flexmock(jobs=[]),
        project=git_project,
        metadata=metadata,
        db_trigger=flexmock(),
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
            )
        },
    )
    metadata = flexmock(
        event_dict={"comment": "/packit-dev test my-namespace/my-repo#10"}
    )

    git_project = flexmock()

    helper = TFJobHelper(
        service_config=flexmock(comment_command_prefix="/packit-dev"),
        package_config=flexmock(jobs=[]),
        project=git_project,
        metadata=metadata,
        db_trigger=flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
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

    flexmock(cb).should_receive("get_valid_build_targets").and_return(
        {"test-target", "another-test-target"}
    )

    flexmock(packit_service.worker.helpers.testing_farm).should_receive(
        "filter_most_recent_target_models_by_status"
    ).with_args(
        models=[additional_copr_build],
        statuses_to_filter_with=[BuildStatus.success],
    ).and_return(
        {additional_copr_build}
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
            )
        },
    )
    metadata = flexmock(
        event_dict={"comment": "/packit-dev test my-namespace/my-repo#10"}
    )

    git_project = flexmock()

    helper = TFJobHelper(
        service_config=flexmock(comment_command_prefix="/packit-dev"),
        package_config=flexmock(jobs=[]),
        project=git_project,
        metadata=metadata,
        db_trigger=flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
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
            )
        },
    )
    metadata = flexmock(
        event_dict={"comment": "/packit-dev test my-namespace/my-repo#10"}
    )

    git_project = flexmock()

    helper = TFJobHelper(
        service_config=flexmock(comment_command_prefix="/packit-dev"),
        package_config=flexmock(jobs=[]),
        project=git_project,
        metadata=metadata,
        db_trigger=flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
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
        .mock()
    )
    flexmock(cb).should_receive("get_valid_build_targets").and_return(
        {"test-target", "another-test-target"}
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
            )
        },
    )
    metadata = flexmock(
        event_dict={"comment": "/packit-dev test my/namespace/my-repo#10"}
    )

    git_project = flexmock()

    helper = TFJobHelper(
        service_config=flexmock(comment_command_prefix="/packit-dev"),
        package_config=flexmock(jobs=[]),
        project=git_project,
        metadata=metadata,
        db_trigger=flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
        job_config=job_config,
    )

    additional_copr_builds = helper.get_copr_builds_from_other_pr()

    assert additional_copr_builds is None


@pytest.mark.parametrize(
    ("chroot," "build," "additional_build," "result"),
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
                }
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
            )
        },
    )
    metadata = flexmock(
        event_dict={"comment": "/packit-dev test my/namespace/my-repo#10"}
    )

    git_project = flexmock()

    helper = TFJobHelper(
        service_config=flexmock(comment_command_prefix="/packit-dev"),
        package_config=flexmock(jobs=[]),
        project=git_project,
        metadata=metadata,
        db_trigger=flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
        job_config=job_config,
    )

    artifacts = helper._get_artifacts(
        chroot=chroot, build=build, additional_build=additional_build
    )

    assert artifacts == result


def test_check_if_actor_can_run_job_and_report():
    job = JobConfig(
        type=JobType.tests,
        trigger=JobConfigTriggerType.pull_request,
        packages={
            "package": CommonPackageConfig(
                use_internal_tf=True,
            )
        },
    )
    package_config = PackageConfig(packages={"package": CommonPackageConfig()})
    package_config.jobs = ([job],)
    flexmock(PullRequestModel).should_receive("get_or_create").and_return(
        flexmock(
            PullRequestModel(),
            job_config_trigger_type=JobConfigTriggerType.pull_request,
            id=123,
            job_trigger_model_type=JobTriggerModelType.pull_request,
            actor="actor",
        )
    )

    gh_project = flexmock(namespace="n", repo="r")
    gh_project.should_receive("can_merge_pr").with_args("actor").and_return(False)
    flexmock(EventData).should_receive("get_project").and_return(gh_project)
    flexmock(ServiceConfig).should_receive("get_project").and_return(gh_project)

    flexmock(TFJobHelper).should_receive("report_status_to_tests").once()

    event_dict = {"event_type": "PullRequestGithubEvent", "project_url": "Url"}
    assert not CanActorRunJob(package_config, job, event_dict).pre_check()
