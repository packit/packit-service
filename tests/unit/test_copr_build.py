# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json
from datetime import datetime, timezone
from typing import Optional, Type

import pytest
from celery import Celery
from copr.v3 import Client
from copr.v3 import CoprAuthException
from copr.v3.proxies.build import BuildProxy
from flexmock import flexmock
from ogr.abstract import GitProject
from ogr.exceptions import GitForgeInternalError, OgrNetworkError
from ogr.services.github import GithubProject

import packit
import packit_service
from packit.api import PackitAPI
from packit.config import (
    CommonPackageConfig,
    JobConfig,
    JobConfigTriggerType,
    JobType,
    PackageConfig,
)
from packit.copr_helper import CoprHelper
from packit.exceptions import (
    PackitCoprSettingsException,
    PackitCoprProjectException,
)
from packit_service.config import ServiceConfig
from packit_service.constants import (
    DEFAULT_RETRY_LIMIT,
    DEFAULT_RETRY_LIMIT_OUTAGE,
)
from packit_service.models import (
    CoprBuildTargetModel,
    GithubInstallationModel,
    GitProjectModel,
    JobTriggerModel,
    JobTriggerModelType,
    SRPMBuildModel,
    PullRequestModel,
)
from packit_service.worker.celery_task import CeleryTask
from packit_service.worker.checker.copr import IsGitForgeProjectAndEventOk
from packit_service.worker.events import (
    PullRequestGithubEvent,
    PushGitHubEvent,
    PushGitlabEvent,
    EventData,
)
from packit_service.worker.handlers import CoprBuildHandler
from packit_service.worker.helpers.build.copr_build import (
    BaseBuildJobHelper,
    CoprBuildJobHelper,
)
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.parser import Parser
from packit_service.worker.reporting import (
    BaseCommitStatus,
    StatusReporterGithubChecks,
)
from tests.spellbook import DATA_DIR

DEFAULT_TARGETS = [
    "fedora-29-x86_64",
    "fedora-30-x86_64",
    "fedora-31-x86_64",
    "fedora-rawhide-x86_64",
]
CACHE_CLEAR = [
    packit.copr_helper.CoprHelper.get_available_chroots,
]

pytestmark = pytest.mark.usefixtures("cache_clear", "mock_get_aliases")
create_table_content = StatusReporterGithubChecks._create_table


@pytest.fixture(scope="module")
def branch_push_event() -> PushGitHubEvent:
    file_content = (DATA_DIR / "webhooks" / "github" / "push_branch.json").read_text()
    return Parser.parse_github_push_event(json.loads(file_content))


@pytest.fixture(scope="module")
def branch_push_event_gitlab() -> PushGitlabEvent:
    file_content = (DATA_DIR / "webhooks" / "gitlab" / "push_branch.json").read_text()
    return Parser.parse_gitlab_push_event(json.loads(file_content))


def build_helper(
    event,
    _targets=None,
    owner=None,
    trigger=None,
    jobs=None,
    db_trigger=None,
    selected_job=None,
    project_type: Type[GitProject] = GithubProject,
    build_targets_override=None,
    task: Optional[CeleryTask] = None,
) -> CoprBuildJobHelper:
    if jobs and (_targets or owner):
        raise Exception("Only one job description can be used.")

    if not _targets:
        _targets = DEFAULT_TARGETS
    if not owner:
        owner = "nobody"

    jobs = jobs or [
        JobConfig(
            type=JobType.copr_build,
            trigger=trigger or JobConfigTriggerType.pull_request,
            packages={
                "package": CommonPackageConfig(
                    _targets=_targets,
                    owner=owner,
                )
            },
        )
    ]

    pkg_conf = PackageConfig(
        jobs=jobs,
        packages={"package": CommonPackageConfig(downstream_package_name="dummy")},
    )
    helper = CoprBuildJobHelper(
        service_config=ServiceConfig.get_service_config(),
        package_config=pkg_conf,
        job_config=selected_job or jobs[0],
        project=project_type(
            repo="the-example-repo",
            service=flexmock(
                instance_url="git.instance.io", hostname="git.instance.io"
            ),
            namespace="the/example/namespace",
        ),
        metadata=flexmock(
            pr_id=event.pr_id,
            git_ref=event.git_ref,
            commit_sha=event.commit_sha,
            identifier=event.identifier,
            tag_name=None,
            task_accepted_time=datetime.now(timezone.utc),
            project_url="https://git.instance.io/the/example/namespace/the-example-repo",
        ),
        db_trigger=db_trigger,
        build_targets_override=build_targets_override,
        pushgateway=Pushgateway(),
        celery_task=task,
    )
    helper._api = PackitAPI(ServiceConfig(), pkg_conf)
    return helper


