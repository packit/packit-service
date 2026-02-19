# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import pytest
from flexmock import flexmock
from github.MainClass import Github
from ogr.services.github import GithubProject
from packit.config import JobConfigTriggerType
from packit.local_project import LocalProject

from packit_service.models import (
    ProjectEventModel,
    PullRequestModel,
)


@pytest.fixture
def mock_pr_functionality(request):
    packit_yaml = "{'specfile_path': 'the-specfile.spec', 'jobs':" + str(request.param) + "}"
    flexmock(
        GithubProject,
        full_repo_name="packit/hello-world",
        get_file_content=lambda path, ref, headers: packit_yaml,
        get_files=lambda ref, filter_regex: ["the-specfile.spec"],
        get_web_url=lambda: "https://github.com/the-namespace/the-repo",
        get_pr=lambda pr_id: flexmock(head_commit="12345"),
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)

    pr_model = (
        flexmock(PullRequestModel(pr_id=123))
        .should_receive("get_project_event_models")
        .and_return([flexmock(commit_sha="12345")])
        .mock()
    )
    project_event = (
        flexmock(ProjectEventModel(type=JobConfigTriggerType.pull_request, id=123456))
        .should_receive("get_project_event_object")
        .and_return(pr_model)
        .mock()
        .should_receive("set_packages_config")
        .mock()
    )
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(ProjectEventModel).should_receive("get_or_create").and_return(
        project_event,
    )
    flexmock(ProjectEventModel).should_receive("get_by_id").with_args(
        123456,
    ).and_return(project_event)
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=123,
        namespace="packit",
        repo_name="hello-world",
        project_url="https://github.com/packit/hello-world",
    ).and_return(pr_model)
    flexmock(PullRequestModel).should_receive("get_by_id").with_args(123).and_return(
        pr_model,
    )
