# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
from pathlib import Path

import pytest
from flexmock import flexmock

from packit.config import PackageConfig, JobConfig, JobType, JobConfigTriggerType
from packit.config.job_config import JobMetadataConfig
from packit.local_project import LocalProject
from packit.utils.repo import RepositoryCache
from packit_service.worker.build import copr_build
from packit_service.worker.build.copr_build import CoprBuildJobHelper
from packit_service.worker.build.koji_build import KojiBuildJobHelper

# packit.config.aliases.get_aliases() return value example
ALIASES = {
    "fedora-development": ["fedora-33", "fedora-rawhide"],
    "fedora-stable": ["fedora-31", "fedora-32"],
    "fedora-all": ["fedora-31", "fedora-32", "fedora-33", "fedora-rawhide"],
    "epel-all": ["epel-6", "epel-7", "epel-8"],
}

STABLE_VERSIONS = ALIASES["fedora-stable"]
STABLE_CHROOTS = {f"{version}-x86_64" for version in STABLE_VERSIONS}
ONE_CHROOT_SET = {list(STABLE_CHROOTS)[0]}
STABLE_KOJI_TARGETS = {f"f{version[-2:]}" for version in STABLE_VERSIONS}
ONE_KOJI_TARGET_SET = {list(STABLE_KOJI_TARGETS)[0]}


