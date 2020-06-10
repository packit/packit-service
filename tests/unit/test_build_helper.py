import pytest
from flexmock import flexmock

from packit.config import PackageConfig, JobConfig, JobType, JobConfigTriggerType
from packit.config.aliases import ALIASES
from packit.config.job_config import JobMetadataConfig
from packit_service.service.events import TheJobTriggerType
from packit_service.worker.build.copr_build import CoprBuildJobHelper
from packit_service.worker.build.koji_build import KojiBuildJobHelper

STABLE_VERSIONS = ALIASES["fedora-stable"]
STABLE_CHROOTS = {f"{version}-x86_64" for version in STABLE_VERSIONS}
ONE_CHROOT_SET = {list(STABLE_CHROOTS)[0]}
STABLE_KOJI_TARGETS = {f"f{version[-2:]}" for version in STABLE_VERSIONS}
ONE_KOJI_TARGET_SET = {list(STABLE_KOJI_TARGETS)[0]}


@pytest.mark.parametrize(
    "jobs,trigger,job_config_trigger_type,build_chroots,test_chroots",
    [
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(targets=STABLE_VERSIONS),
                )
            ],
            TheJobTriggerType.pull_request,
            JobConfigTriggerType.pull_request,
            STABLE_CHROOTS,
            set(),
            id="build_with_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(targets=STABLE_VERSIONS),
                )
            ],
            TheJobTriggerType.pr_comment,
            JobConfigTriggerType.pull_request,
            STABLE_CHROOTS,
            set(),
            id="build_with_targets&pr_comment",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    metadata=JobMetadataConfig(targets=STABLE_VERSIONS),
                )
            ],
            TheJobTriggerType.release,
            JobConfigTriggerType.release,
            STABLE_CHROOTS,
            set(),
            id="build_with_targets&release",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    metadata=JobMetadataConfig(targets=STABLE_VERSIONS),
                )
            ],
            TheJobTriggerType.push,
            JobConfigTriggerType.commit,
            STABLE_CHROOTS,
            set(),
            id="build_with_targets&push",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(targets=STABLE_VERSIONS),
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    metadata=JobMetadataConfig(targets=["different", "os", "target"]),
                ),
            ],
            TheJobTriggerType.pull_request,
            JobConfigTriggerType.pull_request,
            STABLE_CHROOTS,
            set(),
            id="build_with_targets&pull_request_with_pr_and_push_defined",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(targets=STABLE_VERSIONS),
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    metadata=JobMetadataConfig(targets=["different", "os", "target"]),
                ),
            ],
            TheJobTriggerType.pr_comment,
            JobConfigTriggerType.pull_request,
            STABLE_CHROOTS,
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
                    metadata=JobMetadataConfig(targets=STABLE_VERSIONS),
                ),
            ],
            TheJobTriggerType.push,
            JobConfigTriggerType.commit,
            STABLE_CHROOTS,
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
            STABLE_CHROOTS,
            set(),
            id="build_without_targets",
        ),
        pytest.param(
            [JobConfig(type=JobType.tests, trigger=JobConfigTriggerType.pull_request,)],
            TheJobTriggerType.pull_request,
            JobConfigTriggerType.pull_request,
            STABLE_CHROOTS,
            STABLE_CHROOTS,
            id="test_without_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(targets=STABLE_VERSIONS),
                )
            ],
            TheJobTriggerType.pull_request,
            JobConfigTriggerType.pull_request,
            STABLE_CHROOTS,
            STABLE_CHROOTS,
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
            STABLE_CHROOTS,
            STABLE_CHROOTS,
            id="build_without_target&test_without_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(targets=STABLE_VERSIONS),
                ),
                JobConfig(
                    type=JobType.tests, trigger=JobConfigTriggerType.pull_request,
                ),
            ],
            TheJobTriggerType.pull_request,
            JobConfigTriggerType.pull_request,
            STABLE_CHROOTS,
            STABLE_CHROOTS,
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
                    metadata=JobMetadataConfig(targets=STABLE_VERSIONS),
                ),
            ],
            TheJobTriggerType.pull_request,
            JobConfigTriggerType.pull_request,
            STABLE_CHROOTS,
            STABLE_CHROOTS,
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
                    metadata=JobMetadataConfig(targets=list(ONE_CHROOT_SET)),
                ),
            ],
            TheJobTriggerType.pull_request,
            JobConfigTriggerType.pull_request,
            ONE_CHROOT_SET,
            ONE_CHROOT_SET,
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
                    metadata=JobMetadataConfig(targets=list(ONE_CHROOT_SET)),
                ),
            ],
            TheJobTriggerType.pull_request,
            JobConfigTriggerType.pull_request,
            ONE_CHROOT_SET,
            ONE_CHROOT_SET,
            id="build_with_mixed_build_alias",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.production_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(targets=STABLE_VERSIONS),
                )
            ],
            TheJobTriggerType.pull_request,
            JobConfigTriggerType.pull_request,
            STABLE_CHROOTS,
            set(),
            id="koji_build_with_targets_for_pr",
        ),
    ],
)
def test_targets(jobs, trigger, job_config_trigger_type, build_chroots, test_chroots):
    copr_build_handler = CoprBuildJobHelper(
        config=flexmock(),
        package_config=PackageConfig(jobs=jobs),
        project=flexmock(),
        event={"trigger": trigger, "pr_id": None},
        db_trigger=flexmock(job_config_trigger_type=job_config_trigger_type),
    )

    assert copr_build_handler.package_config.jobs
    assert [j.type for j in copr_build_handler.package_config.jobs]

    assert copr_build_handler.build_targets == build_chroots
    assert copr_build_handler.tests_targets == test_chroots


