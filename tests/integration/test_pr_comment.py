# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json
import shutil
from pathlib import Path

import pytest
from celery.canvas import Signature
from celery.canvas import group as celery_group
from flexmock import flexmock
from github.MainClass import Github
from ogr.abstract import AuthMethod, CommitStatus
from ogr.services.github import GithubProject, GithubService
from ogr.services.pagure import PagureProject
from ogr.utils import RequestResponse
from packit.api import PackitAPI
from packit.config import (
    Deployment,
    JobConfigTriggerType,
)
from packit.copr_helper import CoprHelper
from packit.distgit import DistGit
from packit.exceptions import PackitConfigException
from packit.local_project import LocalProject, LocalProjectBuilder
from packit.upstream import GitUpstream
from packit.utils import commands
from packit.utils.koji_helper import KojiHelper

import packit_service.models
import packit_service.service.urls as urls
from packit_service.config import ServiceConfig
from packit_service.constants import (
    COMMENT_REACTION,
    CONTACTS_URL,
    DEFAULT_RETRY_LIMIT,
    DOCS_HOW_TO_CONFIGURE_URL,
    DOCS_VALIDATE_CONFIG,
    DOCS_VALIDATE_HOOKS,
    SANDCASTLE_WORK_DIR,
    TASK_ACCEPTED,
)
from packit_service.events import abstract, pagure
from packit_service.models import (
    BodhiUpdateGroupModel,
    BodhiUpdateTargetModel,
    BuildStatus,
    CoprBuildTargetModel,
    KojiBuildGroupModel,
    KojiBuildTargetModel,
    KojiTagRequestGroupModel,
    KojiTagRequestTargetModel,
    PipelineModel,
    ProjectEventModel,
    ProjectEventModelType,
    PullRequestModel,
    SidetagGroupModel,
    SidetagModel,
    SyncReleaseJobType,
    SyncReleaseModel,
    SyncReleasePullRequestModel,
    SyncReleaseStatus,
    SyncReleaseTargetModel,
    SyncReleaseTargetStatus,
    TestingFarmResult,
    TFTTestRunGroupModel,
    TFTTestRunTargetModel,
)
from packit_service.service.db_project_events import AddPullRequestEventToDb
from packit_service.utils import (
    get_packit_commands_from_comment,
)
from packit_service.worker.allowlist import Allowlist
from packit_service.worker.celery_task import CeleryTask
from packit_service.worker.checker.run_condition import IsRunConditionSatisfied
from packit_service.worker.handlers import distgit
from packit_service.worker.handlers.bodhi import (
    RetriggerBodhiUpdateHandler,
)
from packit_service.worker.handlers.distgit import (
    DownstreamKojiBuildHandler,
    TagIntoSidetagHandler,
    aliases,
)
from packit_service.worker.helpers.build.copr_build import CoprBuildJobHelper
from packit_service.worker.helpers.build.koji_build import KojiBuildJobHelper
from packit_service.worker.helpers.testing_farm import (
    DownstreamTestingFarmJobHelper,
    TestingFarmClient,
    TestingFarmJobHelper,
)
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.mixin import PackitAPIWithDownstreamMixin
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.reporting import (
    BaseCommitStatus,
    StatusReporter,
    StatusReporterGithubChecks,
)
from packit_service.worker.reporting.news import DistgitAnnouncement
from packit_service.worker.result import TaskResults
from packit_service.worker.tasks import (
    run_downstream_koji_build,
    run_downstream_koji_scratch_build_handler,
    run_downstream_testing_farm_handler,
    run_koji_build_handler,
    run_pull_from_upstream_handler,
    run_retrigger_bodhi_update,
    run_tag_into_sidetag_handler,
    run_testing_farm_handler,
)
from tests.spellbook import DATA_DIR, first_dict_value, get_parameters_from_results


@pytest.fixture(scope="module")
def pr_copr_build_comment_event():
    return json.loads(
        (DATA_DIR / "webhooks" / "github" / "pr_comment_copr_build.json").read_text(),
    )


@pytest.fixture(scope="module")
def pr_build_comment_event():
    return json.loads(
        (DATA_DIR / "webhooks" / "github" / "pr_comment_build.json").read_text(),
    )


@pytest.fixture(scope="module")
def pr_production_build_comment_event():
    return json.loads(
        (DATA_DIR / "webhooks" / "github" / "pr_comment_production_build.json").read_text(),
    )


@pytest.fixture(scope="module")
def pr_embedded_command_comment_event():
    return json.loads(
        (DATA_DIR / "webhooks" / "github" / "pr_comment_embedded_command.json").read_text(),
    )


@pytest.fixture(scope="module")
def pr_empty_comment_event():
    return json.loads(
        (DATA_DIR / "webhooks" / "github" / "pr_comment_empty.json").read_text(),
    )


@pytest.fixture(scope="module")
def pr_packit_comment_command_without_argument_event():
    return json.loads(
        (
            DATA_DIR / "webhooks" / "github" / "issue_comment_packit_command_without_argument.json"
        ).read_text(),
    )


@pytest.fixture(scope="module")
def pr_wrong_packit_comment_event():
    return json.loads(
        (DATA_DIR / "webhooks" / "github" / "issue_comment_wrong_packit_command.json").read_text(),
    )


@pytest.fixture
def mock_pr_comment_functionality(request):
    packit_yaml = "{'specfile_path': 'the-specfile.spec', 'jobs': " + str(request.param) + "}"

    flexmock(
        GithubProject,
        full_repo_name="packit-service/hello-world",
        get_file_content=lambda path, ref: packit_yaml,
        get_files=lambda ref, filter_regex: ["the-specfile.spec"],
        get_web_url=lambda: "https://github.com/the-namespace/the-repo",
    )
    flexmock(
        GithubProject,
        get_files=lambda ref, recursive: ["foo.spec", "packit.yaml"],
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)
    db_project_object = flexmock(
        id=9,
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        project_event_model_type=ProjectEventModelType.pull_request,
    )
    db_project_event = (
        flexmock()
        .should_receive("get_project_event_object")
        .and_return(db_project_object)
        .mock()
        .should_receive("set_packages_config")
        .mock()
    )
    flexmock(ProjectEventModel).should_receive("get_or_create").with_args(
        type=ProjectEventModelType.pull_request,
        event_id=9,
        commit_sha="12345",
    ).and_return(db_project_event)
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=9,
        namespace="packit-service",
        repo_name="hello-world",
        project_url="https://github.com/packit-service/hello-world",
    ).and_return(db_project_object)
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(LocalProjectBuilder, _refresh_the_state=lambda *args: flexmock())
    flexmock(Allowlist, check_and_report=True)


def one_job_finished_with_msg(results: list[TaskResults], msg: str):
    for value in results:
        assert value["success"]
        if value["details"]["msg"] == msg:
            break
    else:
        raise AssertionError(f"None of the jobs finished with {msg!r}")


@pytest.mark.parametrize(
    "mock_pr_comment_functionality",
    (
        [
            [
                {
                    "trigger": "pull_request",
                    "job": "tests",
                    "metadata": {"targets": "fedora-rawhide-x86_64"},
                },
            ],
        ]
    ),
    indirect=True,
)
def test_pr_comment_build_test_handler(
    mock_pr_comment_functionality,
    pr_build_comment_event,
):
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(CoprHelper).should_receive("get_valid_build_targets").and_return(set())
    flexmock(TestingFarmJobHelper).should_receive("report_status_to_tests").with_args(
        description="Test job requires build job definition in the configuration.",
        state=BaseCommitStatus.neutral,
        url="",
        markdown_content="Make sure you have a `copr_build` job defined "
        "with trigger `pull_request`.\n\n"
        "For more info, please check out "
        "[the documentation](https://packit.dev/docs/configuration/upstream/tests).\n\n",
    ).once()
    flexmock(celery_group).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(1).and_return()
    pr = flexmock(head_commit="12345")
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    comment = flexmock()
    flexmock(pr).should_receive("get_comment").and_return(comment)
    flexmock(comment).should_receive("add_reaction").with_args(COMMENT_REACTION).once()

    processing_results = SteveJobs().process_message(pr_build_comment_event)
    assert len(processing_results) == 0