def test_copr_build_fails_chroot_update(github_pr_event):
    """Verify that comment we post when we fail to update chroots on our projects
    is correct and not the one about permissions"""
    helper = build_helper(
        event=github_pr_event,
        db_trigger=flexmock(
            job_config_trigger_type=JobConfigTriggerType.pull_request,
            id=123,
            job_trigger_model_type=JobTriggerModelType.pull_request,
        ),
    )
    # enforce that we are reporting on our own Copr project
    helper.job_build.owner = "packit"
    flexmock(CoprHelper).should_receive("get_valid_build_targets").and_return(
        {"f31", "f32"}
    )
    flexmock(CoprHelper).should_receive("create_copr_project_if_not_exists").and_raise(
        PackitCoprSettingsException,
        "Copr project update failed.",
        fields_to_change={
            "chroots": (["f30", "f31"], ["f31", "f32"]),
            "description": ("old", "new"),
        },
    )
    status_reporter = (
        flexmock()
        .should_receive("comment")
        .with_args(
            body="Settings of a Copr project packit/the-example-namespace-the-example-repo-342"
            " need to be updated, but Packit can't do that when there are previous "
            "builds still in progress.\n"
            "You should be able to resolve the problem by recreating this pull request "
            "or running `/packit build` after all builds finished.\n\n"
            "This was the change Packit tried to do:\n"
            "\n"
            "| field | old value | new value |\n"
            "| ----- | --------- | --------- |\n"
            "| chroots | ['f30', 'f31'] | ['f31', 'f32'] |\n"
            "| description | old | new |\n"
            "\n"
            "Diff of chroots:\n"
            "```diff\n"
            "-f30\n"
            "+f32\n"
            "```\n"
        )
        .and_return()
        .mock()
    )

    flexmock(BaseBuildJobHelper).should_receive("status_reporter").and_return(
        status_reporter
    )
    with pytest.raises(PackitCoprSettingsException):
        helper.create_copr_project_if_not_exists()


