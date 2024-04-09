# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import pytest
from flexmock import flexmock

import packit
from packit.config.job_config import JobType, JobConfigTriggerType
from packit_service.models import (
    ProjectEventModelType,
    PullRequestModel,
    ProjectEventModel,
)


@pytest.fixture()
def mock_get_aliases():
    mock_aliases_module = flexmock(packit.config.aliases)
    mock_aliases_module.should_receive("get_aliases").and_return(
        {
            "fedora-all": ["fedora-31", "fedora-32", "fedora-33", "fedora-rawhide"],
            "fedora-stable": ["fedora-31", "fedora-32"],
            "fedora-development": ["fedora-33", "fedora-rawhide"],
            "epel-all": ["epel-6", "epel-7", "epel-8"],
        }
    )


@pytest.fixture
def fake_package_config_job_config_project_db_trigger():
    package_config = flexmock(packages={}, jobs=[])
    job_config = flexmock(
        type=JobType.vm_image_build,
        trigger=JobConfigTriggerType.pull_request,
        copr_chroot="fedora-36-x86_64",
        owner="mmassari",
        project="knx-stack",
        image_customizations={"packages": ["python-knx-stack"]},
        image_distribution="fedora-36",
        image_request={
            "architecture": "x86_64",
            "image_type": "aws",
            "upload_request": {"type": "aws", "options": {}},
        },
        identifier="",
        packages={
            "knx-stack": flexmock(
                specfile_path="knx-stack.spec", copr_chroot="fedora-36-x86_64"
            )
        },
        package=None,
    )
    project = flexmock(
        namespace="mmassari",
        repo="knx-stack",
        default_branch="main",
    )
    db_project_object = flexmock(
        id=1,
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        project_event_model_type=ProjectEventModelType.pull_request,
        commit_sha="123456",
    )
    return (package_config, job_config, project, db_project_object)


@pytest.fixture
def add_pull_request_event_with_empty_sha():
    db_project_object = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        id=123,
        project_event_model_type=ProjectEventModelType.pull_request,
        commit_sha="",
    )
    flexmock(PullRequestModel).should_receive("get_or_create").and_return(
        db_project_object
    )
    db_project_event = (
        flexmock(id=2, type=ProjectEventModelType.pull_request, commit_sha="")
        .should_receive("get_project_event_object")
        .and_return(db_project_object)
        .mock()
    )

    flexmock(ProjectEventModel).should_receive("get_or_create").with_args(
        type=ProjectEventModelType.pull_request,
        event_id=123,
        commit_sha="",
        packages_config=dict,
    ).and_return(db_project_event)
    yield db_project_object, db_project_event


@pytest.fixture
def add_pull_request_event_with_sha_528b80():
    db_project_object = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        pr_id=123,
        project_event_model_type=ProjectEventModelType.pull_request,
        commit_sha="528b803be6f93e19ca4130bf4976f2800a3004c4",
    )
    db_project_event = (
        flexmock(
            id=2,
            type=ProjectEventModelType.pull_request,
            commit_sha="528b803be6f93e19ca4130bf4976f2800a3004c4",
        )
        .should_receive("get_project_event_object")
        .and_return(db_project_object)
        .mock()
    )
    flexmock(ProjectEventModel).should_receive("get_or_create").with_args(
        type=ProjectEventModelType.pull_request,
        event_id=123,
        commit_sha="528b803be6f93e19ca4130bf4976f2800a3004c4",
    ).and_return(db_project_object)
    yield db_project_object, db_project_event