@pytest.mark.parametrize(
    "mock_pr_comment_functionality",
    (
        [
            [
                {
                    "trigger": "pull_request",
                    "job": "copr_build",
                    "metadata": {"targets": "fedora-rawhide-x86_64"},
                },
                {
                    "trigger": "pull_request",
                    "job": "tests",
                    "metadata": {"targets": "fedora-rawhide-x86_64"},
                },
            ],
        ]
    ),
    indirect=True,
)
def test_pr_comment_build_build_and_test_handler(
    mock_pr_comment_functionality,
    pr_build_comment_event,
):
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(CoprHelper).should_receive("get_valid_build_targets").and_return(set())
    flexmock(CoprBuildJobHelper).should_receive("report_status_to_build").with_args(
        description=TASK_ACCEPTED,
        state=BaseCommitStatus.pending,
        url="",
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()
    flexmock(TestingFarmJobHelper).should_receive("report_status_to_tests").with_args(
        description=TASK_ACCEPTED,
        state=BaseCommitStatus.pending,
        url="",
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()
    flexmock(CoprBuildJobHelper).should_receive(
        "is_custom_copr_project_defined",
    ).and_return(False).once()
    flexmock(celery_group).should_receive("apply_async").twice()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    pr = flexmock(head_commit="12345")
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    comment = flexmock()
    flexmock(pr).should_receive("get_comment").and_return(comment)
    flexmock(comment).should_receive("add_reaction").with_args(COMMENT_REACTION).once()

    processing_results = SteveJobs().process_message(pr_build_comment_event)
    assert len(processing_results) == 2

    copr_build_job = [item for item in processing_results if item["details"]["job"] == "copr_build"]
    assert copr_build_job

    test_job = [item for item in processing_results if item["details"]["job"] == "tests"]
    assert test_job

    event_dict, job, job_config, package_config = get_parameters_from_results(test_job)
    assert json.dumps(event_dict)
    results = run_testing_farm_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert first_dict_value(results["job"])["success"]
    assert "already handled" in first_dict_value(results["job"])["details"]["msg"]


@pytest.mark.parametrize(
    "mock_pr_comment_functionality",
    (
        [
            [
                {
                    "trigger": "pull_request",
                    "job": "copr_build",
                    "metadata": {"targets": "fedora-rawhide-x86_64"},
                },
                {
                    "trigger": "pull_request",
                    "job": "tests",
                    "manual_trigger": True,
                    "metadata": {"targets": "fedora-rawhide-x86_64"},
                },
            ],
        ]
    ),
    indirect=True,
)
def test_pr_comment_build_build_and_test_handler_manual_test_reporting(
    mock_pr_comment_functionality,
    pr_build_comment_event,
):
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(CoprHelper).should_receive("get_valid_build_targets").and_return(set())
    flexmock(CoprBuildJobHelper).should_receive("report_status_to_build").with_args(
        description=TASK_ACCEPTED,
        state=BaseCommitStatus.pending,
        url="",
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()
    # check that we are not reporting task accepted if manual_trigger is enabled for tests
    flexmock(TestingFarmJobHelper).should_receive("report_status_to_tests").with_args(
        description=TASK_ACCEPTED,
        state=BaseCommitStatus.pending,
        url="",
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).never()
    flexmock(CoprBuildJobHelper).should_receive(
        "is_custom_copr_project_defined",
    ).and_return(False).once()
    flexmock(celery_group).should_receive("apply_async").twice()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    pr = flexmock(head_commit="12345")
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    comment = flexmock()
    flexmock(pr).should_receive("get_comment").and_return(comment)
    flexmock(comment).should_receive("add_reaction").with_args(COMMENT_REACTION).once()

    processing_results = SteveJobs().process_message(pr_build_comment_event)
    assert len(processing_results) == 2

    copr_build_job = [item for item in processing_results if item["details"]["job"] == "copr_build"]
    assert copr_build_job

    test_job = [item for item in processing_results if item["details"]["job"] == "tests"]
    assert test_job

    event_dict, job, job_config, package_config = get_parameters_from_results(test_job)
    assert json.dumps(event_dict)
    results = run_testing_farm_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert first_dict_value(results["job"])["success"]


def test_pr_comment_production_build_handler(pr_production_build_comment_event):
    packit_yaml = str(
        {
            "specfile_path": "the-specfile.spec",
            "jobs": [
                {
                    "trigger": "pull_request",
                    "job": "upstream_koji_build",
                    "metadata": {"targets": "fedora-rawhide-x86_64", "scratch": "true"},
                },
            ],
        },
    )
    comment = flexmock(add_reaction=lambda reaction: None)
    flexmock(
        GithubProject,
        full_repo_name="packit-service/hello-world",
        get_file_content=lambda path, ref: packit_yaml,
        get_files=lambda ref, filter_regex: ["the-specfile.spec"],
        get_web_url=lambda: "https://github.com/the-namespace/the-repo",
        get_pr=lambda pr_id: flexmock(
            head_commit="12345",
            get_comment=lambda comment_id: comment,
        ),
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)

    db_project_object = flexmock(
        id=9,
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        project_event_model_type=ProjectEventModelType.pull_request,
    )
    project_event = (
        flexmock()
        .should_receive("get_project_event_object")
        .and_return(db_project_object)
        .mock()
        .should_receive("set_packages_config")
        .mock()
    )
    flexmock(PullRequestModel).should_receive("get_by_id").with_args(9).and_return(
        project_event,
    )
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Allowlist, check_and_report=True)

    flexmock(ProjectEventModel).should_receive("get_or_create").with_args(
        type=ProjectEventModelType.pull_request,
        event_id=9,
        commit_sha="12345",
    ).and_return(project_event)
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=9,
        namespace="packit-service",
        repo_name="hello-world",
        project_url="https://github.com/packit-service/hello-world",
    ).and_return(db_project_object)
    flexmock(KojiBuildJobHelper).should_receive("run_koji_build").and_return(
        TaskResults(success=True, details={}),
    )
    flexmock(
        GithubProject,
        get_files=lambda ref, recursive: ["foo.spec", "packit.yaml"],
    )
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(KojiBuildJobHelper).should_receive("report_status_to_build").with_args(
        description=TASK_ACCEPTED,
        state=BaseCommitStatus.pending,
        url="",
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()
    flexmock(celery_group).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    pr = flexmock(head_commit="12345")
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    comment = flexmock()
    flexmock(pr).should_receive("get_comment").and_return(comment)
    flexmock(comment).should_receive("add_reaction").with_args(COMMENT_REACTION).once()

    processing_results = SteveJobs().process_message(pr_production_build_comment_event)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    results = run_koji_build_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert first_dict_value(results["job"])["success"]


@pytest.mark.parametrize(
    "comment",
    (
        "",
        " ",
        "   ",
        "some unrelated",
        "some\nmore\nunrelated\ntext",
        "even\nsome → unicode",
        " stuff",
        " \n ",
        "x ",
        """comment with embedded /packit build not recognized
        unless /packit command is on line by itself""",
        "\n2nd line\n\n4th line",
        "1st line\n\t\n\t\t\n4th line\n",
    ),
)
def test_pr_comment_invalid(comment):
    commands = get_packit_commands_from_comment(
        comment,
        packit_comment_command_prefix="/packit",
    )
    assert len(commands) == 0


@pytest.mark.parametrize(
    "comment, command",
    (
        ("", "/packit"),
        ("", "/packit-stg"),
        ("/packit build", "/packit-stg"),
        ("/packit-something build", "/packit-stg"),
        ("/packit-stgg build", "/packit-stg"),
        ("/packit-stg build", "/packit"),
    ),
)
def test_pr_comment_invalid_with_command_set(comment, command):
    commands = get_packit_commands_from_comment(
        comment,
        packit_comment_command_prefix=command,
    )
    assert len(commands) == 0


@pytest.mark.parametrize(
    "mock_pr_comment_functionality",
    (
        [
            [
                {
                    "trigger": "pull_request",
                    "job": "copr_build",
                    "metadata": {"targets": "fedora-rawhide-x86_64"},
                },
            ],
        ]
    ),
    indirect=True,
)
def test_pr_comment_empty_handler(
    mock_pr_comment_functionality,
    pr_empty_comment_event,
):
    flexmock(
        GithubProject,
        get_files=lambda ref, recursive: ["foo.spec", "packit.yaml"],
    )
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("can_merge_pr").and_return(True)
    pr = flexmock(head_commit="12345")
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    flexmock(Pushgateway).should_receive("push").times(1).and_return()

    results = SteveJobs().process_message(pr_empty_comment_event)[0]
    assert results["success"]
    assert results["details"]["msg"] == "No Packit command found in the comment."


@pytest.mark.parametrize(
    "mock_pr_comment_functionality",
    (
        [
            [
                {
                    "trigger": "pull_request",
                    "job": "copr_build",
                    "metadata": {"targets": "fedora-rawhide-x86_64"},
                },
            ],
        ]
    ),
    indirect=True,
)
def test_pr_comment_packit_only_handler(
    mock_pr_comment_functionality,
    pr_packit_comment_command_without_argument_event,
):
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("can_merge_pr").and_return(True)
    pr = flexmock(head_commit="12345")
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    flexmock(Pushgateway).should_receive("push").times(1).and_return()

    results = SteveJobs().process_message(
        pr_packit_comment_command_without_argument_event,
    )[0]
    assert results["success"]
    assert results["details"]["msg"] == "No Packit command found in the comment."


@pytest.mark.parametrize(
    "mock_pr_comment_functionality",
    (
        [
            [
                {
                    "trigger": "pull_request",
                    "job": "copr_build",
                    "targets": [
                        "fedora-rawhide-x86_64",
                    ],
                },
            ],
        ]
    ),
    indirect=True,
)
def test_pr_comment_wrong_packit_command_handler(
    mock_pr_comment_functionality,
    pr_wrong_packit_comment_event,
):
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("can_merge_pr").and_return(True)
    pr = flexmock(head_commit="12345")
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    flexmock(Pushgateway).should_receive("push").times(1).and_return()

    results = SteveJobs().process_message(pr_wrong_packit_comment_event)[0]
    assert results["success"]
    assert results["details"]["msg"] == "No Packit command found in the comment."


def test_pr_test_command_handler(
    add_pull_request_event_with_pr_id_9,
    pr_embedded_command_comment_event,
):
    jobs = [
        {
            "trigger": "pull_request",
            "job": "tests",
            "metadata": {"targets": "fedora-rawhide-x86_64"},
        },
        {
            "trigger": "pull_request",
            "job": "copr_build",
            "metadata": {"targets": "fedora-rawhide-x86_64"},
        },
    ]
    packit_yaml = "{'specfile_path': 'the-specfile.spec', 'jobs': " + str(jobs) + "}"
    _ = add_pull_request_event_with_pr_id_9
    pr = flexmock(head_commit="12345")
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    comment = flexmock()
    flexmock(pr).should_receive("get_comment").and_return(comment)
    flexmock(comment).should_receive("add_reaction").with_args(COMMENT_REACTION).once()
    flexmock(
        GithubProject,
        full_repo_name="packit-service/hello-world",
        get_file_content=lambda path, ref: packit_yaml,
        get_files=lambda ref, filter_regex: ["the-specfile.spec"],
        get_web_url=lambda: "https://github.com/the-namespace/the-repo",
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Allowlist, check_and_report=True)

    pr_embedded_command_comment_event["comment"]["body"] = "/packit test"
    flexmock(
        GithubProject,
        get_files=lambda ref, recursive: ["the-specfile.spec", "packit.yaml"],
    )
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(celery_group).should_receive("apply_async").once()
    flexmock(CoprHelper).should_receive("get_valid_build_targets").times(5).and_return(
        {"test-target"},
    )
    run = flexmock(test_run_group=None)
    test_run = flexmock(
        id=1,
        status=TestingFarmResult.new,
        copr_builds=[flexmock(status=BuildStatus.success)],
        target="fedora-rawhide-x86_64",
    )
    flexmock(PipelineModel).should_receive("create").and_return(run)
    flexmock(TFTTestRunTargetModel).should_receive("create").and_return(test_run)
    flexmock(TFTTestRunGroupModel).should_receive("create").with_args(
        [run], ranch="public"
    ).and_return(
        flexmock(grouped_targets=[test_run]),
    )
    flexmock(TestingFarmJobHelper).should_receive("get_latest_copr_build").and_return(
        flexmock(status=BuildStatus.success, group_of_targets=flexmock(runs=[run])),
    )
    flexmock(TestingFarmJobHelper).should_receive("run_testing_farm").once().and_return(
        TaskResults(success=True, details={}),
    )
    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    flexmock(TestingFarmJobHelper).should_receive("report_status_to_tests").with_args(
        description=TASK_ACCEPTED,
        state=BaseCommitStatus.pending,
        url="",
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    processing_results = SteveJobs().process_message(pr_embedded_command_comment_event)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    run_testing_farm_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )


def test_pr_test_command_handler_identifiers(
    add_pull_request_event_with_pr_id_9,
    pr_embedded_command_comment_event,
):
    jobs = [
        {
            "trigger": "pull_request",
            "job": "tests",
            "metadata": {"targets": "fedora-rawhide-x86_64"},
            "identifier": "test-job",
        },
        {
            "trigger": "pull_request",
            "job": "copr_build",
            "metadata": {"targets": "fedora-rawhide-x86_64"},
        },
    ]
    packit_yaml = "{'specfile_path': 'the-specfile.spec', 'jobs': " + str(jobs) + "}"
    _ = add_pull_request_event_with_pr_id_9
    pr = flexmock(head_commit="12345")
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    comment = flexmock()
    flexmock(pr).should_receive("get_comment").and_return(comment)
    flexmock(comment).should_receive("add_reaction").with_args(COMMENT_REACTION).once()
    flexmock(
        GithubProject,
        full_repo_name="packit-service/hello-world",
        get_file_content=lambda path, ref: packit_yaml,
        get_files=lambda ref, filter_regex: ["the-specfile.spec"],
        get_web_url=lambda: "https://github.com/the-namespace/the-repo",
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Allowlist, check_and_report=True)
    pr_embedded_command_comment_event["comment"]["body"] = "/packit test"
    flexmock(
        GithubProject,
        get_files=lambda ref, recursive: ["the-specfile.spec", "packit.yaml"],
    )
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(celery_group).should_receive("apply_async").once()
    flexmock(CoprHelper).should_receive("get_valid_build_targets").times(5).and_return(
        {"test-target"},
    )
    run = flexmock(test_run_group=None)
    test_run = flexmock(
        id=1,
        status=TestingFarmResult.new,
        copr_builds=[flexmock(status=BuildStatus.success)],
        target="fedora-rawhide-x86_64",
    )
    flexmock(PipelineModel).should_receive("create").and_return(run)
    flexmock(TFTTestRunTargetModel).should_receive("create").and_return(test_run)
    flexmock(TFTTestRunGroupModel).should_receive("create").with_args(
        [run], ranch="public"
    ).and_return(
        flexmock(grouped_targets=[test_run]),
    )
    flexmock(CoprBuildTargetModel).should_receive("get_all_by").with_args(
        project_name="packit-service-hello-world-9",
        commit_sha="12345",
        owner=None,
        target="test-target",
    ).and_return(
        [flexmock(status=BuildStatus.success, group_of_targets=flexmock(runs=[run]))],
    )
    flexmock(TestingFarmJobHelper).should_receive("run_testing_farm").once().and_return(
        TaskResults(success=True, details={}),
    )
    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    flexmock(TestingFarmJobHelper).should_receive("report_status_to_tests").with_args(
        description=TASK_ACCEPTED,
        state=BaseCommitStatus.pending,
        url="",
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    processing_results = SteveJobs().process_message(pr_embedded_command_comment_event)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    run_testing_farm_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )


@pytest.mark.parametrize(
    "retry_number,description,markdown_content,status,response,delay",
    (
        [
            (
                0,
                "Failed to submit tests. The task will be retried in 1 minute.",
                "Reason",
                BaseCommitStatus.pending,
                RequestResponse(
                    status_code=500,
                    ok=True,
                    content=json.dumps({}).encode(),
                    json={},
                    reason="Reason",
                ),
                60,
            ),
            (
                1,
                "Failed to submit tests. The task will be retried in 2 minutes.",
                "Reason",
                BaseCommitStatus.pending,
                RequestResponse(
                    status_code=500,
                    ok=True,
                    content=json.dumps({}).encode(),
                    json={},
                    reason="Reason",
                ),
                120,
            ),
            (
                2,
                "Failed to submit tests: reason.",
                None,
                BaseCommitStatus.failure,
                RequestResponse(
                    status_code=500,
                    ok=True,
                    content=json.dumps({}).encode(),
                    json={},
                    reason="reason",
                ),
                None,
            ),
        ]
    ),
)
def test_pr_test_command_handler_retries(
    add_pull_request_event_with_sha_0011223344,
    pr_embedded_command_comment_event,
    retry_number,
    description,
    markdown_content,
    status,
    response,
    delay,
):
    jobs = [
        {
            "trigger": "pull_request",
            "job": "tests",
            "metadata": {"targets": "fedora-rawhide-x86_64", "skip_build": True},
        },
    ]
    packit_yaml = "{'specfile_path': 'the-specfile.spec', 'jobs': " + str(jobs) + "}"
    _ = add_pull_request_event_with_sha_0011223344
    pr = flexmock(
        source_project=flexmock(
            get_web_url=lambda: "https://github.com/someone/hello-world",
        ),
        target_project=flexmock(
            get_web_url=lambda: "https://github.com/packit-service/hello-world",
        ),
        head_commit="0011223344",
        target_branch_head_commit="deadbeef",
        source_branch="the-source-branch",
        target_branch="the-target-branch",
    )
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    comment = flexmock()
    flexmock(pr).should_receive("get_comment").and_return(comment)
    flexmock(comment).should_receive("add_reaction").with_args(COMMENT_REACTION).once()
    flexmock(
        GithubProject,
        full_repo_name="packit-service/hello-world",
        get_file_content=lambda path, ref: packit_yaml,
        get_files=lambda ref, filter_regex: ["the-specfile.spec"],
        get_web_url=lambda: "https://github.com/packit-service/hello-world",
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)

    ServiceConfig.get_service_config().testing_farm_api_url = (
        "https://api.dev.testing-farm.io/v0.1/"
    )
    ServiceConfig.get_service_config().testing_farm_secret = "secret-token"

    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Allowlist, check_and_report=True)

    pr_embedded_command_comment_event["comment"]["body"] = "/packit test"
    flexmock(
        GithubProject,
        get_files=lambda ref, recursive: ["the-specfile.spec", "packit.yaml"],
    )
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(CoprHelper).should_receive("get_valid_build_targets").and_return(
        {"fedora-rawhide-x86_64"},
    )
    flexmock(TestingFarmJobHelper).should_receive("get_latest_copr_build").never()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    payload = {
        "test": {
            "tmt": {
                "url": "https://github.com/someone/hello-world",
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
                "variables": {
                    "PACKIT_FULL_REPO_NAME": "packit-service/hello-world",
                    "PACKIT_UPSTREAM_NAME": "hello-world",
                    "PACKIT_DOWNSTREAM_NAME": "hello-world",
                    "PACKIT_DOWNSTREAM_URL": "https://src.fedoraproject.org/rpms/hello-world.git",
                    "PACKIT_PACKAGE_NAME": "hello-world",
                    "PACKIT_COMMIT_SHA": "0011223344",
                    "PACKIT_SOURCE_SHA": "0011223344",
                    "PACKIT_TARGET_SHA": "deadbeef",
                    "PACKIT_SOURCE_BRANCH": "the-source-branch",
                    "PACKIT_TARGET_BRANCH": "the-target-branch",
                    "PACKIT_SOURCE_URL": "https://github.com/someone/hello-world",
                    "PACKIT_TARGET_URL": "https://github.com/packit-service/hello-world",
                    "PACKIT_PR_ID": 9,
                },
            },
        ],
        "notification": {
            "webhook": {
                "url": "https://prod.packit.dev/api/testing-farm/results",
                "token": "secret-token",
            },
        },
    }

    flexmock(TestingFarmJobHelper).should_receive("is_fmf_configured").and_return(True)
    flexmock(TestingFarmClient).should_receive("distro2compose").and_return(
        "Fedora-Rawhide",
    )

    flexmock(TestingFarmClient).should_receive(
        "send_testing_farm_request",
    ).with_args(endpoint="requests", method="POST", data=payload).and_return(response)

    flexmock(StatusReporterGithubChecks).should_receive("set_status").with_args(
        state=BaseCommitStatus.pending,
        description=TASK_ACCEPTED,
        check_name="testing-farm:fedora-rawhide-x86_64",
        url="",
        markdown_content=None,
        links_to_external_services=None,
    ).once()

    flexmock(StatusReporterGithubChecks).should_receive("set_status").with_args(
        state=BaseCommitStatus.running,
        description="Submitting the tests ...",
        check_name="testing-farm:fedora-rawhide-x86_64",
        url="",
        markdown_content=None,
        links_to_external_services=None,
    ).once()

    flexmock(GithubProject).should_receive("get_web_url").and_return(
        "https://github.com/packit-service/hello-world",
    )

    # On first run, we create the model, afterwards, we should get it from the DB
    test_run = flexmock(
        id=1,
        target="fedora-rawhide-x86_64",
        status=TestingFarmResult.new,
    )
    group = flexmock(id=1, grouped_targets=[test_run])
    test_run.group_of_targets = group
    if retry_number > 0:
        flexmock(PipelineModel).should_receive("create").never()
        flexmock(TFTTestRunGroupModel).should_receive("create").never()
        flexmock(TFTTestRunTargetModel).should_receive("create").never()
        flexmock(TFTTestRunTargetModel).should_receive("get_by_id").and_return(test_run)
    else:
        flexmock(PipelineModel).should_receive("create").and_return(
            flexmock(test_run_group=None),
        )
        flexmock(TFTTestRunGroupModel).should_receive("create").and_return(group)
        flexmock(TFTTestRunTargetModel).should_receive("create").and_return(test_run)

    if retry_number == 2:
        flexmock(test_run).should_receive("set_status").with_args(
            TestingFarmResult.error,
        )
    else:
        flexmock(test_run).should_receive("set_status").with_args(
            TestingFarmResult.retry,
        )

    flexmock(StatusReporterGithubChecks).should_receive("set_status").with_args(
        state=status,
        description=description,
        check_name="testing-farm:fedora-rawhide-x86_64",
        url="",
        markdown_content=markdown_content,
        links_to_external_services=None,
    ).once()
    flexmock(celery_group).should_receive("apply_async").once()

    processing_results = SteveJobs().process_message(pr_embedded_command_comment_event)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )

    if delay is not None:
        flexmock(CeleryTask).should_receive("retry").with_args(
            delay=delay,
            kargs={"testing_farm_target_id": test_run.id},
        ).once()

    assert json.dumps(event_dict)
    task = run_testing_farm_handler.__wrapped__.__func__
    task(
        flexmock(
            request=flexmock(retries=retry_number, kwargs={}),
            max_retries=DEFAULT_RETRY_LIMIT,
        ),
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
        testing_farm_target_id=None if retry_number == 0 else test_run.id,
    )


