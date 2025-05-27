# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import copy
import json
from datetime import datetime

import pytest
import requests
from celery.canvas import Signature
from celery.canvas import group as celery_group
from copr.v3 import Client
from flexmock import flexmock
from ogr.services.github import GithubProject
from ogr.utils import RequestResponse
from packit.config import (
    CommonPackageConfig,
    Deployment,
    JobConfig,
    JobConfigTriggerType,
    JobType,
    PackageConfig,
)
from packit.config.requirements import LabelRequirementsConfig, RequirementsConfig
from packit.copr_helper import CoprHelper
from packit.local_project import LocalProject
from packit.utils.koji_helper import KojiHelper

import packit_service.service.urls as urls
from packit_service.config import ServiceConfig
from packit_service.constants import COPR_API_FAIL_STATE, DEFAULT_RETRY_LIMIT
from packit_service.events import copr, koji
from packit_service.models import (
    BuildStatus,
    CoprBuildTargetModel,
    GitBranchModel,
    KojiBuildTargetModel,
    ProjectEventModelType,
    ProjectReleaseModel,
    SidetagModel,
    SRPMBuildModel,
    TestingFarmResult,
    TFTTestRunGroupModel,
    TFTTestRunTargetModel,
)
from packit_service.package_config_getter import PackageConfigGetter
from packit_service.service.urls import (
    get_copr_build_info_url,
    get_koji_build_info_url,
    get_srpm_build_info_url,
)
from packit_service.worker.handlers import CoprBuildEndHandler
from packit_service.worker.handlers.bodhi import BodhiUpdateFromSidetagHandler
from packit_service.worker.handlers.distgit import DownstreamKojiBuildHandler
from packit_service.worker.handlers.testing_farm import aliases
from packit_service.worker.helpers.build.copr_build import CoprBuildJobHelper
from packit_service.worker.helpers.testing_farm import (
    TestingFarmClient,
    TestingFarmJobHelper,
    commands,
)
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.reporting import BaseCommitStatus, StatusReporter
from packit_service.worker.tasks import (
    run_copr_build_end_handler,
    run_copr_build_start_handler,
    run_downstream_koji_scratch_build_report_handler,
    run_downstream_testing_farm_handler,
    run_koji_build_report_handler,
    run_koji_build_tag_handler,
    run_testing_farm_handler,
)
from tests.conftest import copr_build_model
from tests.spellbook import DATA_DIR, first_dict_value, get_parameters_from_results

CHROOT = "fedora-rawhide-x86_64"
EXPECTED_BUILD_CHECK_NAME = f"rpm-build:{CHROOT}"
EXPECTED_TESTING_FARM_CHECK_NAME = f"testing-farm:{CHROOT}"

pytestmark = pytest.mark.usefixtures("mock_get_valid_build_targets")


@pytest.fixture
def mock_get_valid_build_targets():
    flexmock(CoprHelper).should_receive("get_valid_build_targets").and_return(
        {
            "fedora-33-x86_64",
            "fedora-32-x86_64",
            "fedora-31-x86_64",
            "fedora-rawhide-x86_64",
        },
    )


@pytest.fixture(scope="module")
def copr_build_start():
    return json.loads((DATA_DIR / "fedmsg" / "copr_build_start.json").read_text())


@pytest.fixture(scope="module")
def copr_build_end():
    return json.loads((DATA_DIR / "fedmsg" / "copr_build_end.json").read_text())


@pytest.fixture(scope="module")
def copr_build_end_push():
    return json.loads((DATA_DIR / "fedmsg" / "copr_build_end_push.json").read_text())


@pytest.fixture(scope="module")
def copr_build_end_release():
    return json.loads((DATA_DIR / "fedmsg" / "copr_build_end_release.json").read_text())


@pytest.fixture(scope="module")
def srpm_build_start():
    return json.loads((DATA_DIR / "fedmsg" / "srpm_build_start.json").read_text())


@pytest.fixture(scope="module")
def srpm_build_end():
    return json.loads((DATA_DIR / "fedmsg" / "srpm_build_end.json").read_text())


@pytest.fixture(scope="module")
def koji_build_scratch_start():
    return json.loads(
        (DATA_DIR / "fedmsg" / "koji_build_scratch_start.json").read_text(),
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
                packages={
                    "package": CommonPackageConfig(
                        _targets=["fedora-all"],
                        specfile_path="test.spec",
                    ),
                },
            ),
        ],
        packages={
            "package": CommonPackageConfig(
                specfile_path="test.spec",
            ),
        },
    )


@pytest.fixture(scope="module")
def pc_koji_build_pr():
    return PackageConfig(
        jobs=[
            JobConfig(
                type=JobType.upstream_koji_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={
                    "package": CommonPackageConfig(
                        _targets=["fedora-all"],
                        specfile_path="test.spec",
                    ),
                },
            ),
        ],
        packages={
            "package": CommonPackageConfig(
                specfile_path="test.spec",
            ),
        },
    )


@pytest.fixture(scope="module")
def pc_koji_build_tag_specfile():
    return PackageConfig(
        jobs=[
            JobConfig(
                type=JobType.koji_build,
                trigger=JobConfigTriggerType.commit,
                sidetag_group="test",
                dependents=["packit"],
                packages={
                    "specfile": CommonPackageConfig(
                        _targets=["fedora-all"],
                        specfile_path="python-specfile.spec",
                        downstream_package_name="python-specfile",
                    ),
                },
            ),
        ],
        packages={
            "specfile": CommonPackageConfig(
                specfile_path="python-specfile.spec",
                downstream_package_name="python-specfile",
            ),
        },
    )


@pytest.fixture(scope="module")
def pc_koji_build_tag_packit():
    return PackageConfig(
        jobs=[
            JobConfig(
                type=JobType.koji_build,
                trigger=JobConfigTriggerType.koji_build,
                sidetag_group="test",
                dependencies=["python-specfile"],
                packages={
                    "packit": CommonPackageConfig(
                        _targets=["fedora-all"],
                        specfile_path="packit.spec",
                        downstream_package_name="packit",
                    ),
                },
            ),
            JobConfig(
                type=JobType.bodhi_update,
                trigger=JobConfigTriggerType.koji_build,
                sidetag_group="test",
                dependencies=["python-specfile"],
                packages={
                    "packit": CommonPackageConfig(
                        _targets=["fedora-all"],
                        specfile_path="packit.spec",
                        downstream_package_name="packit",
                    ),
                },
            ),
        ],
        packages={
            "packit": CommonPackageConfig(
                specfile_path="packit.spec",
                downstream_package_name="packit",
            ),
        },
    )


@pytest.fixture(scope="module")
def pc_build_push():
    return PackageConfig(
        jobs=[
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.commit,
                packages={
                    "package": CommonPackageConfig(
                        _targets=["fedora-all"],
                        specfile_path="test.spec",
                        branch="build-branch",
                    ),
                },
            ),
        ],
        packages={
            "package": CommonPackageConfig(
                specfile_path="test.spec",
            ),
        },
    )


@pytest.fixture(scope="module")
def pc_build_release():
    return PackageConfig(
        jobs=[
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.release,
                packages={
                    "package": CommonPackageConfig(
                        _targets=["fedora-all"],
                        specfile_path="test.spec",
                    ),
                },
            ),
        ],
        packages={
            "package": CommonPackageConfig(
                specfile_path="test.spec",
            ),
        },
    )


@pytest.fixture(scope="module")
def copr_build_branch_push():
    return copr_build_model(
        job_config_trigger_type=JobConfigTriggerType.commit,
        project_event_model_type=ProjectEventModelType.branch_push,
        name="build-branch",
        task_accepted_time=datetime.now(),
        trigger_kls=GitBranchModel,
    )


@pytest.fixture(scope="module")
def copr_build_release():
    return copr_build_model(
        job_config_trigger_type=JobConfigTriggerType.release,
        project_event_model_type=ProjectEventModelType.release,
        tag_name="v1.0.1",
        commit_hash="0011223344",
        task_accepted_time=datetime.now(),
        trigger_kls=ProjectReleaseModel,
    )


@pytest.mark.parametrize(
    "pc_comment_pr_succ,pr_comment_called,pr_comment_exists",
    (
        (True, True, True),
        (True, True, False),
        (False, False, False),
    ),
)
def test_copr_build_end(
    copr_build_end,
    pc_build_pr,
    copr_build_pr,
    pc_comment_pr_succ,
    pr_comment_called,
    pr_comment_exists,
):
    def get_comments(*args, **kwargs):
        if not pr_comment_exists:
            return []

        return [
            flexmock(
                author="packit-as-a-service[bot]",
                body="Congratulations! One of the builds has completed. :champagne:\n\n"
                "You can install the built RPMs by following these steps:\n\n* "
                "`sudo yum install -y dnf-plugins-core` on RHEL 8\n* "
                "`sudo dnf install -y dnf-plugins-core` on Fedora\n* "
                "`dnf copr enable packit/packit-service-hello-world-24`\n* "
                "And now you can install the packages.\n\n"
                "Please note that the RPMs should be used only in a testing environment.",
            ),
        ]

    pr = flexmock(source_project=flexmock(), get_comments=get_comments)
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    pc_build_pr.jobs[0].notifications.pull_request.successful_build = pc_comment_pr_succ
    flexmock(copr.CoprBuild).should_receive("get_packages_config").and_return(
        pc_build_pr,
    )
    flexmock(CoprHelper).should_receive("get_copr_client").and_return(
        Client(config={"username": "packit", "copr_url": "https://dummy.url"}),
    )
    if pr_comment_called and not pr_comment_exists:
        pr.should_receive("comment")
    else:
        pr.should_receive("comment").never()
    flexmock(CoprBuildTargetModel).should_receive("get_by_build_id").and_return(
        copr_build_pr,
    )

    copr_build_pr.should_call("set_status").with_args(BuildStatus.success).once()
    copr_build_pr.should_receive("set_end_time").once()
    copr_build_pr.should_receive("get_package_name").and_return(None)

    url = get_copr_build_info_url(1)
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)
    # check if packit-service set correct PR status
    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.success,
        description="RPMs were built successfully.",
        url=url,
        check_names=CoprBuildJobHelper.get_build_check_cls(copr_build_end["chroot"]),
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    # no test job defined => testing farm should be skipped
    flexmock(TestingFarmJobHelper).should_receive("run_testing_farm").times(0)
    flexmock(celery_group).should_receive("apply_async").once()

    # fix SRPM url since it touches multiple classes

    (
        flexmock(CoprBuildJobHelper)
        .should_receive("get_build")
        .with_args(1044215)
        .and_return(flexmock(source_package={"url": "https://my.host/my.srpm"}))
        .at_least()
        .once()
    )

    (
        flexmock(CoprBuildJobHelper)
        .should_receive("get_build_chroot")
        .with_args(1, "some-target")
        .and_return(flexmock(ended_on=1666889710))
        .at_least()
        .once()
    )

    # fix SRPM url since it touches multiple classes
    flexmock(copr_build_pr._srpm_build_for_mocking).should_receive("set_url").with_args(
        "https://my.host/my.srpm",
    ).mock()

    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    processing_results = SteveJobs().process_message(copr_build_end)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    flexmock(CoprBuildJobHelper).should_receive("get_built_packages").and_return([])

    run_copr_build_end_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )


def test_copr_build_end_push(
    copr_build_end_push,
    pc_build_push,
    copr_build_branch_push,
):
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("get_pr").and_return(
        # we cannot comment for branch push events
        flexmock(source_project=flexmock()).should_receive("comment").never(),
    )
    flexmock(copr.CoprBuild).should_receive("get_packages_config").and_return(
        pc_build_push,
    )
    flexmock(CoprHelper).should_receive("get_copr_client").and_return(
        Client(config={"username": "packit", "copr_url": "https://dummy.url"}),
    )

    flexmock(CoprBuildTargetModel).should_receive("get_by_build_id").and_return(
        copr_build_branch_push,
    )

    copr_build_branch_push.should_receive("set_status").with_args(BuildStatus.success)
    copr_build_branch_push.should_receive("set_end_time").once()
    copr_build_branch_push.should_receive("get_package_name").and_return(None)
    url = get_copr_build_info_url(1)
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)
    # check if packit-service set correct PR status
    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.success,
        description="RPMs were built successfully.",
        url=url,
        check_names=CoprBuildJobHelper.get_build_check_cls(
            copr_build_end_push["chroot"],
            project_event_identifier=copr_build_branch_push.get_project_event_object().name,
        ),
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    flexmock(celery_group).should_receive("apply_async").once()

    # skip SRPM url since it touches multiple classes
    flexmock(CoprBuildEndHandler).should_receive("set_srpm_url").and_return(None)

    (
        flexmock(CoprBuildJobHelper)
        .should_receive("get_build_chroot")
        .with_args(1, "some-target")
        .and_return(flexmock(ended_on=1666889710))
        .at_least()
        .once()
    )

    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    processing_results = SteveJobs().process_message(copr_build_end_push)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    flexmock(CoprBuildJobHelper).should_receive("get_built_packages").and_return([])

    run_copr_build_end_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )


def test_copr_build_end_release(
    copr_build_end_release,
    pc_build_release,
    copr_build_release,
):
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("get_pr").and_return(
        # we cannot comment for branch push events
        flexmock(source_project=flexmock()).should_receive("comment").never().mock(),
    )
    flexmock(copr.CoprBuild).should_receive("get_packages_config").and_return(
        pc_build_release,
    )
    flexmock(CoprHelper).should_receive("get_copr_client").and_return(
        Client(config={"username": "packit", "copr_url": "https://dummy.url"}),
    )

    flexmock(CoprBuildTargetModel).should_receive("get_by_build_id").and_return(
        copr_build_release,
    )
    copr_build_release.should_receive("set_status").with_args(BuildStatus.success)
    copr_build_release.should_receive("set_end_time").once()
    copr_build_release.should_receive("get_package_name").and_return(None)
    url = get_copr_build_info_url(1)
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)
    # check if packit-service set correct PR status
    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.success,
        description="RPMs were built successfully.",
        url=url,
        check_names=CoprBuildJobHelper.get_build_check_cls(
            copr_build_end_release["chroot"],
            project_event_identifier=copr_build_release.get_project_event_object().tag_name,
        ),
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    flexmock(celery_group).should_receive("apply_async").once()

    flexmock(CoprBuildJobHelper).should_receive("get_build_chroot").with_args(
        1,
        "some-target",
    ).and_return(flexmock(ended_on=1666889710)).once()

    # skip SRPM url since it touches multiple classes
    flexmock(CoprBuildEndHandler).should_receive("set_srpm_url").and_return(None)

    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    processing_results = SteveJobs().process_message(copr_build_end_release)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    flexmock(CoprBuildJobHelper).should_receive("get_built_packages").and_return([])

    run_copr_build_end_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )


def test_copr_build_end_testing_farm(copr_build_end, copr_build_pr):
    ServiceConfig.get_service_config().testing_farm_api_url = (
        "https://api.dev.testing-farm.io/v0.1/"
    )
    ServiceConfig.get_service_config().testing_farm_secret = "secret token"

    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("get_pr").and_return(
        flexmock(
            source_project=flexmock(
                get_web_url=lambda: "https://github.com/source/bar",
            ),
            target_project=flexmock(
                get_web_url=lambda: "https://github.com/target/bar",
            ),
            head_commit="0011223344",
            target_branch_head_commit="deadbeef",
            source_branch="the-source-branch",
            target_branch="the-target-branch",
        )
        .should_receive("comment")
        .mock(),
    )
    urls.DASHBOARD_URL = "https://dashboard.localhost"

    config = PackageConfig(
        packages={
            "package": CommonPackageConfig(
                specfile_path="test.spec",
            ),
        },
        jobs=[
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={
                    "package": CommonPackageConfig(
                        _targets=["fedora-rawhide"],
                        specfile_path="test.spec",
                    ),
                },
            ),
            JobConfig(
                type=JobType.tests,
                trigger=JobConfigTriggerType.pull_request,
                packages={
                    "package": CommonPackageConfig(
                        _targets=["fedora-rawhide"],
                        specfile_path="test.spec",
                    ),
                },
            ),
        ],
    )

    flexmock(copr.CoprBuild).should_receive("get_packages_config").and_return(
        config,
    )
    flexmock(CoprHelper).should_receive("get_copr_client").and_return(
        Client(config={"username": "packit", "copr_url": "https://dummy.url"}),
    )
    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).and_return(config)

    flexmock(LocalProject).should_receive("refresh_the_arguments").and_return(None)

    flexmock(CoprBuildTargetModel).should_receive("get_by_build_id").and_return(
        copr_build_pr,
    )
    flexmock(CoprBuildTargetModel).should_receive("get_by_id").and_return(copr_build_pr)
    copr_build_pr.should_call("set_status").with_args(BuildStatus.success).once()
    copr_build_pr.should_receive("set_end_time").once()
    copr_build_pr.should_receive("get_package_name").and_return(None)
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)
    # check if packit-service set correct PR status
    url = get_copr_build_info_url(1)
    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.success,
        description="RPMs were built successfully.",
        url=url,
        check_names=EXPECTED_BUILD_CHECK_NAME,
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.pending,
        description="RPMs were built successfully.",
        url=url,
        check_names=EXPECTED_TESTING_FARM_CHECK_NAME,
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    payload = {
        "test": {
            "tmt": {
                "url": "https://github.com/source/bar",
                "ref": "0011223344",
                "merge_sha": "deadbeef",
                "path": ".",
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
                        "initiator": "packit",
                    },
                },
                "artifacts": [
                    {
                        "id": "1:fedora-rawhide-x86_64",
                        "type": "fedora-copr-build",
                        "packages": ["hello-world-0.1-1.noarch"],
                    },
                ],
                "variables": {
                    "PACKIT_FULL_REPO_NAME": "packit-service/hello-world",
                    "PACKIT_PACKAGE_NVR": "hello-world-0.1-1",
                    "PACKIT_BUILD_LOG_URL": "https://log-url",
                    "PACKIT_COMMIT_SHA": "0011223344",
                    "PACKIT_SOURCE_SHA": "0011223344",
                    "PACKIT_TARGET_SHA": "deadbeef",
                    "PACKIT_SOURCE_BRANCH": "the-source-branch",
                    "PACKIT_TARGET_BRANCH": "the-target-branch",
                    "PACKIT_SOURCE_URL": "https://github.com/source/bar",
                    "PACKIT_TARGET_URL": "https://github.com/target/bar",
                    "PACKIT_PR_ID": 24,
                    "PACKIT_COPR_PROJECT": "some-owner/some-project",
                    "PACKIT_COPR_RPMS": "hello-world-0.1-1.noarch",
                },
            },
        ],
        "notification": {
            "webhook": {
                "url": "https://prod.packit.dev/api/testing-farm/results",
                "token": "secret token",
            },
        },
    }

    flexmock(TestingFarmJobHelper).should_receive("is_fmf_configured").and_return(True)
    flexmock(TestingFarmClient).should_receive("distro2compose").with_args(
        "fedora-rawhide",
        object,
    ).and_return("Fedora-Rawhide")

    pipeline_id = "5e8079d8-f181-41cf-af96-28e99774eb68"
    flexmock(TestingFarmClient).should_receive(
        "send_testing_farm_request",
    ).with_args(endpoint="requests", method="POST", data=payload).and_return(
        RequestResponse(
            status_code=200,
            ok=True,
            content=json.dumps({"id": pipeline_id}).encode(),
            json={"id": pipeline_id},
        ),
    )

    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.running,
        description="Build succeeded. Submitting the tests ...",
        check_names=EXPECTED_TESTING_FARM_CHECK_NAME,
        url="",
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    flexmock(GithubProject).should_receive("get_web_url").and_return(
        "https://github.com/foo/bar",
    )

    tft_test_run_model = (
        flexmock(
            id=5,
            copr_builds=[copr_build_pr],
            status=TestingFarmResult.new,
            target="fedora-rawhide-x86_64",
        )
        .should_receive("set_pipeline_id")
        .with_args(pipeline_id)
        .once()
        .mock()
    )
    group = flexmock(grouped_targets=[tft_test_run_model])
    flexmock(TFTTestRunGroupModel).should_receive("create").with_args(
        [copr_build_pr.group_of_targets.runs[-1]],
        ranch="public",
    ).and_return(group)
    flexmock(TFTTestRunTargetModel).should_receive("create").with_args(
        pipeline_id=None,
        identifier=None,
        status=TestingFarmResult.new,
        target="fedora-rawhide-x86_64",
        web_url=None,
        test_run_group=group,
        copr_build_targets=[copr_build_pr],
        data={"base_project_url": "https://github.com/foo/bar"},
    ).and_return(tft_test_run_model)

    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.running,
        description="Tests have been submitted ...",
        url="https://dashboard.localhost/jobs/testing-farm/5",
        check_names=EXPECTED_TESTING_FARM_CHECK_NAME,
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(celery_group).should_receive("apply_async").once()

    # skip SRPM url since it touches multiple classes
    flexmock(CoprBuildEndHandler).should_receive("set_srpm_url").and_return(None)

    (
        flexmock(CoprBuildJobHelper)
        .should_receive("get_build_chroot")
        .with_args(1, "some-target")
        .and_return(flexmock(ended_on=1666889710))
        .at_least()
        .once()
    )

    flexmock(Pushgateway).should_receive("push").times(3).and_return()

    processing_results = SteveJobs().process_message(copr_build_end)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    flexmock(CoprBuildJobHelper).should_receive("get_built_packages").and_return([])

    run_copr_build_end_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    flexmock(CoprBuildTargetModel).should_receive("get_all_by").and_return(
        [copr_build_pr],
    )
    event_dict["tests_targets_override"] = [("fedora-rawhide-x86_64", None)]
    run_testing_farm_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
        build_id=1,
    )


