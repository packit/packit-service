# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json
from typing import List

import pytest
from celery.canvas import Signature
from flexmock import flexmock
from github import Github

import packit_service.models
import packit_service.service.urls as urls

from ogr.utils import RequestResponse
from ogr.services.pagure import PagureProject
from ogr.services.github import GithubProject
from packit.api import PackitAPI
from packit.config import (
    JobConfigTriggerType,
)
from packit.exceptions import PackitConfigException
from packit.utils.koji_helper import KojiHelper
from packit.local_project import LocalProject
from packit_service.config import ServiceConfig
from packit_service.constants import (
    COMMENT_REACTION,
    CONTACTS_URL,
    DATE_OF_DEFAULT_SRPM_BUILD_IN_COPR,
    DOCS_HOW_TO_CONFIGURE_URL,
    TASK_ACCEPTED,
)
from packit_service.models import (
    CoprBuildTargetModel,
    GithubInstallationModel,
    GitProjectModel,
    JobTriggerModel,
    JobTriggerModelType,
    PipelineModel,
    PullRequestModel,
    TFTTestRunTargetModel,
    TestingFarmResult,
    BuildStatus,
)
from packit_service.service.db_triggers import AddPullRequestDbTrigger
from packit_service.utils import (
    get_packit_commands_from_comment,
    load_job_config,
)
from packit_service.worker.allowlist import Allowlist
from packit_service.worker.celery_task import CeleryTask
from packit_service.worker.events.event import AbstractForgeIndependentEvent
from packit_service.worker.handlers.bodhi import (
    RetriggerBodhiUpdateHandler,
)
from packit_service.worker.helpers.build import copr_build
from packit_service.worker.helpers.build.copr_build import CoprBuildJobHelper
from packit_service.worker.helpers.build.koji_build import KojiBuildJobHelper
from packit_service.worker.helpers.testing_farm import TestingFarmJobHelper
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.reporting import BaseCommitStatus, StatusReporterGithubChecks
from packit_service.worker.reporting import StatusReporter
from packit_service.worker.result import TaskResults
from packit_service.worker.tasks import (
    run_copr_build_handler,
    run_koji_build_handler,
    run_retrigger_bodhi_update,
    run_testing_farm_handler,
)
from tests.spellbook import DATA_DIR, first_dict_value, get_parameters_from_results


@pytest.fixture(scope="module")
def pr_copr_build_comment_event():
    return json.loads(
        (DATA_DIR / "webhooks" / "github" / "pr_comment_copr_build.json").read_text()
    )


@pytest.fixture(scope="module")
def pr_build_comment_event():
    return json.loads(
        (DATA_DIR / "webhooks" / "github" / "pr_comment_build.json").read_text()
    )


@pytest.fixture(scope="module")
def pr_production_build_comment_event():
    return json.loads(
        (
            DATA_DIR / "webhooks" / "github" / "pr_comment_production_build.json"
        ).read_text()
    )


@pytest.fixture(scope="module")
def pr_embedded_command_comment_event():
    return json.loads(
        (
            DATA_DIR / "webhooks" / "github" / "pr_comment_embedded_command.json"
        ).read_text()
    )


@pytest.fixture(scope="module")
def pr_empty_comment_event():
    return json.loads(
        (DATA_DIR / "webhooks" / "github" / "pr_comment_empty.json").read_text()
    )


@pytest.fixture(scope="module")
def pr_packit_only_comment_event():
    return json.loads(
        (
            DATA_DIR / "webhooks" / "github" / "issue_comment_packit_only.json"
        ).read_text()
    )


@pytest.fixture(scope="module")
def pr_wrong_packit_comment_event():
    return json.loads(
        (
            DATA_DIR / "webhooks" / "github" / "issue_comment_wrong_packit_command.json"
        ).read_text()
    )


@pytest.fixture
def mock_pr_comment_functionality(request):
    packit_yaml = (
        "{'specfile_path': 'the-specfile.spec', 'synced_files': [], 'jobs': "
        + str(request.param)
        + "}"
    )

    flexmock(
        GithubProject,
        full_repo_name="packit-service/hello-world",
        get_file_content=lambda path, ref: packit_yaml,
        get_files=lambda ref, filter_regex: ["the-specfile.spec"],
        get_web_url=lambda: "https://github.com/the-namespace/the-repo",
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)

    trigger = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request, id=123
    )
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    flexmock(PullRequestModel).should_receive("get_by_id").with_args(123).and_return(
        trigger
    )
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Allowlist, check_and_report=True)


def one_job_finished_with_msg(results: List[TaskResults], msg: str):
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
                    "job": "copr_build",
                    "metadata": {"targets": "fedora-rawhide-x86_64"},
                }
            ]
        ]
    ),
    indirect=True,
)
def test_pr_comment_copr_build_handler(
    mock_pr_comment_functionality, pr_copr_build_comment_event
):
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=9,
        namespace="packit-service",
        repo_name="hello-world",
        project_url="https://github.com/packit-service/hello-world",
    ).and_return(
        flexmock(id=9, job_config_trigger_type=JobConfigTriggerType.pull_request)
    )
    flexmock(GithubInstallationModel).should_receive("get_by_account_login").with_args(
        account_login="packit-service"
    ).and_return(
        flexmock(
            created_at=DATE_OF_DEFAULT_SRPM_BUILD_IN_COPR,  # = old behaviour
            repositories=[flexmock(repo_name="hello-world")],
        )
    )
    flexmock(GitProjectModel).should_receive("get_by_id").and_return(
        flexmock(repo_name="hello-world")
    )
    flexmock(CoprBuildJobHelper).should_receive("run_copr_build").and_return(
        TaskResults(success=True, details={})
    ).once()
    flexmock(CoprBuildJobHelper).should_receive(
        "is_custom_copr_project_defined"
    ).and_return(False).once()
    flexmock(GithubProject).should_receive("get_files").and_return(["foo.spec"])
    flexmock(GithubProject).should_receive("get_web_url").and_return(
        "https://github.com/the-namespace/the-repo"
    )
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(copr_build).should_receive("get_valid_build_targets").and_return(set())
    flexmock(CoprBuildJobHelper).should_receive("report_status_to_build").with_args(
        description=TASK_ACCEPTED,
        state=BaseCommitStatus.pending,
        url="",
        markdown_content=None,
    ).once()
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").twice().and_return()
    pr = flexmock(head_commit="12345")
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    comment = flexmock()
    flexmock(pr).should_receive("get_comment").and_return(comment)
    flexmock(comment).should_receive("add_reaction").with_args(COMMENT_REACTION).once()

    processing_results = SteveJobs().process_message(pr_copr_build_comment_event)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)

    results = run_copr_build_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert first_dict_value(results["job"])["success"]