@pytest.mark.parametrize(
    "srpm_build_deps",
    [
        pytest.param(
            None,
            id="new_installation",
        ),
        pytest.param(
            [], id="explicitly_defined_empty_key"
        ),  # user defines this key (it's None by default)
        pytest.param(
            ["make", "findutils"],
            id="explicitly_defined_key_with_custom_deps",
        ),
    ],
)
def test_run_copr_build_from_source_script(github_pr_event, srpm_build_deps):
    helper = build_helper(
        event=github_pr_event,
        db_trigger=flexmock(
            job_config_trigger_type=JobConfigTriggerType.pull_request,
            id=123,
            job_trigger_model_type=JobTriggerModelType.pull_request,
        ),
    )
    helper.job_config.srpm_build_deps = srpm_build_deps
    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=JobTriggerModelType.pull_request, trigger_id=123
    ).and_return(flexmock(id=2, type=JobTriggerModelType.pull_request))
    flexmock(GithubInstallationModel).should_receive("get_by_account_login").with_args(
        account_login="packit-service"
    ).and_return(
        flexmock(
            repositories=[flexmock(repo_name="packit")],
        )
    )
    flexmock(GitProjectModel).should_receive("get_by_id").and_return(
        flexmock(repo_name="packit")
    )
    flexmock(GithubProject).should_receive("create_check_run").and_return().times(4)
    flexmock(GithubProject).should_receive("get_pr").and_return(
        flexmock(source_project=flexmock(), target_branch="main")
    )
    flexmock(SRPMBuildModel).should_receive("create_with_new_run").and_return(
        (
            flexmock(status="success", id=1),
            flexmock(),
        )
    )
    flexmock(CoprBuildTargetModel).should_receive("create").and_return(
        flexmock(id=1)
    ).times(4)
    flexmock(PullRequestGithubEvent).should_receive("db_trigger").and_return(flexmock())

    # copr build
    flexmock(CoprHelper).should_receive("create_copr_project_if_not_exists").and_return(
        None
    )
    flexmock(helper).should_receive("get_latest_fedora_stable_chroot").and_return(
        "fedora-35-x86_64"
    )

    flexmock(helper).should_call("run_copr_build_from_source_script").once()

    flexmock(Client).should_receive("create_from_config_file").and_return(
        flexmock(
            config={"copr_url": "https://copr.fedorainfracloud.org/"},
            build_proxy=flexmock()
            .should_receive("create_from_custom")
            .and_return(
                flexmock(
                    id=2,
                    projectname="the-project-name",
                    ownername="the-owner",
                )
            )
            .mock(),
            mock_chroot_proxy=flexmock()
            .should_receive("get_list")
            .and_return({target: "" for target in DEFAULT_TARGETS})
            .mock(),
        )
    )

    flexmock(Celery).should_receive("send_task").once()
    handler = CoprBuildHandler(
        package_config=helper.package_config,
        job_config=helper.job_config,
        event=github_pr_event.get_dict(),
        celery_task=flexmock(),
    )
    handler._copr_build_helper = helper
    assert handler.run()["success"]


@pytest.mark.parametrize(
    "retry_number,interval,delay,retry, exc",
    [
        (0, "1 minute", 60, True, OgrNetworkError("Get PR failed")),
        (1, "2 minutes", 120, True, OgrNetworkError("Get PR failed")),
        (2, None, None, False, OgrNetworkError("Get PR failed")),
        (0, "10 seconds", 10, True, GitForgeInternalError("Get PR failed")),
        (1, "20 seconds", 20, True, GitForgeInternalError("Get PR failed")),
        (2, None, None, False, GitForgeInternalError("Get PR failed")),
    ],
)
def test_run_copr_build_from_source_script_github_outage_retry(
    github_pr_event, retry_number, interval, delay, retry, exc
):
    helper = build_helper(
        event=github_pr_event,
        db_trigger=flexmock(
            job_config_trigger_type=JobConfigTriggerType.pull_request,
            id=123,
            job_trigger_model_type=JobTriggerModelType.pull_request,
        ),
        task=CeleryTask(
            flexmock(
                request=flexmock(retries=retry_number, kwargs={}),
                max_retries=DEFAULT_RETRY_LIMIT,
            )
        ),
    )
    helper.job_config.srpm_build_deps = ["make", "findutils"]
    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=JobTriggerModelType.pull_request, trigger_id=123
    ).and_return(flexmock(id=2, type=JobTriggerModelType.pull_request))
    flexmock(GithubProject).should_receive("get_pr").and_raise(exc)
    flexmock(SRPMBuildModel).should_receive("create_with_new_run").and_return(
        (
            flexmock(status="success", id=1),
            flexmock(),
        )
    )
    flexmock(PullRequestGithubEvent).should_receive("db_trigger").and_return(flexmock())

    # copr build
    flexmock(CoprHelper).should_receive("create_copr_project_if_not_exists").and_return(
        None
    )
    flexmock(helper).should_receive("get_latest_fedora_stable_chroot").and_return(
        "fedora-35-x86_64"
    )
    flexmock(Client).should_receive("create_from_config_file").and_return(
        flexmock(
            config={"copr_url": "https://copr.fedorainfracloud.org/"},
            build_proxy=flexmock()
            .should_receive("create_from_custom")
            .and_return(
                flexmock(
                    id=2,
                    projectname="the-project-name",
                    ownername="the-owner",
                )
            )
            .mock(),
            mock_chroot_proxy=flexmock()
            .should_receive("get_list")
            .and_return({"bright-future-x86_64": "", "__proxy__": "something"})
            .mock(),
        )
    )
    if retry:
        flexmock(CeleryTask).should_receive("retry").with_args(
            ex=exc,
            delay=delay,
            max_retries=DEFAULT_RETRY_LIMIT_OUTAGE
            if exc.__class__ is OgrNetworkError
            else None,
        ).once()
        flexmock(StatusReporterGithubChecks).should_receive("set_status").with_args(
            state=BaseCommitStatus.pending,
            description=f"Submit of the build failed due to a Git forge error, the task will be"
            f" retried in {interval}.",
            check_name="rpm-build:bright-future-x86_64",
            url="",
            links_to_external_services=None,
            markdown_content=None,
        ).and_return()
    else:
        flexmock(StatusReporterGithubChecks).should_receive("set_status").with_args(
            state=BaseCommitStatus.error,
            description=f"Submit of the build failed: {exc}",
            check_name="rpm-build:bright-future-x86_64",
            url="",
            links_to_external_services=None,
            markdown_content=None,
        ).and_return()

    assert helper.run_copr_build_from_source_script()["success"] is retry


