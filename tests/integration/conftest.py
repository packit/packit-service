# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import pytest
from flexmock import flexmock
from github.MainClass import Github
from ogr.services.github import GithubProject
from packit.config import JobConfigTriggerType
from packit.local_project import LocalProject

from packit_service.models import (
    GitBranchModel,
    ProjectEventModel,
    ProjectReleaseModel,
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


@pytest.fixture
def mock_push_functionality(request):
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

    branch_model = (
        flexmock(GitBranchModel(name="main"))
        .should_receive("get_project_event_models")
        .and_return([flexmock(commit_sha="12345")])
        .mock()
    )
    project_event = (
        flexmock(ProjectEventModel(type=JobConfigTriggerType.commit, id=123456))
        .should_receive("get_project_event_object")
        .and_return(branch_model)
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
    flexmock(GitBranchModel).should_receive("get_or_create").with_args(
        branch_name="main",
        namespace="packit",
        repo_name="hello-world",
        project_url="https://github.com/packit/hello-world",
    ).and_return(branch_model)
    flexmock(GitBranchModel).should_receive("get_by_id").with_args(123).and_return(
        branch_model,
    )


@pytest.fixture
def mock_release_functionality(request):
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

    release_model = (
        flexmock(ProjectReleaseModel(tag_name="0.1.0"))
        .should_receive("get_project_event_models")
        .and_return([flexmock(commit_sha="12345")])
        .mock()
    )
    project_event = (
        flexmock(ProjectEventModel(type=JobConfigTriggerType.release, id=123456))
        .should_receive("get_project_event_object")
        .and_return(release_model)
        .mock()
        .should_receive("set_packages_config")
        .mock()
    )
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(ProjectEventModel).should_receive("get_by_id").with_args(
        123456,
    ).and_return(project_event)
    flexmock(ProjectEventModel).should_receive("get_or_create").and_return(
        project_event,
    )
    flexmock(ProjectReleaseModel).should_receive("get_by_id").with_args(123).and_return(
        release_model,
    )
    flexmock(ProjectReleaseModel).should_receive("get_or_create").with_args(
        tag_name="0.1.0",
        namespace="packit",
        repo_name="hello-world",
        project_url="https://github.com/packit/hello-world",
        commit_hash="0e5d8b51fd5dfa460605e1497d22a76d65c6d7fd",
    ).and_return(release_model)
