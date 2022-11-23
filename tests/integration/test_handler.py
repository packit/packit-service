# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import os

import pytest
from flexmock import flexmock

from ogr.services.github import GithubProject
from packit.config import (
    CommonPackageConfig,
    JobConfig,
    JobConfigTriggerType,
    JobType,
    PackageConfig,
)
from packit_service.config import ServiceConfig
from packit_service.constants import KOJI_PRODUCTION_BUILDS_ISSUE
from packit_service.models import (
    GitBranchModel,
    PullRequestModel,
    JobTriggerModel,
    JobTriggerModelType,
)
from packit_service.worker.handlers import (
    JobHandler,
    CoprBuildHandler,
    KojiBuildHandler,
)
from packit_service.worker.helpers.build import CoprBuildJobHelper
from packit_service.worker.reporting import StatusReporterGithubChecks, BaseCommitStatus


@pytest.fixture()
def trick_p_s_with_k8s():
    os.environ["KUBERNETES_SERVICE_HOST"] = "YEAH"  # trick p-s
    yield
    del os.environ["KUBERNETES_SERVICE_HOST"]


def test_handler_cleanup(tmp_path, trick_p_s_with_k8s):
    tmp_path.joinpath("a").mkdir()
    tmp_path.joinpath("b").write_text("a")
    tmp_path.joinpath("c").symlink_to("b")
    tmp_path.joinpath("d").symlink_to("a", target_is_directory=True)
    tmp_path.joinpath("e").symlink_to("nope", target_is_directory=False)
    tmp_path.joinpath("f").symlink_to("nopez", target_is_directory=True)
    tmp_path.joinpath(".g").write_text("g")
    tmp_path.joinpath(".h").symlink_to(".g", target_is_directory=False)

    c = ServiceConfig()
    pc = flexmock(PackageConfig)
    c.command_handler_work_dir = tmp_path
    jc = JobConfig(
        type=JobType.copr_build,
        trigger=JobConfigTriggerType.pull_request,
        packages={"package": CommonPackageConfig()},
    )
    j = JobHandler(
        package_config=pc,
        job_config=jc,
        event={},
    )

    flexmock(j).should_receive("service_config").and_return(c)

    j._clean_workplace()

    assert len(list(tmp_path.iterdir())) == 0


def test_precheck(github_pr_event):
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=342,
        namespace="packit-service",
        repo_name="packit",
        project_url="https://github.com/packit-service/packit",
    ).and_return(
        flexmock(id=342, job_config_trigger_type=JobConfigTriggerType.pull_request)
    )

    package_config = PackageConfig(
        jobs=[
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={"package": CommonPackageConfig()},
            ),
            JobConfig(
                type=JobType.tests,
                trigger=JobConfigTriggerType.pull_request,
                packages={"package": CommonPackageConfig()},
            ),
        ],
        packages={"package": CommonPackageConfig()},
    )
    job_config = JobConfig(
        type=JobType.copr_build,
        trigger=JobConfigTriggerType.pull_request,
        packages={"package": CommonPackageConfig()},
    )
    event = github_pr_event.get_dict()

    flexmock(CoprBuildJobHelper).should_receive(
        "is_custom_copr_project_defined"
    ).and_return(False).once()
    assert CoprBuildHandler.pre_check(package_config, job_config, event)


def test_precheck_gitlab(gitlab_mr_event):
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=1,
        namespace="testing/packit",
        repo_name="hello-there",
        project_url="https://gitlab.com/testing/packit/hello-there",
    ).and_return(
        flexmock(id=1, job_config_trigger_type=JobConfigTriggerType.pull_request)
    )
    package_config = PackageConfig(
        jobs=[
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={"package": CommonPackageConfig()},
            ),
            JobConfig(
                type=JobType.tests,
                trigger=JobConfigTriggerType.pull_request,
                packages={"package": CommonPackageConfig()},
            ),
        ],
        packages={"package": CommonPackageConfig()},
    )
    job_config = JobConfig(
        type=JobType.copr_build,
        trigger=JobConfigTriggerType.pull_request,
        packages={"package": CommonPackageConfig()},
    )
    event = gitlab_mr_event.get_dict()
    job_config = JobConfig(
        type=JobType.copr_build,
        trigger=JobConfigTriggerType.pull_request,
        packages={"package": CommonPackageConfig()},
    )
    event = gitlab_mr_event.get_dict()

    flexmock(CoprBuildJobHelper).should_receive(
        "is_custom_copr_project_defined"
    ).and_return(False).once()
    assert CoprBuildHandler.pre_check(package_config, job_config, event)