def test_get_latest_fedora_stable_chroot(github_pr_event):
    flexmock(packit_service.worker.helpers.build.copr_build).should_receive(
        "get_aliases"
    ).and_return({"fedora-stable": ["fedora-34", "fedora-35"]})
    flexmock(CoprHelper).should_receive("get_valid_build_targets").with_args(
        "fedora-35"
    ).and_return({"fedora-35-x86_64"})
    assert (
        build_helper(github_pr_event).get_latest_fedora_stable_chroot()
        == "fedora-35-x86_64"
    )


@pytest.mark.parametrize(
    "package_config,job_config,result",
    [
        (
            PackageConfig(
                jobs=[
                    JobConfig(
                        type=JobType.copr_build,
                        trigger=JobConfigTriggerType.pull_request,
                        packages={
                            "package": CommonPackageConfig(
                                _targets=["fedora-all"],
                            )
                        },
                    ),
                    JobConfig(
                        type=JobType.tests,
                        trigger=JobConfigTriggerType.pull_request,
                        packages={"package": CommonPackageConfig()},
                    ),
                ],
                packages={"package": CommonPackageConfig()},
            ),
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={
                    "package": CommonPackageConfig(
                        _targets=["fedora-all"],
                    )
                },
            ),
            0,
        ),
        (
            PackageConfig(
                jobs=[
                    JobConfig(
                        type=JobType.tests,
                        trigger=JobConfigTriggerType.commit,
                        packages={"package": CommonPackageConfig()},
                    ),
                    JobConfig(
                        type=JobType.copr_build,
                        trigger=JobConfigTriggerType.commit,
                        packages={
                            "package": CommonPackageConfig(
                                _targets=["fedora-all"],
                            )
                        },
                    ),
                    JobConfig(
                        type=JobType.copr_build,
                        trigger=JobConfigTriggerType.pull_request,
                        packages={
                            "package": CommonPackageConfig(
                                _targets=["fedora-all"],
                            )
                        },
                    ),
                    JobConfig(
                        type=JobType.tests,
                        trigger=JobConfigTriggerType.pull_request,
                        packages={"package": CommonPackageConfig()},
                    ),
                ],
                packages={"package": CommonPackageConfig()},
            ),
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={
                    "package": CommonPackageConfig(
                        _targets=["fedora-all"],
                    )
                },
            ),
            2,
        ),
    ],
)
def test_get_job_config_index(package_config, job_config, result):
    assert (
        CoprBuildJobHelper(
            package_config=package_config,
            job_config=job_config,
            service_config=ServiceConfig.get_service_config(),
            project=None,
            metadata=None,
            db_trigger=None,
        ).get_job_config_index()
        == result
    )