@pytest.mark.parametrize(
    "jobs,job_config_trigger_type,build_chroots,test_chroots",
    [
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(_targets=STABLE_VERSIONS),
                )
            ],
            JobConfigTriggerType.pull_request,
            set(STABLE_VERSIONS),
            set(),
            id="build_with_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(_targets=STABLE_VERSIONS),
                )
            ],
            JobConfigTriggerType.pull_request,
            set(STABLE_VERSIONS),
            set(),
            id="build_with_targets&pr_comment",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    metadata=JobMetadataConfig(_targets=STABLE_VERSIONS),
                )
            ],
            JobConfigTriggerType.release,
            set(STABLE_VERSIONS),
            set(),
            id="build_with_targets&release",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    metadata=JobMetadataConfig(_targets=STABLE_VERSIONS),
                )
            ],
            JobConfigTriggerType.commit,
            set(STABLE_VERSIONS),
            set(),
            id="build_with_targets&push",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(_targets=STABLE_VERSIONS),
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    metadata=JobMetadataConfig(_targets=["different", "os", "target"]),
                ),
            ],
            JobConfigTriggerType.pull_request,
            set(STABLE_VERSIONS),
            set(),
            id="build_with_targets&pull_request_with_pr_and_push_defined",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(_targets=STABLE_VERSIONS),
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    metadata=JobMetadataConfig(_targets=["different", "os", "target"]),
                ),
            ],
            JobConfigTriggerType.pull_request,
            set(STABLE_VERSIONS),
            set(),
            id="build_with_targets&pr_comment_with_pr_and_push_defined",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(_targets=["different", "os", "target"]),
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    metadata=JobMetadataConfig(_targets=STABLE_VERSIONS),
                ),
            ],
            JobConfigTriggerType.commit,
            set(STABLE_VERSIONS),
            set(),
            id="build_with_targets&push_with_pr_and_push_defined",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                )
            ],
            JobConfigTriggerType.pull_request,
            {"fedora-stable"},
            set(),
            id="build_without_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                )
            ],
            JobConfigTriggerType.pull_request,
            {"fedora-stable"},
            {"fedora-stable"},
            id="test_without_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(_targets=STABLE_VERSIONS),
                )
            ],
            JobConfigTriggerType.pull_request,
            set(STABLE_VERSIONS),
            set(STABLE_VERSIONS),
            id="test_with_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                ),
            ],
            JobConfigTriggerType.pull_request,
            {"fedora-stable"},
            {"fedora-stable"},
            id="build_without_target&test_without_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(_targets=STABLE_VERSIONS),
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                ),
            ],
            JobConfigTriggerType.pull_request,
            set(STABLE_VERSIONS),
            set(STABLE_VERSIONS),
            id="build_with_target&test_without_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(_targets=STABLE_VERSIONS),
                ),
            ],
            JobConfigTriggerType.pull_request,
            set(STABLE_VERSIONS),
            set(STABLE_VERSIONS),
            id="build_without_target&test_with_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(_targets=list(ONE_CHROOT_SET)),
                ),
            ],
            JobConfigTriggerType.pull_request,
            ONE_CHROOT_SET,
            ONE_CHROOT_SET,
            id="build_without_target&test_with_one_str_target",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                ),
            ],
            JobConfigTriggerType.commit,
            {"fedora-stable"},
            set(),
            id="build[pr+commit]&test[pr]&commit",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                ),
            ],
            JobConfigTriggerType.pull_request,
            {"fedora-stable"},
            {"fedora-stable"},
            id="build[pr+commit]&test[pr]&pr",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                ),
                JobConfig(type=JobType.tests, trigger=JobConfigTriggerType.commit),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                ),
            ],
            JobConfigTriggerType.commit,
            {"fedora-stable"},
            {"fedora-stable"},
            id="build[pr+commit]&test[commit]&commit",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                ),
                JobConfig(type=JobType.tests, trigger=JobConfigTriggerType.commit),
            ],
            JobConfigTriggerType.pull_request,
            {"fedora-stable"},
            set(),
            id="build[pr+commit]&test[commit]&pr",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                ),
                JobConfig(
                    type=JobType.tests, trigger=JobConfigTriggerType.pull_request
                ),
            ],
            JobConfigTriggerType.commit,
            {"fedora-stable"},
            set(),
            id="build[pr+commit+release]&test[pr]&commit",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(_targets=list(ONE_CHROOT_SET)),
                ),
            ],
            JobConfigTriggerType.pull_request,
            ONE_CHROOT_SET,
            ONE_CHROOT_SET,
            id="build_with_mixed_build_alias",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(_targets=STABLE_VERSIONS),
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(_targets=["fedora-rawhide"]),
                ),
            ],
            JobConfigTriggerType.pull_request,
            set(STABLE_VERSIONS + ["fedora-rawhide"]),
            {"fedora-rawhide"},
            id="build_with_mixed_build_tests",
        ),
    ],
)
def test_targets(jobs, job_config_trigger_type, build_chroots, test_chroots):
    copr_build_helper = CoprBuildJobHelper(
        service_config=flexmock(),
        package_config=PackageConfig(jobs=jobs),
        job_config=jobs[0],  # BuildHelper looks at all jobs in the end
        project=flexmock(),
        metadata=flexmock(pr_id=None),
        db_trigger=flexmock(job_config_trigger_type=job_config_trigger_type),
    )

    assert copr_build_helper.package_config.jobs
    assert [j.type for j in copr_build_helper.package_config.jobs]

    assert copr_build_helper.configured_build_targets == build_chroots
    assert copr_build_helper.configured_tests_targets == test_chroots


