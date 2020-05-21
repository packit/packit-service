import pytest
from flexmock import flexmock

from packit.config import PackageConfig, JobConfig, JobType, JobConfigTriggerType
from packit.config.job_config import JobMetadataConfig
from packit.config.aliases import ALIASES
from packit_service.service.events import TheJobTriggerType
from packit_service.worker.build.copr_build import CoprBuildJobHelper
from packit_service.worker.build.koji_build import KojiBuildJobHelper

stable_versions = ALIASES["fedora-stable"]
stable_targets = [f"{version}-x86_64" for version in stable_versions]


@pytest.mark.parametrize(
    "jobs,trigger,job_config_trigger_type,build_targets,test_targets",
    [
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(targets=stable_versions),
                )
            ],
            TheJobTriggerType.pull_request,
            JobConfigTriggerType.pull_request,
            stable_targets,
            list(),
            id="build_with_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(targets=stable_versions),
                )
            ],
            TheJobTriggerType.pr_comment,
            JobConfigTriggerType.pull_request,
            stable_targets,
            list(),
            id="build_with_targets&pr_comment",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    metadata=JobMetadataConfig(targets=stable_versions),
                )
            ],
            TheJobTriggerType.release,
            JobConfigTriggerType.release,
            stable_targets,
            list(),
            id="build_with_targets&release",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    metadata=JobMetadataConfig(targets=stable_versions),
                )
            ],
            TheJobTriggerType.push,
            JobConfigTriggerType.commit,
            stable_targets,
            list(),
            id="build_with_targets&push",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(targets=stable_versions),
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    metadata=JobMetadataConfig(targets=["different", "os", "target"]),
                ),
            ],
            TheJobTriggerType.pull_request,
            JobConfigTriggerType.pull_request,
            stable_targets,
            list(),
            id="build_with_targets&pull_request_with_pr_and_push_defined",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(targets=stable_versions),
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    metadata=JobMetadataConfig(targets=["different", "os", "target"]),
                ),
            ],
            TheJobTriggerType.pr_comment,
            JobConfigTriggerType.pull_request,
            stable_targets,
            list(),
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
                    metadata=JobMetadataConfig(targets=stable_versions),
                ),
            ],
            TheJobTriggerType.push,
            JobConfigTriggerType.commit,
            stable_targets,
            list(),
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
            stable_targets,
            list(),
            id="build_without_targets",
        ),
        pytest.param(
            [JobConfig(type=JobType.tests, trigger=JobConfigTriggerType.pull_request,)],
            TheJobTriggerType.pull_request,
            JobConfigTriggerType.pull_request,
            stable_targets,
            stable_targets,
            id="test_without_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(targets=stable_versions),
                )
            ],
            TheJobTriggerType.pull_request,
            JobConfigTriggerType.pull_request,
            stable_targets,
            stable_targets,
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
            stable_targets,
            stable_targets,
            id="build_without_target&test_without_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(targets=stable_versions),
                ),
                JobConfig(
                    type=JobType.tests, trigger=JobConfigTriggerType.pull_request,
                ),
            ],
            TheJobTriggerType.pull_request,
            JobConfigTriggerType.pull_request,
            stable_targets,
            stable_targets,
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
                    metadata=JobMetadataConfig(targets=stable_versions),
                ),
            ],
            TheJobTriggerType.pull_request,
            JobConfigTriggerType.pull_request,
            stable_targets,
            stable_targets,
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
                    metadata=JobMetadataConfig(targets=stable_versions[0:1]),
                ),
            ],
            TheJobTriggerType.pull_request,
            JobConfigTriggerType.pull_request,
            stable_targets[0:1],
            stable_targets[0:1],
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
                    metadata=JobMetadataConfig(targets=stable_versions[0:1]),
                ),
            ],
            TheJobTriggerType.pull_request,
            JobConfigTriggerType.pull_request,
            stable_targets[0:1],
            stable_targets[0:1],
            id="build_with_mixed_build_alias",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.production_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(targets=stable_versions),
                )
            ],
            TheJobTriggerType.pull_request,
            JobConfigTriggerType.pull_request,
            stable_targets,
            list(),
            id="koji_build_with_targets_for_pr",
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
            pr_id=None,
        ),
    )

    assert copr_build_handler.package_config.jobs
    assert [j.type for j in copr_build_handler.package_config.jobs]

    # Compare sets to not get caught by list order
    assert set(copr_build_handler.build_chroots) == set(build_targets)
    assert set(copr_build_handler.tests_chroots) == set(test_targets)


@pytest.mark.parametrize(
    "jobs,trigger,job_config_trigger_type,build_targets,test_targets",
    [
        pytest.param(
            [
                JobConfig(
                    type=JobType.production_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(targets=stable_versions),
                )
            ],
            TheJobTriggerType.pull_request,
            JobConfigTriggerType.pull_request,
            stable_targets,
            list(),
            id="koji_build_with_targets_for_pr",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.production_build,
                    trigger=JobConfigTriggerType.commit,
                    metadata=JobMetadataConfig(
                        targets=stable_versions, branch="build-branch"
                    ),
                )
            ],
            TheJobTriggerType.push,
            JobConfigTriggerType.commit,
            stable_targets,
            list(),
            id="koji_build_with_targets_for_commit",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.production_build,
                    trigger=JobConfigTriggerType.release,
                    metadata=JobMetadataConfig(
                        targets=stable_versions, branch="build-branch"
                    ),
                )
            ],
            TheJobTriggerType.release,
            JobConfigTriggerType.release,
            stable_targets,
            list(),
            id="koji_build_with_targets_for_release",
        ),
    ],
)
def test_targets_for_koji_build(
    jobs, trigger, job_config_trigger_type, build_targets, test_targets
):
    koji_build_handler = KojiBuildJobHelper(
        config=flexmock(),
        package_config=PackageConfig(jobs=jobs),
        project=flexmock(),
        event=flexmock(
            trigger=trigger,
            db_trigger=flexmock(job_config_trigger_type=job_config_trigger_type),
            pr_id=41 if trigger == TheJobTriggerType.pull_request else None,
        ),
    )

    assert koji_build_handler.package_config.jobs
    assert [j.type for j in koji_build_handler.package_config.jobs]

    assert set(koji_build_handler.build_chroots) == set(build_targets)
    assert set(koji_build_handler.tests_chroots) == set(test_targets)