@pytest.mark.parametrize(
    "is_custom_copr_project,copr_server_raise_exc,buildopts",
    [
        (True, True, {"chroots": [], "enable_net": True}),
        (False, True, {"chroots": [], "enable_net": True}),
        (
            True,
            False,
            {"chroots": [], "enable_net": True, "packit_forge_project": ""},
        ),
        (False, False, {"chroots": [], "enable_net": True}),
    ],
)
def test_submit_copr_build(
    github_pr_event,
    is_custom_copr_project,
    copr_server_raise_exc,
    buildopts,
):
    helper = build_helper(event=github_pr_event)
    flexmock(helper).should_receive("create_copr_project_if_not_exists").and_return("")
    flexmock(helper).should_receive("is_custom_copr_project_defined").and_return(
        is_custom_copr_project
    )
    flexmock(helper).should_receive("job_project").and_return("")
    flexmock(helper).should_receive("srpm_path").and_return("")
    flexmock(helper).should_receive("forge_project").and_return("")
    flexmock(helper).should_receive("configured_copr_project").and_return("")
    flexmock(CoprHelper).should_receive("get_copr_settings_url").and_return(
        "https://copr.fedorainfracloud.org/coprs//edit/"
    )
    flexmock(helper).should_receive("status_reporter").and_return(
        flexmock()
        .should_receive("comment")
        .with_args(
            body="Your git-forge project is not allowed to use the configured `` Copr project.\n\n"
            "Please, add this git-forge project `` to `Packit allowed forge projects`in the "
            "[Copr project settings]"
            "(https://copr.fedorainfracloud.org/coprs//edit/#packit_forge_projects_allowed). "
        )
        .mock()
    )
    if copr_server_raise_exc:
        flexmock(BuildProxy).should_receive("create_from_file").and_raise(
            CoprAuthException("Forge project .... can't build in this Copr via Packit.")
        )
        with pytest.raises(CoprAuthException):
            helper.submit_copr_build()

    else:
        flexmock(BuildProxy).should_receive("create_from_file").with_args(
            ownername="", projectname="", path="", buildopts=buildopts
        ).and_return(flexmock(id=0))
        helper.submit_copr_build()


@pytest.mark.parametrize(
    "raw_name,expected_name",
    [
        ("packit-specfile-91-fedora-epel", "packit-specfile-91-fedora-epel"),
        ("packit-specfile-91-fedora+epel", "packit-specfile-91-fedora-epel"),
        ("packit-specfile-my@fancy@branch", "packit-specfile-my-fancy-branch"),
        ("packit-specfile-v23:1", "packit-specfile-v23-1"),
    ],
)
def test_normalise_copr_project_name(raw_name, expected_name):
    assert CoprBuildJobHelper.normalise_copr_project_name(raw_name) == expected_name


