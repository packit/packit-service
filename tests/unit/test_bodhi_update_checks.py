# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import pytest
from flexmock import flexmock
from packit.config import (
    CommonPackageConfig,
    JobConfig,
    JobConfigTriggerType,
    JobType,
    PackageConfig,
)

from packit_service.worker.checker.bodhi import (
    HasIssueCommenterRetriggeringPermissions,
    IsAuthorAPackager,
)
from packit_service.worker.mixin import PackitAPIWithDownstreamMixin


@pytest.mark.parametrize(
    "event_type, has_write_access, result",
    [
        pytest.param(
            "pagure.pr.Comment",
            True,
            True,
        ),
        pytest.param(
            "pagure.pr.Comment",
            False,
            False,
        ),
        pytest.param(
            "AnotherEvent",
            True,
            True,
        ),
    ],
)
def test_check_has_author_write_access(
    event_type: str,
    has_write_access: bool,
    result: bool,
):
    package_config = PackageConfig(
        packages={
            "package": CommonPackageConfig(
                identifier="first",
            ),
        },
        jobs=[
            JobConfig(
                type=JobType.bodhi_update,
                trigger=JobConfigTriggerType.pull_request,
                packages={
                    "package": CommonPackageConfig(
                        identifier="first",
                    ),
                },
            ),
        ],
    )
    job_config = JobConfig(
        type=JobType.bodhi_update,
        trigger=JobConfigTriggerType.pull_request,
        packages={
            "package": CommonPackageConfig(
                identifier="first",
            ),
        },
    )
    data = {
        "event_type": event_type,
        "actor": "happy-packit-user",
        "pr_id": 123,
    }
    project = flexmock(
        has_write_access=lambda user: has_write_access,
        repo="playground-for-pencils",
    )

    checker = HasIssueCommenterRetriggeringPermissions(package_config, job_config, data)
    checker._project = project
    assert checker.pre_check() == result


@pytest.mark.parametrize(
    "author_name, is_packager, result",
    [
        pytest.param(
            "Bob",
            True,
            True,
        ),
        pytest.param(
            "Bob",
            False,
            False,
        ),
        pytest.param(
            None,
            False,
            True,
        ),
    ],
)
def test_check_is_author_a_packager(author_name: str, is_packager: bool, result: bool):
    package_config = PackageConfig(
        packages={
            "package": CommonPackageConfig(
                identifier="first",
            ),
        },
        jobs=[
            JobConfig(
                type=JobType.bodhi_update,
                trigger=JobConfigTriggerType.pull_request,
                packages={
                    "package": CommonPackageConfig(
                        identifier="first",
                    ),
                },
            ),
        ],
    )
    job_config = JobConfig(
        type=JobType.bodhi_update,
        trigger=JobConfigTriggerType.pull_request,
        packages={
            "package": CommonPackageConfig(
                identifier="first",
            ),
        },
    )
    data = {
        "event_type": "pagure.pr.Comment",
        "actor": author_name,
        "pr_id": 123,
    }
    project = flexmock(
        repo="playground-for-pencils",
    )
    flexmock(PackitAPIWithDownstreamMixin).should_receive("is_packager").and_return(
        is_packager,
    )

    checker = IsAuthorAPackager(package_config, job_config, data)
    checker._project = project
    assert checker.pre_check() == result