@pytest.mark.parametrize(
    "jobs,job_config_trigger_type,build_targets_override,"
    "tests_targets_override,build_targets,test_targets",
    [
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(_targets=STABLE_VERSIONS),
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                ),
            ],
            JobConfigTriggerType.pull_request,
            {"fedora-32-x86_64"},
            None,
            {"fedora-32-x86_64"},
            {"fedora-32-x86_64"},
            id="target_in_config_for_both",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(_targets=STABLE_VERSIONS),
                )
            ],
            JobConfigTriggerType.pull_request,
            {"fedora-32-x86_64"},
            None,
            {"fedora-32-x86_64"},
            set(),
            id="target_in_config",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(_targets=STABLE_VERSIONS),
                )
            ],
            JobConfigTriggerType.pull_request,
            {"fedora-33-x86_64"},
            None,
            set(),
            set(),
            id="target_not_in_config",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(
                        _targets={"epel-7-x86_64": {"distros": ["centos-7", "rhel-7"]}}
                    ),
                )
            ],
            JobConfigTriggerType.pull_request,
            None,
            {"centos-7-x86_64"},
            {"epel-7-x86_64"},
            {"centos-7-x86_64"},
            id="build_test_mapping_test_overrides",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(
                        _targets={"epel-7-x86_64": {"distros": ["centos-7", "rhel-7"]}}
                    ),
                )
            ],
            JobConfigTriggerType.pull_request,
            {"epel-7-x86_64"},
            None,
            {"epel-7-x86_64"},
            {"centos-7-x86_64", "rhel-7-x86_64"},
            id="build_test_mapping_build_overrides",
        ),
    ],
)
def test_copr_targets_overrides(
    jobs,
    job_config_trigger_type,
    build_targets_override,
    tests_targets_override,
    build_targets,
    test_targets,
):
    copr_build_helper = CoprBuildJobHelper(
        service_config=flexmock(),
        package_config=PackageConfig(jobs=jobs),
        job_config=jobs[0],  # BuildHelper looks at all jobs in the end
        project=flexmock(),
        metadata=flexmock(pr_id=None),
        db_trigger=flexmock(job_config_trigger_type=job_config_trigger_type),
        build_targets_override=build_targets_override,
        tests_targets_override=tests_targets_override,
    )
    flexmock(copr_build).should_receive("get_valid_build_targets").with_args(
        "fedora-31", "fedora-32", default=None
    ).and_return(STABLE_CHROOTS)
    flexmock(copr_build).should_receive("get_valid_build_targets").with_args(
        "fedora-32", "fedora-31", default=None
    ).and_return(STABLE_CHROOTS)
    flexmock(copr_build).should_receive("get_valid_build_targets").with_args(
        default=None
    ).and_return(set())
    flexmock(copr_build).should_receive("get_valid_build_targets").with_args(
        "epel-7-x86_64", default=None
    ).and_return({"epel-7-x86_64"})
    assert copr_build_helper.build_targets == build_targets
    assert copr_build_helper.tests_targets == test_targets


@pytest.mark.parametrize(
    "configured_targets,use_internal_tf,build_target,test_targets",
    [
        pytest.param(
            STABLE_VERSIONS,
            False,
            "fedora-32-x86_64",
            {"fedora-32-x86_64"},
            id="default_mapping",
        ),
        pytest.param(
            {"epel-7-x86_64": {"distros": ["centos-7", "rhel-7"]}},
            False,
            "epel-7-x86_64",
            {"centos-7-x86_64", "rhel-7-x86_64"},
            id="mapping_defined_in_config",
        ),
        pytest.param(
            ["epel-7-x86_64"],
            False,
            "epel-7-x86_64",
            {"centos-7-x86_64"},
            id="public_tf_default_mapping1",
        ),
        pytest.param(
            ["epel-6-x86_64"],
            False,
            "epel-6-x86_64",
            {"centos-6-x86_64"},
            id="public_tf_default_mapping2",
        ),
        pytest.param(
            ["epel-8-x86_64"],
            False,
            "epel-8-x86_64",
            {"centos-stream-8-x86_64"},
            id="public_tf_default_mapping3",
        ),
        pytest.param(
            ["epel-7-x86_64"],
            True,
            "epel-7-x86_64",
            {"rhel-7-x86_64"},
            id="internal_tf_default_mapping1",
        ),
        pytest.param(
            ["epel-8-x86_64"],
            True,
            "epel-8-x86_64",
            {"rhel-8-x86_64"},
            id="internal_tf_default_mapping2",
        ),
    ],
)
def test_copr_build_target2test_targets(
    configured_targets, use_internal_tf, build_target, test_targets
):
    jobs = [
        JobConfig(
            type=JobType.tests,
            trigger=JobConfigTriggerType.pull_request,
            metadata=JobMetadataConfig(
                _targets=configured_targets, use_internal_tf=use_internal_tf
            ),
        )
    ]
    copr_build_helper = CoprBuildJobHelper(
        service_config=flexmock(),
        package_config=PackageConfig(jobs=jobs),
        job_config=jobs[0],
        project=flexmock(),
        metadata=flexmock(pr_id=None),
        db_trigger=flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
    )
    assert copr_build_helper.build_target2test_targets(build_target) == test_targets