@pytest.mark.parametrize(
    "mock_pr_comment_functionality",
    (
        [
            [
                {
                    "trigger": "pull_request",
                    "job": "copr_build",
                    "metadata": {"targets": "fedora-rawhide-x86_64"},
                }
            ]
        ]
    ),
    indirect=True,
)
def test_pr_comment_build_handler(
    mock_pr_comment_functionality, pr_build_comment_event
):
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=9,
        namespace="packit-service",
        repo_name="hello-world",
        project_url="https://github.com/packit-service/hello-world",
    ).and_return(
        flexmock(id=9, job_config_trigger_type=JobConfigTriggerType.pull_request)
    )
    flexmock(GithubInstallationModel).should_receive("get_by_account_login").with_args(
        account_login="packit-service"
    ).and_return(
        flexmock(
            created_at=DATE_OF_DEFAULT_SRPM_BUILD_IN_COPR,  # = old behaviour
            repositories=[flexmock(repo_name="hello-world")],
        )
    )
    flexmock(GitProjectModel).should_receive("get_by_id").and_return(
        flexmock(repo_name="hello-world")
    )
    flexmock(CoprBuildJobHelper).should_receive("run_copr_build").and_return(
        TaskResults(success=True, details={})
    )
    flexmock(CoprBuildJobHelper).should_receive(
        "is_custom_copr_project_defined"
    ).and_return(False).once()
    flexmock(GithubProject, get_files="foo.spec")
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(copr_build).should_receive("get_valid_build_targets").and_return(set())
    flexmock(CoprBuildJobHelper).should_receive("report_status_to_build").with_args(
        description=TASK_ACCEPTED,
        state=BaseCommitStatus.pending,
        url="",
        markdown_content=None,
    ).once()
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").twice().and_return()
    pr = flexmock(head_commit="12345")
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    comment = flexmock()
    flexmock(pr).should_receive("get_comment").and_return(comment)
    flexmock(comment).should_receive("add_reaction").with_args(COMMENT_REACTION).once()

    processing_results = SteveJobs().process_message(pr_build_comment_event)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)

    results = run_copr_build_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert first_dict_value(results["job"])["success"]


@pytest.mark.parametrize(
    "mock_pr_comment_functionality",
    (
        [
            [
                {
                    "trigger": "pull_request",
                    "job": "tests",
                    "metadata": {"targets": "fedora-rawhide-x86_64"},
                }
            ]
        ]
    ),
    indirect=True,
)
def test_pr_comment_build_test_handler(
    mock_pr_comment_functionality, pr_build_comment_event
):
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=9,
        namespace="packit-service",
        repo_name="hello-world",
        project_url="https://github.com/packit-service/hello-world",
    ).and_return(
        flexmock(id=9, job_config_trigger_type=JobConfigTriggerType.pull_request)
    )
    flexmock(CoprBuildJobHelper).should_receive("run_copr_build").and_return(
        TaskResults(success=True, details={})
    )
    flexmock(GithubProject, get_files="foo.spec")
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(copr_build).should_receive("get_valid_build_targets").and_return(set())
    flexmock(TestingFarmJobHelper).should_receive("report_status_to_tests").with_args(
        description=TASK_ACCEPTED,
        state=BaseCommitStatus.pending,
        url="",
        markdown_content=None,
    ).once()
    flexmock(Signature).should_receive("apply_async").twice()
    flexmock(Pushgateway).should_receive("push").twice().and_return()
    pr = flexmock(head_commit="12345")
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    comment = flexmock()
    flexmock(pr).should_receive("get_comment").and_return(comment)
    flexmock(comment).should_receive("add_reaction").with_args(COMMENT_REACTION).once()

    processing_results = SteveJobs().process_message(pr_build_comment_event)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)
    results = run_testing_farm_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert first_dict_value(results["job"])["success"]
    assert (
        "CoprBuildHandler task sent"
        in first_dict_value(results["job"])["details"]["msg"]
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
                {
                    "trigger": "pull_request",
                    "job": "tests",
                    "metadata": {"targets": "fedora-rawhide-x86_64"},
                },
            ]
        ]
    ),
    indirect=True,
)
def test_pr_comment_build_build_and_test_handler(
    mock_pr_comment_functionality, pr_build_comment_event
):
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=9,
        namespace="packit-service",
        repo_name="hello-world",
        project_url="https://github.com/packit-service/hello-world",
    ).and_return(
        flexmock(id=9, job_config_trigger_type=JobConfigTriggerType.pull_request)
    )
    flexmock(GithubProject, get_files="foo.spec")
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(copr_build).should_receive("get_valid_build_targets").and_return(set())
    flexmock(CoprBuildJobHelper).should_receive("report_status_to_build").with_args(
        description=TASK_ACCEPTED,
        state=BaseCommitStatus.pending,
        url="",
        markdown_content=None,
    ).once()
    flexmock(TestingFarmJobHelper).should_receive("report_status_to_tests").with_args(
        description=TASK_ACCEPTED,
        state=BaseCommitStatus.pending,
        url="",
        markdown_content=None,
    ).once()
    flexmock(CoprBuildJobHelper).should_receive(
        "is_custom_copr_project_defined"
    ).and_return(False).once()
    flexmock(Signature).should_receive("apply_async").twice()
    flexmock(Pushgateway).should_receive("push").times(3).and_return()
    pr = flexmock(head_commit="12345")
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    comment = flexmock()
    flexmock(pr).should_receive("get_comment").and_return(comment)
    flexmock(comment).should_receive("add_reaction").with_args(COMMENT_REACTION).once()

    processing_results = SteveJobs().process_message(pr_build_comment_event)
    assert len(processing_results) == 2

    copr_build_job = [
        item for item in processing_results if item["details"]["job"] == "copr_build"
    ]
    assert copr_build_job

    test_job = [
        item for item in processing_results if item["details"]["job"] == "tests"
    ]
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


def test_pr_comment_production_build_handler(pr_production_build_comment_event):
    packit_yaml = str(
        {
            "specfile_path": "the-specfile.spec",
            "synced_files": [],
            "jobs": [
                {
                    "trigger": "pull_request",
                    "job": "upstream_koji_build",
                    "metadata": {"targets": "fedora-rawhide-x86_64", "scratch": "true"},
                }
            ],
        }
    )
    comment = flexmock(add_reaction=lambda reaction: None)
    flexmock(
        GithubProject,
        full_repo_name="packit-service/hello-world",
        get_file_content=lambda path, ref: packit_yaml,
        get_files=lambda ref, filter_regex: ["the-specfile.spec"],
        get_web_url=lambda: "https://github.com/the-namespace/the-repo",
        get_pr=lambda pr_id: flexmock(
            head_commit="12345", get_comment=lambda comment_id: comment
        ),
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)

    trigger = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request, id=123
    )
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    flexmock(PullRequestModel).should_receive("get_by_id").with_args(123).and_return(
        trigger
    )
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Allowlist, check_and_report=True)

    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=9,
        namespace="packit-service",
        repo_name="hello-world",
        project_url="https://github.com/packit-service/hello-world",
    ).and_return(
        flexmock(id=9, job_config_trigger_type=JobConfigTriggerType.pull_request)
    )
    flexmock(KojiBuildJobHelper).should_receive("run_koji_build").and_return(
        TaskResults(success=True, details={})
    )
    flexmock(GithubProject, get_files="foo.spec")
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(KojiBuildJobHelper).should_receive("report_status_to_build").with_args(
        description=TASK_ACCEPTED,
        state=BaseCommitStatus.pending,
        url="",
        markdown_content=None,
    ).once()
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").twice().and_return()
    pr = flexmock(head_commit="12345")
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    comment = flexmock()
    flexmock(pr).should_receive("get_comment").and_return(comment)
    flexmock(comment).should_receive("add_reaction").with_args(COMMENT_REACTION).once()

    processing_results = SteveJobs().process_message(pr_production_build_comment_event)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
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
        comment, packit_comment_command_prefix="/packit"
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
        comment, packit_comment_command_prefix=command
    )
    assert len(commands) == 0