def test_copr_build_end_testing_farm_pr_branch(copr_build_end, copr_build_pr):
    ServiceConfig.get_service_config().testing_farm_api_url = (
        "https://api.dev.testing-farm.io/v0.1/"
    )
    ServiceConfig.get_service_config().testing_farm_secret = "secret token"

    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("get_pr").and_return(
        flexmock(
            source_project=flexmock(
                get_web_url=lambda: "https://github.com/source/bar",
            ),
            target_project=flexmock(
                get_web_url=lambda: "https://github.com/target/bar",
            ),
            head_commit="0011223344",
            target_branch_head_commit="deadbeef",
            source_branch="the-source-branch",
            target_branch="the-target-branch",
        )
        .should_receive("comment")
        .mock(),
    )
    urls.DASHBOARD_URL = "https://dashboard.localhost"

    config = PackageConfig(
        packages={
            "package": CommonPackageConfig(
                specfile_path="test.spec",
            ),
        },
        jobs=[
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={
                    "package": CommonPackageConfig(
                        _targets=["fedora-rawhide"],
                        specfile_path="test.spec",
                    ),
                },
            ),
            JobConfig(
                type=JobType.tests,
                trigger=JobConfigTriggerType.pull_request,
                packages={
                    "package": CommonPackageConfig(
                        _targets=["fedora-rawhide"],
                        specfile_path="test.spec",
                        branch="the-target-branch",
                    ),
                },
            ),
        ],
    )

    flexmock(copr.CoprBuild).should_receive("get_packages_config").and_return(
        config,
    )
    flexmock(CoprHelper).should_receive("get_copr_client").and_return(
        Client(config={"username": "packit", "copr_url": "https://dummy.url"}),
    )
    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).and_return(config)

    flexmock(LocalProject).should_receive("refresh_the_arguments").and_return(None)

    flexmock(CoprBuildTargetModel).should_receive("get_by_build_id").and_return(
        copr_build_pr,
    )
    flexmock(CoprBuildTargetModel).should_receive("get_by_id").and_return(copr_build_pr)
    copr_build_pr.should_call("set_status").with_args(BuildStatus.success).once()
    copr_build_pr.should_receive("set_end_time").once()
    copr_build_pr.should_receive("get_package_name").and_return(None)
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)
    # check if packit-service set correct PR status
    url = get_copr_build_info_url(1)
    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.success,
        description="RPMs were built successfully.",
        url=url,
        check_names=EXPECTED_BUILD_CHECK_NAME,
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.pending,
        description="RPMs were built successfully.",
        url=url,
        check_names=EXPECTED_TESTING_FARM_CHECK_NAME,
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    flexmock(GithubProject).should_receive("get_web_url").and_return(
        "https://github.com/foo/bar",
    )

    flexmock(Signature).should_receive("apply_async").once()
    flexmock(celery_group).should_receive("apply_async").once()

    # skip SRPM url since it touches multiple classes
    flexmock(CoprBuildEndHandler).should_receive("set_srpm_url").and_return(None)

    (
        flexmock(CoprBuildJobHelper)
        .should_receive("get_build_chroot")
        .with_args(1, "some-target")
        .and_return(flexmock(ended_on=1666889710))
        .at_least()
        .once()
    )

    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    processing_results = SteveJobs().process_message(copr_build_end)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    flexmock(CoprBuildJobHelper).should_receive("get_built_packages").and_return([])

    run_copr_build_end_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )


def test_copr_build_end_testing_farm_different_pr_branch(copr_build_end, copr_build_pr):
    ServiceConfig.get_service_config().testing_farm_api_url = (
        "https://api.dev.testing-farm.io/v0.1/"
    )
    ServiceConfig.get_service_config().testing_farm_secret = "secret token"

    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("get_pr").and_return(
        flexmock(
            source_project=flexmock(
                get_web_url=lambda: "https://github.com/source/bar",
            ),
            target_project=flexmock(
                get_web_url=lambda: "https://github.com/target/bar",
            ),
            head_commit="0011223344",
            target_branch_head_commit="deadbeef",
            source_branch="the-source-branch",
            target_branch="the-target-branch",
        )
        .should_receive("comment")
        .mock(),
    )
    urls.DASHBOARD_URL = "https://dashboard.localhost"

    config = PackageConfig(
        packages={
            "package": CommonPackageConfig(
                specfile_path="test.spec",
            ),
        },
        jobs=[
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={
                    "package": CommonPackageConfig(
                        _targets=["fedora-rawhide"],
                        specfile_path="test.spec",
                    ),
                },
            ),
            JobConfig(
                type=JobType.tests,
                trigger=JobConfigTriggerType.pull_request,
                packages={
                    "package": CommonPackageConfig(
                        _targets=["fedora-rawhide"],
                        specfile_path="test.spec",
                        branch="the-other-branch",
                    ),
                },
            ),
        ],
    )

    flexmock(copr.CoprBuild).should_receive("get_packages_config").and_return(
        config,
    )
    flexmock(CoprHelper).should_receive("get_copr_client").and_return(
        Client(config={"username": "packit", "copr_url": "https://dummy.url"}),
    )
    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).and_return(config)

    flexmock(LocalProject).should_receive("refresh_the_arguments").and_return(None)

    flexmock(CoprBuildTargetModel).should_receive("get_by_build_id").and_return(
        copr_build_pr,
    )
    flexmock(CoprBuildTargetModel).should_receive("get_by_id").and_return(copr_build_pr)
    copr_build_pr.should_call("set_status").with_args(BuildStatus.success).once()
    copr_build_pr.should_receive("set_end_time").once()
    copr_build_pr.should_receive("get_package_name").and_return(None)
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)
    # check if packit-service set correct PR status
    url = get_copr_build_info_url(1)
    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.success,
        description="RPMs were built successfully.",
        url=url,
        check_names=EXPECTED_BUILD_CHECK_NAME,
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.pending,
        description="RPMs were built successfully.",
        url=url,
        check_names=EXPECTED_TESTING_FARM_CHECK_NAME,
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).never()

    flexmock(GithubProject).should_receive("get_web_url").and_return(
        "https://github.com/foo/bar",
    )

    flexmock(celery_group).should_receive("apply_async").once()

    # skip SRPM url since it touches multiple classes
    flexmock(CoprBuildEndHandler).should_receive("set_srpm_url").and_return(None)

    (
        flexmock(CoprBuildJobHelper)
        .should_receive("get_build_chroot")
        .with_args(1, "some-target")
        .and_return(flexmock(ended_on=1666889710))
        .at_least()
        .once()
    )

    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    processing_results = SteveJobs().process_message(copr_build_end)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    flexmock(CoprBuildJobHelper).should_receive("get_built_packages").and_return([])

    run_copr_build_end_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )


@pytest.mark.parametrize(
    "build_status,report_manual_triggered_times",
    (
        (BuildStatus.success, 0),
        (BuildStatus.failure, 0),
    ),
)
def test_copr_build_end_testing_farm_manual_trigger(
    copr_build_end,
    copr_build_pr,
    build_status,
    report_manual_triggered_times,
):
    ServiceConfig.get_service_config().testing_farm_api_url = (
        "https://api.dev.testing-farm.io/v0.1/"
    )
    ServiceConfig.get_service_config().testing_farm_secret = "secret token"

    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("get_pr").and_return(
        flexmock(
            source_project=flexmock(
                get_web_url=lambda: "https://github.com/source/bar",
            ),
            target_project=flexmock(
                get_web_url=lambda: "https://github.com/target/bar",
            ),
            head_commit="0011223344",
            target_branch_head_commit="deadbeef",
            source_branch="the-source-branch",
            target_branch="the-target-branch",
        )
        .should_receive("comment")
        .mock(),
    )
    urls.DASHBOARD_URL = "https://dashboard.localhost"

    config = PackageConfig(
        packages={
            "package": CommonPackageConfig(
                specfile_path="test.spec",
            ),
        },
        jobs=[
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={
                    "package": CommonPackageConfig(
                        _targets=["fedora-rawhide"],
                        specfile_path="test.spec",
                    ),
                },
            ),
            JobConfig(
                type=JobType.tests,
                trigger=JobConfigTriggerType.pull_request,
                manual_trigger=True,
                packages={
                    "package": CommonPackageConfig(
                        _targets=["fedora-rawhide"],
                        specfile_path="test.spec",
                    ),
                },
            ),
        ],
    )

    flexmock(copr.CoprBuild).should_receive("get_packages_config").and_return(
        config,
    )
    flexmock(CoprHelper).should_receive("get_copr_client").and_return(
        Client(config={"username": "packit", "copr_url": "https://dummy.url"}),
    )
    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).and_return(config)

    flexmock(LocalProject).should_receive("refresh_the_arguments").and_return(None)

    flexmock(CoprBuildTargetModel).should_receive("get_by_build_id").and_return(
        copr_build_pr,
    )
    flexmock(CoprBuildTargetModel).should_receive("get_by_id").and_return(copr_build_pr)
    copr_build_pr.should_call("set_status").with_args(build_status).once()
    copr_build_pr.should_receive("set_end_time").once()
    copr_build_pr.should_receive("get_package_name").and_return(None)
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)
    # check if packit-service set correct PR status
    url = get_copr_build_info_url(1)
    if build_status == BuildStatus.success:
        flexmock(StatusReporter).should_receive("report").with_args(
            state=BaseCommitStatus.success,
            description="RPMs were built successfully.",
            url=url,
            check_names=EXPECTED_BUILD_CHECK_NAME,
            markdown_content=None,
            links_to_external_services=None,
            update_feedback_time=object,
        ).once()
    else:
        flexmock(StatusReporter).should_receive("report").with_args(
            description="RPMs failed to be built.",
            state=BaseCommitStatus.failure,
            url=url,
            check_names=EXPECTED_BUILD_CHECK_NAME,
            markdown_content=None,
            links_to_external_services=None,
            update_feedback_time=object,
        ).once()

    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.failure,
        description="RPMs failed to be built.",
        url=url,
        check_names=EXPECTED_TESTING_FARM_CHECK_NAME,
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).times(report_manual_triggered_times)

    flexmock(GithubProject).should_receive("get_web_url").and_return(
        "https://github.com/foo/bar",
    )

    flexmock(celery_group).should_receive("apply_async").once()

    # skip SRPM url since it touches multiple classes
    flexmock(CoprBuildEndHandler).should_receive("set_srpm_url").and_return(None)

    (
        flexmock(CoprBuildJobHelper)
        .should_receive("get_build_chroot")
        .with_args(1, "some-target")
        .and_return(flexmock(ended_on=1666889710))
        .at_least()
        .once()
    )

    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    copr_event = copy.deepcopy(copr_build_end)
    copr_event["status"] = 1 if build_status == BuildStatus.success else 0
    processing_results = SteveJobs().process_message(copr_event)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    flexmock(CoprBuildJobHelper).should_receive("get_built_packages").and_return([])

    run_copr_build_end_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )


def test_copr_build_end_testing_farm_labels_matching(copr_build_end, copr_build_pr):
    ServiceConfig.get_service_config().testing_farm_api_url = (
        "https://api.dev.testing-farm.io/v0.1/"
    )
    ServiceConfig.get_service_config().testing_farm_secret = "secret token"

    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("get_pr").and_return(
        flexmock(
            source_project=flexmock(
                get_web_url=lambda: "https://github.com/source/bar",
            ),
            target_project=flexmock(
                get_web_url=lambda: "https://github.com/target/bar",
            ),
            head_commit="0011223344",
            target_branch_head_commit="deadbeef",
            source_branch="the-source-branch",
            target_branch="the-target-branch",
            id=123,
            labels=[flexmock(name="a-label")],
        )
        .should_receive("comment")
        .mock(),
    )
    urls.DASHBOARD_URL = "https://dashboard.localhost"

    config = PackageConfig(
        packages={
            "package": CommonPackageConfig(
                specfile_path="test.spec",
            ),
        },
        jobs=[
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={
                    "package": CommonPackageConfig(
                        _targets=["fedora-rawhide"],
                        specfile_path="test.spec",
                    ),
                },
            ),
            JobConfig(
                type=JobType.tests,
                trigger=JobConfigTriggerType.pull_request,
                packages={
                    "package": CommonPackageConfig(
                        _targets=["fedora-rawhide"],
                        specfile_path="test.spec",
                        require=RequirementsConfig(
                            label=LabelRequirementsConfig(present=["a-label"]),
                        ),
                    ),
                },
            ),
        ],
    )

    flexmock(copr.CoprBuild).should_receive("get_packages_config").and_return(
        config,
    )
    flexmock(CoprHelper).should_receive("get_copr_client").and_return(
        Client(config={"username": "packit", "copr_url": "https://dummy.url"}),
    )
    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).and_return(config)

    flexmock(LocalProject).should_receive("refresh_the_arguments").and_return(None)

    flexmock(CoprBuildTargetModel).should_receive("get_by_build_id").and_return(
        copr_build_pr,
    )
    flexmock(CoprBuildTargetModel).should_receive("get_by_id").and_return(copr_build_pr)
    copr_build_pr.should_call("set_status").with_args(BuildStatus.success).once()
    copr_build_pr.should_receive("set_end_time").once()
    copr_build_pr.should_receive("get_package_name").and_return(None)
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)
    # check if packit-service set correct PR status
    url = get_copr_build_info_url(1)
    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.success,
        description="RPMs were built successfully.",
        url=url,
        check_names=EXPECTED_BUILD_CHECK_NAME,
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.pending,
        description="RPMs were built successfully.",
        url=url,
        check_names=EXPECTED_TESTING_FARM_CHECK_NAME,
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    flexmock(GithubProject).should_receive("get_web_url").and_return(
        "https://github.com/foo/bar",
    )

    flexmock(Signature).should_receive("apply_async").once()
    flexmock(celery_group).should_receive("apply_async").once()

    # skip SRPM url since it touches multiple classes
    flexmock(CoprBuildEndHandler).should_receive("set_srpm_url").and_return(None)

    (
        flexmock(CoprBuildJobHelper)
        .should_receive("get_build_chroot")
        .with_args(1, "some-target")
        .and_return(flexmock(ended_on=1666889710))
        .at_least()
        .once()
    )

    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    processing_results = SteveJobs().process_message(copr_build_end)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    flexmock(CoprBuildJobHelper).should_receive("get_built_packages").and_return([])

    run_copr_build_end_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )


def test_copr_build_end_testing_farm_labels_not_matching(copr_build_end, copr_build_pr):
    ServiceConfig.get_service_config().testing_farm_api_url = (
        "https://api.dev.testing-farm.io/v0.1/"
    )
    ServiceConfig.get_service_config().testing_farm_secret = "secret token"

    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("get_pr").and_return(
        flexmock(
            source_project=flexmock(
                get_web_url=lambda: "https://github.com/source/bar",
            ),
            target_project=flexmock(
                get_web_url=lambda: "https://github.com/target/bar",
            ),
            head_commit="0011223344",
            target_branch_head_commit="deadbeef",
            source_branch="the-source-branch",
            target_branch="the-target-branch",
            id=123,
            labels=[flexmock(name="another-label")],
        )
        .should_receive("comment")
        .mock(),
    )
    urls.DASHBOARD_URL = "https://dashboard.localhost"

    config = PackageConfig(
        packages={
            "package": CommonPackageConfig(
                specfile_path="test.spec",
            ),
        },
        jobs=[
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={
                    "package": CommonPackageConfig(
                        _targets=["fedora-rawhide"],
                        specfile_path="test.spec",
                    ),
                },
            ),
            JobConfig(
                type=JobType.tests,
                trigger=JobConfigTriggerType.pull_request,
                packages={
                    "package": CommonPackageConfig(
                        _targets=["fedora-rawhide"],
                        specfile_path="test.spec",
                        require=RequirementsConfig(
                            label=LabelRequirementsConfig(present=["a-label"]),
                        ),
                    ),
                },
            ),
        ],
    )

    flexmock(copr.CoprBuild).should_receive("get_packages_config").and_return(
        config,
    )
    flexmock(CoprHelper).should_receive("get_copr_client").and_return(
        Client(config={"username": "packit", "copr_url": "https://dummy.url"}),
    )
    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).and_return(config)

    flexmock(LocalProject).should_receive("refresh_the_arguments").and_return(None)

    flexmock(CoprBuildTargetModel).should_receive("get_by_build_id").and_return(
        copr_build_pr,
    )
    flexmock(CoprBuildTargetModel).should_receive("get_by_id").and_return(copr_build_pr)
    copr_build_pr.should_call("set_status").with_args(BuildStatus.success).once()
    copr_build_pr.should_receive("set_end_time").once()
    copr_build_pr.should_receive("get_package_name").and_return(None)
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)
    # check if packit-service set correct PR status
    url = get_copr_build_info_url(1)
    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.success,
        description="RPMs were built successfully.",
        url=url,
        check_names=EXPECTED_BUILD_CHECK_NAME,
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.pending,
        description="RPMs were built successfully.",
        url=url,
        check_names=EXPECTED_TESTING_FARM_CHECK_NAME,
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    flexmock(GithubProject).should_receive("get_web_url").and_return(
        "https://github.com/foo/bar",
    )

    flexmock(celery_group).should_receive("apply_async").once()

    # skip SRPM url since it touches multiple classes
    flexmock(CoprBuildEndHandler).should_receive("set_srpm_url").and_return(None)

    (
        flexmock(CoprBuildJobHelper)
        .should_receive("get_build_chroot")
        .with_args(1, "some-target")
        .and_return(flexmock(ended_on=1666889710))
        .at_least()
        .once()
    )

    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    processing_results = SteveJobs().process_message(copr_build_end)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    flexmock(CoprBuildJobHelper).should_receive("get_built_packages").and_return([])

    run_copr_build_end_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )


def test_copr_build_end_push_testing_farm(copr_build_end_push, copr_build_branch_push):
    config = PackageConfig(
        packages={
            "package": CommonPackageConfig(
                specfile_path="test.spec",
            ),
        },
        jobs=[
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.commit,
                packages={
                    "package": CommonPackageConfig(
                        _targets=["fedora-all"],
                        specfile_path="test.spec",
                        branch="build-branch",
                    ),
                },
            ),
            JobConfig(
                type=JobType.tests,
                trigger=JobConfigTriggerType.commit,
                packages={
                    "package": CommonPackageConfig(
                        _targets=["fedora-all"],
                        specfile_path="test.spec",
                        branch="build-branch",
                    ),
                },
            ),
        ],
    )

    flexmock(copr.CoprBuild).should_receive("get_packages_config").and_return(
        config,
    )
    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).and_return(config)

    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("get_pr").and_return(
        # we cannot comment for branch push events
        flexmock(source_project=flexmock()).should_receive("comment").never(),
    )
    flexmock(CoprHelper).should_receive("get_copr_client").and_return(
        Client(config={"username": "packit", "copr_url": "https://dummy.url"}),
    )

    flexmock(CoprBuildTargetModel).should_receive("get_by_build_id").and_return(
        copr_build_branch_push,
    )

    copr_build_branch_push.should_receive("set_status").with_args(BuildStatus.success)
    copr_build_branch_push.should_receive("set_end_time").once()
    copr_build_branch_push.should_receive("get_package_name").and_return(None)
    url = get_copr_build_info_url(1)
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)
    flexmock(CoprBuildJobHelper).should_receive(
        "report_status_to_build_for_chroot",
    ).with_args(
        state=BaseCommitStatus.success,
        description="RPMs were built successfully.",
        url=url,
        chroot=copr_build_end_push["chroot"],
    ).once()
    flexmock(CoprBuildJobHelper).should_receive(
        "report_status_to_all_test_jobs_for_chroot",
    ).with_args(
        state=BaseCommitStatus.pending,
        description="RPMs were built successfully.",
        url=url,
        chroot=copr_build_end_push["chroot"],
    ).once()

    flexmock(Signature).should_receive("apply_async").once()
    flexmock(celery_group).should_receive("apply_async").once()

    # skip SRPM url since it touches multiple classes
    flexmock(CoprBuildEndHandler).should_receive("set_srpm_url").and_return(None)

    (
        flexmock(CoprBuildJobHelper)
        .should_receive("get_build_chroot")
        .with_args(1, "some-target")
        .and_return(flexmock(ended_on=1666889710))
        .at_least()
        .once()
    )

    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    processing_results = SteveJobs().process_message(copr_build_end_push)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    flexmock(CoprBuildJobHelper).should_receive("get_built_packages").and_return([])

    run_copr_build_end_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )


def test_copr_build_end_push_testing_farm_different_branch(
    copr_build_end_push,
    copr_build_branch_push,
):
    config = PackageConfig(
        packages={
            "package": CommonPackageConfig(
                specfile_path="test.spec",
            ),
        },
        jobs=[
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.commit,
                packages={
                    "package": CommonPackageConfig(
                        _targets=["fedora-all"],
                        specfile_path="test.spec",
                        branch="build-branch",
                    ),
                },
            ),
            JobConfig(
                type=JobType.tests,
                trigger=JobConfigTriggerType.commit,
                packages={
                    "package": CommonPackageConfig(
                        _targets=["fedora-all"],
                        specfile_path="test.spec",
                        branch="test-branch",
                    ),
                },
            ),
        ],
    )

    flexmock(copr.CoprBuild).should_receive("get_packages_config").and_return(
        config,
    )
    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).and_return(config)

    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("get_pr").and_return(
        # we cannot comment for branch push events
        flexmock(source_project=flexmock()).should_receive("comment").never(),
    )
    flexmock(CoprHelper).should_receive("get_copr_client").and_return(
        Client(config={"username": "packit", "copr_url": "https://dummy.url"}),
    )

    flexmock(CoprBuildTargetModel).should_receive("get_by_build_id").and_return(
        copr_build_branch_push,
    )

    copr_build_branch_push.should_receive("set_status").with_args(BuildStatus.success)
    copr_build_branch_push.should_receive("set_end_time").once()
    copr_build_branch_push.should_receive("get_package_name").and_return(None)
    url = get_copr_build_info_url(1)
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)
    # check if packit-service set correct PR status
    flexmock(CoprBuildJobHelper).should_receive(
        "report_status_to_build_for_chroot",
    ).with_args(
        state=BaseCommitStatus.success,
        description="RPMs were built successfully.",
        url=url,
        chroot=copr_build_end_push["chroot"],
    ).once()

    flexmock(celery_group).should_receive("apply_async").once()

    # skip SRPM url since it touches multiple classes
    flexmock(CoprBuildEndHandler).should_receive("set_srpm_url").and_return(None)

    (
        flexmock(CoprBuildJobHelper)
        .should_receive("get_build_chroot")
        .with_args(1, "some-target")
        .and_return(flexmock(ended_on=1666889710))
        .at_least()
        .once()
    )

    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    processing_results = SteveJobs().process_message(copr_build_end_push)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    flexmock(CoprBuildJobHelper).should_receive("get_built_packages").and_return([])

    run_copr_build_end_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )


def test_copr_build_end_report_multiple_testing_farm_jobs(
    copr_build_end,
    copr_build_pr,
):
    ServiceConfig.get_service_config().testing_farm_api_url = (
        "https://api.dev.testing-farm.io/v0.1/"
    )
    ServiceConfig.get_service_config().testing_farm_secret = "secret token"

    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("get_pr").and_return(
        flexmock(
            source_project=flexmock(
                get_web_url=lambda: "https://github.com/source/bar",
            ),
            target_project=flexmock(
                get_web_url=lambda: "https://github.com/target/bar",
            ),
            head_commit="0011223344",
            target_branch_head_commit="deadbeef",
            source_branch="the-source-branch",
            target_branch="the-target-branch",
        )
        .should_receive("comment")
        .mock(),
    )
    urls.DASHBOARD_URL = "https://dashboard.localhost"

    config = PackageConfig(
        packages={
            "package": CommonPackageConfig(
                specfile_path="test.spec",
            ),
        },
        jobs=[
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={
                    "package": CommonPackageConfig(
                        _targets=["fedora-rawhide"],
                        specfile_path="test.spec",
                    ),
                },
            ),
            JobConfig(
                type=JobType.tests,
                trigger=JobConfigTriggerType.pull_request,
                packages={
                    "package": CommonPackageConfig(
                        identifier="test1",
                        _targets=["fedora-rawhide"],
                        specfile_path="test.spec",
                    ),
                },
            ),
            JobConfig(
                type=JobType.tests,
                trigger=JobConfigTriggerType.pull_request,
                packages={
                    "package": CommonPackageConfig(
                        identifier="test2",
                        _targets=["fedora-rawhide", "other-target"],
                        specfile_path="test.spec",
                    ),
                },
            ),
        ],
    )

    flexmock(copr.CoprBuild).should_receive("get_packages_config").and_return(
        config,
    )
    flexmock(CoprHelper).should_receive("get_copr_client").and_return(
        Client(config={"username": "packit", "copr_url": "https://dummy.url"}),
    )
    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).and_return(config)

    flexmock(LocalProject).should_receive("refresh_the_arguments").and_return(None)

    flexmock(CoprBuildTargetModel).should_receive("get_by_build_id").and_return(
        copr_build_pr,
    )
    flexmock(CoprBuildTargetModel).should_receive("get_by_id").and_return(copr_build_pr)
    copr_build_pr.should_call("set_status").with_args(BuildStatus.success).once()
    copr_build_pr.should_receive("set_end_time").once()
    copr_build_pr.should_receive("get_package_name").and_return(None)
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)
    # check if packit-service set correct PR status
    url = get_copr_build_info_url(1)
    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.success,
        description="RPMs were built successfully.",
        url=url,
        check_names=EXPECTED_BUILD_CHECK_NAME,
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.pending,
        description="RPMs were built successfully.",
        url=url,
        check_names="testing-farm:fedora-rawhide-x86_64:test1",
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.pending,
        description="RPMs were built successfully.",
        url=url,
        check_names="testing-farm:fedora-rawhide-x86_64:test2",
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    flexmock(TestingFarmJobHelper).should_receive("prepare_and_send_tf_request")

    flexmock(GithubProject).should_receive("get_web_url").and_return(
        "https://github.com/foo/bar",
    )

    flexmock(Signature).should_receive("apply_async").times(2)
    flexmock(celery_group).should_receive("apply_async").times(1)

    # skip SRPM url since it touches multiple classes
    flexmock(CoprBuildEndHandler).should_receive("set_srpm_url").and_return(None)

    (
        flexmock(CoprBuildJobHelper)
        .should_receive("get_build_chroot")
        .with_args(1, "some-target")
        .and_return(flexmock(ended_on=1666889710))
        .at_least()
        .once()
    )

    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    processing_results = SteveJobs().process_message(copr_build_end)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    flexmock(CoprBuildJobHelper).should_receive("get_built_packages").and_return([])

    run_copr_build_end_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )


def test_copr_build_end_failed_testing_farm(copr_build_end, copr_build_pr):
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("get_web_url").and_return(
        "https://github.com/target/bar",
    )
    flexmock(GithubProject).should_receive("get_pr").and_return(
        flexmock(
            source_project=flexmock(
                get_web_url=lambda: "https://github.com/source/bar",
            ),
            target_project=flexmock(
                get_web_url=lambda: "https://github.com/target/bar",
            ),
            head_commit="0011223344",
            target_branch_head_commit="deadbeef",
            source_branch="the-source-branch",
            target_branch="the-target-branch",
        )
        .should_receive("comment")
        .mock(),
    )

    config = PackageConfig(
        packages={
            "package": CommonPackageConfig(
                specfile_path="test.spec",
            ),
        },
        jobs=[
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={
                    "package": CommonPackageConfig(
                        _targets=["fedora-rawhide"],
                        specfile_path="test.spec",
                    ),
                },
            ),
            JobConfig(
                type=JobType.tests,
                trigger=JobConfigTriggerType.pull_request,
                packages={
                    "package": CommonPackageConfig(
                        _targets=["fedora-rawhide"],
                        specfile_path="test.spec",
                    ),
                },
            ),
        ],
    )

    flexmock(copr.CoprBuild).should_receive("get_packages_config").and_return(
        config,
    )
    flexmock(CoprHelper).should_receive("get_copr_client").and_return(
        Client(config={"username": "packit", "copr_url": "https://dummy.url"}),
    )
    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).and_return(config)

    flexmock(LocalProject).should_receive("refresh_the_arguments").and_return(None)

    flexmock(CoprBuildTargetModel).should_receive("get_by_build_id").and_return(
        copr_build_pr,
    )
    flexmock(CoprBuildTargetModel).should_receive("get_by_id").and_return(copr_build_pr)
    copr_build_pr.should_call("set_status").with_args(BuildStatus.success).once()
    copr_build_pr.should_receive("set_end_time").once()
    copr_build_pr.should_receive("get_package_name").and_return(None)
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)
    # check if packit-service set correct PR status
    url = get_copr_build_info_url(1)
    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.success,
        description="RPMs were built successfully.",
        url=url,
        check_names=EXPECTED_BUILD_CHECK_NAME,
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.pending,
        description="RPMs were built successfully.",
        url=url,
        check_names=EXPECTED_TESTING_FARM_CHECK_NAME,
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    test = (
        flexmock(
            status=TestingFarmResult.new,
            copr_builds=[copr_build_pr],
            target="fedora-rawhide-x86_64",
        )
        .should_receive("set_status")
        .with_args(TestingFarmResult.error)
        .mock()
    )
    flexmock(TFTTestRunTargetModel).should_receive("create").and_return(test)
    flexmock(TFTTestRunGroupModel).should_receive("create").with_args(
        [copr_build_pr.group_of_targets.runs[-1]], ranch="public"
    ).and_return(flexmock(grouped_targets=[test]))
    flexmock(TestingFarmJobHelper).should_receive("is_fmf_configured").and_return(True)
    flexmock(TestingFarmClient).should_receive("distro2compose").with_args(
        "fedora-rawhide",
        object,
    ).and_return("Fedora-Rawhide")
    flexmock(TestingFarmClient).should_receive(
        "send_testing_farm_request",
    ).and_return(
        RequestResponse(
            status_code=400,
            ok=False,
            content=b'{"errors": "some error"}',
            json={
                "errors": {
                    "settings": {"pipeline": {"timeout": "unexpected type str"}},
                },
            },
        ),
    )

    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.running,
        description="Build succeeded. Submitting the tests ...",
        check_names=EXPECTED_TESTING_FARM_CHECK_NAME,
        url="",
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()
    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.failure,
        description="Failed to submit tests: There was an error in the API request.",
        check_names=EXPECTED_TESTING_FARM_CHECK_NAME,
        url="",
        markdown_content="There was an error in the API request: {'settings': {'pipeline': {"
        "'timeout': 'unexpected type str'}}}\nFor the details"
        " of the API request parameters, see [the Testing Farm API definition]"
        "(https://testing-farm.gitlab.io/api/#operation/requestsPost)",
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    flexmock(Signature).should_receive("apply_async").once()
    flexmock(celery_group).should_receive("apply_async").once()

    (
        flexmock(CoprBuildJobHelper)
        .should_receive("get_build_chroot")
        .with_args(1, "some-target")
        .and_return(flexmock(ended_on=1666889710))
        .at_least()
        .once()
    )

    flexmock(Pushgateway).should_receive("push").times(3).and_return()

    # skip SRPM url since it touches multiple classes
    flexmock(CoprBuildEndHandler).should_receive("set_srpm_url").and_return(None)

    processing_results = SteveJobs().process_message(copr_build_end)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    flexmock(CoprBuildJobHelper).should_receive("get_built_packages").and_return([])

    run_copr_build_end_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    event_dict["tests_targets_override"] = [("fedora-rawhide-x86_64", None)]
    run_testing_farm_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
        build_id=flexmock(),
    )


def test_copr_build_end_failed_testing_farm_no_json(copr_build_end, copr_build_pr):
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("get_web_url").and_return(
        "https://github.com/target/bar",
    )
    flexmock(GithubProject).should_receive("get_pr").and_return(
        flexmock(
            source_project=flexmock(
                get_web_url=lambda: "https://github.com/source/bar",
            ),
            target_project=flexmock(
                get_web_url=lambda: "https://github.com/target/bar",
            ),
            head_commit="0011223344",
            target_branch_head_commit="deadbeef",
            source_branch="the-source-branch",
            target_branch="the-target-branch",
        )
        .should_receive("comment")
        .mock(),
    )

    config = PackageConfig(
        packages={
            "package": CommonPackageConfig(
                specfile_path="test.spec",
            ),
        },
        jobs=[
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={
                    "package": CommonPackageConfig(
                        _targets=["fedora-rawhide"],
                        specfile_path="test.spec",
                    ),
                },
            ),
            JobConfig(
                type=JobType.tests,
                trigger=JobConfigTriggerType.pull_request,
                packages={
                    "package": CommonPackageConfig(
                        _targets=["fedora-rawhide"],
                        specfile_path="test.spec",
                    ),
                },
            ),
        ],
    )

    flexmock(copr.CoprBuild).should_receive("get_packages_config").and_return(
        config,
    )
    flexmock(CoprHelper).should_receive("get_copr_client").and_return(
        Client(config={"username": "packit", "copr_url": "https://dummy.url"}),
    )
    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).and_return(config)

    flexmock(LocalProject).should_receive("refresh_the_arguments").and_return(None)

    flexmock(CoprBuildTargetModel).should_receive("get_by_build_id").and_return(
        copr_build_pr,
    )
    flexmock(CoprBuildTargetModel).should_receive("get_by_id").and_return(copr_build_pr)
    copr_build_pr.should_call("set_status").with_args(BuildStatus.success).once()
    copr_build_pr.should_receive("set_end_time").once()
    copr_build_pr.should_receive("get_package_name").and_return(None)
    url = get_copr_build_info_url(1)
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)
    # check if packit-service set correct PR status
    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.success,
        description="RPMs were built successfully.",
        url=url,
        check_names=EXPECTED_BUILD_CHECK_NAME,
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.pending,
        description="RPMs were built successfully.",
        url=url,
        check_names=EXPECTED_TESTING_FARM_CHECK_NAME,
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    test = (
        flexmock(
            status=TestingFarmResult.new,
            copr_builds=[copr_build_pr],
            target="fedora-rawhide-x86_64",
        )
        .should_receive("set_status")
        .with_args(TestingFarmResult.error)
        .mock()
    )
    flexmock(TFTTestRunGroupModel).should_receive("create").with_args(
        [copr_build_pr.group_of_targets.runs[-1]],
        ranch="public",
    ).and_return(flexmock(grouped_targets=[test]))
    flexmock(TFTTestRunTargetModel).should_receive("create").and_return(test)
    flexmock(TestingFarmJobHelper).should_receive("is_fmf_configured").and_return(True)
    flexmock(TestingFarmClient).should_receive("distro2compose").with_args(
        "fedora-rawhide",
        object,
    ).and_return("Fedora-Rawhide")
    flexmock(TestingFarmClient).should_receive(
        "send_testing_farm_request",
    ).and_return(
        RequestResponse(
            status_code=400,
            ok=False,
            content=b"some text error",
            reason="some text error",
            json=None,
        ),
    )

    flexmock(CoprBuildTargetModel).should_receive("set_status").with_args(
        BuildStatus.failure,
    )
    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.running,
        description="Build succeeded. Submitting the tests ...",
        check_names=EXPECTED_TESTING_FARM_CHECK_NAME,
        url="",
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()
    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.failure,
        description="Failed to submit tests: some text error.",
        check_names=EXPECTED_TESTING_FARM_CHECK_NAME,
        url="",
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    flexmock(Signature).should_receive("apply_async").once()
    flexmock(celery_group).should_receive("apply_async").once()

    (
        flexmock(CoprBuildJobHelper)
        .should_receive("get_build_chroot")
        .with_args(1, "some-target")
        .and_return(flexmock(ended_on=1666889710))
        .at_least()
        .once()
    )
    flexmock(Pushgateway).should_receive("push").times(3).and_return()

    # skip SRPM url since it touches multiple classes
    flexmock(CoprBuildEndHandler).should_receive("set_srpm_url").and_return(None)

    processing_results = SteveJobs().process_message(copr_build_end)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    flexmock(CoprBuildJobHelper).should_receive("get_built_packages").and_return([])

    run_copr_build_end_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    event_dict["tests_targets_override"] = [("fedora-rawhide-x86_64", None)]
    task = run_testing_farm_handler.__wrapped__.__func__
    task(
        flexmock(
            request=flexmock(retries=DEFAULT_RETRY_LIMIT),
            max_retries=DEFAULT_RETRY_LIMIT,
        ),
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
        build_id=flexmock(),
    )


def test_copr_build_start(copr_build_start, pc_build_pr, copr_build_pr):
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("get_pr").and_return(
        flexmock(source_project=flexmock()),
    )
    flexmock(copr.CoprBuild).should_receive("get_packages_config").and_return(
        pc_build_pr,
    )
    flexmock(CoprHelper).should_receive("get_copr_client").and_return(
        Client(config={"username": "packit", "copr_url": "https://dummy.url"}),
    )
    flexmock(CoprBuildJobHelper).should_receive("get_build_check").and_return(
        EXPECTED_BUILD_CHECK_NAME,
    )

    flexmock(CoprBuildTargetModel).should_receive("get_by_build_id").and_return(
        copr_build_pr,
    )
    url = get_copr_build_info_url(1)
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)

    copr_build_pr.should_receive("set_start_time").once()
    copr_build_pr.should_call("set_status").with_args(BuildStatus.pending).once()
    copr_build_pr.should_receive("set_build_logs_url")
    copr_build_pr.should_receive("get_package_name").and_return(None)

    # check if packit-service set correct PR status
    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.running,
        description="RPM build is in progress...",
        url=url,
        check_names=EXPECTED_BUILD_CHECK_NAME,
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    flexmock(celery_group).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    processing_results = SteveJobs().process_message(copr_build_start)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    run_copr_build_start_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )


def test_copr_build_start_already_ended(copr_build_start, pc_build_pr, copr_build_pr):
    copr_build_pr.status = BuildStatus.failure
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("get_pr").and_return(
        flexmock(source_project=flexmock()),
    )
    flexmock(copr.CoprBuild).should_receive("get_packages_config").and_return(
        pc_build_pr,
    )
    flexmock(CoprHelper).should_receive("get_copr_client").and_return(
        Client(config={"username": "packit", "copr_url": "https://dummy.url"}),
    )
    flexmock(CoprBuildJobHelper).should_receive("get_build_check").and_return(
        EXPECTED_BUILD_CHECK_NAME,
    )

    flexmock(CoprBuildTargetModel).should_receive("get_by_build_id").and_return(
        copr_build_pr,
    )
    url = get_copr_build_info_url(1)
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)

    copr_build_pr.should_receive("set_start_time").once()
    copr_build_pr.should_call("set_status").with_args(BuildStatus.pending).never()
    copr_build_pr.should_receive("get_package_name").and_return(None)

    # check that the outdated status was not reported
    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.running,
        description="RPM build is in progress...",
        url=url,
        check_names=EXPECTED_BUILD_CHECK_NAME,
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).never()

    flexmock(celery_group).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    processing_results = SteveJobs().process_message(copr_build_start)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
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
        flexmock(source_project=flexmock()).should_receive("comment").never(),
    )
    flexmock(copr.CoprBuild).should_receive("get_packages_config").and_return(
        pc_build_pr,
    )
    flexmock(CoprHelper).should_receive("get_copr_client").and_return(
        Client(config={"username": "packit", "copr_url": "https://dummy.url"}),
    )
    flexmock(CoprBuildJobHelper).should_receive("get_build_check").and_return(
        EXPECTED_BUILD_CHECK_NAME,
    )

    flexmock(CoprBuildTargetModel).should_receive("get_by_build_id").and_return(
        copr_build_pr,
    )
    copr_build_pr.should_call("set_status").with_args(BuildStatus.success).once()
    copr_build_pr.should_receive("set_end_time").once()
    copr_build_pr.should_receive("get_package_name").and_return(None)
    url = get_copr_build_info_url(1)
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)

    # check if packit-service set correct PR status
    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.success,
        description="RPMs were built successfully.",
        url=url,
        check_names=CoprBuildJobHelper.get_build_check_cls(copr_build_end["chroot"]),
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    flexmock(CoprBuildJobHelper).should_receive("get_built_packages").and_return([])
    flexmock(celery_group).should_receive("apply_async").once()

    (
        flexmock(CoprBuildJobHelper)
        .should_receive("get_build_chroot")
        .with_args(1, "some-target")
        .and_return(flexmock(ended_on=1666889710))
        .at_least()
        .once()
    )

    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    # skip SRPM url since it touches multiple classes
    flexmock(CoprBuildEndHandler).should_receive("set_srpm_url").and_return(None)

    processing_results = SteveJobs().process_message(copr_build_end)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
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
    flexmock(koji.result.Task).should_receive("get_packages_config").and_return(
        pc_koji_build_pr,
    )

    flexmock(KojiBuildTargetModel).should_receive("get_by_task_id").and_return(
        koji_build_pr,
    )
    url = get_koji_build_info_url(1)
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)

    koji_build_pr.should_receive("set_build_start_time").once()
    koji_build_pr.should_receive("set_build_finished_time").with_args(None).once()
    koji_build_pr.should_receive("set_status").with_args("running").once()
    koji_build_pr.should_receive("set_build_logs_urls")
    koji_build_pr.should_receive("set_web_url")

    # check if packit-service set correct PR status
    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.running,
        description="RPM build is in progress...",
        url=url,
        check_names="koji-build:rawhide",
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()
    flexmock(celery_group).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    processing_results = SteveJobs().process_message(koji_build_scratch_start)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    results = run_koji_build_report_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]


def test_koji_build_start_build_not_found(koji_build_scratch_start):
    flexmock(KojiBuildTargetModel).should_receive("get_by_task_id").and_return(None)

    # check if packit-service set correct PR status
    flexmock(StatusReporter).should_receive("report").never()
    flexmock(Pushgateway).should_receive("push").times(1).and_return()

    processing_results = SteveJobs().process_message(koji_build_scratch_start)

    assert len(processing_results) == 1
    assert processing_results[0]["success"]
    assert processing_results[0]["details"]["msg"] == "No packit config found in the repository."


def test_koji_build_end(koji_build_scratch_end, pc_koji_build_pr, koji_build_pr):
    koji_build_pr.target = "rawhide"
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(koji.result.Task).should_receive("get_packages_config").and_return(
        pc_koji_build_pr,
    )

    flexmock(KojiBuildTargetModel).should_receive("get_by_task_id").and_return(
        koji_build_pr,
    )
    url = get_koji_build_info_url(1)
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)

    koji_build_pr.should_receive("set_build_start_time").once()
    koji_build_pr.should_receive("set_build_finished_time").once()
    koji_build_pr.should_receive("set_status").with_args("success").once()
    koji_build_pr.should_receive("set_build_logs_urls")
    koji_build_pr.should_receive("set_web_url")

    # check if packit-service set correct PR status
    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.success,
        description="RPM build succeeded.",
        url=url,
        check_names="koji-build:rawhide",
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()
    flexmock(celery_group).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    processing_results = SteveJobs().process_message(koji_build_scratch_end)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    results = run_koji_build_report_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]


def test_koji_build_end_downstream(
    koji_build_scratch_end, pc_koji_build_pr, koji_build_pr_downstream
):
    service_config = (
        flexmock(
            testing_farm_api_url="API URL",
            enabled_projects_for_fedora_ci="https://src.fedoraproject.org/rpms/packit",
            koji_logs_url="",
            koji_web_url="",
            deployment=Deployment.stg,
            testing_farm_secret="secret token",
        )
        .should_receive("get_project")
        .and_return(
            flexmock(
                namespace="rpms",
                repo="packit",
                get_web_url=lambda: "https://src.fedoraproject.org/rpms/packit",
                get_file_content=lambda path, ref: flexmock(),
                get_pr=lambda _: flexmock(
                    source_project=flexmock(
                        get_git_urls=lambda: {
                            "git": "https://src.fedoraproject.org/fork/packit/rpms/packit.git"
                        }
                    )
                ),
            )
        )
        .mock()
    )
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(service_config)
    koji_build_pr_downstream.target = "rawhide"
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(koji.result.Task).should_receive("get_packages_config").and_return(
        pc_koji_build_pr,
    )

    flexmock(KojiBuildTargetModel).should_receive("get_by_task_id").and_return(
        koji_build_pr_downstream,
    )
    url = get_koji_build_info_url(1)
    flexmock(requests).should_receive("get").and_return(requests.Response())
    flexmock(requests.Response).should_receive("raise_for_status").and_return(None)

    koji_build_pr_downstream.should_receive("set_build_start_time").once()
    koji_build_pr_downstream.should_receive("set_build_finished_time").once()
    koji_build_pr_downstream.should_receive("set_status").with_args("success").once()
    koji_build_pr_downstream.should_receive("set_build_logs_urls")
    koji_build_pr_downstream.should_receive("set_web_url")

    installability_repo = "https://github.com/fedora-ci/installability-pipeline.git"
    installability_hash = "f6cd4a50476d9c8ffa36472c5ab2d2c8aad6cee1"

    flexmock(commands).should_receive("run_command").with_args(
        ["git", "ls-remote", installability_repo, "HEAD"], output=True
    ).and_return(flexmock(stdout=f"{installability_hash}\tHEAD"))

    payload_installability = {
        "test": {
            "tmt": {
                "url": installability_repo,
                "ref": installability_hash,
            },
        },
        "environments": [
            {
                "arch": "x86_64",
                "os": {"compose": "Fedora-Rawhide"},
                "variables": {
                    "PROFILE_NAME": "fedora-43",
                    "TASK_ID": "1",
                },
            },
        ],
        "notification": {
            "webhook": {
                "url": "https://stg.packit.dev/api/testing-farm/results",
                "token": "secret token",
            },
        },
    }

    payload_custom = {
        "test": {
            "tmt": {
                "url": "https://src.fedoraproject.org/fork/packit/rpms/packit.git",
                "ref": "0011223344",
            },
        },
        "environments": [
            {
                "arch": "x86_64",
                "os": {"compose": "Fedora-Rawhide"},
                "variables": {
                    "KOJI_TASK_ID": "1",
                },
                "artifacts": [
                    {
                        "id": "1",
                        "type": "fedora-koji-build",
                    },
                ],
                "tmt": {
                    "context": {
                        "distro": "fedora-rawhide",
                        "arch": "x86_64",
                        "trigger": "commit",
                        "initiator": "fedora-ci",
                    },
                },
            },
        ],
        "notification": {
            "webhook": {
                "url": "https://stg.packit.dev/api/testing-farm/results",
                "token": "secret token",
            },
        },
    }

    flexmock(aliases).should_receive("get_build_targets").with_args("rawhide").and_return(
        ["fedora-rawhide-x86_64"]
    )

    flexmock(TestingFarmClient).should_receive("distro2compose").with_args(
        "fedora-rawhide",
    ).and_return("Fedora-Rawhide")

    flexmock(KojiHelper).should_receive("get_candidate_tag").with_args("rawhide").and_return(
        "f43-updates-candidate"
    )

    pipeline_id = "5e8079d8-f181-41cf-af96-28e99774eb68"
    flexmock(TestingFarmClient).should_receive(
        "send_testing_farm_request",
    ).with_args(endpoint="requests", method="POST", data=payload_installability).and_return(
        RequestResponse(
            status_code=200,
            ok=True,
            content=json.dumps({"id": pipeline_id}).encode(),
            json={"id": pipeline_id},
        ),
    ).once()
    flexmock(TestingFarmClient).should_receive(
        "send_testing_farm_request",
    ).with_args(endpoint="requests", method="POST", data=payload_custom).and_return(
        RequestResponse(
            status_code=200,
            ok=True,
            content=json.dumps({"id": pipeline_id}).encode(),
            json={"id": pipeline_id},
        ),
    ).once()

    tft_test_run_model_installability = (
        flexmock(
            id=5,
            koji_builds=[koji_build_pr_downstream],
            status=TestingFarmResult.new,
            target="fedora-rawhide",
            data={"fedora_ci_test": "installability"},
        )
        .should_receive("set_pipeline_id")
        .with_args(pipeline_id)
        .once()
        .mock()
    )
    tft_test_run_model_custom = (
        flexmock(
            id=6,
            koji_builds=[koji_build_pr_downstream],
            status=TestingFarmResult.new,
            target="fedora-rawhide",
            data={"fedora_ci_test": "custom"},
        )
        .should_receive("set_pipeline_id")
        .with_args(pipeline_id)
        .once()
        .mock()
    )
    group = flexmock(grouped_targets=[tft_test_run_model_installability, tft_test_run_model_custom])
    flexmock(TFTTestRunGroupModel).should_receive("create").with_args(
        [koji_build_pr_downstream.group_of_targets.runs[-1]],
        ranch="public",
    ).and_return(group)
    flexmock(TFTTestRunTargetModel).should_receive("create").with_args(
        pipeline_id=None,
        identifier=None,
        status=TestingFarmResult.new,
        target="fedora-rawhide",
        web_url=None,
        test_run_group=group,
        koji_build_targets=[koji_build_pr_downstream],
        data={
            "base_project_url": "https://src.fedoraproject.org/rpms/packit",
            "fedora_ci_test": "installability",
        },
    ).and_return(tft_test_run_model_installability).once()
    flexmock(TFTTestRunTargetModel).should_receive("create").with_args(
        pipeline_id=None,
        identifier=None,
        status=TestingFarmResult.new,
        target="fedora-rawhide",
        web_url=None,
        test_run_group=group,
        koji_build_targets=[koji_build_pr_downstream],
        data={
            "base_project_url": "https://src.fedoraproject.org/rpms/packit",
            "fedora_ci_test": "custom",
        },
    ).and_return(tft_test_run_model_custom).once()

    # check if packit-service set correct PR statuses
    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=BaseCommitStatus.success,
        description="RPM build succeeded.",
        url=url,
        check_name="Packit - scratch build",
        target_branch="rawhide",
    ).once()
    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=BaseCommitStatus.running,
        description="Submitting the tests ...",
        url="",
        check_name="Packit - installability test(s)",
        target_branch="rawhide",
    ).once()
    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=BaseCommitStatus.running,
        description="Tests have been submitted ...",
        url="https://dashboard.localhost/jobs/testing-farm/5",
        check_name="Packit - installability test(s)",
        target_branch="rawhide",
    ).once()
    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=BaseCommitStatus.running,
        description="Submitting the tests ...",
        url="",
        check_name="Packit - custom test(s)",
        target_branch="rawhide",
    ).once()
    flexmock(StatusReporter).should_receive("set_status").with_args(
        state=BaseCommitStatus.running,
        description="Tests have been submitted ...",
        url="https://dashboard.localhost/jobs/testing-farm/6",
        check_name="Packit - custom test(s)",
        target_branch="rawhide",
    ).once()

    urls.DASHBOARD_URL = "https://dashboard.localhost"

    flexmock(Signature).should_receive("apply_async").twice()
    flexmock(Pushgateway).should_receive("push").times(3).and_return()

    processing_results = SteveJobs().process_message(koji_build_scratch_end)

    # reporting
    event_dict, job, job_config, package_config = get_parameters_from_results(
        [processing_results[0]],
    )
    assert json.dumps(event_dict)

    results = run_downstream_koji_scratch_build_report_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]

    # Testing Farm
    event_dict, job, job_config, package_config = get_parameters_from_results(
        [processing_results[1]],
    )
    assert json.dumps(event_dict)

    results = run_downstream_testing_farm_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]


