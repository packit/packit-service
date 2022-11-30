# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import pytest
from flexmock import flexmock

import packit
from packit.config.job_config import JobType, JobConfigTriggerType
from packit_service.models import JobTriggerModelType


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
    package_config = flexmock(jobs=[])
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
    )
    project = flexmock(
        namespace="mmassari",
        repo="knx-stack",
        default_branch="main",
    )
    db_trigger = flexmock(
        id=1,
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        job_trigger_model_type=JobTriggerModelType.pull_request,
    )
    return (package_config, job_config, project, db_trigger)
