# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
from pathlib import Path

import pytest
from flexmock import flexmock

from packit.copr_helper import CoprHelper
from packit.config import PackageConfig, JobConfig, JobType, JobConfigTriggerType
from packit.config.aliases import get_build_targets
from packit.local_project import LocalProject
from packit.utils.repo import RepositoryCache
from packit_service.config import ServiceConfig
from packit_service.models import JobTriggerModelType
from packit_service.worker.helpers.build import copr_build
from packit_service.worker.helpers.build.copr_build import CoprBuildJobHelper
from packit_service.worker.helpers.build.koji_build import KojiBuildJobHelper

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


def _mock_targets(jobs, job, job_type):
    job_config_trigger_type, job_trigger_model_type = job_type

    project_service = flexmock(instance_url="https://github.com")
    return CoprBuildJobHelper(
        service_config=ServiceConfig.get_service_config(),
        package_config=PackageConfig(jobs=jobs),
        job_config=job,  # BuildHelper looks at all jobs in the end
        project=flexmock(
            service=project_service, namespace="packit", repo="testing_package"
        ),
        metadata=flexmock(pr_id=None, identifier=None),
        db_trigger=flexmock(
            job_config_trigger_type=job_config_trigger_type,
            job_trigger_model_type=job_trigger_model_type,
        ),
    )


@pytest.mark.parametrize(
    "jobs,job_type,build_chroots,test_chroots",
    [
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    _targets=STABLE_VERSIONS,
                )
            ],
            (JobConfigTriggerType.pull_request, JobTriggerModelType.pull_request),
            set(STABLE_VERSIONS),
            set(),
            id="build_with_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    _targets=STABLE_VERSIONS,
                )
            ],
            (JobConfigTriggerType.pull_request, JobTriggerModelType.pull_request),
            set(STABLE_VERSIONS),
            set(),
            id="build_with_targets&pr_comment",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    _targets=STABLE_VERSIONS,
                )
            ],
            (JobConfigTriggerType.release, JobTriggerModelType.release),
            set(STABLE_VERSIONS),
            set(),
            id="build_with_targets&release",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    _targets=STABLE_VERSIONS,
                )
            ],
            (JobConfigTriggerType.commit, JobTriggerModelType.branch_push),
            set(STABLE_VERSIONS),
            set(),
            id="build_with_targets&push",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    _targets=STABLE_VERSIONS,
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    _targets=["different", "os", "target"],
                ),
            ],
            (JobConfigTriggerType.pull_request, JobTriggerModelType.pull_request),
            set(STABLE_VERSIONS),
            set(),
            id="build_with_targets&pull_request_with_pr_and_push_defined",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    _targets=STABLE_VERSIONS,
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    _targets=["different", "os", "target"],
                ),
            ],
            (JobConfigTriggerType.pull_request, JobTriggerModelType.pull_request),
            set(STABLE_VERSIONS),
            set(),
            id="build_with_targets&pr_comment_with_pr_and_push_defined",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    _targets=["different", "os", "target"],
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    _targets=STABLE_VERSIONS,
                ),
            ],
            (JobConfigTriggerType.commit, JobTriggerModelType.branch_push),
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
            (JobConfigTriggerType.pull_request, JobTriggerModelType.pull_request),
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
            (JobConfigTriggerType.pull_request, JobTriggerModelType.pull_request),
            {"fedora-stable"},
            {"fedora-stable"},
            id="test_without_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    _targets=STABLE_VERSIONS,
                )
            ],
            (JobConfigTriggerType.pull_request, JobTriggerModelType.pull_request),
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
            (JobConfigTriggerType.pull_request, JobTriggerModelType.pull_request),
            {"fedora-stable"},
            {"fedora-stable"},
            id="build_without_target&test_without_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    _targets=STABLE_VERSIONS,
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                ),
            ],
            (JobConfigTriggerType.pull_request, JobTriggerModelType.pull_request),
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
                    _targets=STABLE_VERSIONS,
                ),
            ],
            (JobConfigTriggerType.pull_request, JobTriggerModelType.pull_request),
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
                    _targets=list(ONE_CHROOT_SET),
                ),
            ],
            (JobConfigTriggerType.pull_request, JobTriggerModelType.pull_request),
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
            (JobConfigTriggerType.commit, JobTriggerModelType.branch_push),
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
            (JobConfigTriggerType.pull_request, JobTriggerModelType.pull_request),
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
            (JobConfigTriggerType.commit, JobTriggerModelType.branch_push),
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
            (JobConfigTriggerType.pull_request, JobTriggerModelType.pull_request),
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
            (JobConfigTriggerType.commit, JobTriggerModelType.branch_push),
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
                    _targets=list(ONE_CHROOT_SET),
                ),
            ],
            (JobConfigTriggerType.pull_request, JobTriggerModelType.pull_request),
            ONE_CHROOT_SET,
            ONE_CHROOT_SET,
            id="build_with_mixed_build_alias",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    _targets=STABLE_VERSIONS,
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    _targets=["fedora-rawhide"],
                ),
            ],
            (JobConfigTriggerType.pull_request, JobTriggerModelType.pull_request),
            set(STABLE_VERSIONS + ["fedora-rawhide"]),
            {"fedora-rawhide"},
            id="build_with_mixed_build_tests",
        ),
    ],
)
def test_targets(jobs, job_type, build_chroots, test_chroots):
    copr_build_helper = _mock_targets(jobs, jobs[0], job_type)

    assert copr_build_helper.package_config.jobs
    assert [j.type for j in copr_build_helper.package_config.jobs]

    assert copr_build_helper.configured_build_targets == build_chroots
    assert copr_build_helper.configured_tests_targets == test_chroots