@pytest.mark.parametrize(
    "comments_list, command",
    (
        ("/packit build", "/packit"),
        ("/packit-stg build", "/packit-stg"),
        ("/packit build ", "/packit"),
        ("/packit  build ", "/packit"),
        (" /packit build", "/packit"),
        (" /packit build ", "/packit"),
        ("asd\n/packit build\n", "/packit"),
        ("asd\n /packit build \n", "/packit"),
        ("Should be fixed now, let's\n /packit build\n it.", "/packit"),
    ),
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
                }
            ]
        ]
    ),
    indirect=True,
)
def test_pr_embedded_command_handler(
    mock_pr_comment_functionality,
    pr_embedded_command_comment_event,
    comments_list,
    command,
):
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=9,
        namespace="packit-service",
        repo_name="hello-world",
        project_url="https://github.com/packit-service/hello-world",
    ).and_return(
        flexmock(id=9, job_config_trigger_type=JobConfigTriggerType.pull_request)
    )
    flexmock(GithubInstallationModel).should_receive("get_by_account_login").with_args(
        account_login="packit-service"
    ).and_return(
        flexmock(
            created_at=DATE_OF_DEFAULT_SRPM_BUILD_IN_COPR,  # = old behaviour
            repositories=[flexmock(repo_name="hello-world")],
        )
    )
    flexmock(GitProjectModel).should_receive("get_by_id").and_return(
        flexmock(repo_name="hello-world")
    )
    ServiceConfig.get_service_config().comment_command_prefix = command
    pr_embedded_command_comment_event["comment"]["body"] = comments_list
    flexmock(CoprBuildJobHelper).should_receive("run_copr_build").and_return(
        TaskResults(success=True, details={})
    )
    flexmock(CoprBuildJobHelper).should_receive(
        "is_custom_copr_project_defined"
    ).and_return(False).once()
    flexmock(GithubProject, get_files="foo.spec")
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    pr = flexmock(head_commit="12345")
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    comment = flexmock()
    flexmock(pr).should_receive("get_comment").and_return(comment)
    flexmock(comment).should_receive("add_reaction").with_args(COMMENT_REACTION).once()
    flexmock(copr_build).should_receive("get_valid_build_targets").and_return(set())
    flexmock(CoprBuildJobHelper).should_receive("report_status_to_build").with_args(
        description=TASK_ACCEPTED,
        state=BaseCommitStatus.pending,
        url="",
        markdown_content=None,
    ).once()
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").twice().and_return()

    processing_results = SteveJobs().process_message(pr_embedded_command_comment_event)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)

    results = run_copr_build_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert first_dict_value(results["job"])["success"]


@pytest.mark.parametrize(
    "mock_pr_comment_functionality",
    (
        [
            [
                {
                    "trigger": "pull_request",
                    "job": "copr_build",
                    "metadata": {"targets": "fedora-rawhide-x86_64"},
                }
            ]
        ]
    ),
    indirect=True,
)
def test_pr_comment_empty_handler(
    mock_pr_comment_functionality, pr_empty_comment_event
):
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("can_merge_pr").and_return(True)
    pr = flexmock(head_commit="12345")
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)

    results = SteveJobs().process_message(pr_empty_comment_event)
    assert results == []


@pytest.mark.parametrize(
    "mock_pr_comment_functionality",
    (
        [
            [
                {
                    "trigger": "pull_request",
                    "job": "copr_build",
                    "metadata": {"targets": "fedora-rawhide-x86_64"},
                }
            ]
        ]
    ),
    indirect=True,
)
def test_pr_comment_packit_only_handler(
    mock_pr_comment_functionality, pr_packit_only_comment_event
):
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("can_merge_pr").and_return(True)
    pr = flexmock(head_commit="12345")
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)

    results = SteveJobs().process_message(pr_packit_only_comment_event)
    assert results == []


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
                }
            ]
        ]
    ),
    indirect=True,
)
def test_pr_comment_wrong_packit_command_handler(
    mock_pr_comment_functionality, pr_wrong_packit_comment_event
):
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("can_merge_pr").and_return(True)
    pr = flexmock(head_commit="12345")
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)

    results = SteveJobs().process_message(pr_wrong_packit_comment_event)
    assert results == []


def test_pr_test_command_handler(pr_embedded_command_comment_event):
    jobs = [
        {
            "trigger": "pull_request",
            "job": "tests",
            "metadata": {"targets": "fedora-rawhide-x86_64"},
        }
    ]
    packit_yaml = (
        "{'specfile_path': 'the-specfile.spec', 'synced_files': [], 'jobs': "
        + str(jobs)
        + "}"
    )
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

    trigger = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request, id=123
    )
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    flexmock(PullRequestModel).should_receive("get_by_id").with_args(123).and_return(
        trigger
    )
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Allowlist, check_and_report=True)
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=9,
        namespace="packit-service",
        repo_name="hello-world",
        project_url="https://github.com/packit-service/hello-world",
    ).and_return(
        flexmock(id=9, job_config_trigger_type=JobConfigTriggerType.pull_request)
    )
    pr_embedded_command_comment_event["comment"]["body"] = "/packit test"
    flexmock(GithubProject, get_files="foo.spec")
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(copr_build).should_receive("get_valid_build_targets").times(5).and_return(
        {"test-target"}
    )
    flexmock(TestingFarmJobHelper).should_receive("get_latest_copr_build").and_return(
        flexmock(status=BuildStatus.success)
    )
    flexmock(TestingFarmJobHelper).should_receive("run_testing_farm").once().and_return(
        TaskResults(success=True, details={})
    )
    flexmock(Pushgateway).should_receive("push").twice().and_return()
    flexmock(TestingFarmJobHelper).should_receive("report_status_to_tests").with_args(
        description=TASK_ACCEPTED,
        state=BaseCommitStatus.pending,
        url="",
        markdown_content=None,
    ).once()

    processing_results = SteveJobs().process_message(pr_embedded_command_comment_event)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)

    run_testing_farm_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )


def test_pr_test_command_handler_identifiers(pr_embedded_command_comment_event):
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
    packit_yaml = (
        "{'specfile_path': 'the-specfile.spec', 'synced_files': [], 'jobs': "
        + str(jobs)
        + "}"
    )
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

    trigger = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        id=123,
        job_trigger_model_type=JobTriggerModelType.pull_request,
    )
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    flexmock(PullRequestModel).should_receive("get_by_id").with_args(123).and_return(
        trigger
    )
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Allowlist, check_and_report=True)
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=9,
        namespace="packit-service",
        repo_name="hello-world",
        project_url="https://github.com/packit-service/hello-world",
    ).and_return(
        flexmock(
            id=9,
            job_config_trigger_type=JobConfigTriggerType.pull_request,
            job_trigger_model_type=JobTriggerModelType.pull_request,
        )
    )
    pr_embedded_command_comment_event["comment"]["body"] = "/packit test"
    flexmock(GithubProject, get_files="foo.spec")
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(copr_build).should_receive("get_valid_build_targets").times(5).and_return(
        {"test-target"}
    )
    flexmock(CoprBuildTargetModel).should_receive("get_all_by").with_args(
        project_name="packit-service-hello-world-9",
        commit_sha="12345",
        owner=None,
        target="test-target",
    ).and_return([flexmock(status=BuildStatus.success)])
    flexmock(TestingFarmJobHelper).should_receive("run_testing_farm").once().and_return(
        TaskResults(success=True, details={})
    )
    flexmock(Pushgateway).should_receive("push").twice().and_return()
    flexmock(TestingFarmJobHelper).should_receive("report_status_to_tests").with_args(
        description=TASK_ACCEPTED,
        state=BaseCommitStatus.pending,
        url="",
        markdown_content=None,
    ).once()

    processing_results = SteveJobs().process_message(pr_embedded_command_comment_event)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
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
                0,
                "Failed to submit tests. The task will be retried in 1 minute.",
                "Failed to post request to testing farm API.",
                BaseCommitStatus.pending,
                None,
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
                1,
                "Failed to submit tests. The task will be retried in 2 minutes.",
                "Failed to post request to testing farm API.",
                BaseCommitStatus.pending,
                None,
                120,
            ),
            (
                2,
                "Failed to post request to testing farm API.",
                None,
                BaseCommitStatus.error,
                None,
                None,
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
        }
    ]
    packit_yaml = (
        "{'specfile_path': 'the-specfile.spec', 'synced_files': [], 'jobs': "
        + str(jobs)
        + "}"
    )
    pr = flexmock(
        source_project=flexmock(
            get_web_url=lambda: "https://github.com/someone/hello-world"
        ),
        target_project=flexmock(
            get_web_url=lambda: "https://github.com/packit-service/hello-world"
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

    trigger = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        id=123,
        job_trigger_model_type=JobTriggerModelType.pull_request,
    )
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    flexmock(PullRequestModel).should_receive("get_by_id").with_args(123).and_return(
        trigger
    )
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Allowlist, check_and_report=True)
    pr_model = flexmock(
        id=9,
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        job_trigger_model_type=JobTriggerModelType.pull_request,
    )
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=9,
        namespace="packit-service",
        repo_name="hello-world",
        project_url="https://github.com/packit-service/hello-world",
    ).and_return(pr_model)

    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=JobTriggerModelType.pull_request, trigger_id=9
    ).and_return(flexmock(id=2, type=JobTriggerModelType.pull_request))

    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=JobTriggerModelType.pull_request, trigger_id=123
    ).and_return(flexmock(id=2, type=JobTriggerModelType.pull_request))

    pr_embedded_command_comment_event["comment"]["body"] = "/packit test"
    flexmock(GithubProject, get_files="foo.spec")
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(copr_build).should_receive("get_valid_build_targets").and_return(
        {"fedora-rawhide-x86_64"}
    )
    flexmock(TestingFarmJobHelper).should_receive("get_latest_copr_build").never()
    flexmock(Pushgateway).should_receive("push").twice().and_return()

    payload = {
        "api_key": "secret-token",
        "test": {
            "fmf": {
                "url": "https://github.com/someone/hello-world",
                "ref": "0011223344",
            }
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
            }
        ],
        "notification": {
            "webhook": {
                "url": "https://prod.packit.dev/api/testing-farm/results",
                "token": "secret-token",
            }
        },
    }

    flexmock(TestingFarmJobHelper).should_receive("is_fmf_configured").and_return(True)
    flexmock(TestingFarmJobHelper).should_receive("distro2compose").and_return(
        "Fedora-Rawhide"
    )

    flexmock(TestingFarmJobHelper).should_receive(
        "send_testing_farm_request"
    ).with_args(endpoint="requests", method="POST", data=payload).and_return(response)

    flexmock(StatusReporterGithubChecks).should_receive("set_status").with_args(
        state=BaseCommitStatus.pending,
        description=TASK_ACCEPTED,
        check_name="testing-farm:fedora-rawhide-x86_64",
        url="",
        links_to_external_services=None,
        markdown_content=None,
    ).once()

    flexmock(StatusReporterGithubChecks).should_receive("set_status").with_args(
        state=BaseCommitStatus.running,
        description="Submitting the tests ...",
        check_name="testing-farm:fedora-rawhide-x86_64",
        url="",
        links_to_external_services=None,
        markdown_content=None,
    ).once()

    flexmock(GithubProject).should_receive("get_web_url").and_return(
        "https://github.com/packit-service/hello-world"
    )

    flexmock(PipelineModel).should_receive("create").never()
    flexmock(TFTTestRunTargetModel).should_receive("create").never()

    flexmock(StatusReporterGithubChecks).should_receive("set_status").with_args(
        state=status,
        description=description,
        check_name="testing-farm:fedora-rawhide-x86_64",
        url="",
        links_to_external_services=None,
        markdown_content=markdown_content,
    ).once()
    flexmock(Signature).should_receive("apply_async").once()

    processing_results = SteveJobs().process_message(pr_embedded_command_comment_event)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )

    if delay is not None:
        flexmock(CeleryTask).should_receive("retry").with_args(delay=delay).once()

    assert json.dumps(event_dict)
    task = run_testing_farm_handler.__wrapped__.__func__
    task(
        flexmock(request=flexmock(retries=retry_number)),
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )


def test_pr_test_command_handler_skip_build_option(pr_embedded_command_comment_event):
    jobs = [
        {
            "trigger": "pull_request",
            "job": "tests",
            "metadata": {"targets": "fedora-rawhide-x86_64", "skip_build": True},
        }
    ]
    packit_yaml = (
        "{'specfile_path': 'the-specfile.spec', 'synced_files': [], 'jobs': "
        + str(jobs)
        + "}"
    )
    pr = flexmock(
        source_project=flexmock(
            get_web_url=lambda: "https://github.com/someone/hello-world"
        ),
        target_project=flexmock(
            get_web_url=lambda: "https://github.com/packit-service/hello-world"
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

    trigger = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request, id=123
    )
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    flexmock(PullRequestModel).should_receive("get_by_id").with_args(123).and_return(
        trigger
    )
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Allowlist, check_and_report=True)
    pr_model = flexmock(
        id=9,
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        job_trigger_model_type=JobTriggerModelType.pull_request,
    )
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=9,
        namespace="packit-service",
        repo_name="hello-world",
        project_url="https://github.com/packit-service/hello-world",
    ).and_return(pr_model)
    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=JobTriggerModelType.pull_request, trigger_id=9
    ).and_return(flexmock(id=2, type=JobTriggerModelType.pull_request))
    pr_embedded_command_comment_event["comment"]["body"] = "/packit test"
    flexmock(GithubProject, get_files="foo.spec")
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(copr_build).should_receive("get_valid_build_targets").and_return(
        {"fedora-rawhide-x86_64"}
    )
    flexmock(TestingFarmJobHelper).should_receive("get_latest_copr_build").never()
    flexmock(Pushgateway).should_receive("push").twice().and_return()
    flexmock(TestingFarmJobHelper).should_receive("report_status_to_tests").with_args(
        description=TASK_ACCEPTED,
        state=BaseCommitStatus.pending,
        url="",
        markdown_content=None,
    ).once()

    payload = {
        "api_key": "secret-token",
        "test": {
            "fmf": {
                "url": "https://github.com/someone/hello-world",
                "ref": "0011223344",
            }
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
            }
        ],
        "notification": {
            "webhook": {
                "url": "https://prod.packit.dev/api/testing-farm/results",
                "token": "secret-token",
            }
        },
    }

    flexmock(TestingFarmJobHelper).should_receive("is_fmf_configured").and_return(True)
    flexmock(TestingFarmJobHelper).should_receive("distro2compose").and_return(
        "Fedora-Rawhide"
    )

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
        description="Submitting the tests ...",
        check_names="testing-farm:fedora-rawhide-x86_64",
        url="",
        markdown_content=None,
    ).once()

    flexmock(GithubProject).should_receive("get_web_url").and_return(
        "https://github.com/packit-service/hello-world"
    )

    tft_test_run_model = flexmock(id=5)
    run_model = flexmock()
    flexmock(PipelineModel).should_receive("create").and_return(run_model)
    flexmock(TFTTestRunTargetModel).should_receive("create").with_args(
        pipeline_id=pipeline_id,
        identifier=None,
        commit_sha="0011223344",
        status=TestingFarmResult.new,
        target="fedora-rawhide-x86_64",
        web_url=None,
        run_models=[run_model],
        data={"base_project_url": "https://github.com/packit-service/hello-world"},
    ).and_return(tft_test_run_model)

    urls.DASHBOARD_URL = "https://dashboard.localhost"
    flexmock(StatusReporter).should_receive("report").with_args(
        description="Tests have been submitted ...",
        state=BaseCommitStatus.running,
        url="https://dashboard.localhost/results/testing-farm/5",
        check_names="testing-farm:fedora-rawhide-x86_64",
        markdown_content=None,
    ).once()
    flexmock(Signature).should_receive("apply_async").once()

    processing_results = SteveJobs().process_message(pr_embedded_command_comment_event)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)

    run_testing_farm_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )


def test_pr_test_command_handler_compose_not_present(
    pr_embedded_command_comment_event,
):
    jobs = [
        {
            "trigger": "pull_request",
            "job": "tests",
            "metadata": {"targets": "fedora-rawhide-x86_64", "skip_build": True},
        }
    ]
    packit_yaml = (
        "{'specfile_path': 'the-specfile.spec', 'synced_files': [], 'jobs': "
        + str(jobs)
        + "}"
    )
    pr = flexmock(
        source_project=flexmock(
            get_web_url=lambda: "https://github.com/someone/hello-world"
        ),
        target_project=flexmock(
            get_web_url=lambda: "https://github.com/packit-service/hello-world"
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

    trigger = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request, id=123
    )
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    flexmock(PullRequestModel).should_receive("get_by_id").with_args(123).and_return(
        trigger
    )
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Allowlist, check_and_report=True)
    pr_model = flexmock(
        id=9,
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        job_trigger_model_type=JobTriggerModelType.pull_request,
    )
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=9,
        namespace="packit-service",
        repo_name="hello-world",
        project_url="https://github.com/packit-service/hello-world",
    ).and_return(pr_model)
    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=JobTriggerModelType.pull_request, trigger_id=9
    ).and_return(flexmock(id=2, type=JobTriggerModelType.pull_request))
    pr_embedded_command_comment_event["comment"]["body"] = "/packit test"
    flexmock(GithubProject, get_files="foo.spec")
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(copr_build).should_receive("get_valid_build_targets").and_return(
        {"fedora-rawhide-x86_64"}
    )
    flexmock(TestingFarmJobHelper).should_receive("get_latest_copr_build").never()
    flexmock(Pushgateway).should_receive("push").twice().and_return()
    flexmock(TestingFarmJobHelper).should_receive("report_status_to_tests").with_args(
        description=TASK_ACCEPTED,
        state=BaseCommitStatus.pending,
        url="",
        markdown_content=None,
    ).once()

    flexmock(TestingFarmJobHelper).should_receive("is_fmf_configured").and_return(True)

    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.running,
        description="Submitting the tests ...",
        check_names="testing-farm:fedora-rawhide-x86_64",
        url="",
        markdown_content=None,
    ).once()

    response = flexmock(
        status_code=200, json=lambda: {"composes": [{"name": "some-other-compose"}]}
    )
    flexmock(TestingFarmJobHelper).should_receive(
        "send_testing_farm_request"
    ).with_args(endpoint="composes/public").and_return(response).once()

    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.error,
        description="The compose Fedora-Rawhide is not available in the public "
        "Testing Farm infrastructure.",
        check_names="testing-farm:fedora-rawhide-x86_64",
        url="",
        markdown_content="The compose Fedora-Rawhide (from target fedora-rawhide) is not in "
        "the list of available composes:\n"
        "{'some-other-compose'}. Please, check the targets defined in your test job configuration. "
        "If you think your configuration is correct, get "
        "in touch with [us](https://packit.dev/#contact).",
    ).once()

    flexmock(Signature).should_receive("apply_async").once()

    processing_results = SteveJobs().process_message(pr_embedded_command_comment_event)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)

    run_testing_farm_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )


def test_pr_test_command_handler_composes_not_available(
    pr_embedded_command_comment_event,
):
    jobs = [
        {
            "trigger": "pull_request",
            "job": "tests",
            "metadata": {"targets": "fedora-rawhide-x86_64", "skip_build": True},
        }
    ]
    packit_yaml = (
        "{'specfile_path': 'the-specfile.spec', 'synced_files': [], 'jobs': "
        + str(jobs)
        + "}"
    )
    pr = flexmock(
        source_project=flexmock(
            get_web_url=lambda: "https://github.com/someone/hello-world"
        ),
        target_project=flexmock(
            get_web_url=lambda: "https://github.com/packit-service/hello-world"
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

    trigger = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request, id=123
    )
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    flexmock(PullRequestModel).should_receive("get_by_id").with_args(123).and_return(
        trigger
    )
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Allowlist, check_and_report=True)
    pr_model = flexmock(
        id=9,
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        job_trigger_model_type=JobTriggerModelType.pull_request,
    )
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=9,
        namespace="packit-service",
        repo_name="hello-world",
        project_url="https://github.com/packit-service/hello-world",
    ).and_return(pr_model)
    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=JobTriggerModelType.pull_request, trigger_id=9
    ).and_return(flexmock(id=2, type=JobTriggerModelType.pull_request))
    pr_embedded_command_comment_event["comment"]["body"] = "/packit test"
    flexmock(GithubProject, get_files="foo.spec")
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(copr_build).should_receive("get_valid_build_targets").and_return(
        {"fedora-rawhide-x86_64"}
    )
    flexmock(TestingFarmJobHelper).should_receive("get_latest_copr_build").never()
    flexmock(Pushgateway).should_receive("push").twice().and_return()
    flexmock(TestingFarmJobHelper).should_receive("report_status_to_tests").with_args(
        description=TASK_ACCEPTED,
        state=BaseCommitStatus.pending,
        url="",
        markdown_content=None,
    ).once()

    flexmock(TestingFarmJobHelper).should_receive("is_fmf_configured").and_return(True)

    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.running,
        description="Submitting the tests ...",
        check_names="testing-farm:fedora-rawhide-x86_64",
        url="",
        markdown_content=None,
    ).once()

    flexmock(TestingFarmJobHelper).should_receive(
        "send_testing_farm_request"
    ).with_args(endpoint="composes/public").and_return(flexmock(status_code=500)).once()

    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.error,
        description="We were not able to get the available TF composes.",
        check_names="testing-farm:fedora-rawhide-x86_64",
        url="",
        markdown_content=None,
    ).once()

    flexmock(Signature).should_receive("apply_async").once()

    processing_results = SteveJobs().process_message(pr_embedded_command_comment_event)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)

    run_testing_farm_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )


def test_pr_test_command_handler_missing_build(pr_embedded_command_comment_event):
    jobs = [
        {
            "trigger": "pull_request",
            "job": "tests",
            "metadata": {"targets": "fedora-rawhide-x86_64"},
        }
    ]
    packit_yaml = (
        "{'specfile_path': 'the-specfile.spec', 'synced_files': [], 'jobs': "
        + str(jobs)
        + "}"
    )
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

    trigger = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request, id=123
    )
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    flexmock(PullRequestModel).should_receive("get_by_id").with_args(123).and_return(
        trigger
    )
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Allowlist, check_and_report=True)
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=9,
        namespace="packit-service",
        repo_name="hello-world",
        project_url="https://github.com/packit-service/hello-world",
    ).and_return(
        flexmock(
            id=9,
            job_config_trigger_type=JobConfigTriggerType.pull_request,
            job_trigger_model_type=JobTriggerModelType.pull_request,
        )
    )
    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=JobTriggerModelType.pull_request, trigger_id=9
    ).and_return(trigger)

    pr_embedded_command_comment_event["comment"]["body"] = "/packit test"
    flexmock(GithubProject, get_files="foo.spec")
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(Signature).should_receive("apply_async").twice()
    flexmock(copr_build).should_receive("get_valid_build_targets").and_return(
        {"test-target", "test-target-without-build"}
    )
    flexmock(TestingFarmJobHelper).should_receive("get_latest_copr_build").and_return(
        flexmock(status=BuildStatus.success)
    ).and_return()

    flexmock(TestingFarmJobHelper).should_receive("job_owner").and_return("owner")
    flexmock(TestingFarmJobHelper).should_receive("job_project").and_return("project")
    flexmock(TestingFarmJobHelper).should_receive("report_status_to_tests").once()
    flexmock(TestingFarmJobHelper).should_receive(
        "report_status_to_tests_for_chroot"
    ).once()
    flexmock(TestingFarmJobHelper).should_receive("run_testing_farm").once().and_return(
        TaskResults(success=False, details={})
    )
    flexmock(Pushgateway).should_receive("push").twice().and_return()

    processing_results = SteveJobs().process_message(pr_embedded_command_comment_event)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)

    flexmock(packit_service.worker.handlers.testing_farm).should_receive(
        "dump_job_config"
    ).with_args(job_config=load_job_config(job_config)).once()

    run_testing_farm_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )


def test_pr_test_command_handler_missing_build_trigger_with_build_job_config(
    pr_embedded_command_comment_event,
):
    jobs = [
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
    ]
    packit_yaml = (
        "{'specfile_path': 'the-specfile.spec', 'synced_files': [], 'jobs': "
        + str(jobs)
        + "}"
    )
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

    trigger = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request, id=123
    )
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    flexmock(PullRequestModel).should_receive("get_by_id").with_args(123).and_return(
        trigger
    )
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Allowlist, check_and_report=True)
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=9,
        namespace="packit-service",
        repo_name="hello-world",
        project_url="https://github.com/packit-service/hello-world",
    ).and_return(
        flexmock(
            id=9,
            job_config_trigger_type=JobConfigTriggerType.pull_request,
            job_trigger_model_type=JobTriggerModelType.pull_request,
        )
    )
    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=JobTriggerModelType.pull_request, trigger_id=9
    ).and_return(trigger)

    pr_embedded_command_comment_event["comment"]["body"] = "/packit test"
    flexmock(GithubProject, get_files="foo.spec")
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(Signature).should_receive("apply_async").twice()
    flexmock(copr_build).should_receive("get_valid_build_targets").and_return(
        {"test-target", "test-target-without-build"}
    )
    flexmock(TestingFarmJobHelper).should_receive("get_latest_copr_build").and_return(
        flexmock(status=BuildStatus.success)
    ).and_return()

    flexmock(TestingFarmJobHelper).should_receive("job_owner").and_return("owner")
    flexmock(TestingFarmJobHelper).should_receive("job_project").and_return("project")
    flexmock(TestingFarmJobHelper).should_receive("report_status_to_tests").once()
    flexmock(TestingFarmJobHelper).should_receive(
        "report_status_to_tests_for_chroot"
    ).once()
    flexmock(TestingFarmJobHelper).should_receive("run_testing_farm").once().and_return(
        TaskResults(success=False, details={})
    )
    flexmock(Pushgateway).should_receive("push").twice().and_return()

    processing_results = SteveJobs().process_message(pr_embedded_command_comment_event)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)

    build_job_config = [
        job for job in package_config["jobs"] if job["job"] == "copr_build"
    ][0]

    flexmock(packit_service.worker.handlers.testing_farm).should_receive(
        "dump_job_config"
    ).with_args(job_config=load_job_config(build_job_config)).once()

    run_testing_farm_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )


def test_pr_test_command_handler_not_allowed_external_contributor_on_internal_TF(
    pr_embedded_command_comment_event,
):
    jobs = [
        {
            "trigger": "pull_request",
            "job": "tests",
            "metadata": {"targets": "fedora-rawhide-x86_64", "use_internal_tf": True},
        }
    ]
    packit_yaml = (
        "{'specfile_path': 'the-specfile.spec', 'synced_files': [], 'jobs': "
        + str(jobs)
        + "}"
    )
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
    pr_model = PullRequestModel()
    flexmock(
        pr_model,
        id=123,
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        actor="phracek",
    )
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(pr_model)
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Allowlist, check_and_report=True)
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=9,
        namespace="packit-service",
        repo_name="hello-world",
        project_url="https://github.com/packit-service/hello-world",
    ).and_return(pr_model).twice()
    pr_embedded_command_comment_event["comment"]["body"] = "/packit test"
    flexmock(GithubProject, get_files="foo.spec")
    flexmock(GithubProject).should_receive("is_private").and_return(False).once()
    flexmock(Signature).should_receive("apply_async").times(0)
    flexmock(TestingFarmJobHelper).should_receive("run_testing_farm").times(0)
    flexmock(TestingFarmJobHelper).should_receive("report_status_to_tests").with_args(
        description="phracek can't run tests internally",
        state=BaseCommitStatus.neutral,
        markdown_content="*As a project maintainer, "
        "you can trigger the test job manually via `/packit test` comment.*",
    ).once()

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
    pr_embedded_command_comment_event, comments
):
    flexmock(
        GithubProject,
        full_repo_name="namespace/repo",
        # just throwing an exception
        get_file_content=lambda path, ref: (_ for _ in ()).throw(FileNotFoundError),
        get_files=lambda ref, filter_regex: ["specfile.spec"],
        get_web_url=lambda: "https://github.com/namespace/repo",
    )

    pr_embedded_command_comment_event["comment"]["body"] = comments
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    pr = flexmock(head_commit="12345")
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    err_msg = (
        "No config file for packit (e.g. `.packit.yaml`) found in namespace/repo on commit 12345"
        "\n\n"
        "For more info, please check out "
        f"[the documentation]({DOCS_HOW_TO_CONFIGURE_URL}) "
        "or [contact the Packit team]"
        f"({CONTACTS_URL})."
    )
    flexmock(pr).should_receive("comment").with_args(err_msg)

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
                    "job": "copr_build",
                    "metadata": {"targets": "fedora-rawhide-x86_64"},
                }
            ]
        ]
    ),
    indirect=True,
)
def test_rebuild_failed(
    mock_pr_comment_functionality, pr_embedded_command_comment_event
):
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=9,
        namespace="packit-service",
        repo_name="hello-world",
        project_url="https://github.com/packit-service/hello-world",
    ).and_return(
        flexmock(id=9, job_config_trigger_type=JobConfigTriggerType.pull_request)
    )
    flexmock(GithubInstallationModel).should_receive("get_by_account_login").with_args(
        account_login="packit-service"
    ).and_return(
        flexmock(
            created_at=DATE_OF_DEFAULT_SRPM_BUILD_IN_COPR,  # = old behaviour
            repositories=[flexmock(repo_name="hello-world")],
        )
    )
    flexmock(GitProjectModel).should_receive("get_by_id").and_return(
        flexmock(repo_name="hello-world")
    )

    pr_embedded_command_comment_event["comment"]["body"] = "/packit rebuild-failed"
    flexmock(CoprBuildJobHelper).should_receive("run_copr_build").and_return(
        TaskResults(success=True, details={})
    )
    flexmock(CoprBuildJobHelper).should_receive(
        "is_custom_copr_project_defined"
    ).and_return(False).once()
    flexmock(GithubProject, get_files="foo.spec")
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    pr = flexmock(head_commit="12345")
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    comment = flexmock()
    flexmock(pr).should_receive("get_comment").and_return(comment)
    flexmock(comment).should_receive("add_reaction").with_args(COMMENT_REACTION).once()
    flexmock(copr_build).should_receive("get_valid_build_targets").and_return(set())

    model = flexmock(
        CoprBuildTargetModel, status=BuildStatus.failure, target="some_target"
    )
    flexmock(model).should_receive("get_all_by_commit").with_args(
        commit_sha="12345"
    ).and_return(model)
    flexmock(AbstractForgeIndependentEvent).should_receive(
        "get_all_build_targets_by_status"
    ).with_args(statuses_to_filter_with=[BuildStatus.failure]).and_return(
        {"some_target"}
    )
    flexmock(packit_service.models).should_receive(
        "filter_most_recent_target_names_by_status"
    ).with_args(
        models=[model], statuses_to_filter_with=[BuildStatus.failure]
    ).and_return(
        {"some_target"}
    )

    flexmock(CoprBuildJobHelper).should_receive("report_status_to_build").with_args(
        description=TASK_ACCEPTED,
        state=BaseCommitStatus.pending,
        url="",
        markdown_content=None,
    ).once()
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").twice().and_return()

    processing_results = SteveJobs().process_message(pr_embedded_command_comment_event)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert event_dict["build_targets_override"] == ["some_target"]
    assert json.dumps(event_dict)

    results = run_copr_build_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert first_dict_value(results["job"])["success"]