def test_pr_test_command_handler_skip_build_option(
    add_pull_request_event_with_sha_0011223344,
    pr_embedded_command_comment_event,
):
    _ = add_pull_request_event_with_sha_0011223344
    jobs = [
        {
            "trigger": "pull_request",
            "job": "tests",
            "metadata": {"targets": "fedora-rawhide-x86_64", "skip_build": True},
        },
    ]
    packit_yaml = "{'specfile_path': 'the-specfile.spec', 'jobs': " + str(jobs) + "}"
    pr = flexmock(
        source_project=flexmock(
            get_web_url=lambda: "https://github.com/someone/hello-world",
        ),
        target_project=flexmock(
            get_web_url=lambda: "https://github.com/packit-service/hello-world",
        ),
        head_commit="0011223344",
        target_branch_head_commit="deadbeef",
        source_branch="the-source-branch",
        target_branch="the-target-branch",
    )
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    comment = flexmock()
    flexmock(pr).should_receive("get_comment").and_return(comment)
    flexmock(comment).should_receive("add_reaction").with_args(COMMENT_REACTION).once()
    flexmock(
        GithubProject,
        full_repo_name="packit-service/hello-world",
        get_file_content=lambda path, ref: packit_yaml,
        get_files=lambda ref, filter_regex: ["the-specfile.spec"],
        get_web_url=lambda: "https://github.com/packit-service/hello-world",
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)

    ServiceConfig.get_service_config().testing_farm_api_url = (
        "https://api.dev.testing-farm.io/v0.1/"
    )
    ServiceConfig.get_service_config().testing_farm_secret = "secret-token"

    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Allowlist, check_and_report=True)
    pr_embedded_command_comment_event["comment"]["body"] = "/packit test"
    flexmock(
        GithubProject,
        get_files=lambda ref, recursive: ["foo.spec", ".packit.yaml"],
    )
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(CoprHelper).should_receive("get_valid_build_targets").and_return(
        {"fedora-rawhide-x86_64"},
    )
    flexmock(TestingFarmJobHelper).should_receive("get_latest_copr_build").never()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    flexmock(TestingFarmJobHelper).should_receive("report_status_to_tests").with_args(
        description=TASK_ACCEPTED,
        state=BaseCommitStatus.pending,
        url="",
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    payload = {
        "test": {
            "tmt": {
                "url": "https://github.com/someone/hello-world",
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
                "variables": {
                    "PACKIT_FULL_REPO_NAME": "packit-service/hello-world",
                    "PACKIT_UPSTREAM_NAME": "hello-world",
                    "PACKIT_DOWNSTREAM_NAME": "hello-world",
                    "PACKIT_DOWNSTREAM_URL": "https://src.fedoraproject.org/rpms/hello-world.git",
                    "PACKIT_PACKAGE_NAME": "hello-world",
                    "PACKIT_COMMIT_SHA": "0011223344",
                    "PACKIT_SOURCE_SHA": "0011223344",
                    "PACKIT_TARGET_SHA": "deadbeef",
                    "PACKIT_SOURCE_BRANCH": "the-source-branch",
                    "PACKIT_TARGET_BRANCH": "the-target-branch",
                    "PACKIT_SOURCE_URL": "https://github.com/someone/hello-world",
                    "PACKIT_TARGET_URL": "https://github.com/packit-service/hello-world",
                    "PACKIT_PR_ID": 9,
                },
            },
        ],
        "notification": {
            "webhook": {
                "url": "https://prod.packit.dev/api/testing-farm/results",
                "token": "secret-token",
            },
        },
    }

    flexmock(TestingFarmJobHelper).should_receive("is_fmf_configured").and_return(True)
    flexmock(TestingFarmClient).should_receive("distro2compose").and_return(
        "Fedora-Rawhide",
    )

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
        description="Submitting the tests ...",
        check_names="testing-farm:fedora-rawhide-x86_64",
        url="",
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    flexmock(GithubProject).should_receive("get_web_url").and_return(
        "https://github.com/packit-service/hello-world",
    )

    tft_test_run_model = flexmock(
        id=5,
        status=TestingFarmResult.new,
        target="fedora-rawhide-x86_64",
    )
    run_model = flexmock(test_run_group=None)
    flexmock(PipelineModel).should_receive("create").and_return(run_model)
    group = flexmock(grouped_targets=[tft_test_run_model])
    flexmock(TFTTestRunGroupModel).should_receive("create").with_args(
        [run_model],
        ranch="public",
    ).and_return(group)
    flexmock(TFTTestRunTargetModel).should_receive("create").with_args(
        pipeline_id=None,
        identifier=None,
        status=TestingFarmResult.new,
        target="fedora-rawhide-x86_64",
        web_url=None,
        test_run_group=group,
        copr_build_targets=[],
        data={"base_project_url": "https://github.com/packit-service/hello-world"},
    ).and_return(tft_test_run_model)
    flexmock(tft_test_run_model).should_receive("set_pipeline_id").with_args(
        pipeline_id,
    ).once()

    urls.DASHBOARD_URL = "https://dashboard.localhost"
    flexmock(StatusReporter).should_receive("report").with_args(
        description="Tests have been submitted ...",
        state=BaseCommitStatus.running,
        url="https://dashboard.localhost/jobs/testing-farm/5",
        check_names="testing-farm:fedora-rawhide-x86_64",
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()
    flexmock(celery_group).should_receive("apply_async").once()

    processing_results = SteveJobs().process_message(pr_embedded_command_comment_event)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    run_testing_farm_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )


def test_pr_test_command_handler_compose_not_present(
    add_pull_request_event_with_sha_0011223344,
    pr_embedded_command_comment_event,
):
    jobs = [
        {
            "trigger": "pull_request",
            "job": "tests",
            "metadata": {"targets": "fedora-rawhide-x86_64", "skip_build": True},
        },
    ]
    packit_yaml = "{'specfile_path': 'the-specfile.spec', 'jobs': " + str(jobs) + "}"
    _ = add_pull_request_event_with_sha_0011223344
    pr = flexmock(
        source_project=flexmock(
            get_web_url=lambda: "https://github.com/someone/hello-world",
        ),
        target_project=flexmock(
            get_web_url=lambda: "https://github.com/packit-service/hello-world",
        ),
        head_commit="0011223344",
        target_branch_head_commit="deadbeef",
        source_branch="the-source-branch",
        target_branch="the-target-branch",
    )
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    comment = flexmock()
    flexmock(pr).should_receive("get_comment").and_return(comment)
    flexmock(comment).should_receive("add_reaction").with_args(COMMENT_REACTION).once()
    flexmock(
        GithubProject,
        full_repo_name="packit-service/hello-world",
        get_file_content=lambda path, ref: packit_yaml,
        get_files=lambda ref, filter_regex: ["the-specfile.spec"],
        get_web_url=lambda: "https://github.com/packit-service/hello-world",
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)

    ServiceConfig.get_service_config().testing_farm_api_url = (
        "https://api.dev.testing-farm.io/v0.1/"
    )
    ServiceConfig.get_service_config().testing_farm_secret = "secret-token"

    run_model = flexmock(test_run_group=None)
    test_run = flexmock(
        id=1,
        status=TestingFarmResult.new,
        copr_builds=[flexmock(status=BuildStatus.success)],
        target="fedora-rawhide-x86_64",
    )
    flexmock(PipelineModel).should_receive("create").and_return(run_model)
    flexmock(TFTTestRunTargetModel).should_receive("create").and_return(test_run)
    flexmock(TFTTestRunGroupModel).should_receive("create").with_args(
        [run_model],
        ranch="public",
    ).and_return(flexmock(grouped_targets=[test_run]))
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Allowlist, check_and_report=True)
    pr_embedded_command_comment_event["comment"]["body"] = "/packit test"
    flexmock(
        GithubProject,
        get_files=lambda ref, recursive: ["foo.spec", ".packit.yaml"],
    )
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(CoprHelper).should_receive("get_valid_build_targets").and_return(
        {"fedora-rawhide-x86_64"},
    )
    flexmock(TestingFarmJobHelper).should_receive("get_latest_copr_build").never()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    flexmock(TestingFarmJobHelper).should_receive("report_status_to_tests").with_args(
        description=TASK_ACCEPTED,
        state=BaseCommitStatus.pending,
        url="",
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    flexmock(TestingFarmJobHelper).should_receive("is_fmf_configured").and_return(True)

    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.running,
        description="Submitting the tests ...",
        check_names="testing-farm:fedora-rawhide-x86_64",
        url="",
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    response = flexmock(
        status_code=200,
        json=lambda: {"composes": [{"name": "some-other-compose"}]},
    )
    flexmock(TestingFarmClient).should_receive(
        "send_testing_farm_request",
    ).with_args(endpoint="composes/public").and_return(response).once()

    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.error,
        description="The compose Fedora-Rawhide is not available in the public "
        "Testing Farm infrastructure.",
        check_names="testing-farm:fedora-rawhide-x86_64",
        url="",
        markdown_content="The compose Fedora-Rawhide (from target fedora-rawhide) does not match "
        "any compose in the list of available composes:\n"
        "{'some-other-compose'}. Please, check the targets defined in your test job configuration. "
        "If you think your configuration is correct, get "
        "in touch with [us](https://packit.dev/#contact).",
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    flexmock(celery_group).should_receive("apply_async").once()

    processing_results = SteveJobs().process_message(pr_embedded_command_comment_event)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    run_testing_farm_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )


def test_pr_test_command_handler_composes_not_available(
    add_pull_request_event_with_sha_0011223344,
    pr_embedded_command_comment_event,
):
    jobs = [
        {
            "trigger": "pull_request",
            "job": "tests",
            "metadata": {"targets": "fedora-rawhide-x86_64", "skip_build": True},
        },
    ]
    packit_yaml = "{'specfile_path': 'the-specfile.spec', 'jobs': " + str(jobs) + "}"
    _ = add_pull_request_event_with_sha_0011223344
    pr = flexmock(
        source_project=flexmock(
            get_web_url=lambda: "https://github.com/someone/hello-world",
        ),
        target_project=flexmock(
            get_web_url=lambda: "https://github.com/packit-service/hello-world",
        ),
        head_commit="0011223344",
        target_branch_head_commit="deadbeef",
        source_branch="the-source-branch",
        target_branch="the-target-branch",
    )
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    comment = flexmock()
    flexmock(pr).should_receive("get_comment").and_return(comment)
    flexmock(comment).should_receive("add_reaction").with_args(COMMENT_REACTION).once()
    flexmock(
        GithubProject,
        full_repo_name="packit-service/hello-world",
        get_file_content=lambda path, ref: packit_yaml,
        get_files=lambda ref, filter_regex: ["the-specfile.spec"],
        get_web_url=lambda: "https://github.com/packit-service/hello-world",
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)

    ServiceConfig.get_service_config().testing_farm_api_url = (
        "https://api.dev.testing-farm.io/v0.1/"
    )
    ServiceConfig.get_service_config().testing_farm_secret = "secret-token"

    run_model = flexmock(test_run_group=None)
    test_run = flexmock(
        id=1,
        status=TestingFarmResult.new,
        copr_builds=[flexmock(status=BuildStatus.success)],
        target="fedora-rawhide-x86_64",
    )
    flexmock(PipelineModel).should_receive("create").and_return(run_model)
    flexmock(TFTTestRunTargetModel).should_receive("create").and_return(test_run)
    flexmock(TFTTestRunGroupModel).should_receive("create").with_args(
        [run_model],
        ranch="public",
    ).and_return(flexmock(grouped_targets=[test_run]))
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Allowlist, check_and_report=True)
    pr_embedded_command_comment_event["comment"]["body"] = "/packit test"
    flexmock(
        GithubProject,
        get_files=lambda ref, recursive: ["foo.spec", ".packit.yaml"],
    )
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(CoprHelper).should_receive("get_valid_build_targets").and_return(
        {"fedora-rawhide-x86_64"},
    )
    flexmock(TestingFarmJobHelper).should_receive("get_latest_copr_build").never()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    flexmock(TestingFarmJobHelper).should_receive("report_status_to_tests").with_args(
        description=TASK_ACCEPTED,
        state=BaseCommitStatus.pending,
        url="",
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    flexmock(TestingFarmJobHelper).should_receive("is_fmf_configured").and_return(True)

    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.running,
        description="Submitting the tests ...",
        check_names="testing-farm:fedora-rawhide-x86_64",
        url="",
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    flexmock(TestingFarmClient).should_receive(
        "send_testing_farm_request",
    ).with_args(endpoint="composes/public").and_return(flexmock(status_code=500)).once()

    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.error,
        description="We were not able to get the available TF composes.",
        check_names="testing-farm:fedora-rawhide-x86_64",
        url="",
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    flexmock(celery_group).should_receive("apply_async").once()

    processing_results = SteveJobs().process_message(pr_embedded_command_comment_event)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    run_testing_farm_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )


def test_pr_test_command_handler_not_allowed_external_contributor_on_internal_TF(
    add_pull_request_event_with_pr_id_9,
    pr_embedded_command_comment_event,
):
    jobs = [
        {
            "trigger": "pull_request",
            "job": "tests",
            "metadata": {"targets": "fedora-rawhide-x86_64", "use_internal_tf": True},
        },
        {
            "trigger": "pull_request",
            "job": "copr_build",
            "metadata": {"targets": "fedora-rawhide-x86_64"},
        },
    ]
    packit_yaml = "{'specfile_path': 'the-specfile.spec', 'jobs': " + str(jobs) + "}"
    db_project_object, _ = add_pull_request_event_with_pr_id_9
    pr = flexmock(head_commit="12345")
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    comment = flexmock()
    flexmock(pr).should_receive("get_comment").and_return(comment)
    flexmock(comment).should_receive("add_reaction").with_args(COMMENT_REACTION).once()
    gh_project = flexmock(
        GithubProject,
        full_repo_name="packit-service/hello-world",
        get_file_content=lambda path, ref: packit_yaml,
        get_files=lambda ref, filter_regex: ["the-specfile.spec"],
        get_web_url=lambda: "https://github.com/the-namespace/the-repo",
    )
    gh_project.should_receive("can_merge_pr").with_args("phracek").and_return(False)
    flexmock(Github, get_repo=lambda full_name_or_id: None)

    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Allowlist, check_and_report=True)
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=9,
        namespace="packit-service",
        repo_name="hello-world",
        project_url="https://github.com/packit-service/hello-world",
    ).and_return(db_project_object).times(5)
    pr_embedded_command_comment_event["comment"]["body"] = "/packit test"
    flexmock(
        GithubProject,
        get_files=lambda ref, recursive: ["foo.spec", ".packit.yaml"],
    )
    flexmock(GithubProject).should_receive("is_private").and_return(False).once()
    flexmock(Pushgateway).should_receive("push").times(1).and_return()
    flexmock(celery_group).should_receive("apply_async").once()
    flexmock(TestingFarmJobHelper).should_receive("run_testing_farm").times(0)
    flexmock(TestingFarmJobHelper).should_receive("report_status_to_tests").with_args(
        description="phracek can't run tests (and builds) internally",
        state=BaseCommitStatus.neutral,
        markdown_content="*As a project maintainer, "
        "you can trigger the build and test jobs manually via `/packit build`"
        " comment or only test job via `/packit test` comment.*",
    ).once()

    processing_results = SteveJobs().process_message(pr_embedded_command_comment_event)
    assert not processing_results


def test_pr_build_command_handler_not_allowed_external_contributor_on_internal_TF(
    add_pull_request_event_with_pr_id_9,
    pr_embedded_command_comment_event,
):
    db_project_object, _ = add_pull_request_event_with_pr_id_9
    jobs = [
        {
            "trigger": "pull_request",
            "job": "tests",
            "metadata": {"targets": "fedora-rawhide-x86_64", "use_internal_tf": True},
        },
        {
            "trigger": "pull_request",
            "job": "copr_build",
            "metadata": {"targets": "fedora-rawhide-x86_64"},
        },
    ]
    packit_yaml = "{'specfile_path': 'the-specfile.spec', 'jobs': " + str(jobs) + "}"
    pr = flexmock(head_commit="12345")
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    comment = flexmock()
    flexmock(pr).should_receive("get_comment").and_return(comment)
    flexmock(comment).should_receive("add_reaction").with_args(COMMENT_REACTION).once()
    gh_project = flexmock(
        GithubProject,
        full_repo_name="packit-service/hello-world",
        get_file_content=lambda path, ref: packit_yaml,
        get_files=lambda ref, filter_regex: ["the-specfile.spec"],
        get_web_url=lambda: "https://github.com/the-namespace/the-repo",
    )
    gh_project.should_receive("can_merge_pr").with_args("phracek").and_return(False)
    flexmock(Github, get_repo=lambda full_name_or_id: None)

    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Allowlist, check_and_report=True)
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=9,
        namespace="packit-service",
        repo_name="hello-world",
        project_url="https://github.com/packit-service/hello-world",
    ).and_return(db_project_object).times(8)
    pr_embedded_command_comment_event["comment"]["body"] = "/packit build"
    flexmock(
        GithubProject,
        get_files=lambda ref, recursive: ["foo.spec", ".packit.yaml"],
    )
    flexmock(GithubProject).should_receive("is_private").and_return(False).once()
    flexmock(celery_group).should_receive("apply_async").twice()
    flexmock(Pushgateway).should_receive("push").times(1).and_return()
    flexmock(TestingFarmJobHelper).should_receive("run_testing_farm").times(0)
    flexmock(CoprBuildJobHelper).should_receive("report_status_to_build").with_args(
        description="phracek can't run tests (and builds) internally",
        state=BaseCommitStatus.neutral,
        markdown_content="*As a project maintainer, "
        "you can trigger the build and test jobs manually via `/packit build` comment "
        "or only test job via `/packit test` comment.*",
    ).once()
    flexmock(TestingFarmJobHelper).should_receive("report_status_to_tests").with_args(
        description="phracek can't run tests (and builds) internally",
        state=BaseCommitStatus.neutral,
        markdown_content="*As a project maintainer, "
        "you can trigger the build and test jobs manually via `/packit build` comment "
        "or only test job via `/packit test` comment.*",
    ).once()
    flexmock(CoprBuildJobHelper).should_receive(
        "is_custom_copr_project_defined",
    ).and_return(False).once()

    processing_results = SteveJobs().process_message(pr_embedded_command_comment_event)
    assert not processing_results


@pytest.mark.parametrize(
    "comments",
    [
        "/packit build",
        "Should be fixed now, let's\n /packit build\n it.",
        "/packit test",
    ],
)
def test_trigger_packit_command_without_config(
    pr_embedded_command_comment_event,
    comments,
):
    flexmock(
        GithubProject,
        full_repo_name="namespace/repo",
        get_files=lambda ref, recursive: ["specfile.spec"],
        get_web_url=lambda: "https://github.com/namespace/repo",
    )

    pr_embedded_command_comment_event["comment"]["body"] = comments
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    pr = flexmock(head_commit="12345", get_comments=lambda *args, **kwargs: [])
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    err_msg = (
        "No config file for packit (e.g. `.packit.yaml`) found in namespace/repo on commit 12345"
        "\n\n"
        "For more info, please check out "
        f"[the documentation]({DOCS_HOW_TO_CONFIGURE_URL}) "
        "or [contact the Packit team]"
        f"({CONTACTS_URL}). You can also use "
        f"our CLI command [`config validate`]({DOCS_VALIDATE_CONFIG}) or our "
        f"[pre-commit hooks]({DOCS_VALIDATE_HOOKS}) for validation of the configuration."
    )
    flexmock(pr).should_receive("comment").with_args(body=err_msg)

    with pytest.raises(PackitConfigException) as exc:
        SteveJobs().process_message(pr_embedded_command_comment_event)
        assert "No config file found in " in exc.value


@pytest.mark.parametrize(
    "mock_pr_comment_functionality",
    (
        [
            [
                {
                    "trigger": "pull_request",
                    "job": "tests",
                    "metadata": {"targets": "fedora-rawhide-x86_64"},
                },
                {
                    "trigger": "pull_request",
                    "job": "copr_build",
                    "metadata": {"targets": "fedora-rawhide-x86_64"},
                },
            ],
        ]
    ),
    indirect=True,
)
def test_retest_failed(
    mock_pr_comment_functionality,
    pr_embedded_command_comment_event,
):
    pr = flexmock(head_commit="12345")
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    comment = flexmock()
    flexmock(pr).should_receive("get_comment").and_return(comment)
    flexmock(comment).should_receive("add_reaction").with_args(COMMENT_REACTION).once()

    db_project_object = flexmock(
        id=9,
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        project_event_model_type=ProjectEventModelType.pull_request,
    )
    db_project_event = (
        flexmock()
        .should_receive("get_project_event_object")
        .and_return(db_project_object)
        .mock()
        .should_receive("set_packages_config")
        .mock()
    )
    flexmock(ProjectEventModel).should_receive("get_or_create").with_args(
        type=ProjectEventModelType.pull_request,
        event_id=9,
        commit_sha="12345",
    ).and_return(db_project_event)
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=9,
        namespace="packit-service",
        repo_name="hello-world",
        project_url="https://github.com/packit-service/hello-world",
    ).and_return(db_project_object)
    run_model = flexmock()
    flexmock(PipelineModel).should_receive("create").and_return(run_model)
    flexmock(TFTTestRunGroupModel).should_receive("create").with_args(
        [run_model],
    ).and_return(flexmock())

    pr_embedded_command_comment_event["comment"]["body"] = "/packit retest-failed"
    flexmock(
        GithubProject,
        get_files=lambda ref, recursive: ["foo.spec", ".packit.yaml"],
    )
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(celery_group).should_receive("apply_async").once()
    flexmock(CoprHelper).should_receive("get_valid_build_targets").times(4).and_return(
        {"test-target"},
    )
    flexmock(TestingFarmJobHelper).should_receive("get_latest_copr_build").and_return(
        flexmock(status=BuildStatus.success),
    )

    build_model = flexmock(
        CoprBuildTargetModel,
        status=TestingFarmResult.failed,
        target="some_build_target",
    )
    model = flexmock(
        TFTTestRunTargetModel,
        status=TestingFarmResult.failed,
        target="some_tf_target",
    )
    flexmock(build_model).should_receive("get_all_by_commit_target").with_args(
        commit_sha="12345",
    ).and_return(build_model)
    flexmock(model).should_receive("get_all_by_commit_target").with_args(
        commit_sha="12345",
    ).and_return(model)
    flexmock(abstract.base.ForgeIndependent).should_receive(
        "get_all_build_targets_by_status",
    ).with_args(
        statuses_to_filter_with=[BuildStatus.failure],
    ).and_return(
        {("some_build_target", None)},
    )

    flexmock(abstract.base.ForgeIndependent).should_receive(
        "get_all_tf_targets_by_status",
    ).with_args(
        statuses_to_filter_with=[TestingFarmResult.failed, TestingFarmResult.error],
    ).and_return(
        {("some_tf_target", None)},
    )
    flexmock(packit_service.models).should_receive(
        "filter_most_recent_target_names_by_status",
    ).with_args(
        models=[model],
        statuses_to_filter_with=[TestingFarmResult.failed, TestingFarmResult.error],
    ).and_return(
        {("some_target", None)},
    )

    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    flexmock(TestingFarmJobHelper).should_receive("report_status_to_tests").with_args(
        description=TASK_ACCEPTED,
        state=BaseCommitStatus.pending,
        url="",
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    processing_results = SteveJobs().process_message(pr_embedded_command_comment_event)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert event_dict["tests_targets_override"] == [("some_tf_target", None)]
    assert json.dumps(event_dict)

    run_testing_farm_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )


def test_pr_test_command_handler_skip_build_option_no_fmf_metadata(
    add_pull_request_event_with_sha_0011223344,
    pr_embedded_command_comment_event,
):
    _ = add_pull_request_event_with_sha_0011223344
    jobs = [
        {
            "trigger": "pull_request",
            "job": "tests",
            "metadata": {"targets": "fedora-rawhide-x86_64", "skip_build": True},
        },
    ]
    packit_yaml = "{'specfile_path': 'the-specfile.spec', 'jobs': " + str(jobs) + "}"
    pr = flexmock(
        source_project=flexmock(
            get_web_url=lambda: "https://github.com/someone/hello-world",
        ),
        target_project=flexmock(
            get_web_url=lambda: "https://github.com/packit-service/hello-world",
        ),
        head_commit="0011223344",
        target_branch_head_commit="deadbeef",
        source_branch="the-source-branch",
        target_branch="the-target-branch",
    )
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    comment = flexmock()
    flexmock(pr).should_receive("get_comment").and_return(comment)
    flexmock(comment).should_receive("add_reaction").with_args(COMMENT_REACTION).once()
    flexmock(
        GithubProject,
        full_repo_name="packit-service/hello-world",
        get_file_content=lambda path, ref: packit_yaml,
        get_files=lambda ref, filter_regex: ["the-specfile.spec"],
        get_web_url=lambda: "https://github.com/packit-service/hello-world",
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)

    ServiceConfig.get_service_config().testing_farm_api_url = (
        "https://api.dev.testing-farm.io/v0.1/"
    )
    ServiceConfig.get_service_config().testing_farm_secret = "secret-token"

    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Allowlist, check_and_report=True)

    pr_embedded_command_comment_event["comment"]["body"] = "/packit test"
    flexmock(
        GithubProject,
        get_files=lambda ref, recursive: ["foo.spec", ".packit.yaml"],
    )
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(CoprHelper).should_receive("get_valid_build_targets").and_return(
        {"fedora-rawhide-x86_64"},
    )
    run_model = flexmock(test_run_group=None)
    test_run = flexmock(
        id=1,
        target="fedora-rawhide-x86_64",
        status=TestingFarmResult.new,
    )
    flexmock(PipelineModel).should_receive("create").and_return(run_model)
    group_model = flexmock(grouped_targets=[test_run])
    flexmock(TFTTestRunGroupModel).should_receive("create").with_args(
        [run_model],
        ranch="public",
    ).and_return(group_model)
    flexmock(TFTTestRunTargetModel).should_receive("create").and_return(test_run)
    flexmock(TestingFarmJobHelper).should_receive("get_latest_copr_build").never()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    flexmock(TestingFarmJobHelper).should_receive("report_status_to_tests").with_args(
        description=TASK_ACCEPTED,
        state=BaseCommitStatus.pending,
        url="",
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.running,
        description="Submitting the tests ...",
        check_names="testing-farm:fedora-rawhide-x86_64",
        url="",
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    flexmock(TestingFarmJobHelper).should_receive("is_fmf_configured").and_return(False)
    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.neutral,
        description="No FMF metadata found. Please, initialize the metadata tree with `fmf init`.",
        check_names="testing-farm:fedora-rawhide-x86_64",
        url="",
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    flexmock(GithubProject).should_receive("get_web_url").and_return(
        "https://github.com/packit-service/hello-world",
    )
    flexmock(celery_group).should_receive("apply_async").once()

    processing_results = SteveJobs().process_message(pr_embedded_command_comment_event)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    run_testing_farm_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )


@pytest.mark.parametrize(
    "mock_pr_comment_functionality",
    (
        [
            [
                {
                    "trigger": "pull_request",
                    "job": "copr_build",
                    "metadata": {"targets": "fedora-rawhide-x86_64"},
                },
            ],
        ]
    ),
    indirect=True,
)
def test_invalid_packit_command_with_config(
    mock_pr_comment_functionality,
    pr_embedded_command_comment_event,
):
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=9,
        namespace="packit-service",
        repo_name="hello-world",
        project_url="https://github.com/packit-service/hello-world",
    ).and_return(
        flexmock(id=9, job_config_trigger_type=JobConfigTriggerType.pull_request),
    )
    ServiceConfig.get_service_config().comment_command_prefix = "/packit"
    pr_embedded_command_comment_event["comment"]["body"] = "/packit i-hate-testing-with-flexmock"
    flexmock(
        GithubProject,
        get_files=lambda ref, recursive: ["foo.spec", ".packit.yaml"],
    )
    flexmock(Pushgateway).should_receive("push").times(1).and_return()
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    pr = flexmock(head_commit="12345")
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)

    processing_result = SteveJobs().process_message(pr_embedded_command_comment_event)[0]
    assert processing_result["success"]
    assert processing_result["details"]["msg"] == "No Packit command found in the comment."


def test_invalid_packit_command_without_config(
    pr_embedded_command_comment_event,
):
    flexmock(
        GithubProject,
        full_repo_name="packit-service/hello-world",
        # no config
        get_files=lambda ref, recursive: ["the-specfile.spec"],
        get_web_url=lambda: "https://github.com/the-namespace/the-repo",
    )

    ServiceConfig.get_service_config().comment_command_prefix = "/packit"
    pr_embedded_command_comment_event["comment"]["body"] = (
        "/packit 10minutesOfImplementing3HoursOfTesting"
    )
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    pr = flexmock(head_commit="12345")
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    flexmock(Pushgateway).should_receive("push").times(1).and_return()

    processing_result = SteveJobs().process_message(pr_embedded_command_comment_event)[0]
    assert processing_result["success"]
    assert processing_result["details"]["msg"] == "No Packit command found in the comment."


def test_pr_test_command_handler_multiple_builds(
    add_pull_request_event_with_sha_0011223344,
    pr_embedded_command_comment_event,
):
    _ = add_pull_request_event_with_sha_0011223344
    pr_embedded_command_comment_event["comment"]["body"] = "/packit test packit/packit-service#16"
    jobs = [
        {
            "trigger": "pull_request",
            "job": "tests",
            "metadata": {"targets": ["fedora-rawhide-x86_64", "fedora-35-x86_64"]},
        },
        {
            "trigger": "pull_request",
            "job": "copr_build",
            "metadata": {"targets": ["fedora-rawhide-x86_64", "fedora-35-x86_64"]},
        },
    ]
    packit_yaml = "{'specfile_path': 'the-specfile.spec', 'jobs': " + str(jobs) + "}"
    pr = flexmock(
        source_project=flexmock(
            get_web_url=lambda: "https://github.com/someone/hello-world",
        ),
        target_project=flexmock(
            get_web_url=lambda: "https://github.com/packit-service/hello-world",
        ),
        head_commit="0011223344",
        target_branch_head_commit="deadbeef",
        source_branch="the-source-branch",
        target_branch="the-target-branch",
    )
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    comment = flexmock()
    flexmock(pr).should_receive("get_comment").and_return(comment)
    flexmock(comment).should_receive("add_reaction").with_args(COMMENT_REACTION).once()
    flexmock(
        GithubProject,
        full_repo_name="packit-service/hello-world",
        get_file_content=lambda path, ref: packit_yaml,
        get_files=lambda ref, filter_regex: ["the-specfile.spec"],
        get_web_url=lambda: "https://github.com/packit-service/hello-world",
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)

    ServiceConfig.get_service_config().testing_farm_api_url = (
        "https://api.dev.testing-farm.io/v0.1/"
    )
    ServiceConfig.get_service_config().testing_farm_secret = "secret-token"

    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Allowlist, check_and_report=True)
    flexmock(
        GithubProject,
        get_files=lambda ref, recursive: ["foo.spec", ".packit.yaml"],
    )
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(CoprHelper).should_receive("get_valid_build_targets").and_return(
        {"fedora-rawhide-x86_64", "fedora-35-x86_64"},
    )

    run_model = flexmock(test_run_group=None)

    build = flexmock(
        build_id="123456",
        built_packages=[
            {
                "name": "repo",
                "version": "0.1",
                "release": "1",
                "arch": "noarch",
                "epoch": "0",
            },
        ],
        build_logs_url=None,
        owner="mf",
        project_name="tree",
        status=BuildStatus.success,
        group_of_targets=flexmock(runs=[run_model]),
    )
    build.should_receive("get_srpm_build").and_return(flexmock(url=None))

    flexmock(TestingFarmJobHelper).should_receive("get_latest_copr_build").and_return(
        build,
    )
    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    flexmock(TestingFarmJobHelper).should_receive("report_status_to_tests").with_args(
        description=TASK_ACCEPTED,
        state=BaseCommitStatus.pending,
        url="",
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    payload = {
        "test": {
            "tmt": {
                "url": "https://github.com/someone/hello-world",
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
                "variables": {
                    "PACKIT_FULL_REPO_NAME": "packit-service/hello-world",
                    "PACKIT_UPSTREAM_NAME": "hello-world",
                    "PACKIT_DOWNSTREAM_NAME": "hello-world",
                    "PACKIT_DOWNSTREAM_URL": "https://src.fedoraproject.org/rpms/hello-world.git",
                    "PACKIT_PACKAGE_NAME": "hello-world",
                    "PACKIT_PACKAGE_NVR": "repo-0.1-1",
                    "PACKIT_COMMIT_SHA": "0011223344",
                    "PACKIT_SOURCE_SHA": "0011223344",
                    "PACKIT_TARGET_SHA": "deadbeef",
                    "PACKIT_SOURCE_BRANCH": "the-source-branch",
                    "PACKIT_TARGET_BRANCH": "the-target-branch",
                    "PACKIT_SOURCE_URL": "https://github.com/someone/hello-world",
                    "PACKIT_TARGET_URL": "https://github.com/packit-service/hello-world",
                    "PACKIT_PR_ID": 9,
                    "PACKIT_COPR_PROJECT": "mf/tree another-owner/another-repo",
                    "PACKIT_COPR_RPMS": "repo-0:0.1-1.noarch another-repo-0:0.1-1.noarch",
                },
                "artifacts": [
                    {
                        "id": "123456:fedora-rawhide-x86_64",
                        "type": "fedora-copr-build",
                        "packages": ["repo-0:0.1-1.noarch"],
                    },
                    {
                        "id": "54321:fedora-rawhide-x86_64",
                        "type": "fedora-copr-build",
                        "packages": ["another-repo-0:0.1-1.noarch"],
                    },
                ],
            },
        ],
        "notification": {
            "webhook": {
                "url": "https://prod.packit.dev/api/testing-farm/results",
                "token": "secret-token",
            },
        },
    }

    flexmock(TestingFarmJobHelper).should_receive("is_fmf_configured").and_return(True)
    flexmock(TestingFarmClient).should_receive("distro2compose").and_return(
        "Fedora-Rawhide",
    )

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
        check_names="testing-farm:fedora-rawhide-x86_64",
        url="",
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    flexmock(StatusReporter).should_receive("report").with_args(
        description="No latest successful Copr build from the other PR found.",
        state=BaseCommitStatus.failure,
        url="",
        check_names="testing-farm:fedora-35-x86_64",
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()

    flexmock(GithubProject).should_receive("get_web_url").and_return(
        "https://github.com/packit-service/hello-world",
    )

    tft_test_run_model_rawhide = flexmock(
        id=5,
        copr_builds=[build],
        status=TestingFarmResult.new,
        target="fedora-rawhide-x86_64",
    )
    tft_test_run_model_35 = flexmock(
        id=6,
        copr_builds=[build],
        status=TestingFarmResult.new,
        target="fedora-35-x86_64",
    )

    run_model2 = flexmock(PipelineModel)
    additional_copr_build = flexmock(
        target="fedora-rawhide-x86_64",
        build_id="54321",
        built_packages=[
            {
                "name": "another-repo",
                "version": "0.1",
                "release": "1",
                "arch": "noarch",
                "epoch": "0",
            },
        ],
        owner="another-owner",
        project_name="another-repo",
        runs=[run_model2],
    )

    flexmock(PullRequestModel).should_receive("get").with_args(
        pr_id=16,
        namespace="packit",
        repo_name="packit-service",
        project_url="https://github.com/packit/packit-service",
    ).and_return(
        flexmock(id=16, job_config_trigger_type=JobConfigTriggerType.pull_request)
        .should_receive("get_copr_builds")
        .and_return([additional_copr_build])
        .mock(),
    )

    flexmock(packit_service.worker.helpers.testing_farm).should_receive(
        "filter_most_recent_target_models_by_status",
    ).with_args(
        models=[additional_copr_build],
        statuses_to_filter_with=[BuildStatus.success],
    ).and_return(
        {additional_copr_build},
    ).times(
        2,
    )

    group = flexmock(
        id=1,
        runs=[run_model],
        grouped_targets=[tft_test_run_model_rawhide, tft_test_run_model_35],
    )
    flexmock(TFTTestRunGroupModel).should_receive("create").and_return(group)
    for t, model in (
        ("fedora-35-x86_64", tft_test_run_model_35),
        ("fedora-rawhide-x86_64", tft_test_run_model_rawhide),
    ):
        flexmock(TFTTestRunTargetModel).should_receive("create").with_args(
            pipeline_id=None,
            identifier=None,
            status=TestingFarmResult.new,
            target=t,
            web_url=None,
            test_run_group=group,
            copr_build_targets=[build],
            data={"base_project_url": "https://github.com/packit-service/hello-world"},
        ).and_return(model)
    flexmock(tft_test_run_model_rawhide).should_receive("add_copr_build").with_args(
        additional_copr_build,
    )
    flexmock(tft_test_run_model_rawhide).should_receive("set_pipeline_id").with_args(
        pipeline_id,
    )

    urls.DASHBOARD_URL = "https://dashboard.localhost"
    flexmock(StatusReporter).should_receive("report").with_args(
        description="Tests have been submitted ...",
        state=BaseCommitStatus.running,
        url="https://dashboard.localhost/jobs/testing-farm/5",
        check_names="testing-farm:fedora-rawhide-x86_64",
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()
    flexmock(celery_group).should_receive("apply_async").once()

    processing_results = SteveJobs().process_message(pr_embedded_command_comment_event)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    run_testing_farm_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )


def test_koji_build_retrigger_via_dist_git_pr_comment(pagure_pr_comment_added):
    packit_yaml = (
        "{'specfile_path': 'python-teamcity-messages.spec',"
        "'jobs': [{'trigger': 'commit', 'job': 'koji_build'}],"
        "'downstream_package_name': 'python-ogr', 'issue_repository': "
        "'https://github.com/namespace/repo'}"
    )
    pagure_project = flexmock(
        PagureProject,
        full_repo_name="rpms/packit",
        get_web_url=lambda: "https://src.fedoraproject.org/rpms/python-teamcity-messages",
        default_branch="main",
    )
    pagure_project.should_receive("get_files").with_args(
        ref="main",
        filter_regex=r".+\.spec$",
    ).and_return(["python-teamcity-messages.spec"])
    pagure_project.should_receive("get_file_content").with_args(
        path=".packit.yaml",
        ref="main",
    ).and_return(packit_yaml)
    pagure_project.should_receive("get_files").with_args(
        ref="main",
        recursive=False,
    ).and_return(["python-teamcity-messages.spec", ".packit.yaml"])

    pagure_pr_comment_added["pullrequest"]["comments"][0]["comment"] = "/packit koji-build"

    project_event = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        project=flexmock(project_url=None),
        id=123,
    )
    flexmock(AddPullRequestEventToDb).should_receive("db_project_object").and_return(
        project_event,
    )
    flexmock(PullRequestModel).should_receive("get_by_id").with_args(123).and_return(
        project_event,
    )
    flexmock(PipelineModel).should_receive("create")

    nvr = "package-1.2.3-1.fc40"

    koji_build = flexmock(
        target="the_distgit_branch",
        status="queued",
        sidetag=None,
        nvr=nvr,
        set_status=lambda x: None,
        set_task_id=lambda x: None,
        set_web_url=lambda x: None,
        set_build_logs_urls=lambda x: None,
        set_data=lambda x: None,
    )

    flexmock(DistGit).should_receive("get_nvr").and_return(nvr)

    flexmock(KojiBuildTargetModel).should_receive("create").and_return(koji_build)
    flexmock(KojiBuildTargetModel).should_receive(
        "get_all_successful_or_in_progress_by_nvr",
    ).and_return({koji_build})
    flexmock(KojiBuildGroupModel).should_receive("create").and_return(
        flexmock(grouped_targets=[koji_build]),
    )

    db_project_object = flexmock(
        id=12,
        project_event_model_type=ProjectEventModelType.pull_request,
        job_config_trigger_type=JobConfigTriggerType.pull_request,
    )
    db_project_event = (
        flexmock().should_receive("get_project_event_object").and_return(db_project_object).mock()
    )
    flexmock(ProjectEventModel).should_receive("get_or_create").with_args(
        type=ProjectEventModelType.pull_request,
        event_id=12,
        commit_sha="beaf90bcecc51968a46663f8d6f092bfdc92e682",
    ).and_return(db_project_event)
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=pagure_pr_comment_added["pullrequest"]["id"],
        namespace=pagure_pr_comment_added["pullrequest"]["project"]["namespace"],
        repo_name=pagure_pr_comment_added["pullrequest"]["project"]["name"],
        project_url=pagure_pr_comment_added["pullrequest"]["project"]["full_url"],
    ).and_return(db_project_object)

    pr_mock = (
        flexmock(target_branch="the_distgit_branch")
        .should_receive("comment")
        .with_args(
            "The task was accepted. You can check the recent runs of downstream Koji jobs "
            "in [Packit dashboard](/jobs/downstream-koji-builds). "
            "You can also check the recent Koji build activity of "
            "`packit` in [the Koji interface]"
            "(https://koji.fedoraproject.org/koji/userinfo?userID=4641)."
            f"{DistgitAnnouncement.get_comment_footer_with_announcement_if_present()}",
        )
        .mock()
    )
    flexmock(
        PagureProject,
        full_repo_name="rpms/jouduv-dort",
        get_web_url=lambda: "https://src.fedoraproject.org/rpms/jouduv-dort",
        default_branch="main",
        get_pr=lambda id: pr_mock,
    )

    flexmock(DownstreamKojiBuildHandler).should_receive("pre_check").and_return(True)
    flexmock(LocalProjectBuilder, _refresh_the_state=lambda *args: flexmock())
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(celery_group).should_receive("apply_async").once()
    flexmock(PackitAPI).should_receive("build").with_args(
        dist_git_branch="the_distgit_branch",
        scratch=False,
        nowait=True,
        from_upstream=False,
        koji_target=None,
    ).and_return("")

    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    processing_results = SteveJobs().process_message(pagure_pr_comment_added)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)
    results = run_downstream_koji_build(
        event=event_dict,
        package_config=package_config,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]


