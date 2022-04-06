# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import pytest
from flexmock import flexmock

from packit.config import PackageConfig, JobConfig, JobType
from packit.config.job_config import JobMetadataConfig, JobConfigTriggerType
from packit.distgit import DistGit
from packit.local_project import LocalProject
from packit_service.config import ServiceConfig
from packit_service.worker.helpers.propose_downstream import ProposeDownstreamJobHelper


@pytest.mark.parametrize(
    "jobs,job_config_trigger_type,branches_override,branches",
    [
        pytest.param(
            [
                JobConfig(
                    type=JobType.propose_downstream,
                    trigger=JobConfigTriggerType.release,
                    metadata=JobMetadataConfig(dist_git_branches=["main", "f34"]),
                ),
            ],
            JobConfigTriggerType.release,
            None,
            {"main", "f34"},
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.propose_downstream,
                    trigger=JobConfigTriggerType.release,
                    metadata=JobMetadataConfig(dist_git_branches=["f34", "main"]),
                ),
            ],
            JobConfigTriggerType.release,
            {"main"},
            {"main"},
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.propose_downstream,
                    trigger=JobConfigTriggerType.release,
                    metadata=JobMetadataConfig(dist_git_branches=["f35", "f34"]),
                ),
            ],
            JobConfigTriggerType.release,
            {"f35"},
            {"f35"},
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.propose_downstream,
                    trigger=JobConfigTriggerType.release,
                ),
            ],
            JobConfigTriggerType.release,
            None,
            {"main"},
        ),
    ],
)
def test_branches(jobs, job_config_trigger_type, branches_override, branches):
    propose_downstream_helper = ProposeDownstreamJobHelper(
        service_config=ServiceConfig(),
        package_config=PackageConfig(jobs=jobs),
        job_config=jobs[0],
        project=flexmock(),
        metadata=flexmock(pr_id=None),
        db_trigger=flexmock(job_config_trigger_type=job_config_trigger_type),
        branches_override=branches_override,
    )

    lp = flexmock(LocalProject, refresh_the_arguments=lambda: None)
    lp.git_project = flexmock(
        default_branch="main",
    )
    lp.working_dir = ""
    flexmock(DistGit).should_receive("local_project").and_return(lp)

    assert propose_downstream_helper.branches == branches
