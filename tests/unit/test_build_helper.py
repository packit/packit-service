import pytest
from flexmock import flexmock

from packit.config import PackageConfig, JobConfig, JobType, JobConfigTriggerType
from packit.config.job_config import JobMetadataConfig
from packit_service.service.events import TheJobTriggerType
from packit_service.worker.build.copr_build import CoprBuildJobHelper


@pytest.mark.parametrize(
    "jobs,trigger,job_config_trigger_type,build_targets,test_targets",
    [
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(targets=["fedora-29", "fedora-31"]),
                )
            ],
            TheJobTriggerType.pull_request,
            JobConfigTriggerType.pull_request,
            {"fedora-29-x86_64", "fedora-31-x86_64"},
            set(),
            id="build_with_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(targets=["fedora-29", "fedora-31"]),
                )
            ],
            TheJobTriggerType.pr_comment,
            JobConfigTriggerType.pull_request,
            {"fedora-29-x86_64", "fedora-31-x86_64"},
            set(),
            id="build_with_targets&pr_comment",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    metadata=JobMetadataConfig(targets=["fedora-29", "fedora-31"]),
                )
            ],
            TheJobTriggerType.release,
            JobConfigTriggerType.release,
            {"fedora-29-x86_64", "fedora-31-x86_64"},
            set(),
            id="build_with_targets&release",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    metadata=JobMetadataConfig(targets=["fedora-29", "fedora-31"]),
                )
            ],
            TheJobTriggerType.push,
            JobConfigTriggerType.commit,
            {"fedora-29-x86_64", "fedora-31-x86_64"},
            set(),
            id="build_with_targets&push",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(targets=["fedora-29", "fedora-31"]),
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    metadata=JobMetadataConfig(targets=["different", "os", "target"]),
                ),
            ],
            TheJobTriggerType.pull_request,
            JobConfigTriggerType.pull_request,
            {"fedora-29-x86_64", "fedora-31-x86_64"},
            set(),
            id="build_with_targets&pull_request_with_pr_and_push_defined",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(targets=["fedora-29", "fedora-31"]),
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    metadata=JobMetadataConfig(targets=["different", "os", "target"]),
                ),
            ],
            TheJobTriggerType.pr_comment,
            JobConfigTriggerType.pull_request,
            {"fedora-29-x86_64", "fedora-31-x86_64"},
            set(),
            id="build_with_targets&pr_comment_with_pr_and_push_defined",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(targets=["different", "os", "target"]),
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    metadata=JobMetadataConfig(targets=["fedora-29", "fedora-31"]),
                ),
            ],
            TheJobTriggerType.push,
            JobConfigTriggerType.commit,
            {"fedora-29-x86_64", "fedora-31-x86_64"},
            set(),
            id="build_with_targets&push_with_pr_and_push_defined",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build, trigger=JobConfigTriggerType.pull_request,
                )
            ],
            TheJobTriggerType.pull_request,
            JobConfigTriggerType.pull_request,
            {"fedora-30-x86_64", "fedora-31-x86_64"},
            set(),
            id="build_without_targets",
        ),
        pytest.param(
            [JobConfig(type=JobType.tests, trigger=JobConfigTriggerType.pull_request,)],
            TheJobTriggerType.pull_request,
            JobConfigTriggerType.pull_request,
            {"fedora-30-x86_64", "fedora-31-x86_64"},
            {"fedora-30-x86_64", "fedora-31-x86_64"},
            id="test_without_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(targets=["fedora-29", "fedora-31"]),
                )
            ],
            TheJobTriggerType.pull_request,
            JobConfigTriggerType.pull_request,
            {"fedora-29-x86_64", "fedora-31-x86_64"},
            {"fedora-29-x86_64", "fedora-31-x86_64"},
            id="test_with_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build, trigger=JobConfigTriggerType.pull_request,
                ),
                JobConfig(
                    type=JobType.tests, trigger=JobConfigTriggerType.pull_request,
                ),
            ],
            TheJobTriggerType.pull_request,
            JobConfigTriggerType.pull_request,
            {"fedora-30-x86_64", "fedora-31-x86_64"},
            {"fedora-30-x86_64", "fedora-31-x86_64"},
            id="build_without_target&test_without_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(targets=["fedora-29", "fedora-31"]),
                ),
                JobConfig(
                    type=JobType.tests, trigger=JobConfigTriggerType.pull_request,
                ),
            ],
            TheJobTriggerType.pull_request,
            JobConfigTriggerType.pull_request,
            {"fedora-29-x86_64", "fedora-31-x86_64"},
            {"fedora-29-x86_64", "fedora-31-x86_64"},
            id="build_with_target&test_without_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build, trigger=JobConfigTriggerType.pull_request,
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(targets=["fedora-29", "fedora-31"]),
                ),
            ],
            TheJobTriggerType.pull_request,
            JobConfigTriggerType.pull_request,
            {"fedora-29-x86_64", "fedora-31-x86_64"},
            {"fedora-29-x86_64", "fedora-31-x86_64"},
            id="build_without_target&test_with_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build, trigger=JobConfigTriggerType.pull_request,
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(targets=["fedora-29"]),
                ),
            ],
            TheJobTriggerType.pull_request,
            JobConfigTriggerType.pull_request,
            {"fedora-29-x86_64"},
            {"fedora-29-x86_64"},
            id="build_without_target&test_with_one_str_target",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.build, trigger=JobConfigTriggerType.pull_request,
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(targets=["fedora-29"]),
                ),
            ],
            TheJobTriggerType.pull_request,
            JobConfigTriggerType.pull_request,
            {"fedora-29-x86_64"},
            {"fedora-29-x86_64"},
            id="build_with_mixed_build_alias",
        ),
    ],
)
def test_targets(jobs, trigger, job_config_trigger_type, build_targets, test_targets):
    copr_build_handler = CoprBuildJobHelper(
        config=flexmock(),
        package_config=PackageConfig(jobs=jobs),
        project=flexmock(),
        event=flexmock(
            trigger=trigger,
            db_trigger=flexmock(job_config_trigger_type=job_config_trigger_type),
        ),
    )

    assert copr_build_handler.package_config.jobs
    assert [j.type for j in copr_build_handler.package_config.jobs]

    assert set(copr_build_handler.build_chroots) == build_targets
    assert set(copr_build_handler.tests_chroots) == test_targets