@pytest.mark.parametrize(
    "target_branch, uid, check_name",
    [
        pytest.param(
            "rawhide",
            "e0091d5fbcb20572cbf2e6442af9bed5",
            "Packit - scratch build - rawhide",
            id="rawhide target branch",
        ),
        pytest.param(
            "f42",
            "6f08c3bbb20660dc8c597bc7dbe4f056",
            "Packit - scratch build - f42",
            id="f42 target branch",
        ),
    ],
)
def test_downstream_koji_scratch_build_retrigger_via_dist_git_pr_comment(
    pagure_pr_comment_added, target_branch, uid, check_name
):
    pagure_pr_comment_added["pullrequest"]["comments"][0]["comment"] = "/packit-ci scratch-build"
    pagure_pr_comment_added["pullrequest"]["branch"] = target_branch
    pr_object = (
        flexmock(target_branch=target_branch)
        .should_receive("set_flag")
        .with_args(
            username=check_name,
            comment="The task was accepted.",
            url=str,
            status=CommitStatus,
            uid=uid,
        )
        .once()
        .mock()
        .should_receive("set_flag")
        .with_args(
            username=check_name,
            comment="RPM build was submitted ...",
            url=str,
            status=CommitStatus,
            uid=uid,
        )
        .once()
        .mock()
    )
    dg_project = (
        flexmock(
            PagureProject(
                namespace="rpms", repo="python-teamcity-messages", service=flexmock(read_only=False)
            ),
            default_branch="main",
        )
        .should_receive("is_private")
        .and_return(False)
        .mock()
        .should_receive("get_pr")
        .and_return(pr_object)
        .mock()
        .should_receive("get_files")
        .and_return([])
        .mock()
    )
    service_config = (
        flexmock(
            enabled_projects_for_fedora_ci="https://src.fedoraproject.org/rpms/python-teamcity-messages",
            command_handler_work_dir=SANDCASTLE_WORK_DIR,
            repository_cache="/tmp/repository-cache",
            add_repositories_to_repository_cache=False,
            deployment=Deployment.stg,
            comment_command_prefix="/packit",
            package_config_path_override=None,
        )
        .should_receive("get_project")
        .and_return(dg_project)
        .mock()
    )
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(service_config)
    flexmock(PackitAPIWithDownstreamMixin).should_receive("is_packager").and_return(
        True,
    )
    db_project_object = flexmock(
        id=9,
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        project_event_model_type=ProjectEventModelType.pull_request,
        project=flexmock(project_url="https://src.fedoraproject.org/rpms/python-teamcity-messages"),
    )
    db_project_event = (
        flexmock().should_receive("get_project_event_object").and_return(db_project_object).mock()
    )
    flexmock(ProjectEventModel).should_receive("get_or_create").with_args(
        type=ProjectEventModelType.pull_request,
        event_id=9,
        commit_sha="abcd",
    ).and_return(flexmock())
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=36,
        namespace="rpms",
        repo_name="python-teamcity-messages",
        project_url="https://src.fedoraproject.org/rpms/python-teamcity-messages",
    ).and_return(db_project_object)
    flexmock(ProjectEventModel).should_receive("get_or_create").and_return(
        db_project_event,
    )
    flexmock(PipelineModel).should_receive("create")

    koji_build = flexmock(
        id=123,
        target="main",
        status="queued",
        set_status=lambda x: None,
        set_task_id=lambda x: None,
        set_web_url=lambda x: None,
        set_build_logs_urls=lambda x: None,
        set_data=lambda x: None,
        set_build_submission_stdout=lambda x: None,
    )

    flexmock(KojiBuildTargetModel).should_receive("create").and_return(koji_build)
    flexmock(KojiBuildGroupModel).should_receive("create").and_return(
        flexmock(grouped_targets=[koji_build]),
    )

    flexmock(LocalProjectBuilder, _refresh_the_state=lambda *args: None)
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    flexmock(commands).should_receive("run_command_remote").with_args(
        cmd=[
            "koji",
            "build",
            "--scratch",
            "--nowait",
            target_branch,
            "git+https://src.fedoraproject.org/rpms/python-teamcity-messages.git#beaf90bcecc51968a46663f8d6f092bfdc92e682",
        ],
        cwd=Path,
        output=True,
        print_live=True,
    ).and_return(flexmock(stdout="some output"))
    flexmock(PackitAPI).should_receive("init_kerberos_ticket")

    flexmock(distgit).should_receive("get_koji_task_id_and_url_from_stdout").and_return(
        (123, "koji-web-url")
    ).once()

    processing_results = SteveJobs().process_message(pagure_pr_comment_added)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)
    results = run_downstream_koji_scratch_build_handler(
        event=event_dict,
        package_config=package_config,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]


def test_bodhi_update_retrigger_via_dist_git_pr_comment(pagure_pr_comment_added):
    pagure_pr_comment_added["pullrequest"]["comments"][0]["comment"] = "/packit create-update"
    project = pagure_pr_comment_added["pullrequest"]["project"]
    project["full_url"] = "https://src.fedoraproject.org/rpms/jouduv-dort"
    project["fullname"] = "rpms/jouduv-dort"
    project["name"] = "jouduv-dort"
    project["url_path"] = "rpms/jouduv-dort"

    packit_yaml = (
        "{'specfile_path': 'jouduv-dort.spec',"
        "'jobs': [{'trigger': 'commit', 'job': 'bodhi_update'}],"
        "'downstream_package_name': 'jouduv-dort'}"
    )

    project_event = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        project=flexmock(project_url=None),
        id=123,
    )
    flexmock(AddPullRequestEventToDb).should_receive("db_project_object").and_return(
        project_event,
    )
    flexmock(PullRequestModel).should_receive("get_by_id").with_args(123).and_return(
        project_event,
    )
    run_model_flexmock = flexmock()
    db_project_object = flexmock(
        id=12,
        project_event_model_type=ProjectEventModelType.pull_request,
        job_config_trigger_type=JobConfigTriggerType.pull_request,
    )
    flexmock(KojiBuildTargetModel).should_receive("get_by_task_id").with_args(
        79721403,
    ).and_return(None)
    flexmock(ProjectEventModel).should_receive("get_or_create").with_args(
        type=ProjectEventModelType.pull_request,
        event_id=12,
        commit_sha="beaf90bcecc51968a46663f8d6f092bfdc92e682",
    ).and_return(project_event)
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=36,
        namespace="rpms",
        repo_name="jouduv-dort",
        project_url="https://src.fedoraproject.org/rpms/jouduv-dort",
    ).and_return(db_project_object)
    flexmock(PipelineModel).should_receive("create").and_return(run_model_flexmock)
    group_model = flexmock(
        id=23,
        grouped_targets=[
            flexmock(
                target="the_distgit_branch",
                koji_nvrs="123",
                sidetag=None,
                set_status=lambda x: None,
                set_data=lambda x: None,
                set_web_url=lambda x: None,
                set_alias=lambda x: None,
                set_update_creation_time=lambda x: None,
            ),
        ],
    )
    flexmock(BodhiUpdateGroupModel).should_receive("create").and_return(group_model)
    flexmock(BodhiUpdateTargetModel).should_receive(
        "get_all_successful_or_in_progress_by_nvrs",
    ).with_args("123").and_return(set())
    flexmock(BodhiUpdateTargetModel).should_receive("create").with_args(
        target="the_distgit_branch",
        koji_nvrs="123",
        sidetag=None,
        status="queued",
        bodhi_update_group=group_model,
    ).and_return()

    pr_mock = (
        flexmock(target_branch="the_distgit_branch")
        .should_receive("comment")
        .with_args(
            "The task was accepted. You can check the recent Bodhi update submissions of Packit "
            "in [Packit dashboard](/jobs/bodhi-updates). "
            "You can also check the recent Bodhi update activity of `packit` in "
            "[the Bodhi interface](https://bodhi.fedoraproject.org/users/packit)."
            f"{DistgitAnnouncement.get_comment_footer_with_announcement_if_present()}",
        )
        .mock()
    )

    pagure_project = flexmock(
        PagureProject,
        full_repo_name="rpms/jouduv-dort",
        get_web_url=lambda: "https://src.fedoraproject.org/rpms/jouduv-dort",
        default_branch="main",
        get_pr=lambda id: pr_mock,
    )

    flexmock(KojiHelper).should_receive("get_latest_candidate_build").and_return(
        {"nvr": "123", "build_id": 321, "state": 0, "task_id": 123},
    )

    pagure_project.should_receive("get_files").with_args(
        ref="main",
        filter_regex=r".+\.spec$",
    ).and_return(["jouduv-dort.spec"])
    pagure_project.should_receive("get_file_content").with_args(
        path=".packit.yaml",
        ref="main",
    ).and_return(packit_yaml)
    pagure_project.should_receive("get_files").with_args(
        ref="main",
        recursive=False,
    ).and_return(["jouduv-dort.spec", ".packit.yaml"])

    flexmock(RetriggerBodhiUpdateHandler).should_receive("pre_check").and_return(True)

    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(celery_group).should_receive("apply_async").once()
    flexmock(PackitAPI).should_receive("create_update").with_args(
        dist_git_branch="the_distgit_branch",
        update_type="enhancement",
        koji_builds=["123"],
        sidetag=None,
        alias=None,
    ).once().and_return(("alias", "url"))

    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    processing_results = SteveJobs().process_message(pagure_pr_comment_added)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)
    results = run_retrigger_bodhi_update(
        event=event_dict,
        package_config=package_config,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]


