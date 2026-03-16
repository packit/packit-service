# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from typing import Optional

import pytest
from flexmock import flexmock
from github.MainClass import Github
from ogr.services.github import GithubProject
from ogr.services.pagure import PagureProject
from packit.config import JobConfigTriggerType
from packit.local_project import LocalProject
from packit.utils.koji_helper import KojiHelper

from packit_service.config import ServiceConfig
from packit_service.models import (
    BodhiUpdateGroupModel,
    BodhiUpdateTargetModel,
    GitBranchModel,
    KojiBuildTargetModel,
    PipelineModel,
    ProjectEventModel,
    ProjectEventModelType,
    ProjectReleaseModel,
    PullRequestModel,
    SidetagGroupModel,
)
from packit_service.service.db_project_events import AddPullRequestEventToDb
from packit_service.worker.reporting.news import DistgitAnnouncement


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


@pytest.fixture
def bodhi_update_db_mocks():
    """Fixture factory for database model mocks used in bodhi update tests."""

    def _setup(package_name: str, project_url: str):
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
            repo_name=package_name,
            project_url=project_url,
        ).and_return(db_project_object)
        flexmock(PipelineModel).should_receive("create").and_return(run_model_flexmock)

    return _setup


@pytest.fixture
def bodhi_update_target_mocks():
    """Fixture factory for bodhi update group and target model mocks.
    Can be used for both regular and sidetag bodhi updates."""

    def _setup(target: str, nvr: str, sidetag: Optional[str] = None):
        group_model = flexmock(
            id=23,
            grouped_targets=[
                flexmock(
                    id=456,
                    target=target,
                    koji_nvrs=nvr,
                    sidetag=sidetag,
                    set_status=lambda x: None,
                    set_data=lambda x: None,
                    set_web_url=lambda x: None,
                    set_alias=lambda x: None,
                    set_update_creation_time=lambda x: None,
                ),
            ],
        )
        flexmock(BodhiUpdateGroupModel).should_receive("create").and_return(group_model)

        if sidetag:
            flexmock(BodhiUpdateTargetModel).should_receive(
                "get_last_successful_by_sidetag",
            ).with_args(sidetag).and_return(None)

        flexmock(BodhiUpdateTargetModel).should_receive(
            "get_all_successful_or_in_progress_by_nvrs",
        ).with_args(nvr).and_return(set())
        flexmock(BodhiUpdateTargetModel).should_receive("create").with_args(
            target=target,
            koji_nvrs=nvr,
            sidetag=sidetag,
            status="queued",
            bodhi_update_group=group_model,
        ).and_return()
        return group_model

    return _setup


@pytest.fixture
def sidetag_koji_mocks():
    """Fixture factory for sidetag-specific Koji mocks."""

    def _setup(
        sidetag_group_name: str,
        target: str,
        sidetag_koji_name: str,
        package_name: str,
        nvr: str,
        build_id: int,
        task_id: int,
    ):
        sidetag_model = flexmock(
            koji_name=sidetag_koji_name,
            target=target,
            sidetag_group=flexmock(name=sidetag_group_name),
        )
        sidetag_group = flexmock(name=sidetag_group_name)
        flexmock(SidetagGroupModel).should_receive("get_or_create").with_args(
            sidetag_group_name,
        ).and_return(sidetag_group)
        flexmock(sidetag_group).should_receive("get_sidetag_by_target").with_args(
            target,
        ).and_return(sidetag_model)
        flexmock(KojiHelper).should_receive("get_tag_info").with_args(
            sidetag_koji_name,
        ).and_return(flexmock())

        flexmock(KojiHelper).should_receive("get_builds_in_tag").with_args(
            sidetag_koji_name,
        ).and_return(
            [
                {
                    "name": package_name,
                    "nvr": nvr,
                    "build_id": build_id,
                    "state": 1,
                    "task_id": task_id,
                },
            ],
        )

        flexmock(KojiHelper).should_receive("get_latest_stable_nvr").with_args(
            package_name,
            target,
        ).and_return(None)

        flexmock(KojiHelper).should_receive("get_build_info").with_args(nvr).and_return(
            {
                "name": package_name,
                "nvr": nvr,
                "build_id": build_id,
                "state": 1,
                "task_id": task_id,
            }
        )

    return _setup


@pytest.fixture
def bodhi_update_pagure_project():
    """Fixture factory for pagure project mocks in bodhi update tests.
    Can be used for both regular and sidetag bodhi updates."""

    def _setup(package_name: str, project_url: str, target_branch: str, packit_yaml: str):
        pr_mock = (
            flexmock(target_branch=target_branch)
            .should_receive("comment")
            .with_args(
                "The task was accepted. You can check the recent Bodhi update submissions "
                "of Packit in [Packit dashboard](/jobs/bodhi-updates). "
                "You can also check the recent Bodhi update activity of `packit` in "
                "[the Bodhi interface](https://bodhi.fedoraproject.org/users/packit)."
                f"{DistgitAnnouncement.get_comment_footer_with_announcement_if_present()}",
            )
            .mock()
        )

        pagure_service = flexmock()
        pagure_service.should_receive("get_project").and_return(flexmock(default_branch="main"))

        pagure_project = flexmock(
            PagureProject,
            full_repo_name=f"rpms/{package_name}",
            get_web_url=lambda: project_url,
            default_branch="main",
            get_pr=lambda id: pr_mock,
        )
        pagure_project.service = pagure_service

        pagure_project.should_receive("get_files").with_args(
            ref="main",
            filter_regex=r".+\.spec$",
        ).and_return([f"{package_name}.spec"])
        pagure_project.should_receive("get_file_content").with_args(
            path=".packit.yaml",
            ref="main",
            headers=dict,
        ).and_return(packit_yaml)
        pagure_project.should_receive("get_files").with_args(
            ref="main",
            recursive=False,
        ).and_return([f"{package_name}.spec", ".packit.yaml"])

        # Mock for ServiceConfig.get_project (used by sidetag handler)
        project_mock = flexmock(
            repo=package_name,
            namespace="rpms",
            full_repo_name=f"rpms/{package_name}",
            get_pr=lambda id: pr_mock,
            is_private=lambda: False,
            default_branch="main",
            get_file_content=lambda path, ref, headers: packit_yaml,
            get_files=lambda ref, recursive=False, filter_regex=None: (
                [f"{package_name}.spec"]
                if filter_regex
                else [f"{package_name}.spec", ".packit.yaml"]
            ),
        )
        project_mock.service = pagure_service
        flexmock(ServiceConfig).should_receive("get_project").and_return(project_mock)

        return pagure_project

    return _setup