@pytest.mark.parametrize(
    "mock_pr_comment_functionality",
    (
        [
            [
                {
                    "trigger": "pull_request",
                    "job": "tests",
                    "metadata": {"targets": "fedora-rawhide-x86_64"},
                }
            ]
        ]
    ),
    indirect=True,
)
def test_retest_failed(
    mock_pr_comment_functionality, pr_embedded_command_comment_event
):
    pr = flexmock(head_commit="12345")
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    comment = flexmock()
    flexmock(pr).should_receive("get_comment").and_return(comment)
    flexmock(comment).should_receive("add_reaction").with_args(COMMENT_REACTION).once()

    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=9,
        namespace="packit-service",
        repo_name="hello-world",
        project_url="https://github.com/packit-service/hello-world",
    ).and_return(
        flexmock(id=9, job_config_trigger_type=JobConfigTriggerType.pull_request)
    )

    pr_embedded_command_comment_event["comment"]["body"] = "/packit retest-failed"
    flexmock(GithubProject, get_files="foo.spec")
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(copr_build).should_receive("get_valid_build_targets").times(3).and_return(
        {"test-target"}
    )
    flexmock(TestingFarmJobHelper).should_receive("get_latest_copr_build").and_return(
        flexmock(status=BuildStatus.success)
    )

    model = flexmock(
        TFTTestRunTargetModel, status=TestingFarmResult.failed, target="some_tf_target"
    )
    flexmock(model).should_receive("get_all_by_commit_target").with_args(
        commit_sha="12345"
    ).and_return(model)
    flexmock(AbstractForgeIndependentEvent).should_receive(
        "get_all_tf_targets_by_status"
    ).with_args(
        statuses_to_filter_with=[TestingFarmResult.failed, TestingFarmResult.error]
    ).and_return(
        {"some_tf_target"}
    )
    flexmock(packit_service.models).should_receive(
        "filter_most_recent_target_names_by_status"
    ).with_args(
        models=[model],
        statuses_to_filter_with=[TestingFarmResult.failed, TestingFarmResult.error],
    ).and_return(
        {"some_target"}
    )

    flexmock(Pushgateway).should_receive("push").twice().and_return()
    flexmock(TestingFarmJobHelper).should_receive("report_status_to_tests").with_args(
        description=TASK_ACCEPTED,
        state=BaseCommitStatus.pending,
        url="",
        markdown_content=None,
    ).once()

    processing_results = SteveJobs().process_message(pr_embedded_command_comment_event)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert event_dict["tests_targets_override"] == ["some_tf_target"]
    assert json.dumps(event_dict)

    run_testing_farm_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )


def test_pr_test_command_handler_skip_build_option_no_fmf_metadata(
    pr_embedded_command_comment_event,
):
    jobs = [
        {
            "trigger": "pull_request",
            "job": "tests",
            "metadata": {"targets": "fedora-rawhide-x86_64", "skip_build": True},
        }
    ]
    packit_yaml = (
        "{'specfile_path': 'the-specfile.spec', 'synced_files': [], 'jobs': "
        + str(jobs)
        + "}"
    )
    pr = flexmock(
        source_project=flexmock(
            get_web_url=lambda: "https://github.com/someone/hello-world"
        ),
        target_project=flexmock(
            get_web_url=lambda: "https://github.com/packit-service/hello-world"
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

    trigger = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request, id=123
    )
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    flexmock(PullRequestModel).should_receive("get_by_id").with_args(123).and_return(
        trigger
    )
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Allowlist, check_and_report=True)
    pr_model = flexmock(
        id=9,
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        job_trigger_model_type=JobTriggerModelType.pull_request,
    )
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=9,
        namespace="packit-service",
        repo_name="hello-world",
        project_url="https://github.com/packit-service/hello-world",
    ).and_return(pr_model)

    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=JobTriggerModelType.pull_request, trigger_id=9
    ).and_return(flexmock(id=2, type=JobTriggerModelType.pull_request))

    pr_embedded_command_comment_event["comment"]["body"] = "/packit test"
    flexmock(GithubProject, get_files="foo.spec")
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(copr_build).should_receive("get_valid_build_targets").and_return(
        {"fedora-rawhide-x86_64"}
    )
    flexmock(TestingFarmJobHelper).should_receive("get_latest_copr_build").never()
    flexmock(Pushgateway).should_receive("push").twice().and_return()
    flexmock(TestingFarmJobHelper).should_receive("report_status_to_tests").with_args(
        description=TASK_ACCEPTED,
        state=BaseCommitStatus.pending,
        url="",
        markdown_content=None,
    ).once()

    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.running,
        description="Submitting the tests ...",
        check_names="testing-farm:fedora-rawhide-x86_64",
        url="",
        markdown_content=None,
    ).once()

    flexmock(TestingFarmJobHelper).should_receive("is_fmf_configured").and_return(False)
    flexmock(StatusReporter).should_receive("report").with_args(
        state=BaseCommitStatus.neutral,
        description="No FMF metadata found. Please, initialize the metadata tree with `fmf init`.",
        check_names="testing-farm:fedora-rawhide-x86_64",
        url="",
        markdown_content=None,
    ).once()

    flexmock(GithubProject).should_receive("get_web_url").and_return(
        "https://github.com/packit-service/hello-world"
    )
    flexmock(Signature).should_receive("apply_async").once()

    processing_results = SteveJobs().process_message(pr_embedded_command_comment_event)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
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
                }
            ]
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
        flexmock(id=9, job_config_trigger_type=JobConfigTriggerType.pull_request)
    )
    ServiceConfig.get_service_config().comment_command_prefix = "/packit"
    pr_embedded_command_comment_event["comment"][
        "body"
    ] = "/packit i-hate-testing-with-flexmock"
    flexmock(GithubProject, get_files="foo.spec")
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    pr = flexmock(head_commit="12345")
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)

    processing_results = SteveJobs().process_message(pr_embedded_command_comment_event)
    assert processing_results == []


def test_invalid_packit_command_without_config(
    pr_embedded_command_comment_event,
):
    flexmock(
        GithubProject,
        full_repo_name="packit-service/hello-world",
        get_file_content=lambda path, ref: (_ for _ in ()).throw(
            FileNotFoundError
        ),  # no config
        get_files=lambda ref, filter_regex: ["the-specfile.spec"],
        get_web_url=lambda: "https://github.com/the-namespace/the-repo",
    )

    ServiceConfig.get_service_config().comment_command_prefix = "/packit"
    pr_embedded_command_comment_event["comment"][
        "body"
    ] = "/packit 10minutesOfImplementing3HoursOfTesting"
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    pr = flexmock(head_commit="12345")
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)

    processing_result = SteveJobs().process_message(pr_embedded_command_comment_event)[
        0
    ]
    assert processing_result["success"]
    assert (
        processing_result["details"]["msg"]
        == "No packit config found in the repository."
    )