def test_koji_build_tag(
    koji_build_tagged,
    pc_koji_build_tag_specfile,
    pc_koji_build_tag_packit,
):
    flexmock(KojiHelper).should_receive("get_build_info").with_args(1234567).and_return(
        {"task_id": 7654321},
    )

    flexmock(koji.tag.Build).should_receive("get_packages_config").and_return(
        pc_koji_build_tag_specfile,
    )

    flexmock(SidetagModel).should_receive("get_by_koji_name").with_args(
        "f40-build-side-12345",
    ).and_return(
        flexmock(
            sidetag_group=flexmock(name="test"),
            target="f40",
            koji_name="f40-build-side-12345",
        ),
    )

    flexmock(KojiHelper).should_receive("get_tag_info").with_args(
        "f40-build-side-12345",
    ).and_return({"name": "f40-build-side-12345"})
    flexmock(KojiHelper).should_receive("get_builds_in_tag").with_args(
        "f40-build-side-12345",
    ).and_return(
        [{"package_name": "python-specfile", "nvr": "python-specfile-0.32.0-1.fc40"}],
    )

    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo",
    ).and_return(pc_koji_build_tag_specfile).and_return(pc_koji_build_tag_packit)

    flexmock(DownstreamKojiBuildHandler).should_receive("pre_check").and_return(True)
    flexmock(BodhiUpdateFromSidetagHandler).should_receive("pre_check").and_return(True)

    flexmock(Signature).should_receive("apply_async").twice()
    flexmock(celery_group).should_receive("apply_async").once()

    processing_results = SteveJobs().process_message(koji_build_tagged)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    results = run_koji_build_tag_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]


def test_srpm_build_end(srpm_build_end, pc_build_pr, srpm_build_model):
    pr = flexmock(source_project=flexmock())
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    flexmock(copr.CoprBuild).should_receive("get_packages_config").and_return(
        pc_build_pr,
    )
    flexmock(CoprHelper).should_receive("get_copr_client").and_return(
        Client(config={"username": "packit", "copr_url": "https://dummy.url"}),
    )
    flexmock(CoprBuildTargetModel).should_receive("get_all_by_build_id").and_return(
        [
            flexmock(target="fedora-33-x86_64", identifier=None)
            .should_receive("set_status")
            .with_args(BuildStatus.pending)
            .mock(),
        ],
    )
    (
        flexmock(CoprBuildJobHelper)
        .should_receive("get_build")
        .with_args(3122876)
        .and_return(flexmock(source_package={"url": "https://my.host/my.srpm"}))
        .at_least()
        .once()
    )
    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    flexmock(SRPMBuildModel).should_receive("get_by_copr_build_id").and_return(
        srpm_build_model,
    )
    srpm_build_model.should_call("set_status").with_args(BuildStatus.success).once()
    srpm_build_model.should_receive("set_end_time").once()
    srpm_build_model.should_receive("get_package_name").and_return(None)

    url = get_srpm_build_info_url(1)
    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.running,
        description="SRPM build succeeded. Waiting for RPM build to start...",
        url=url,
        check_names=["rpm-build:fedora-33-x86_64"],
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    flexmock(celery_group).should_receive("apply_async").once()

    flexmock(srpm_build_model).should_receive("set_url").with_args(
        "https://my.host/my.srpm",
    ).mock()

    processing_results = SteveJobs().process_message(srpm_build_end)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    results = run_copr_build_end_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]


def test_srpm_build_end_failure(srpm_build_end, pc_build_pr, srpm_build_model):
    srpm_build_end["status"] = COPR_API_FAIL_STATE
    pr = flexmock(source_project=flexmock())
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    flexmock(copr.CoprBuild).should_receive("get_packages_config").and_return(
        pc_build_pr,
    )
    flexmock(CoprHelper).should_receive("get_copr_client").and_return(
        Client(config={"username": "packit", "copr_url": "https://dummy.url"}),
    )
    flexmock(CoprBuildTargetModel).should_receive("get_all_by_build_id").and_return(
        [flexmock(target="fedora-33-x86_64", identifier=None)],
    )
    (
        flexmock(CoprBuildJobHelper)
        .should_receive("get_build")
        .with_args(3122876)
        .and_return(flexmock(source_package={"url": "https://my.host/my.srpm"}))
        .at_least()
        .once()
    )
    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    flexmock(CoprBuildJobHelper).should_receive("monitor_not_submitted_copr_builds")

    flexmock(SRPMBuildModel).should_receive("get_by_copr_build_id").and_return(
        srpm_build_model,
    )
    srpm_build_model.should_call("set_status").with_args(BuildStatus.failure).once()
    srpm_build_model.should_receive("set_end_time").once()
    srpm_build_model.should_receive("get_package_name").and_return(None)

    url = get_srpm_build_info_url(1)
    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.failure,
        description="SRPM build failed, check the logs for details.",
        url=url,
        check_names=["rpm-build:fedora-33-x86_64"],
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    flexmock(celery_group).should_receive("apply_async").once()

    flexmock(srpm_build_model).should_receive("set_url").with_args(
        "https://my.host/my.srpm",
    ).mock()

    processing_results = SteveJobs().process_message(srpm_build_end)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    results = run_copr_build_end_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert not first_dict_value(results["job"])["success"]


def test_srpm_build_start(srpm_build_start, pc_build_pr, srpm_build_model):
    pr = flexmock(source_project=flexmock())
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    flexmock(copr.CoprBuild).should_receive("get_packages_config").and_return(
        pc_build_pr,
    )
    flexmock(CoprHelper).should_receive("get_copr_client").and_return(
        Client(config={"username": "packit", "copr_url": "https://dummy.url"}),
    )
    flexmock(CoprBuildTargetModel).should_receive("get_all_by_build_id").and_return(
        [flexmock(target="fedora-33-x86_64", identifier=None)],
    )
    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    flexmock(SRPMBuildModel).should_receive("get_by_copr_build_id").and_return(
        srpm_build_model,
    )
    srpm_build_model.should_receive("get_package_name").and_return(None)
    flexmock(SRPMBuildModel).should_receive("set_start_time")
    flexmock(SRPMBuildModel).should_receive("set_build_logs_url")

    url = get_srpm_build_info_url(1)
    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.running,
        description="SRPM build is in progress...",
        url=url,
        check_names=["rpm-build:fedora-33-x86_64"],
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    flexmock(celery_group).should_receive("apply_async").once()

    processing_results = SteveJobs().process_message(srpm_build_start)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    results = run_copr_build_start_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert first_dict_value(results["job"])["success"]