def test_deduced_copr_targets():
    jobs = [
        JobConfig(
            type=JobType.copr_build,
            trigger=JobConfigTriggerType.commit,
            owner="mf",
            project="custom-copr-targets",
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
            type=JobType.tests,
            trigger=JobConfigTriggerType.commit,
        ),
    ]
    job_type = (JobConfigTriggerType.commit, JobTriggerModelType.branch_push)
    copr_build_helper = _mock_targets(jobs, jobs[0], job_type)
    flexmock(CoprHelper).should_receive("get_chroots").with_args(
        owner=jobs[0].owner,
        project=jobs[0].project,
    ).and_return({"opensuse-tumbleweed-x86_64"})

    assert copr_build_helper.package_config.jobs
    assert [j.type for j in copr_build_helper.package_config.jobs]

    assert copr_build_helper.configured_build_targets == {"opensuse-tumbleweed-x86_64"}
    assert copr_build_helper.configured_tests_targets == {"opensuse-tumbleweed-x86_64"}


@pytest.mark.parametrize(
    "jobs,job_config_trigger_type,build_targets_override,"
    "tests_targets_override,build_targets,test_targets",
    [
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    _targets=STABLE_VERSIONS,
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
                    _targets=STABLE_VERSIONS,
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
                    _targets=STABLE_VERSIONS,
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
                    _targets={"epel-7-x86_64": {"distros": ["centos-7", "rhel-7"]}},
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
                    _targets={"epel-7-x86_64": {"distros": ["centos-7", "rhel-7"]}},
                )
            ],
            JobConfigTriggerType.pull_request,
            {"epel-7-x86_64"},
            None,
            {"epel-7-x86_64"},
            {"centos-7-x86_64", "rhel-7-x86_64"},
            id="build_test_mapping_build_overrides",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    _targets=["centos-stream-8"],
                )
            ],
            JobConfigTriggerType.pull_request,
            None,
            {"centos-stream-8-x86_64"},
            {"centos-stream-8-x86_64"},
            {"centos-stream-8-x86_64"},
            id="targets_in_tests_no_mapping",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    _targets=["centos-stream-8"],
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                ),
            ],
            JobConfigTriggerType.pull_request,
            None,
            {"centos-stream-8-x86_64"},
            {"centos-stream-8-x86_64"},
            {"centos-stream-8-x86_64"},
            id="targets_in_build_no_mapping",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    _targets=["epel-7-x86_64"],
                )
            ],
            JobConfigTriggerType.pull_request,
            {"epel-7-x86_64"},
            None,
            {"epel-7-x86_64"},
            {"centos-7-x86_64"},
            id="default_mapping_build_override",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    _targets=["epel-7-x86_64"],
                )
            ],
            JobConfigTriggerType.pull_request,
            None,
            {"centos-7-x86_64"},
            {"epel-7-x86_64"},
            {"centos-7-x86_64"},
            id="default_mapping_test_override",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    _targets=["epel-7-ppc64le"],
                )
            ],
            JobConfigTriggerType.pull_request,
            {"epel-7-ppc64le"},
            None,
            {"epel-7-ppc64le"},
            {"centos-7-ppc64le"},
            id="default_mapping_build_override_different_arch",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    _targets=["epel-7-ppc64le"],
                )
            ],
            JobConfigTriggerType.pull_request,
            None,
            {"centos-7-ppc64le"},
            {"epel-7-ppc64le"},
            {"centos-7-ppc64le"},
            id="default_mapping_test_override_different_arch",
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
        service_config=ServiceConfig.get_service_config(),
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
    flexmock(copr_build).should_receive("get_valid_build_targets").with_args(
        "epel-7-ppc64le", default=None
    ).and_return({"epel-7-ppc64le"})
    flexmock(copr_build).should_receive("get_valid_build_targets").with_args(
        "centos-stream-8", default=None
    ).and_return({"centos-stream-8-x86_64"})
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
            _targets=configured_targets,
            use_internal_tf=use_internal_tf,
        )
    ]
    copr_build_helper = CoprBuildJobHelper(
        service_config=ServiceConfig.get_service_config(),
        package_config=PackageConfig(jobs=jobs),
        job_config=jobs[0],
        project=flexmock(),
        metadata=flexmock(pr_id=None),
        db_trigger=flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
    )
    flexmock(copr_build, get_valid_build_targets=get_build_targets)
    assert copr_build_helper.build_target2test_targets(build_target) == test_targets