def test_pr_test_command_handler_multiple_builds(pr_embedded_command_comment_event):
    pr_embedded_command_comment_event["comment"][
        "body"
    ] = "/packit test packit/packit-service#16"
    jobs = [
        {
            "trigger": "pull_request",
            "job": "tests",
            "metadata": {"targets": ["fedora-rawhide-x86_64", "fedora-35-x86_64"]},
        }
    ]
    packit_yaml = (
        "{'specfile_path': 'the-specfile.spec', 'synced_files': [], 'jobs': "
        + str(jobs)
        + "}"
    )
    pr = flexmock(
        source_project=flexmock(
            get_web_url=lambda: "https://github.com/someone/hello-world"
        ),
        target_project=flexmock(
            get_web_url=lambda: "https://github.com/packit-service/hello-world"
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

    trigger = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request, id=123
    )
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    flexmock(PullRequestModel).should_receive("get_by_id").with_args(123).and_return(
        trigger
    )
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Allowlist, check_and_report=True)
    pr_model = flexmock(
        id=9,
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        job_trigger_model_type=JobTriggerModelType.pull_request,
    )
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=9,
        namespace="packit-service",
        repo_name="hello-world",
        project_url="https://github.com/packit-service/hello-world",
    ).and_return(pr_model)
    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=JobTriggerModelType.pull_request, trigger_id=9
    ).and_return(flexmock(id=2, type=JobTriggerModelType.pull_request))
    flexmock(GithubProject, get_files="foo.spec")
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(copr_build).should_receive("get_valid_build_targets").and_return(
        {"fedora-rawhide-x86_64", "fedora-35-x86_64"}
    )

    run_model = flexmock(PipelineModel)

    build = flexmock(
        build_id="123456",
        built_packages=[
            {
                "name": "repo",
                "version": "0.1",
                "release": "1",
                "arch": "noarch",
                "epoch": "0",
            }
        ],
        build_logs_url=None,
        owner="mf",
        project_name="tree",
        status=BuildStatus.success,
        runs=[run_model],
    )
    build.should_receive("get_srpm_build").and_return(flexmock(url=None))

    flexmock(TestingFarmJobHelper).should_receive("get_latest_copr_build").and_return(
        build
    )
    flexmock(Pushgateway).should_receive("push").twice().and_return()
    flexmock(TestingFarmJobHelper).should_receive("report_status_to_tests").with_args(
        description=TASK_ACCEPTED,
        state=BaseCommitStatus.pending,
        url="",
        markdown_content=None,
    ).once()

    payload = {
        "api_key": "secret-token",
        "test": {
            "fmf": {
                "url": "https://github.com/someone/hello-world",
                "ref": "0011223344",
            }
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
                    "PACKIT_COPR_PROJECT": "mf/tree",
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
            }
        ],
        "notification": {
            "webhook": {
                "url": "https://prod.packit.dev/api/testing-farm/results",
                "token": "secret-token",
            }
        },
    }

    flexmock(TestingFarmJobHelper).should_receive("is_fmf_configured").and_return(True)
    flexmock(TestingFarmJobHelper).should_receive("distro2compose").and_return(
        "Fedora-Rawhide"
    )

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
        check_names="testing-farm:fedora-rawhide-x86_64",
        url="",
        markdown_content=None,
    ).once()

    flexmock(StatusReporter).should_receive("report").with_args(
        description="No latest successful Copr build from the other PR found.",
        state=BaseCommitStatus.failure,
        url="",
        check_names="testing-farm:fedora-35-x86_64",
        markdown_content=None,
    ).once()

    flexmock(GithubProject).should_receive("get_web_url").and_return(
        "https://github.com/packit-service/hello-world"
    )

    tft_test_run_model = flexmock(id=5)

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
            }
        ],
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
        .mock()
    )

    flexmock(packit_service.worker.helpers.testing_farm).should_receive(
        "filter_most_recent_target_models_by_status"
    ).with_args(
        models=[additional_copr_build],
        statuses_to_filter_with=[BuildStatus.success],
    ).and_return(
        {additional_copr_build}
    ).times(
        2
    )

    flexmock(TFTTestRunTargetModel).should_receive("create").with_args(
        pipeline_id=pipeline_id,
        identifier=None,
        commit_sha="0011223344",
        status=TestingFarmResult.new,
        target="fedora-rawhide-x86_64",
        web_url=None,
        run_models=[run_model, run_model2],
        data={"base_project_url": "https://github.com/packit-service/hello-world"},
    ).and_return(tft_test_run_model)

    urls.DASHBOARD_URL = "https://dashboard.localhost"
    flexmock(StatusReporter).should_receive("report").with_args(
        description="Tests have been submitted ...",
        state=BaseCommitStatus.running,
        url="https://dashboard.localhost/results/testing-farm/5",
        check_names="testing-farm:fedora-rawhide-x86_64",
        markdown_content=None,
    ).once()
    flexmock(Signature).should_receive("apply_async").once()

    processing_results = SteveJobs().process_message(pr_embedded_command_comment_event)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)

    run_testing_farm_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )


def test_bodhi_update_retrigger_via_dist_git_pr_comment(pagure_pr_comment_added):
    pagure_pr_comment_added["pullrequest"]["comments"][0][
        "comment"
    ] = "/packit create-update"
    project = pagure_pr_comment_added["pullrequest"]["project"]
    project["full_url"] = "https://src.fedoraproject.org/rpms/jouduv-dort"
    project["fullname"] = "rpms/jouduv-dort"
    project["name"] = "jouduv-dort"
    project["url_path"] = "rpms/jouduv-dort"

    packit_yaml = (
        "{'specfile_path': 'jouduv-dort.spec', 'synced_files': [],"
        "'jobs': [{'trigger': 'commit', 'job': 'bodhi_update'}],"
        "'downstream_package_name': 'jouduv-dort'}"
    )

    trigger = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request, id=123
    )
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    flexmock(PullRequestModel).should_receive("get_by_id").with_args(123).and_return(
        trigger
    )

    pagure_project = flexmock(
        PagureProject,
        full_repo_name="rpms/jouduv-dort",
        get_web_url=lambda: "https://src.fedoraproject.org/rpms/jouduv-dort",
        default_branch="main",
        get_pr=lambda id: flexmock(target_branch="the_distgit_branch"),
    )

    flexmock(KojiHelper).should_receive("get_latest_build_in_tag").and_return(
        {"nvr": "123"}
    )

    pagure_project.should_receive("get_files").with_args(
        ref="beaf90bcecc51968a46663f8d6f092bfdc92e682", filter_regex=r".+\.spec$"
    ).and_return(["jouduv-dort.spec"])
    pagure_project.should_receive("get_file_content").with_args(
        path=".distro/source-git.yaml",
        ref="beaf90bcecc51968a46663f8d6f092bfdc92e682",
    ).and_raise(FileNotFoundError, "Not found.")
    pagure_project.should_receive("get_file_content").with_args(
        path=".packit.yaml", ref="beaf90bcecc51968a46663f8d6f092bfdc92e682"
    ).and_return(packit_yaml)

    flexmock(RetriggerBodhiUpdateHandler).should_receive("pre_check").and_return(True)

    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(PackitAPI).should_receive("create_update").with_args(
        dist_git_branch="the_distgit_branch",
        update_type="enhancement",
        koji_builds=["123"],
    ).once()

    flexmock(Pushgateway).should_receive("push").once().and_return()

    processing_results = SteveJobs().process_message(pagure_pr_comment_added)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)
    results = run_retrigger_bodhi_update(
        event=event_dict,
        package_config=package_config,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]