@pytest.mark.parametrize(
    "configured_targets,use_internal_tf,test_target,build_target",
    [
        pytest.param(
            STABLE_VERSIONS,
            False,
            "fedora-32-x86_64",
            "fedora-32-x86_64",
            id="default_mapping",
        ),
        pytest.param(
            {"epel-7-x86_64": {"distros": ["centos-7", "rhel-7"]}},
            False,
            "centos-7-x86_64",
            "epel-7-x86_64",
            id="mapping_defined_in_config1",
        ),
        pytest.param(
            {"epel-7-x86_64": {"distros": ["centos-7", "rhel-7"]}},
            False,
            "rhel-7-x86_64",
            "epel-7-x86_64",
            id="mapping_defined_in_config2",
        ),
        pytest.param(
            {"epel-7": {"distros": ["centos-7", "rhel-7"]}},
            False,
            "rhel-7-x86_64",
            "epel-7-x86_64",
            id="mapping_defined_in_config_without_arch",
        ),
        pytest.param(
            ["epel-7-x86_64"],
            False,
            "centos-7-x86_64",
            "epel-7-x86_64",
            id="public_tf_default_mapping",
        ),
        pytest.param(
            ["epel-7-x86_64"],
            True,
            "rhel-7-x86_64",
            "epel-7-x86_64",
            id="internal_tf_default_mapping",
        ),
    ],
)
def test_copr_test_target2build_target(
    configured_targets, use_internal_tf, test_target, build_target
):
    jobs = [
        JobConfig(
            type=JobType.copr_build,
            trigger=JobConfigTriggerType.pull_request,
            metadata=JobMetadataConfig(
                _targets=configured_targets, use_internal_tf=use_internal_tf
            ),
        )
    ]
    copr_build_helper = CoprBuildJobHelper(
        service_config=flexmock(),
        package_config=PackageConfig(jobs=jobs),
        job_config=jobs[0],
        project=flexmock(),
        metadata=flexmock(pr_id=None),
        db_trigger=flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
    )
    assert copr_build_helper.test_target2build_target(test_target) == build_target


@pytest.mark.parametrize(
    "jobs,job_config_trigger_type,targets_override,build_targets",
    [
        pytest.param(
            [
                JobConfig(
                    type=JobType.production_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(_targets=STABLE_VERSIONS),
                )
            ],
            JobConfigTriggerType.pull_request,
            {"f32"},
            {"f32"},
            id="target_in_config",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.production_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(_targets=STABLE_VERSIONS),
                )
            ],
            JobConfigTriggerType.pull_request,
            {"f33"},
            set(),
            id="target_not_in_config",
        ),
    ],
)
def test_koji_targets_overrides(
    jobs, job_config_trigger_type, targets_override, build_targets
):
    koji_build_helper = KojiBuildJobHelper(
        service_config=flexmock(),
        package_config=PackageConfig(jobs=jobs),
        job_config=jobs[0],
        project=flexmock(),
        metadata=flexmock(),
        db_trigger=flexmock(job_config_trigger_type=job_config_trigger_type),
        build_targets_override=targets_override,
    )
    assert koji_build_helper.build_targets == build_targets


