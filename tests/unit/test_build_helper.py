import pytest
from flexmock import flexmock
from packit.config import PackageConfig, JobConfig, JobType, JobConfigTriggerType

from packit_service.service.events import TheJobTriggerType
from packit_service.worker.build.copr_build import CoprBuildJobHelper


@pytest.mark.parametrize(
    "jobs,trigger,build_targets,test_targets",
    [
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata={"targets": ["fedora-29", "fedora-31"]},
                )
            ],
            TheJobTriggerType.pull_request,
            {"fedora-29-x86_64", "fedora-31-x86_64"},
            set(),
            id="build_with_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata={"targets": ["fedora-29", "fedora-31"]},
                )
            ],
            TheJobTriggerType.pr_comment,
            {"fedora-29-x86_64", "fedora-31-x86_64"},
            set(),
            id="build_with_targets&pr_comment",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    metadata={"targets": ["fedora-29", "fedora-31"]},
                )
            ],
            TheJobTriggerType.release,
            {"fedora-29-x86_64", "fedora-31-x86_64"},
            set(),
            id="build_with_targets&release",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    metadata={"targets": ["fedora-29", "fedora-31"]},
                )
            ],
            TheJobTriggerType.push,
            {"fedora-29-x86_64", "fedora-31-x86_64"},
            set(),
            id="build_with_targets&push",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata={"targets": ["fedora-29", "fedora-31"]},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    metadata={"targets": ["different", "os", "target"]},
                ),
            ],
            TheJobTriggerType.pull_request,
            {"fedora-29-x86_64", "fedora-31-x86_64"},
            set(),
            id="build_with_targets&pull_request_with_pr_and_push_defined",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata={"targets": ["fedora-29", "fedora-31"]},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    metadata={"targets": ["different", "os", "target"]},
                ),
            ],
            TheJobTriggerType.pr_comment,
            {"fedora-29-x86_64", "fedora-31-x86_64"},
            set(),
            id="build_with_targets&pr_comment_with_pr_and_push_defined",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata={"targets": ["different", "os", "target"]},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    metadata={"targets": ["fedora-29", "fedora-31"]},
                ),
            ],
            TheJobTriggerType.push,
            {"fedora-29-x86_64", "fedora-31-x86_64"},
            set(),
            id="build_with_targets&push_with_pr_and_push_defined",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata={},
                )
            ],
            TheJobTriggerType.pull_request,
            {"fedora-30-x86_64", "fedora-31-x86_64"},
            set(),
            id="build_without_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata={},
                )
            ],
            TheJobTriggerType.pull_request,
            {"fedora-30-x86_64", "fedora-31-x86_64"},
            {"fedora-30-x86_64", "fedora-31-x86_64"},
            id="test_without_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata={"targets": ["fedora-29", "fedora-31"]},
                )
            ],
            TheJobTriggerType.pull_request,
            {"fedora-29-x86_64", "fedora-31-x86_64"},
            {"fedora-29-x86_64", "fedora-31-x86_64"},
            id="test_with_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata={},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata={},
                ),
            ],
            TheJobTriggerType.pull_request,
            {"fedora-30-x86_64", "fedora-31-x86_64"},
            {"fedora-30-x86_64", "fedora-31-x86_64"},
            id="build_without_target&test_without_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata={"targets": ["fedora-29", "fedora-31"]},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata={},
                ),
            ],
            TheJobTriggerType.pull_request,
            {"fedora-29-x86_64", "fedora-31-x86_64"},
            {"fedora-29-x86_64", "fedora-31-x86_64"},
            id="build_with_target&test_without_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata={},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata={"targets": ["fedora-29", "fedora-31"]},
                ),
            ],
            TheJobTriggerType.pull_request,
            {"fedora-29-x86_64", "fedora-31-x86_64"},
            {"fedora-29-x86_64", "fedora-31-x86_64"},
            id="build_without_target&test_with_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata={},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata={"targets": "fedora-29"},
                ),
            ],
            TheJobTriggerType.pull_request,
            {"fedora-29-x86_64"},
            {"fedora-29-x86_64"},
            id="build_without_target&test_with_one_str_target",
        ),
    ],
)
def test_targets(jobs, trigger, build_targets, test_targets):
    copr_build_handler = CoprBuildJobHelper(
        config=flexmock(),
        package_config=PackageConfig(jobs=jobs),
        project=flexmock(),
        event=flexmock(trigger=trigger),
    )

    assert copr_build_handler.package_config.jobs
    assert [j.type for j in copr_build_handler.package_config.jobs]

    assert set(copr_build_handler.build_chroots) == build_targets
    assert set(copr_build_handler.tests_chroots) == test_targets
