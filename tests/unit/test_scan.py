# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import pytest
from flexmock import flexmock

from packit.api import PackitAPI
from packit.config import JobType, JobConfigTriggerType
from packit_service.models import (
    CoprBuildTargetModel,
    ProjectEventModelType,
    BuildStatus,
)
from packit_service.worker.events import AbstractCoprBuildEvent
from packit_service.worker.handlers import copr
from packit_service.worker.handlers.copr import ScanHelper
from packit_service.worker.helpers.build import CoprBuildJobHelper


@pytest.mark.parametrize(
    "build_models",
    [
        [("abcdef", [flexmock(get_srpm_build=lambda: flexmock(url="base-srpm-url"))])],
        [
            ("abcdef", []),
            (
                "fedcba",
                [flexmock(get_srpm_build=lambda: flexmock(url="base-srpm-url"))],
            ),
        ],
    ],
)
def test_handle_scan(build_models):
    srpm_mock = flexmock(url="https://some-url/my-srpm.src.rpm")
    flexmock(AbstractCoprBuildEvent).should_receive("from_event_dict").and_return(
        flexmock(chroot="fedora-rawhide-x86_64", build_id="123", pr_id=12)
    )
    flexmock(copr).should_receive("download_file").twice().and_return(True)

    for commit_sha, models in build_models:
        flexmock(CoprBuildTargetModel).should_receive("get_all_by").with_args(
            commit_sha=commit_sha,
            project_name="commit-project",
            owner="user-123",
            target="fedora-rawhide-x86_64",
            status=BuildStatus.success,
        ).and_return(models).once()

    flexmock(PackitAPI).should_receive("run_osh_build").once().and_return(
        'some\nmultiline\noutput\n{"id": 123}\nand\nmore\n{"url": "scan-url"}\n'
    )

    flexmock(CoprBuildJobHelper).should_receive("_report")
    package_config = flexmock(
        get_job_views=lambda: [
            flexmock(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.commit,
                branch="main",
                project="commit-project",
                owner="user-123",
            )
        ]
    )

    project = flexmock(
        get_pr=lambda pr_id: flexmock(
            target_branch="main", target_branch_head_commit="abcdef"
        ),
        get_commits=lambda ref: ["abcdef", "fedcba"],
    )

    ScanHelper(
        build=flexmock(
            id=1,
            get_srpm_build=lambda: srpm_mock,
            target="fedora-rawhide-x86_64",
            get_project_event_model=lambda: flexmock(
                type=ProjectEventModelType.pull_request,
                get_project_event_object=lambda: flexmock(),
            ),
        ),
        copr_build_helper=CoprBuildJobHelper(
            service_config=flexmock(),
            package_config=package_config,
            project=project,
            metadata=flexmock(pr_id=12),
            db_project_event=flexmock(get_project_event_object=lambda: None),
            job_config=flexmock(),
        ),
    ).handle_scan()