@pytest.mark.parametrize(
    "jobs,init_job,job_config_trigger_type,result_job_build,result_job_tests",
    [
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                )
            ],
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
            ),
            JobConfigTriggerType.pull_request,
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
            ),
            None,
            id="copr_build&pull_request",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                )
            ],
            JobConfig(
                type=JobType.build,
                trigger=JobConfigTriggerType.pull_request,
            ),
            JobConfigTriggerType.pull_request,
            JobConfig(
                type=JobType.build,
                trigger=JobConfigTriggerType.pull_request,
            ),
            None,
            id="build&pull_request",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                )
            ],
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
            ),
            JobConfigTriggerType.pull_request,
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
            ),
            None,
            id="copr_build&pr_comment",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                )
            ],
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.release,
            ),
            JobConfigTriggerType.release,
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.release,
            ),
            None,
            id="copr_build&release",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                )
            ],
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.commit,
            ),
            JobConfigTriggerType.commit,
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.commit,
            ),
            None,
            id="copr_build&push",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                ),
            ],
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
            ),
            JobConfigTriggerType.pull_request,
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
            ),
            None,
            id="copr_build[pr+commit]&pull_request",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                ),
            ],
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
            ),
            JobConfigTriggerType.pull_request,
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
            ),
            None,
            id="copr_build[commit+pr]&pull_request",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                ),
            ],
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.commit,
            ),
            JobConfigTriggerType.commit,
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.commit,
            ),
            None,
            id="copr_build[pr+commit]&push",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                )
            ],
            JobConfig(
                type=JobType.tests,
                trigger=JobConfigTriggerType.pull_request,
            ),
            JobConfigTriggerType.pull_request,
            None,
            JobConfig(
                type=JobType.tests,
                trigger=JobConfigTriggerType.pull_request,
            ),
            id="test&pr",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                ),
            ],
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
            ),
            JobConfigTriggerType.pull_request,
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
            ),
            JobConfig(
                type=JobType.tests,
                trigger=JobConfigTriggerType.pull_request,
            ),
            id="copr_build+test&pr",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                ),
            ],
            JobConfig(
                type=JobType.build,
                trigger=JobConfigTriggerType.pull_request,
            ),
            JobConfigTriggerType.pull_request,
            JobConfig(
                type=JobType.build,
                trigger=JobConfigTriggerType.pull_request,
            ),
            JobConfig(
                type=JobType.tests,
                trigger=JobConfigTriggerType.pull_request,
            ),
            id="build+test&pr",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                ),
            ],
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
            ),
            JobConfigTriggerType.pull_request,
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
            ),
            JobConfig(
                type=JobType.tests,
                trigger=JobConfigTriggerType.pull_request,
            ),
            id="copr_build[pr+commit]+test[pr]&pr",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                ),
            ],
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.commit,
            ),
            JobConfigTriggerType.commit,
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.commit,
            ),
            None,
            id="copr_build[pr+commit]+test[pr]&commit",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                ),
            ],
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.release,
            ),
            JobConfigTriggerType.release,
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.release,
            ),
            None,
            id="copr_build[pr+commit]+test[pr]&commit",
        ),
    ],
)
def test_build_handler_job_and_test_properties(
    jobs,
    init_job,
    job_config_trigger_type,
    result_job_build,
    result_job_tests,
):
    copr_build_helper = CoprBuildJobHelper(
        service_config=flexmock(),
        package_config=PackageConfig(jobs=jobs),
        job_config=init_job,
        project=flexmock(),
        metadata=flexmock(pr_id=None),
        db_trigger=flexmock(job_config_trigger_type=job_config_trigger_type),
    )

    assert copr_build_helper.package_config.jobs
    assert [j.type for j in copr_build_helper.package_config.jobs]

    assert copr_build_helper.job_build == result_job_build
    assert copr_build_helper.job_tests == result_job_tests