def test_copr_build_invalid_copr_project_name(github_pr_event):
    """Verify that comment we post when we fail to update chroots on our projects
    is correct and not the one about permissions"""
    helper = build_helper(
        event=github_pr_event,
        db_trigger=flexmock(
            job_config_trigger_type=JobConfigTriggerType.pull_request,
            id=123,
            job_trigger_model_type=JobTriggerModelType.pull_request,
        ),
    )
    # enforce that we are reporting on our own Copr project
    helper.job_build.owner = "packit"
    flexmock(CoprHelper).should_receive("get_valid_build_targets").and_return(
        {"f31", "f32"}
    )
    flexmock(CoprHelper).should_receive("create_copr_project_if_not_exists").and_raise(
        PackitCoprProjectException(
            "Cannot create a new Copr project (owner=packit-stg project="
            "packit-specfile-91-fedora+epel chroots=['fedora-rawhide-x86_64', "
            "'epel-9-x86_64', 'fedora-36-x86_64', 'fedora-35-x86_64']): name: "
            "Name must contain only letters, digits, underscores, dashes and dots.",
        )
    )
    expected_body = (
        "We were not able to find or create Copr project "
        "`packit/the-example-namespace-the-example-repo-342` "
        "specified in the config with the following error:\n"
        "```\nCannot create a new Copr project (owner=packit-stg project="
        "packit-specfile-91-fedora+epel chroots=['fedora-rawhide-x86_64', "
        "'epel-9-x86_64', 'fedora-36-x86_64', 'fedora-35-x86_64']): name: "
        "Name must contain only letters, digits, underscores, dashes and dots.\n```\n---\n"
        "Please check your configuration for:\n\n"
        "1. typos in owner and project name (groups need to be prefixed with `@`)\n"
        "2. whether the project name doesn't contain not allowed characters (only letters, "
        "digits, underscores, dashes and dots must be used)\n"
        "3. whether the project itself exists (Packit creates projects"
        " only in its own namespace)\n"
        "4. whether Packit is allowed to build in your Copr project\n"
        "5. whether your Copr project/group is not private"
    )
    status_reporter = (
        flexmock()
        .should_receive("comment")
        .with_args(body=expected_body)
        .and_return()
        .mock()
    )

    flexmock(CoprBuildJobHelper).should_receive("status_reporter").and_return(
        status_reporter
    )
    with pytest.raises(PackitCoprProjectException):
        helper.create_copr_project_if_not_exists()


@pytest.mark.parametrize(
    "jobs,should_pass",
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
                        )
                    },
                ),
            ],
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
                        )
                    },
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            use_internal_tf=True,
                        )
                    },
                ),
            ],
            False,
            id="multiple_test_jobs_one_internal",
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
                        )
                    },
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    skip_build=True,
                    packages={
                        "package": CommonPackageConfig(
                            use_internal_tf=True,
                        )
                    },
                ),
            ],
            True,
            id="multiple_test_jobs_one_internal_skip_build",
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
                    skip_build=True,
                    packages={
                        "package": CommonPackageConfig(
                            identifier="public",
                        )
                    },
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            use_internal_tf=True,
                        )
                    },
                ),
            ],
            False,
            id="multiple_test_jobs_one_internal_another_skip_build",
        ),
    ],
)
def test_check_if_actor_can_run_job_and_report(jobs, should_pass):
    package_config = PackageConfig(packages={"package": CommonPackageConfig()})
    package_config.jobs = jobs

    flexmock(PullRequestModel).should_receive("get_or_create").and_return(
        flexmock(
            job_config_trigger_type=JobConfigTriggerType.pull_request,
            id=123,
            job_trigger_model_type=JobTriggerModelType.pull_request,
        )
    )

    gh_project = flexmock(namespace="n", repo="r")
    gh_project.should_receive("can_merge_pr").with_args("actor").and_return(False)
    flexmock(EventData).should_receive("get_project").and_return(gh_project)
    flexmock(ServiceConfig).should_receive("get_project").and_return(gh_project)

    flexmock(IsGitForgeProjectAndEventOk).should_receive("pre_check").and_return(True)

    if not should_pass:
        flexmock(CoprBuildJobHelper).should_receive("report_status_to_build").once()

    assert (
        CoprBuildHandler.pre_check(
            package_config,
            jobs[0],
            {
                "event_type": "PullRequestGithubEvent",
                "actor": "actor",
                "project_url": "url",
            },
        )
        == should_pass
    )