def test_precheck_push(github_push_event):
    flexmock(GitBranchModel).should_receive("get_or_create").and_return(
        flexmock(
            id=1,
            job_config_trigger_type=JobConfigTriggerType.commit,
            job_trigger_model_type=JobTriggerModelType.branch_push,
        )
    )
    jc = JobConfig(
        type=JobType.copr_build,
        trigger=JobConfigTriggerType.commit,
        packages={
            "package": CommonPackageConfig(
                branch="build-branch",
                owner="@foo",
                project="bar",
            )
        },
    )

    package_config = PackageConfig(
        jobs=[jc], packages={"package": CommonPackageConfig()}
    )
    event = github_push_event.get_dict()
    api = flexmock(
        copr_helper=flexmock(
            copr_client=flexmock(
                config={"username": "nobody"},
                project_proxy=flexmock(
                    get=lambda owner, project: {
                        "packit_forge_projects_allowed": "github.com/packit-service/hello-world"
                    }
                ),
            )
        )
    )
    flexmock(CoprBuildJobHelper).should_receive("api").and_return(api)

    permission_checker = CoprBuildHandler.get_checkers()[0]
    assert permission_checker(package_config, jc, event).pre_check()


def test_precheck_push_to_a_different_branch(github_push_event):
    flexmock(GitBranchModel).should_receive("get_or_create").and_return(
        flexmock(id=1, job_config_trigger_type=JobConfigTriggerType.commit)
    )

    package_config = PackageConfig(
        jobs=[
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.commit,
                packages={
                    "package": CommonPackageConfig(
                        branch="bad-branch",
                    )
                },
            ),
        ],
        packages={"package": CommonPackageConfig()},
    )
    job_config = JobConfig(
        type=JobType.copr_build,
        trigger=JobConfigTriggerType.commit,
        packages={
            "package": CommonPackageConfig(
                branch="bad-branch",
            )
        },
    )
    event = github_push_event.get_dict()
    assert not CoprBuildHandler.pre_check(package_config, job_config, event)


def test_precheck_koji_build_non_scratch(github_pr_event):
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=342,
        namespace="packit-service",
        repo_name="packit",
        project_url="https://github.com/packit-service/packit",
    ).and_return(
        flexmock(
            id=342,
            job_config_trigger_type=JobConfigTriggerType.pull_request,
            job_trigger_model_type=JobTriggerModelType.pull_request,
        )
    )
    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=JobTriggerModelType.pull_request, trigger_id=342
    ).and_return(flexmock(id=2, type=JobTriggerModelType.pull_request))
    flexmock(StatusReporterGithubChecks).should_receive("set_status").with_args(
        state=BaseCommitStatus.neutral,
        description="Non-scratch builds not possible from upstream.",
        check_name="koji-build:bright-future",
        url=KOJI_PRODUCTION_BUILDS_ISSUE,
        links_to_external_services=None,
        markdown_content=None,
    ).and_return().once()
    flexmock(GithubProject).should_receive("can_merge_pr").and_return(True)

    package_config = PackageConfig(
        jobs=[
            JobConfig(
                type=JobType.production_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={
                    "package": CommonPackageConfig(
                        _targets=["bright-future"],
                        scratch=False,
                    )
                },
            ),
        ],
        packages={"package": CommonPackageConfig()},
    )
    job_config = JobConfig(
        type=JobType.production_build,
        trigger=JobConfigTriggerType.pull_request,
        packages={
            "package": CommonPackageConfig(
                _targets=["bright-future"],
                scratch=False,
            )
        },
    )
    event = github_pr_event.get_dict()
    assert not KojiBuildHandler.pre_check(package_config, job_config, event)