@pytest.mark.parametrize(
    "jobs,job_config_trigger_type,tag_name,job_owner,job_project",
    [
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(),
                )
            ],
            JobConfigTriggerType.pull_request,
            None,
            "nobody",
            "git.instance.io-the-example-namespace-the-example-repo-the-event-identifier",
            id="default-values",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(owner="custom-owner"),
                )
            ],
            JobConfigTriggerType.pull_request,
            None,
            "custom-owner",
            "git.instance.io-the-example-namespace-the-example-repo-the-event-identifier",
            id="custom-owner&default-project",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(project="custom-project"),
                )
            ],
            JobConfigTriggerType.pull_request,
            None,
            "nobody",
            "custom-project",
            id="default-owner&custom-project",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(
                        owner="custom-owner", project="custom-project"
                    ),
                )
            ],
            JobConfigTriggerType.pull_request,
            None,
            "custom-owner",
            "custom-project",
            id="custom-owner&custom-project",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(
                        owner="custom-owner", project="custom-project"
                    ),
                )
            ],
            JobConfigTriggerType.pull_request,
            None,
            "custom-owner",
            "custom-project",
            id="custom-owner-build&custom-project",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    metadata=JobMetadataConfig(),
                )
            ],
            JobConfigTriggerType.commit,
            None,
            "nobody",
            "git.instance.io-the-example-namespace-the-example-repo-the-event-identifier",
            id="commit&default-owner&default-project",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    metadata=JobMetadataConfig(),
                )
            ],
            JobConfigTriggerType.release,
            "v1.O.0",
            "nobody",
            "git.instance.io-the-example-namespace-the-example-repo-releases",
            id="release&default-owner&default-project",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    metadata=JobMetadataConfig(
                        owner="commit-owner", project="commit-project"
                    ),
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(owner="pr-owner", project="pr-project"),
                ),
            ],
            JobConfigTriggerType.pull_request,
            None,
            "pr-owner",
            "pr-project",
            id="two-copr-builds&custom-owner&custom-project",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(),
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(),
                ),
            ],
            JobConfigTriggerType.pull_request,
            None,
            "nobody",
            "git.instance.io-the-example-namespace-the-example-repo-the-event-identifier",
            id="build+test&default-owner&default-project",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(
                        owner="custom-owner", project="custom-project"
                    ),
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(),
                ),
            ],
            JobConfigTriggerType.pull_request,
            None,
            "custom-owner",
            "custom-project",
            id="build+test&custom-owner&custom-project-from-build",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(),
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(
                        owner="custom-owner", project="custom-project"
                    ),
                ),
            ],
            JobConfigTriggerType.pull_request,
            None,
            "custom-owner",
            "custom-project",
            id="build+test&custom-owner&custom-project-from-test",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(owner="pr-owner", project="pr-project"),
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(),
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    metadata=JobMetadataConfig(
                        owner="commit-owner", project="commit-project"
                    ),
                ),
            ],
            JobConfigTriggerType.pull_request,
            None,
            "pr-owner",
            "pr-project",
            id="two-copr-builds+test-pr&custom-owner&custom-project",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(owner="pr-owner", project="pr-project"),
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(),
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    metadata=JobMetadataConfig(
                        owner="commit-owner", project="commit-project"
                    ),
                ),
            ],
            JobConfigTriggerType.commit,
            None,
            "commit-owner",
            "commit-project",
            id="two-copr-builds+test-commit&custom-owner&custom-project",
        ),
    ],
)
def test_copr_project_and_namespace(
    jobs,
    job_config_trigger_type,
    tag_name,
    job_owner,
    job_project,
):
    copr_build_helper = CoprBuildJobHelper(
        service_config=flexmock(deployment="stg"),
        package_config=PackageConfig(jobs=jobs),
        job_config=jobs[0],  # BuildHelper looks at all jobs in the end
        project=flexmock(
            namespace="the/example/namespace",
            repo="the-example-repo",
            service=flexmock(instance_url="https://git.instance.io"),
        ),
        metadata=flexmock(
            pr_id=None, identifier="the-event-identifier", tag_name=tag_name
        ),
        db_trigger=flexmock(job_config_trigger_type=job_config_trigger_type),
    )
    copr_build_helper._api = flexmock(
        copr_helper=flexmock(copr_client=flexmock(config={"username": "nobody"}))
    )

    assert copr_build_helper.job_project == job_project
    assert copr_build_helper.job_owner == job_owner