def test_copr_build_and_test_targets_both_jobs_defined():
    jobs = [
        JobConfig(
            type=JobType.tests,
            trigger=JobConfigTriggerType.pull_request,
            _targets={
                "epel-8-x86_64": {},
                "fedora-35-x86_64": {"distros": ["fedora-35", "fedora-36"]},
            },
        ),
        JobConfig(
            type=JobType.copr_build,
            trigger=JobConfigTriggerType.pull_request,
            _targets=["fedora-35", "fedora-36", "epel-8"],
        ),
    ]
    flexmock(copr_build, get_valid_build_targets=get_build_targets)
    for i in [0, 1]:
        copr_build_helper = CoprBuildJobHelper(
            service_config=ServiceConfig.get_service_config(),
            package_config=PackageConfig(jobs=jobs),
            job_config=jobs[i],
            project=flexmock(),
            metadata=flexmock(pr_id=None),
            db_trigger=flexmock(
                job_config_trigger_type=JobConfigTriggerType.pull_request
            ),
        )
        assert copr_build_helper.build_target2test_targets("fedora-35-x86_64") == {
            "fedora-35-x86_64",
            "fedora-36-x86_64",
        }
        assert copr_build_helper.build_target2test_targets("fedora-36-x86_64") == set()
        assert copr_build_helper.build_target2test_targets("epel-8-x86_64") == {
            "centos-stream-8-x86_64"
        }
        assert copr_build_helper.build_targets_for_tests == {
            "fedora-35-x86_64",
            "epel-8-x86_64",
        }
        assert copr_build_helper.tests_targets == {
            "fedora-35-x86_64",
            "fedora-36-x86_64",
            "centos-stream-8-x86_64",
        }
        assert copr_build_helper.build_targets == {
            "fedora-35-x86_64",
            "fedora-36-x86_64",
            "epel-8-x86_64",
        }