def test_pull_from_upstream_retrigger_via_dist_git_pr_comment(pagure_pr_comment_added):
    pagure_pr_comment_added["pullrequest"]["comments"][0]["comment"] = (
        "/packit pull-from-upstream --with-pr-config --resolve-bug rhbz#123,rhbz#124"
    )
    sync_release_pr_model = flexmock(sync_release_targets=[flexmock(), flexmock()])
    model = flexmock(status="queued", id=1234, branch="main")
    flexmock(SyncReleaseTargetModel).should_receive("create").with_args(
        status=SyncReleaseTargetStatus.queued,
        branch="main",
    ).and_return(model)
    flexmock(SyncReleasePullRequestModel).should_receive("get_or_create").with_args(
        pr_id=21,
        namespace="downstream-namespace",
        repo_name="downstream-repo",
        project_url="https://src.fedoraproject.org/rpms/downstream-repo",
        target_branch=str,
        url=str,
    ).and_return(sync_release_pr_model)

    packit_yaml = (
        "{'specfile_path': 'hello-world.spec', 'upstream_project_url': "
        "'https://github.com/packit-service/hello-world'"
        ", jobs: [{trigger: release, job: pull_from_upstream, metadata: {targets:[]}}]}"
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)
    pr_mock = (
        flexmock()
        .should_receive("comment")
        .with_args(
            "The task was accepted. You can check the recent runs of pull from upstream jobs in "
            "[Packit dashboard](/jobs/pull-from-upstreams)"
            f"{DistgitAnnouncement.get_comment_footer_with_announcement_if_present()}",
        )
        .mock()
    )
    distgit_project = flexmock(
        get_files=lambda ref, recursive: [".packit.yaml"],
        get_file_content=lambda path, ref: packit_yaml,
        full_repo_name=pagure_pr_comment_added["pullrequest"]["project"]["fullname"],
        repo=pagure_pr_comment_added["pullrequest"]["project"]["name"],
        namespace=pagure_pr_comment_added["pullrequest"]["project"]["namespace"],
        is_private=lambda: False,
        default_branch="main",
        service=flexmock(get_project=lambda **_: None),
        get_pr=lambda pr_id: pr_mock,
    )
    project = flexmock(
        full_repo_name="packit-service/hello-world",
        repo="hello-world",
        namespace="packit-service",
        get_files=lambda ref, filter_regex: [],
        get_sha_from_tag=lambda tag_name: "123456",
        get_web_url=lambda: "https://github.com/packit/hello-world",
        is_private=lambda: False,
        default_branch="main",
    )
    lp = flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(LocalProjectBuilder, _refresh_the_state=lambda *args: lp)
    lp.working_dir = ""
    lp.git_project = project
    flexmock(DistGit).should_receive("local_project").and_return(lp)
    # reset of the upstream repo
    flexmock(LocalProject).should_receive("git_repo").and_return(
        flexmock(
            head=flexmock()
            .should_receive("reset")
            .with_args("HEAD", index=True, working_tree=True)
            .once()
            .mock(),
            git=flexmock(clear_cache=lambda: None),
            submodules=[
                flexmock()
                .should_receive("update")
                .with_args(init=True, recursive=True, force=True)
                .once()
                .mock()
            ],
        ),
    )

    flexmock(GithubService).should_receive("set_auth_method").with_args(
        AuthMethod.token,
    ).once()

    flexmock(GitUpstream).should_receive("get_last_tag").and_return("7.0.3")

    flexmock(Allowlist, check_and_report=True)
    flexmock(PackitAPIWithDownstreamMixin).should_receive("is_packager").and_return(
        True,
    )

    def _get_project(url, *_, **__):
        if url == pagure_pr_comment_added["pullrequest"]["project"]["full_url"]:
            return distgit_project
        return project

    service_config = ServiceConfig().get_service_config()
    flexmock(service_config).should_receive("get_project").replace_with(_get_project)
    target_project = (
        flexmock(namespace="downstream-namespace", repo="downstream-repo")
        .should_receive("get_web_url")
        .and_return("https://src.fedoraproject.org/rpms/downstream-repo")
        .mock()
    )
    pr = (
        flexmock(id=21, url="some_url", target_project=target_project, description="")
        .should_receive("comment")
        .mock()
    )
    flexmock(PackitAPI).should_receive("sync_release").with_args(
        dist_git_branch="main",
        tag="7.0.3",
        create_pr=True,
        local_pr_branch_suffix="update-pull_from_upstream",
        use_downstream_specfile=True,
        add_pr_instructions=True,
        resolved_bugs=["rhbz#123", "rhbz#124"],
        release_monitoring_project_id=None,
        sync_acls=True,
        pr_description_footer=DistgitAnnouncement.get_announcement(),
        add_new_sources=True,
        fast_forward_merge_branches=set(),
        warn_about_koji_build_triggering_bug=False,
    ).and_return((pr, {})).once()
    flexmock(PackitAPI).should_receive("clean")

    flexmock(model).should_receive("set_status").with_args(
        status=SyncReleaseTargetStatus.running,
    ).once()
    flexmock(model).should_receive("set_downstream_pr_url").with_args(
        downstream_pr_url="some_url",
    ).once()
    flexmock(model).should_receive("set_downstream_prs").with_args(
        downstream_prs=[sync_release_pr_model],
    ).once()
    flexmock(model).should_receive("set_status").with_args(
        status=SyncReleaseTargetStatus.submitted,
    ).once()
    flexmock(model).should_receive("set_start_time").once()
    flexmock(model).should_receive("set_finished_time").once()
    flexmock(model).should_receive("set_logs").once()

    db_project_object = flexmock(
        id=12,
        project_event_model_type=ProjectEventModelType.pull_request,
        job_config_trigger_type=JobConfigTriggerType.pull_request,
    )
    db_project_event = (
        flexmock().should_receive("get_project_event_object").and_return(db_project_object).mock()
    )
    run_model = flexmock(PipelineModel)
    flexmock(ProjectEventModel).should_receive("get_or_create").with_args(
        type=ProjectEventModelType.pull_request,
        event_id=12,
        commit_sha="beaf90bcecc51968a46663f8d6f092bfdc92e682",
    ).and_return(db_project_event)
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=pagure_pr_comment_added["pullrequest"]["id"],
        namespace=pagure_pr_comment_added["pullrequest"]["project"]["namespace"],
        repo_name=pagure_pr_comment_added["pullrequest"]["project"]["name"],
        project_url=pagure_pr_comment_added["pullrequest"]["project"]["full_url"],
    ).and_return(db_project_object)
    sync_release_model = flexmock(id=123, sync_release_targets=[])
    flexmock(SyncReleaseModel).should_receive("create_with_new_run").with_args(
        status=SyncReleaseStatus.running,
        project_event_model=db_project_event,
        job_type=SyncReleaseJobType.pull_from_upstream,
        package_name="python-teamcity-messages",
    ).and_return(sync_release_model, run_model).once()
    flexmock(sync_release_model).should_receive("set_status").with_args(
        status=SyncReleaseStatus.finished,
    ).once()

    flexmock(IsRunConditionSatisfied).should_receive("pre_check").and_return(True)

    flexmock(AddPullRequestEventToDb).should_receive("db_project_object").and_return(
        flexmock(
            job_config_trigger_type=JobConfigTriggerType.pull_request,
            id=123,
            project=flexmock(project_url=None),
            project_event_model_type=ProjectEventModelType.pull_request,
        ),
    )
    flexmock(celery_group).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    flexmock(shutil).should_receive("rmtree").with_args("")
    flexmock(pagure.pr.Comment).should_receive(
        "get_base_project",
    ).once().and_return(distgit_project)

    processing_results = SteveJobs().process_message(pagure_pr_comment_added)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    results = run_pull_from_upstream_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert first_dict_value(results["job"])["success"]


def test_pull_from_upstream_retrigger_via_dist_git_pr_comment_non_git(
    pagure_pr_comment_added,
):
    pagure_pr_comment_added["pullrequest"]["comments"][0]["comment"] = (
        "/packit pull-from-upstream --with-pr-config --resolve-bug rhbz#123,rhbz#124"
    )
    sync_release_pr_model = flexmock(sync_release_targets=[flexmock(), flexmock()])
    model = flexmock(status="queued", id=1234, branch="main")
    flexmock(SyncReleaseTargetModel).should_receive("create").with_args(
        status=SyncReleaseTargetStatus.queued,
        branch="main",
    ).and_return(model)
    flexmock(SyncReleasePullRequestModel).should_receive("get_or_create").with_args(
        pr_id=21,
        namespace="downstream-namespace",
        repo_name="downstream-repo",
        project_url="https://src.fedoraproject.org/rpms/downstream-repo",
        target_branch=str,
        url=str,
    ).and_return(sync_release_pr_model)

    packit_yaml = (
        "{'specfile_path': 'hello-world.spec', "
        "jobs: [{trigger: release, job: pull_from_upstream, metadata: {targets:[]}}]}"
    )
    pr_mock = (
        flexmock()
        .should_receive("comment")
        .with_args(
            "The task was accepted. You can check the recent runs of pull from upstream jobs in "
            "[Packit dashboard](/jobs/pull-from-upstreams)"
            f"{DistgitAnnouncement.get_comment_footer_with_announcement_if_present()}",
        )
        .mock()
    )
    distgit_project = flexmock(
        get_files=lambda ref, recursive: [".packit.yaml"],
        get_file_content=lambda path, ref: packit_yaml,
        full_repo_name=pagure_pr_comment_added["pullrequest"]["project"]["fullname"],
        repo=pagure_pr_comment_added["pullrequest"]["project"]["name"],
        namespace=pagure_pr_comment_added["pullrequest"]["project"]["namespace"],
        is_private=lambda: False,
        default_branch="main",
        service=flexmock(get_project=lambda **_: None),
        get_pr=lambda pr_id: pr_mock,
    )
    lp = flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(LocalProjectBuilder, _refresh_the_state=lambda *args: lp)
    lp.working_dir = ""
    flexmock(DistGit).should_receive("local_project").and_return(lp)

    flexmock(GithubService).should_receive("set_auth_method").with_args(
        AuthMethod.token,
    ).once()
    flexmock(Allowlist, check_and_report=True)
    flexmock(PackitAPIWithDownstreamMixin).should_receive("is_packager").and_return(
        True,
    )

    def _get_project(url, *_, **__):
        if url == pagure_pr_comment_added["pullrequest"]["project"]["full_url"]:
            return distgit_project
        return None

    service_config = ServiceConfig().get_service_config()
    flexmock(service_config).should_receive("get_project").replace_with(_get_project)
    target_project = (
        flexmock(namespace="downstream-namespace", repo="downstream-repo")
        .should_receive("get_web_url")
        .and_return("https://src.fedoraproject.org/rpms/downstream-repo")
        .mock()
    )
    pr = (
        flexmock(id=21, url="some_url", target_project=target_project, description="")
        .should_receive("comment")
        .mock()
    )
    flexmock(PackitAPI).should_receive("sync_release").with_args(
        dist_git_branch="main",
        create_pr=True,
        local_pr_branch_suffix="update-pull_from_upstream",
        use_downstream_specfile=True,
        add_pr_instructions=True,
        resolved_bugs=["rhbz#123", "rhbz#124"],
        release_monitoring_project_id=None,
        sync_acls=True,
        pr_description_footer=DistgitAnnouncement.get_announcement(),
        add_new_sources=True,
        fast_forward_merge_branches=set(),
        warn_about_koji_build_triggering_bug=False,
    ).and_return((pr, {})).once()
    flexmock(PackitAPI).should_receive("clean")

    flexmock(model).should_receive("set_status").with_args(
        status=SyncReleaseTargetStatus.running,
    ).once()
    flexmock(model).should_receive("set_downstream_pr_url").with_args(
        downstream_pr_url="some_url",
    ).once()
    flexmock(model).should_receive("set_downstream_prs").with_args(
        downstream_prs=[sync_release_pr_model],
    ).once()
    flexmock(model).should_receive("set_status").with_args(
        status=SyncReleaseTargetStatus.submitted,
    ).once()
    flexmock(model).should_receive("set_start_time").once()
    flexmock(model).should_receive("set_finished_time").once()
    flexmock(model).should_receive("set_logs").once()

    db_project_object = flexmock(
        id=12,
        project_event_model_type=ProjectEventModelType.pull_request,
        job_config_trigger_type=JobConfigTriggerType.pull_request,
    )
    db_project_event = (
        flexmock().should_receive("get_project_event_object").and_return(db_project_object).mock()
    )
    run_model = flexmock(PipelineModel)
    flexmock(ProjectEventModel).should_receive("get_or_create").with_args(
        type=ProjectEventModelType.pull_request,
        event_id=12,
        commit_sha="beaf90bcecc51968a46663f8d6f092bfdc92e682",
    ).and_return(db_project_event)
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=pagure_pr_comment_added["pullrequest"]["id"],
        namespace=pagure_pr_comment_added["pullrequest"]["project"]["namespace"],
        repo_name=pagure_pr_comment_added["pullrequest"]["project"]["name"],
        project_url=pagure_pr_comment_added["pullrequest"]["project"]["full_url"],
    ).and_return(db_project_object)
    sync_release_model = flexmock(id=123, sync_release_targets=[])
    flexmock(SyncReleaseModel).should_receive("create_with_new_run").with_args(
        status=SyncReleaseStatus.running,
        project_event_model=db_project_event,
        job_type=SyncReleaseJobType.pull_from_upstream,
        package_name="python-teamcity-messages",
    ).and_return(sync_release_model, run_model).once()
    flexmock(sync_release_model).should_receive("set_status").with_args(
        status=SyncReleaseStatus.finished,
    ).once()

    flexmock(IsRunConditionSatisfied).should_receive("pre_check").and_return(True)

    flexmock(AddPullRequestEventToDb).should_receive("db_project_object").and_return(
        flexmock(
            job_config_trigger_type=JobConfigTriggerType.pull_request,
            id=123,
            project_event_model_type=ProjectEventModelType.pull_request,
            project=flexmock(project_url=None),
        ),
    )
    flexmock(celery_group).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    flexmock(shutil).should_receive("rmtree").with_args("")
    flexmock(pagure.pr.Comment).should_receive(
        "get_base_project",
    ).once().and_return(distgit_project)

    processing_results = SteveJobs().process_message(pagure_pr_comment_added)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    results = run_pull_from_upstream_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert first_dict_value(results["job"])["success"]