@pytest.mark.parametrize(
    "jobs,job_config_trigger_type,build_targets,koji_targets",
    [
        pytest.param(
            [
                JobConfig(
                    type=JobType.production_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(_targets=STABLE_VERSIONS),
                )
            ],
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
                        _targets=STABLE_VERSIONS, branch="build-branch"
                    ),
                )
            ],
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
                        _targets=STABLE_VERSIONS, branch="build-branch"
                    ),
                )
            ],
            JobConfigTriggerType.release,
            set(STABLE_VERSIONS),
            STABLE_KOJI_TARGETS,
            id="koji_build_with_targets_for_release",
        ),
    ],
)
def test_targets_for_koji_build(
    jobs, job_config_trigger_type, build_targets, koji_targets
):
    pr_id = 41 if job_config_trigger_type == JobConfigTriggerType.pull_request else None
    koji_build_helper = KojiBuildJobHelper(
        service_config=flexmock(),
        package_config=PackageConfig(jobs=jobs),
        job_config=jobs[0],
        project=flexmock(),
        metadata=flexmock(pr_id=pr_id),
        db_trigger=flexmock(job_config_trigger_type=job_config_trigger_type),
    )

    assert koji_build_helper.package_config.jobs
    assert [j.type for j in koji_build_helper.package_config.jobs]

    assert koji_build_helper.configured_build_targets == build_targets
    assert koji_build_helper.build_targets == koji_targets


def test_repository_cache_invocation():
    copr_build_helper = CoprBuildJobHelper(
        service_config=flexmock(
            repository_cache="/tmp/repository-cache",
            add_repositories_to_repository_cache=False,
            command_handler_work_dir="/tmp/some-dir",
        ),
        package_config=PackageConfig(
            jobs=[
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(_targets=STABLE_VERSIONS),
                )
            ],
        ),
        job_config=JobConfig(
            type=JobType.copr_build,
            trigger=JobConfigTriggerType.pull_request,
            metadata=JobMetadataConfig(_targets=STABLE_VERSIONS),
        ),
        project=flexmock(
            service=flexmock(),
            get_git_urls=lambda: {
                "git": "https://github.com/some-namespace/some-repo.git"
            },
            repo=flexmock(),
            namespace=flexmock(),
        ),
        metadata=flexmock(pr_id=None, git_ref=flexmock()),
        db_trigger=flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
    )

    flexmock(RepositoryCache).should_call("__init__").once()
    flexmock(RepositoryCache).should_receive("get_repo").with_args(
        "https://github.com/some-namespace/some-repo.git",
        directory=Path("/tmp/some-dir"),
    ).and_return(
        flexmock(
            git=flexmock().should_receive("checkout").and_return().mock(),
            commit=lambda: "commit",
        )
    ).once()
    assert copr_build_helper.local_project


def test_local_project_not_called_when_initializing_api():
    jobs = [
        JobConfig(
            type=JobType.copr_build,
            trigger=JobConfigTriggerType.pull_request,
            metadata=JobMetadataConfig(),
        )
    ]
    copr_build_helper = CoprBuildJobHelper(
        service_config=flexmock(use_stage=lambda: False),
        package_config=PackageConfig(jobs=jobs),
        job_config=jobs[0],
        project=flexmock(),
        metadata=flexmock(pr_id=1),
        db_trigger=flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
    )
    flexmock(LocalProject).should_receive("__init__").never()
    assert copr_build_helper.api
    assert copr_build_helper.api.copr_helper
