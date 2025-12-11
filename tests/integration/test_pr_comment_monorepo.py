# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json

import pytest
from celery.canvas import group as celery_group
from flexmock import flexmock
from github.MainClass import Github
from ogr.services.github import GithubProject
from packit.config import JobConfigTriggerType
from packit.copr_helper import CoprHelper
from packit.local_project import LocalProject, LocalProjectBuilder

from packit_service.constants import (
    COMMENT_REACTION,
    TASK_ACCEPTED,
)
from packit_service.models import (
    ProjectEventModel,
    ProjectEventModelType,
    PullRequestModel,
)
from packit_service.worker.allowlist import Allowlist
from packit_service.worker.helpers.build.copr_build import CoprBuildJobHelper
from packit_service.worker.helpers.testing_farm import TestingFarmJobHelper
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.reporting import BaseCommitStatus
from packit_service.worker.tasks import run_testing_farm_handler
from tests.spellbook import DATA_DIR, first_dict_value, get_parameters_from_results


@pytest.fixture(scope="module")
def pr_build_comment_monorepo_event():
    return json.loads(
        (DATA_DIR / "webhooks" / "github" / "pr_comment_monorepo_build.json").read_text(),
    )


@pytest.fixture
def mock_pr_comment_monorepo_functionality(request):
    packit_yaml = (
        "{'packages': "
        "  {'hello': {'specfile_path': 'hello.spec'}, "
        "   'world': {'specfile_path': 'world.spec'} }, "
        " 'jobs': " + str(request.param) + "}"
    )

    flexmock(
        GithubProject,
        full_repo_name="packit/hello-world",
        get_file_content=lambda path, ref, headers: packit_yaml,
        get_files=lambda ref, filter_regex: ["hello.spec", "world.spec"],
        get_web_url=lambda: "https://github.com/the-namespace/the-repo",
    )
    flexmock(
        GithubProject,
        get_files=lambda ref, recursive: ["foo.spec", "packit.yaml"],
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)
    db_project_object = flexmock(
        id=1418,
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
        event_id=1418,
        commit_sha="12345",
    ).and_return(db_project_event)
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=1418,
        namespace="packit",
        repo_name="hello-world",
        project_url="https://github.com/packit/hello-world",
    ).and_return(db_project_object)
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(LocalProjectBuilder, _refresh_the_state=lambda *args: flexmock())
    flexmock(Allowlist, check_and_report=True)


@pytest.mark.parametrize(
    "mock_pr_comment_monorepo_functionality",
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
def test_pr_comment_monorepo_build_build_and_test_handler(
    mock_pr_comment_monorepo_functionality,
    pr_build_comment_monorepo_event,
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

    processing_results = SteveJobs().process_message(pr_build_comment_monorepo_event)
    assert len(processing_results) == 2

    copr_build_job = [item for item in processing_results if item["details"]["job"] == "copr_build"]
    assert copr_build_job

    test_job = [item for item in processing_results if item["details"]["job"] == "tests"]
    assert test_job

    event_dict, _, job_config, package_config = get_parameters_from_results(test_job)
    assert json.dumps(event_dict)
    results = run_testing_farm_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert first_dict_value(results["job"])["success"]
    assert "already handled" in first_dict_value(results["job"])["details"]["msg"]
