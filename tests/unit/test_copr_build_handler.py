import pytest
from flexmock import flexmock
from packit.config import PackageConfig, JobConfig, JobType, JobTriggerType

from packit_service.worker.copr_build import CoprBuildHandler


@pytest.mark.parametrize(
    "jobs,build_targets,test_targets",
    [
        pytest.param(
            [
                JobConfig(
                    job=JobType.copr_build,
                    trigger=JobTriggerType.pull_request,
                    metadata={"targets": ["fedora-29", "fedora-31"]},
                )
            ],
            {"fedora-29-x86_64", "fedora-31-x86_64"},
            set(),
            id="build_with_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    job=JobType.copr_build,
                    trigger=JobTriggerType.pull_request,
                    metadata={},
                )
            ],
            {"fedora-30-x86_64", "fedora-31-x86_64"},
            set(),
            id="build_without_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    job=JobType.tests, trigger=JobTriggerType.pull_request, metadata={},
                )
            ],
            {"fedora-30-x86_64", "fedora-31-x86_64"},
            {"fedora-30-x86_64", "fedora-31-x86_64"},
            id="test_without_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    job=JobType.tests,
                    trigger=JobTriggerType.pull_request,
                    metadata={"targets": ["fedora-29", "fedora-31"]},
                )
            ],
            {"fedora-29-x86_64", "fedora-31-x86_64"},
            {"fedora-29-x86_64", "fedora-31-x86_64"},
            id="test_with_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    job=JobType.copr_build,
                    trigger=JobTriggerType.pull_request,
                    metadata={},
                ),
                JobConfig(
                    job=JobType.tests, trigger=JobTriggerType.pull_request, metadata={},
                ),
            ],
            {"fedora-30-x86_64", "fedora-31-x86_64"},
            {"fedora-30-x86_64", "fedora-31-x86_64"},
            id="build_without_target&test_without_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    job=JobType.copr_build,
                    trigger=JobTriggerType.pull_request,
                    metadata={"targets": ["fedora-29", "fedora-31"]},
                ),
                JobConfig(
                    job=JobType.tests, trigger=JobTriggerType.pull_request, metadata={},
                ),
            ],
            {"fedora-29-x86_64", "fedora-31-x86_64"},
            {"fedora-29-x86_64", "fedora-31-x86_64"},
            id="build_with_target&test_without_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    job=JobType.copr_build,
                    trigger=JobTriggerType.pull_request,
                    metadata={},
                ),
                JobConfig(
                    job=JobType.tests,
                    trigger=JobTriggerType.pull_request,
                    metadata={"targets": ["fedora-29", "fedora-31"]},
                ),
            ],
            {"fedora-29-x86_64", "fedora-31-x86_64"},
            {"fedora-29-x86_64", "fedora-31-x86_64"},
            id="build_without_target&test_with_targets",
        ),
    ],
)
def test_targets(jobs, build_targets, test_targets):
    copr_build_handler = CoprBuildHandler(
        config=flexmock(),
        package_config=PackageConfig(jobs=jobs),
        project=flexmock(),
        event=flexmock(),
    )

    assert set(copr_build_handler.build_chroots) == build_targets
    assert set(copr_build_handler.tests_chroots) == test_targets