@pytest.mark.parametrize(
    "all_branches",
    [False, True],
)
def test_koji_build_tag_via_dist_git_pr_comment(pagure_pr_comment_added, all_branches):
    packit_yaml = (
        "{'specfile_path': 'python-teamcity-messages.spec',"
        "'jobs': [{'trigger': 'commit', 'job': 'koji_build', 'sidetag_group': 'test',"
        "'dist_git_branches': ['fedora-stable']}],"
        "'downstream_package_name': 'python-ogr', 'issue_repository': "
        "'https://github.com/namespace/repo'}"
    )
    pagure_project = flexmock(
        PagureProject,
        full_repo_name="rpms/packit",
        get_web_url=lambda: "https://src.fedoraproject.org/rpms/python-teamcity-messages",
        default_branch="main",
    )
    pagure_project.should_receive("get_files").with_args(
        ref="main",
        filter_regex=r".+\.spec$",
    ).and_return(["python-teamcity-messages.spec"])
    pagure_project.should_receive("get_file_content").with_args(
        path=".packit.yaml",
        ref="main",
    ).and_return(packit_yaml)
    pagure_project.should_receive("get_files").with_args(
        ref="main",
        recursive=False,
    ).and_return(["python-teamcity-messages.spec", ".packit.yaml"])

    pagure_pr_comment_added["pullrequest"]["comments"][0]["comment"] = "/packit koji-tag" + (
        " --all-branches" if all_branches else ""
    )

    project_event = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        project=flexmock(project_url=None),
        id=123,
    )
    flexmock(AddPullRequestEventToDb).should_receive("db_project_object").and_return(
        project_event,
    )
    flexmock(PullRequestModel).should_receive("get_by_id").with_args(123).and_return(
        project_event,
    )
    flexmock(PipelineModel).should_receive("create")

    db_project_object = flexmock(
        id=12,
        project_event_model_type=ProjectEventModelType.pull_request,
        job_config_trigger_type=JobConfigTriggerType.pull_request,
    )
    db_project_event = (
        flexmock().should_receive("get_project_event_object").and_return(db_project_object).mock()
    )
    flexmock(ProjectEventModel).should_receive("get_or_create").with_args(
        type=ProjectEventModelType.pull_request,
        event_id=12,
        commit_sha="beaf90bcecc51968a46663f8d6f092bfdc92e682",
    ).and_return(db_project_event)
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=pagure_pr_comment_added["pullrequest"]["id"],
        namespace=pagure_pr_comment_added["pullrequest"]["project"]["namespace"],
        repo_name=pagure_pr_comment_added["pullrequest"]["project"]["name"],
        project_url=pagure_pr_comment_added["pullrequest"]["project"]["full_url"],
    ).and_return(db_project_object)

    pr_mock = (
        flexmock(target_branch="f40")
        .should_receive("comment")
        .with_args(
            "The task was accepted. You can check the recent Koji tagging requests "
            "in [Packit dashboard](/jobs/koji-tag-requests). "
            f"{DistgitAnnouncement.get_comment_footer_with_announcement_if_present()}",
        )
        .once()
        .mock()
    )
    flexmock(
        PagureProject,
        full_repo_name="rpms/jouduv-dort",
        get_web_url=lambda: "https://src.fedoraproject.org/rpms/jouduv-dort",
        default_branch="main",
        get_pr=lambda id: pr_mock,
    )

    flexmock(TagIntoSidetagHandler).should_receive("pre_check").and_return(True)
    flexmock(celery_group).should_receive("apply_async").once()

    flexmock(PackitAPI).should_receive("init_kerberos_ticket").and_return()
    flexmock(aliases).should_receive("get_branches").with_args(
        "fedora-stable",
        with_aliases=True,
    ).and_return({"f39", "f40"}).once()
    flexmock(aliases).should_receive("get_branches").with_args(
        "fedora-stable",
    ).and_return({"f39", "f40"}).times(1 if all_branches else 0)

    sidetag_group = flexmock(name="test")
    flexmock(SidetagGroupModel).should_receive("get_or_create").with_args(
        "test",
    ).and_return(sidetag_group)
    flexmock(SidetagModel).should_receive("get_or_create_for_updating").with_args(
        sidetag_group.name,
        "f39",
    ).and_return(flexmock(koji_name="f39-build-side-12345", target="f39"))
    flexmock(SidetagModel).should_receive("get_or_create_for_updating").with_args(
        sidetag_group.name,
        "f40",
    ).and_return(flexmock(koji_name="f40-build-side-12345", target="f40"))
    flexmock(KojiHelper).should_receive("get_tag_info").with_args(
        "f39-build-side-12345",
    ).and_return(flexmock())
    flexmock(KojiHelper).should_receive("get_tag_info").with_args(
        "f40-build-side-12345",
    ).and_return(flexmock())
    flexmock(KojiHelper).should_receive("get_latest_stable_nvr").with_args(
        "python-ogr",
        "f39",
    ).and_return("python-ogr-0.1-1.fc39")
    flexmock(KojiHelper).should_receive("get_latest_stable_nvr").with_args(
        "python-ogr",
        "f40",
    ).and_return("python-ogr-0.1-1.fc40")

    if all_branches:
        flexmock(KojiHelper).should_receive("tag_build").with_args(
            "python-ogr-0.1-1.fc39",
            "f39-build-side-12345",
        ).and_return("123456").once()
    flexmock(KojiHelper).should_receive("tag_build").with_args(
        "python-ogr-0.1-1.fc40",
        "f40-build-side-12345",
    ).and_return("654321").once()

    flexmock(PipelineModel).should_receive("create").and_return(flexmock()).once()
    koji_tag_request_group = flexmock()
    flexmock(KojiTagRequestGroupModel).should_receive("create").and_return(
        koji_tag_request_group
    ).once()
    if all_branches:
        flexmock(KojiTagRequestTargetModel).should_receive("create").with_args(
            task_id="123456",
            web_url=str,
            target="f39",
            sidetag="f39-build-side-12345",
            nvr="python-ogr-0.1-1.fc39",
            koji_tag_request_group=koji_tag_request_group,
        ).and_return().once()
    flexmock(KojiTagRequestTargetModel).should_receive("create").with_args(
        task_id="654321",
        web_url=str,
        target="f40",
        sidetag="f40-build-side-12345",
        nvr="python-ogr-0.1-1.fc40",
        koji_tag_request_group=koji_tag_request_group,
    ).and_return().once()

    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    processing_results = SteveJobs().process_message(pagure_pr_comment_added)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)
    results = run_tag_into_sidetag_handler(
        event=event_dict,
        package_config=package_config,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]


@pytest.mark.parametrize(
    "comment",
    [
        pytest.param("/packit-ci test"),
        pytest.param("/packit-ci test installability"),
    ],
)
@pytest.mark.parametrize(
    "target_branch, uid, check_name",
    [
        pytest.param(
            "rawhide",
            "380723461ab74e1cde4eb89b711c8f1d",
            "Packit - installability test(s) - rawhide",
            id="rawhide target branch",
        ),
        pytest.param(
            "f42",
            "a478035df5f8599527e3e37bfc2ca25f",
            "Packit - installability test(s) - f42",
            id="f42 target branch",
        ),
    ],
)
def test_downstream_testing_farm_retrigger_via_dist_git_pr_comment(
    pagure_pr_comment_added, comment, target_branch, uid, check_name
):
    pagure_pr_comment_added["pullrequest"]["comments"][0]["comment"] = comment
    pagure_pr_comment_added["pullrequest"]["branch"] = target_branch
    pr_object = (
        flexmock(target_branch=target_branch, head_commit="abcdef")
        .should_receive("set_flag")
        .with_args(
            username=check_name,
            comment="The task was accepted.",
            url=str,
            status=CommitStatus,
            uid=uid,
        )
        .once()
        .mock()
    )
    dg_project = (
        flexmock(
            PagureProject(
                namespace="rpms", repo="python-teamcity-messages", service=flexmock(read_only=False)
            ),
            default_branch="main",
        )
        .should_receive("is_private")
        .and_return(False)
        .mock()
        .should_receive("get_pr")
        .and_return(pr_object)
        .mock()
        .should_receive("get_files")
        .and_return([])
        .mock()
        .should_receive("get_file_content")
        .and_raise(FileNotFoundError)
        .mock()
        .should_receive("get_web_url")
        .and_return("URL")
        .mock()
    )
    service_config = (
        flexmock(
            enabled_projects_for_fedora_ci="https://src.fedoraproject.org/rpms/python-teamcity-messages",
            command_handler_work_dir=SANDCASTLE_WORK_DIR,
            repository_cache="/tmp/repository-cache",
            add_repositories_to_repository_cache=False,
            deployment=Deployment.stg,
            comment_command_prefix="/packit",
            package_config_path_override=None,
            testing_farm_api_url="https://api.dev.testing-farm.io/api",
            testing_farm_secret="secret",
        )
        .should_receive("get_project")
        .and_return(dg_project)
        .mock()
    )
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(service_config)
    flexmock(PackitAPIWithDownstreamMixin).should_receive("is_packager").and_return(
        True,
    )
    db_project_object = flexmock(
        id=9,
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        project_event_model_type=ProjectEventModelType.pull_request,
        project=flexmock(project_url="https://src.fedoraproject.org/rpms/python-teamcity-messages"),
    )
    db_project_event = (
        flexmock().should_receive("get_project_event_object").and_return(db_project_object).mock()
    )
    flexmock(ProjectEventModel).should_receive("get_or_create").with_args(
        type=ProjectEventModelType.pull_request,
        event_id=9,
        commit_sha="abcd",
    ).and_return(flexmock())
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=36,
        namespace="rpms",
        repo_name="python-teamcity-messages",
        project_url="https://src.fedoraproject.org/rpms/python-teamcity-messages",
    ).and_return(db_project_object)
    flexmock(ProjectEventModel).should_receive("get_or_create").and_return(
        db_project_event,
    )

    run = flexmock(test_run_group=None)
    koji_build = flexmock(
        id=123,
        target="main",
        status="success",
        group_of_targets=flexmock(runs=[run]),
    )
    test_run = flexmock(
        id=1,
        status=TestingFarmResult.new,
        koji_builds=[koji_build],
        target=target_branch,
    )
    flexmock(PipelineModel).should_receive("create").and_return(run)
    flexmock(TFTTestRunTargetModel).should_receive("create").and_return(test_run)
    flexmock(TFTTestRunGroupModel).should_receive("create").with_args(
        [run], ranch="public"
    ).and_return(
        flexmock(grouped_targets=[test_run]),
    )

    flexmock(KojiBuildTargetModel).should_receive(
        "get_last_successful_scratch_by_commit_target"
    ).with_args("abcdef", target_branch).and_return(koji_build)

    flexmock(DownstreamTestingFarmJobHelper).should_receive("run_testing_farm").once().and_return(
        TaskResults(success=True, details={}),
    )

    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    processing_results = SteveJobs().process_message(pagure_pr_comment_added)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)
    results = run_downstream_testing_farm_handler(
        event=event_dict,
        package_config=package_config,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]
