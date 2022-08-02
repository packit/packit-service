import pytest

from flexmock import flexmock

from packit_service.worker.checker.bodhi import HasAuthorWriteAccess, IsAuthorAPackager
from packit_service.worker.mixin import PackitAPIWithDownstreamMixin
from packit.config import (
    JobConfig,
    JobConfigTriggerType,
    JobType,
    PackageConfig,
    CommonPackageConfig,
)


@pytest.mark.parametrize(
    "event_type, has_write_access, result",
    [
        pytest.param(
            "PullRequestCommentPagureEvent",
            True,
            True,
        ),
        pytest.param(
            "PullRequestCommentPagureEvent",
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
    event_type: str, has_write_access: bool, result: bool
):
    package_config = PackageConfig(
        packages={
            "package": CommonPackageConfig(
                identifier="first",
            )
        },
        jobs=[
            JobConfig(
                type=JobType.bodhi_update,
                trigger=JobConfigTriggerType.pull_request,
                packages={
                    "package": CommonPackageConfig(
                        identifier="first",
                    )
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
            )
        },
    )
    data = dict(
        event_type=event_type,
        actor="happy-packit-user",
        pr_id=123,
    )
    project = flexmock(
        has_write_access=lambda user: has_write_access,
        repo="playground-for-pencils",
    )

    checker = HasAuthorWriteAccess(package_config, job_config, data)
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
            )
        },
        jobs=[
            JobConfig(
                type=JobType.bodhi_update,
                trigger=JobConfigTriggerType.pull_request,
                packages={
                    "package": CommonPackageConfig(
                        identifier="first",
                    )
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
            )
        },
    )
    data = dict(
        event_type="PullRequestCommentPagureEvent",
        actor=author_name,
        pr_id=123,
    )
    project = flexmock(
        repo="playground-for-pencils",
    )
    flexmock(PackitAPIWithDownstreamMixin).should_receive("is_packager").and_return(
        is_packager
    )

    checker = IsAuthorAPackager(package_config, job_config, data)
    checker._project = project
    assert checker.pre_check() == result