@pytest.mark.parametrize(
    "jobs,trigger,job_config_trigger_type,build_targets,koji_targets",
    [
        pytest.param(
            [
                JobConfig(
                    type=JobType.production_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(targets=STABLE_VERSIONS),
                )
            ],
            TheJobTriggerType.pull_request,
            JobConfigTriggerType.pull_request,
            set(STABLE_VERSIONS),
            STABLE_KOJI_TARGETS,
            id="koji_build_with_targets_for_pr",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.production_build,
                    trigger=JobConfigTriggerType.commit,
                    metadata=JobMetadataConfig(
                        targets=STABLE_VERSIONS, branch="build-branch"
                    ),
                )
            ],
            TheJobTriggerType.push,
            JobConfigTriggerType.commit,
            set(STABLE_VERSIONS),
            STABLE_KOJI_TARGETS,
            id="koji_build_with_targets_for_commit",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.production_build,
                    trigger=JobConfigTriggerType.release,
                    metadata=JobMetadataConfig(
                        targets=STABLE_VERSIONS, branch="build-branch"
                    ),
                )
            ],
            TheJobTriggerType.release,
            JobConfigTriggerType.release,
            set(STABLE_VERSIONS),
            STABLE_KOJI_TARGETS,
            id="koji_build_with_targets_for_release",
        ),
    ],
)
def test_targets_for_koji_build(
    jobs, trigger, job_config_trigger_type, build_targets, koji_targets
):
    pr_id = 41 if trigger == TheJobTriggerType.pull_request else None
    koji_build_handler = KojiBuildJobHelper(
        config=flexmock(),
        package_config=PackageConfig(jobs=jobs),
        project=flexmock(),
        event={"trigger": trigger, "pr_id": pr_id},
        db_trigger=flexmock(job_config_trigger_type=job_config_trigger_type),
    )

    assert koji_build_handler.package_config.jobs
    assert [j.type for j in koji_build_handler.package_config.jobs]

    assert koji_build_handler.configured_build_targets == build_targets
    assert koji_build_handler.build_targets == koji_targets