@pytest.mark.parametrize(
    "job_config,test_target,build_target",
    [
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    _targets=STABLE_VERSIONS,
                )
            ],
            "fedora-32-x86_64",
            "fedora-32-x86_64",
            id="default_mapping",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    _targets={"epel-7-x86_64": {"distros": ["centos-7", "rhel-7"]}},
                )
            ],
            "centos-7-x86_64",
            "epel-7-x86_64",
            id="mapping_defined_in_config1",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    _targets={"epel-7-x86_64": {"distros": ["centos-7", "rhel-7"]}},
                )
            ],
            "rhel-7-x86_64",
            "epel-7-x86_64",
            id="mapping_defined_in_config2",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    _targets={"epel-7-x86_64": {"distros": ["centos-7", "rhel-7"]}},
                )
            ],
            "rhel-7-x86_64",
            "epel-7-x86_64",
            id="mapping_defined_in_config_without_arch",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    _targets=["epel-7-x86_64"],
                )
            ],
            "centos-7-x86_64",
            "epel-7-x86_64",
            id="public_tf_default_mapping",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    _targets=["epel-7-x86_64"],
                    use_internal_tf=True,
                )
            ],
            "rhel-7-x86_64",
            "epel-7-x86_64",
            id="internal_tf_default_mapping",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    _targets=["centos-stream-9-x86_64"],
                )
            ],
            "centos-stream-9-x86_64",
            "centos-stream-9-x86_64",
            id="no_mapping",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    _targets=["centos-stream-9-x86_64"],
                ),
            ],
            "centos-stream-9-x86_64",
            "centos-stream-9-x86_64",
            id="no_mapping_targets_defined_in_build",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    _targets=["centos-stream-9-x86_64"],
                    use_internal_tf=True,
                )
            ],
            "centos-stream-9-x86_64",
            "centos-stream-9-x86_64",
            id="no_mapping_internal_tf",
        ),
    ],
)
def test_copr_test_target2build_target(job_config, test_target, build_target):
    jobs = job_config
    copr_build_helper = CoprBuildJobHelper(
        service_config=ServiceConfig.get_service_config(),
        package_config=PackageConfig(jobs=jobs),
        job_config=jobs[0],
        project=flexmock(),
        metadata=flexmock(pr_id=None),
        db_trigger=flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
    )
    flexmock(copr_build).should_receive("get_valid_build_targets").with_args(
        "fedora-31", "fedora-32", default=None
    ).and_return(STABLE_CHROOTS)
    flexmock(copr_build).should_receive("get_valid_build_targets").with_args(
        "fedora-32", "fedora-31", default=None
    ).and_return(STABLE_CHROOTS)
    flexmock(copr_build).should_receive("get_valid_build_targets").with_args(
        "centos-stream-9-x86_64", default=None
    ).and_return({"centos-stream-9-x86_64"})
    flexmock(copr_build).should_receive("get_valid_build_targets").with_args(
        "epel-7-x86_64", default=None
    ).and_return({"epel-7-x86_64"})
    assert copr_build_helper.test_target2build_target(test_target) == build_target


@pytest.mark.parametrize(
    "jobs,job_config_trigger_type,targets_override,build_targets",
    [
        pytest.param(
            [
                JobConfig(
                    type=JobType.production_build,
                    trigger=JobConfigTriggerType.pull_request,
                    _targets=STABLE_VERSIONS,
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
                    _targets=STABLE_VERSIONS,
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
        service_config=ServiceConfig.get_service_config(),
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
        service_config=ServiceConfig.get_service_config(),
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
    "jobs,job_config_trigger_type,job_trigger_model_type,tag_name,job_owner,job_project",
    [
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                )
            ],
            JobConfigTriggerType.pull_request,
            JobTriggerModelType.pull_request,
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
                    owner="custom-owner",
                )
            ],
            JobConfigTriggerType.pull_request,
            JobTriggerModelType.pull_request,
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
                    project="custom-project",
                )
            ],
            JobConfigTriggerType.pull_request,
            JobTriggerModelType.pull_request,
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
                    owner="custom-owner",
                    project="custom-project",
                )
            ],
            JobConfigTriggerType.pull_request,
            JobTriggerModelType.pull_request,
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
                    owner="custom-owner",
                    project="custom-project",
                )
            ],
            JobConfigTriggerType.pull_request,
            JobTriggerModelType.pull_request,
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
                )
            ],
            JobConfigTriggerType.commit,
            JobTriggerModelType.branch_push,
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
                )
            ],
            JobConfigTriggerType.release,
            JobTriggerModelType.release,
            "v1.O.0",
            "nobody",
            "git.instance.io-the-example-namespace-the-example-repo-releases",
            id="release&default-owner&default-project",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                )
            ],
            JobConfigTriggerType.release,
            JobTriggerModelType.release,
            None,
            "nobody",
            "git.instance.io-the-example-namespace-the-example-repo-releases",
            id="release-without-tag&default-owner&default-project",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    owner="commit-owner",
                    project="commit-project",
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    owner="pr-owner",
                    project="pr-project",
                ),
            ],
            JobConfigTriggerType.pull_request,
            JobTriggerModelType.pull_request,
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
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                ),
            ],
            JobConfigTriggerType.pull_request,
            JobTriggerModelType.pull_request,
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
                    owner="custom-owner",
                    project="custom-project",
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                ),
            ],
            JobConfigTriggerType.pull_request,
            JobTriggerModelType.pull_request,
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
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    owner="custom-owner",
                    project="custom-project",
                ),
            ],
            JobConfigTriggerType.pull_request,
            JobTriggerModelType.pull_request,
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
                    owner="pr-owner",
                    project="pr-project",
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    owner="commit-owner",
                    project="commit-project",
                ),
            ],
            JobConfigTriggerType.pull_request,
            JobTriggerModelType.pull_request,
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
                    owner="pr-owner",
                    project="pr-project",
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    owner="commit-owner",
                    project="commit-project",
                ),
            ],
            JobConfigTriggerType.commit,
            JobTriggerModelType.branch_push,
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
    job_trigger_model_type,
    tag_name,
    job_owner,
    job_project,
):
    copr_build_helper = CoprBuildJobHelper(
        service_config=ServiceConfig.get_service_config(),
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
        db_trigger=flexmock(
            job_config_trigger_type=job_config_trigger_type,
            job_trigger_model_type=job_trigger_model_type,
        ),
    )
    copr_build_helper._api = flexmock(
        copr_helper=flexmock(copr_client=flexmock(config={"username": "nobody"}))
    )

    assert copr_build_helper.job_project == job_project
    assert copr_build_helper.job_owner == job_owner


