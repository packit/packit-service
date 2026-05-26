# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import pytest
from flexmock import flexmock
from packit.actions import ActionName
from packit.config import CommonPackageConfig, JobConfig, JobType, PackageConfig
from packit.config.job_config import JobConfigTriggerType
from packit.sync import SyncFilesItem

from packit_service.config import ServiceConfig
from packit_service.worker.helpers.sync_release.propose_downstream import (
    ProposeDownstreamJobHelper,
)


@pytest.mark.parametrize(
    "jobs,job_config_trigger_type,branches_override,branches,ff_branches",
    [
        pytest.param(
            [
                JobConfig(
                    type=JobType.propose_downstream,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            dist_git_branches=["main", "f34"],
                        ),
                    },
                ),
            ],
            JobConfigTriggerType.release,
            None,
            {"main", "f34"},
            {"main": set(), "f34": set()},
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.propose_downstream,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            dist_git_branches=["f34", "main"],
                        ),
                    },
                ),
            ],
            JobConfigTriggerType.release,
            {"main"},
            {"main"},
            {"main": set()},
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.propose_downstream,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            dist_git_branches=["f35", "f34"],
                        ),
                    },
                ),
            ],
            JobConfigTriggerType.release,
            {"f35"},
            {"f35"},
            {"f35": set()},
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.propose_downstream,
                    trigger=JobConfigTriggerType.release,
                    packages={"packages": CommonPackageConfig()},
                ),
            ],
            JobConfigTriggerType.release,
            None,
            {"main"},
            {"main": set()},
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.propose_downstream,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            dist_git_branches={
                                "rawhide": {"fast_forward_merge_into": ["f33"]},
                                "f35": {},
                                "f34": {},
                            },
                        ),
                    },
                ),
            ],
            JobConfigTriggerType.release,
            None,
            {"main", "f35", "f34"},
            {"main": {"f33"}, "f35": set(), "f34": set()},
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.propose_downstream,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            # no sense but possible!
                            dist_git_branches={
                                "fedora-branched": {
                                    "fast_forward_merge_into": ["fedora-stable"],
                                },
                            },
                        ),
                    },
                ),
            ],
            JobConfigTriggerType.release,
            None,
            {"f39", "f40"},
            {"f39": {"f39", "f40"}, "f40": {"f39", "f40"}},
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.propose_downstream,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            dist_git_branches={
                                "f41": {"fast_forward_merge_into": ["f40", "f39"]},
                                "f38": {"fast_forward_merge_into": ["f37"]},
                            },
                        ),
                    },
                ),
            ],
            JobConfigTriggerType.release,
            {"f41"},
            {"f41"},
            {"f41": {"f40", "f39"}},
        ),
    ],
)
def test_branches(
    mock_get_fast_forward_aliases,
    jobs,
    job_config_trigger_type,
    branches_override,
    branches,
    ff_branches,
):
    project = flexmock(
        default_branch="main",
    )
    flexmock(ServiceConfig, get_project=lambda url: project)
    propose_downstream_helper = ProposeDownstreamJobHelper(
        service_config=ServiceConfig(),
        package_config=PackageConfig(
            jobs=jobs,
            packages={"package": CommonPackageConfig()},
        ),
        job_config=jobs[0],
        project=flexmock(),
        metadata=flexmock(pr_id=None),
        db_project_event=flexmock()
        .should_receive("get_project_event_object")
        .and_return(flexmock(job_config_trigger_type=job_config_trigger_type))
        .mock(),
        branches_override=branches_override,
    )
    assert propose_downstream_helper.branches == branches
    for source_branch in branches:
        assert (
            propose_downstream_helper.get_fast_forward_merge_branches_for(source_branch)
            == ff_branches[source_branch]
        )


def test_propose_downstream_tracks_extra_source_in_dist_git():
    """
    https://github.com/packit/packit/issues/2365

    The documented workaround for tracking a local ``Source`` file in
    dist-git instead of the lookaside cache is to combine ``files_to_sync``
    with a ``post-modifications`` action that stages the file in dist-git's
    Git index. Make sure the configuration survives through to the
    ``propose_downstream`` helper used to drive the job.
    """
    add_to_index = "bash -c 'cd \"$PACKIT_DOWNSTREAM_REPO\" && git add pkg.service'"
    common = CommonPackageConfig(
        specfile_path="pkg.spec",
        downstream_package_name="pkg",
        files_to_sync=[
            SyncFilesItem(src=["pkg.spec"], dest="pkg.spec"),
            SyncFilesItem(src=["pkg.service"], dest="pkg.service"),
        ],
        actions={ActionName.post_modifications: [add_to_index]},
    )
    job = JobConfig(
        type=JobType.propose_downstream,
        trigger=JobConfigTriggerType.release,
        packages={"pkg": common},
    )

    flexmock(ServiceConfig, get_project=lambda url: flexmock(default_branch="main"))
    helper = ProposeDownstreamJobHelper(
        service_config=ServiceConfig(),
        package_config=PackageConfig(jobs=[job], packages={"pkg": common}),
        job_config=job,
        project=flexmock(),
        metadata=flexmock(pr_id=None),
        db_project_event=flexmock()
        .should_receive("get_project_event_object")
        .and_return(flexmock(job_config_trigger_type=JobConfigTriggerType.release))
        .mock(),
    )

    # The ``post-modifications`` action with the ``git add`` command is
    # what makes Packit treat the file as VCS-tracked and skip the
    # lookaside cache upload.
    actions = helper.job_config.actions
    assert ActionName.post_modifications in actions
    assert any("git add pkg.service" in cmd for cmd in actions[ActionName.post_modifications])

    # ``pkg.service`` is listed in ``files_to_sync`` so Packit copies it
    # into dist-git in the first place.
    synced = {item.dest for item in helper.package_config.files_to_sync}
    assert "pkg.service" in synced