@pytest.mark.parametrize(
    "job,git_forge_allowed_list,allowed",
    [
        pytest.param(
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                owner="the-owner",
                project="the-project",
            ),
            "",
            False,
            id="empty",
        ),
        pytest.param(
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                owner="the-owner",
                project="the-project",
            ),
            "something/different",
            False,
            id="not-present",
        ),
        pytest.param(
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                owner="the-owner",
                project="the-project",
            ),
            "git.instance.io/the/example/namespace/the-example-repo",
            True,
            id="present",
        ),
        pytest.param(
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                owner="the-owner",
                project="the-project",
            ),
            "something/different\ngit.instance.io/the/example/namespace/the-example-repo",
            True,
            id="present-more-values",
        ),
    ],
)
def test_check_if_custom_copr_can_be_used_and_report(
    job,
    git_forge_allowed_list,
    allowed,
):
    service_config = ServiceConfig.get_service_config()
    copr_build_helper = CoprBuildJobHelper(
        service_config=service_config,
        package_config=PackageConfig(jobs=[job]),
        job_config=job,  # BuildHelper looks at all jobs in the end
        project=flexmock(
            namespace="the/example/namespace",
            repo="the-example-repo",
            service=flexmock(
                instance_url="https://git.instance.io", hostname="git.instance.io"
            ),
        ),
        metadata=flexmock(pr_id=None, identifier="the-event-identifier", tag_name=None),
        db_trigger=flexmock(
            job_config_trigger_type=JobConfigTriggerType.pull_request,
            job_trigger_model_type=JobTriggerModelType.pull_request,
        ),
    )
    copr_helper = flexmock(
        copr_client=flexmock(
            config={"username": "nobody"},
            project_proxy=flexmock(
                get=lambda owner, project: {
                    "packit_forge_projects_allowed": git_forge_allowed_list
                }
            ),
        )
    )
    copr_helper.should_receive("get_copr_settings_url").with_args(
        "the-owner", "the-project"
    ).and_return().times(0 if allowed else 1)
    copr_build_helper._api = flexmock(copr_helper=copr_helper)
    flexmock(CoprBuildJobHelper).should_receive("report_status_to_build").times(
        0 if allowed else 1
    )
    assert copr_build_helper.check_if_custom_copr_can_be_used_and_report() is allowed


@pytest.mark.parametrize(
    "jobs,job_config_trigger_type,build_targets,koji_targets",
    [
        pytest.param(
            [
                JobConfig(
                    type=JobType.production_build,
                    trigger=JobConfigTriggerType.pull_request,
                    _targets=STABLE_VERSIONS,
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
                    _targets=STABLE_VERSIONS,
                    branch="build-branch",
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
                    _targets=STABLE_VERSIONS,
                    branch="build-branch",
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
        service_config=ServiceConfig.get_service_config(),
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
    service_config = ServiceConfig.get_service_config()
    service_config.repository_cache = "/tmp/repository-cache"
    service_config.add_repositories_to_repository_cache = False
    service_config.command_handler_work_dir = "/tmp/some-dir"

    copr_build_helper = CoprBuildJobHelper(
        service_config=service_config,
        package_config=PackageConfig(
            jobs=[
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    _targets=STABLE_VERSIONS,
                )
            ],
        ),
        job_config=JobConfig(
            type=JobType.copr_build,
            trigger=JobConfigTriggerType.pull_request,
            _targets=STABLE_VERSIONS,
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
        )
    ]
    copr_build_helper = CoprBuildJobHelper(
        service_config=ServiceConfig.get_service_config(),
        package_config=PackageConfig(jobs=jobs),
        job_config=jobs[0],
        project=flexmock(),
        metadata=flexmock(pr_id=1),
        db_trigger=flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
    )
    flexmock(LocalProject).should_receive("__init__").never()
    assert copr_build_helper.api
    assert copr_build_helper.api.copr_helper
