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
from packit.config.aliases import Distro, get_build_targets
from packit.config.notifications import (
    FailureCommentNotificationsConfig,
    NotificationsConfig,
)
from packit.copr_helper import CoprHelper
from packit.local_project import LocalProject
from packit.utils.repo import RepositoryCache

from packit_service.config import ServiceConfig
from packit_service.models import ProjectEventModelType
from packit_service.worker.helpers.build.copr_build import CoprBuildJobHelper
from packit_service.worker.helpers.build.koji_build import KojiBuildJobHelper

# packit.config.aliases.get_aliases() return value example
from packit_service.worker.helpers.testing_farm import TestingFarmJobHelper
from packit_service.worker.reporting import DuplicateCheckMode, StatusReporter

ALIASES = {
    "fedora-development": [Distro("fedora-33", "f33"), Distro("fedora-rawhide", "rawhide")],
    "fedora-stable": [Distro("fedora-31", "f31"), Distro("fedora-32", "f32")],
    "fedora-all": [
        Distro("fedora-31", "f31"),
        Distro("fedora-32", "f32"),
        Distro("fedora-33", "f33"),
        Distro("fedora-rawhide", "rawhide"),
    ],
    "epel-all": [Distro("epel-6", "el6"), Distro("epel-7", "epel7"), Distro("epel-8", "epel8")],
}

STABLE_VERSIONS = [d.namever for d in ALIASES["fedora-stable"]]
STABLE_CHROOTS = {f"{version}-x86_64" for version in STABLE_VERSIONS}
ONE_CHROOT_SET = {next(iter(STABLE_CHROOTS))}
STABLE_KOJI_TARGETS = {d.branch for d in ALIASES["fedora-stable"]}
ONE_KOJI_TARGET_SET = {next(iter(STABLE_KOJI_TARGETS))}

pytestmark = pytest.mark.usefixtures("mock_get_aliases")


def _mock_targets(jobs, job, job_type):
    job_config_trigger_type, project_event_model_type = job_type

    project_service = flexmock(instance_url="https://github.com")
    db_project_object = flexmock(
        job_config_trigger_type=job_config_trigger_type,
        project_event_model_type=project_event_model_type,
    )
    if job_config_trigger_type == JobConfigTriggerType.commit:
        db_project_object.name = "main"

    return CoprBuildJobHelper(
        service_config=ServiceConfig.get_service_config(),
        package_config=PackageConfig(
            jobs=jobs,
            packages={"package": CommonPackageConfig()},
        ),
        job_config=job,  # BuildHelper looks at all jobs in the end
        project=flexmock(
            service=project_service,
            namespace="packit",
            repo="testing_package",
            default_branch="main",
        ),
        metadata=flexmock(pr_id=None, identifier=None),
        db_project_event=flexmock()
        .should_receive("get_project_event_object")
        .and_return(db_project_object)
        .mock(),
    )


@pytest.mark.parametrize(
    "jobs,job_type,build_chroots",
    [
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=STABLE_VERSIONS,
                        ),
                    },
                ),
            ],
            (JobConfigTriggerType.pull_request, ProjectEventModelType.pull_request),
            set(STABLE_VERSIONS),
            id="build_with_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=STABLE_VERSIONS,
                        ),
                    },
                ),
            ],
            (JobConfigTriggerType.pull_request, ProjectEventModelType.pull_request),
            set(STABLE_VERSIONS),
            id="build_with_targets&pr_comment",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=STABLE_VERSIONS,
                        ),
                    },
                ),
            ],
            (JobConfigTriggerType.release, ProjectEventModelType.release),
            set(STABLE_VERSIONS),
            id="build_with_targets&release",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=STABLE_VERSIONS,
                        ),
                    },
                ),
            ],
            (JobConfigTriggerType.commit, ProjectEventModelType.branch_push),
            set(STABLE_VERSIONS),
            id="build_with_targets&push",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=STABLE_VERSIONS,
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=["different", "os", "target"],
                        ),
                    },
                ),
            ],
            (JobConfigTriggerType.pull_request, ProjectEventModelType.pull_request),
            set(STABLE_VERSIONS),
            id="build_with_targets&pull_request_with_pr_and_push_defined",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=STABLE_VERSIONS,
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=["different", "os", "target"],
                        ),
                    },
                ),
            ],
            (JobConfigTriggerType.pull_request, ProjectEventModelType.pull_request),
            set(STABLE_VERSIONS),
            id="build_with_targets&pr_comment_with_pr_and_push_defined",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=["different", "os", "target"],
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=STABLE_VERSIONS,
                        ),
                    },
                ),
            ],
            (JobConfigTriggerType.commit, ProjectEventModelType.branch_push),
            set(STABLE_VERSIONS),
            id="build_with_targets&push_with_pr_and_push_defined",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"packages": CommonPackageConfig()},
                ),
            ],
            (JobConfigTriggerType.pull_request, ProjectEventModelType.pull_request),
            {"fedora-stable"},
            id="build_without_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"packages": CommonPackageConfig()},
                ),
            ],
            (JobConfigTriggerType.pull_request, ProjectEventModelType.pull_request),
            {"fedora-stable"},
            id="test_without_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=STABLE_VERSIONS,
                        ),
                    },
                ),
            ],
            (JobConfigTriggerType.pull_request, ProjectEventModelType.pull_request),
            set(STABLE_VERSIONS),
            id="test_with_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"packages": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"packages": CommonPackageConfig()},
                ),
            ],
            (JobConfigTriggerType.pull_request, ProjectEventModelType.pull_request),
            {"fedora-stable"},
            id="build_without_target&test_without_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=STABLE_VERSIONS,
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"packages": CommonPackageConfig()},
                ),
            ],
            (JobConfigTriggerType.pull_request, ProjectEventModelType.pull_request),
            set(STABLE_VERSIONS),
            id="build_with_target&test_without_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"packages": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=STABLE_VERSIONS,
                        ),
                    },
                ),
            ],
            (JobConfigTriggerType.pull_request, ProjectEventModelType.pull_request),
            set(STABLE_VERSIONS),
            id="build_without_target&test_with_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"packages": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=list(ONE_CHROOT_SET),
                        ),
                    },
                ),
            ],
            (JobConfigTriggerType.pull_request, ProjectEventModelType.pull_request),
            ONE_CHROOT_SET,
            id="build_without_target&test_with_one_str_target",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"packages": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"packages": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"packages": CommonPackageConfig()},
                ),
            ],
            (JobConfigTriggerType.commit, ProjectEventModelType.branch_push),
            {"fedora-stable"},
            id="build[pr+commit]&test[pr]&commit",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"packages": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"packages": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"packages": CommonPackageConfig()},
                ),
            ],
            (JobConfigTriggerType.pull_request, ProjectEventModelType.pull_request),
            {"fedora-stable"},
            id="build[pr+commit]&test[pr]&pr",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"packages": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.commit,
                    packages={"packages": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"packages": CommonPackageConfig()},
                ),
            ],
            (JobConfigTriggerType.commit, ProjectEventModelType.branch_push),
            {"fedora-stable"},
            id="build[pr+commit]&test[commit]&commit",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"packages": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"packages": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.commit,
                    packages={"packages": CommonPackageConfig()},
                ),
            ],
            (JobConfigTriggerType.pull_request, ProjectEventModelType.pull_request),
            {"fedora-stable"},
            id="build[pr+commit]&test[commit]&pr",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"packages": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    packages={"packages": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"packages": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"packages": CommonPackageConfig()},
                ),
            ],
            (JobConfigTriggerType.commit, ProjectEventModelType.branch_push),
            {"fedora-stable"},
            id="build[pr+commit+release]&test[pr]&commit",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"packages": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=list(ONE_CHROOT_SET),
                        ),
                    },
                ),
            ],
            (JobConfigTriggerType.pull_request, ProjectEventModelType.pull_request),
            ONE_CHROOT_SET,
            id="build_with_mixed_build_alias",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=STABLE_VERSIONS,
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=["fedora-rawhide"],
                        ),
                    },
                ),
            ],
            (JobConfigTriggerType.pull_request, ProjectEventModelType.pull_request),
            {*STABLE_VERSIONS, "fedora-rawhide"},
            id="build_with_mixed_build_tests",
        ),
    ],
)
def test_configured_build_targets(jobs, job_type, build_chroots):
    copr_build_helper = _mock_targets(jobs, jobs[0], job_type)

    assert copr_build_helper.package_config.jobs
    assert [j.type for j in copr_build_helper.package_config.jobs]

    assert copr_build_helper.configured_build_targets == build_chroots


@pytest.mark.parametrize(
    "jobs,job_type,test_chroots",
    [
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            (JobConfigTriggerType.pull_request, ProjectEventModelType.pull_request),
            {"fedora-stable"},
            id="test_without_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=STABLE_VERSIONS,
                        ),
                    },
                ),
            ],
            (JobConfigTriggerType.pull_request, ProjectEventModelType.pull_request),
            set(STABLE_VERSIONS),
            id="test_with_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            (JobConfigTriggerType.pull_request, ProjectEventModelType.pull_request),
            {"fedora-stable"},
            id="build_without_target&test_without_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=STABLE_VERSIONS,
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            (JobConfigTriggerType.pull_request, ProjectEventModelType.pull_request),
            set(STABLE_VERSIONS),
            id="build_with_target&test_without_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=STABLE_VERSIONS,
                        ),
                    },
                ),
            ],
            (JobConfigTriggerType.pull_request, ProjectEventModelType.pull_request),
            set(STABLE_VERSIONS),
            id="build_without_target&test_with_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=list(ONE_CHROOT_SET),
                        ),
                    },
                ),
            ],
            (JobConfigTriggerType.pull_request, ProjectEventModelType.pull_request),
            ONE_CHROOT_SET,
            id="build_without_target&test_with_one_str_target",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            (JobConfigTriggerType.commit, ProjectEventModelType.branch_push),
            set(),
            id="build[pr+commit]&test[pr]&commit",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            (JobConfigTriggerType.pull_request, ProjectEventModelType.pull_request),
            {"fedora-stable"},
            id="build[pr+commit]&test[pr]&pr",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            (JobConfigTriggerType.commit, ProjectEventModelType.branch_push),
            {"fedora-stable"},
            id="build[pr+commit]&test[commit]&commit",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            (JobConfigTriggerType.pull_request, ProjectEventModelType.pull_request),
            set(),
            id="build[pr+commit]&test[commit]&pr",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            (JobConfigTriggerType.commit, ProjectEventModelType.branch_push),
            set(),
            id="build[pr+commit+release]&test[pr]&commit",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=list(ONE_CHROOT_SET),
                        ),
                    },
                ),
            ],
            (JobConfigTriggerType.pull_request, ProjectEventModelType.pull_request),
            ONE_CHROOT_SET,
            id="build_with_mixed_build_alias",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=STABLE_VERSIONS,
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=["fedora-rawhide"],
                        ),
                    },
                ),
            ],
            (JobConfigTriggerType.pull_request, ProjectEventModelType.pull_request),
            {"fedora-rawhide"},
            id="build_with_mixed_build_tests",
        ),
    ],
)
def test_configured_tests_targets(jobs, job_type, test_chroots):
    job_config_trigger_type, project_event_model_type = job_type
    db_project_object = flexmock(
        job_config_trigger_type=job_config_trigger_type,
        project_event_model_type=project_event_model_type,
    )
    db_project_event = (
        flexmock().should_receive("get_project_event_object").and_return(db_project_object).mock()
    )
    if job_config_trigger_type == JobConfigTriggerType.commit:
        db_project_object.name = "main"

    project_service = flexmock(instance_url="https://github.com")
    helper = TestingFarmJobHelper(
        service_config=ServiceConfig.get_service_config(),
        package_config=PackageConfig(
            jobs=jobs,
            packages={"package": CommonPackageConfig()},
        ),
        job_config=jobs[-1],  # test job is always the last in the list
        project=flexmock(
            service=project_service,
            namespace="packit",
            repo="testing_package",
            default_branch="main",
        ),
        metadata=flexmock(pr_id=None, identifier=None),
        db_project_event=db_project_event,
    )

    assert helper.package_config.jobs
    assert [j.type for j in helper.package_config.jobs]

    assert helper.configured_tests_targets == test_chroots


def test_deduced_copr_targets():
    jobs = [
        JobConfig(
            type=JobType.copr_build,
            trigger=JobConfigTriggerType.commit,
            packages={
                "package": CommonPackageConfig(
                    owner="mf",
                    project="custom-copr-targets",
                ),
            },
        ),
        JobConfig(
            type=JobType.copr_build,
            trigger=JobConfigTriggerType.release,
            packages={"packages": CommonPackageConfig()},
        ),
        JobConfig(
            type=JobType.copr_build,
            trigger=JobConfigTriggerType.pull_request,
            packages={"packages": CommonPackageConfig()},
        ),
        JobConfig(
            type=JobType.tests,
            trigger=JobConfigTriggerType.commit,
            packages={"packages": CommonPackageConfig()},
        ),
    ]
    job_type = (JobConfigTriggerType.commit, ProjectEventModelType.branch_push)
    copr_build_helper = _mock_targets(jobs, jobs[0], job_type)
    flexmock(CoprHelper).should_receive("get_chroots").with_args(
        owner=jobs[0].owner,
        project=jobs[0].project,
    ).and_return({"opensuse-tumbleweed-x86_64"})

    assert copr_build_helper.package_config.jobs
    assert [j.type for j in copr_build_helper.package_config.jobs]

    assert copr_build_helper.configured_build_targets == {"opensuse-tumbleweed-x86_64"}
    assert TestingFarmJobHelper(
        service_config=ServiceConfig.get_service_config(),
        package_config=PackageConfig(
            jobs=jobs,
            packages={"package": CommonPackageConfig()},
        ),
        job_config=jobs[-1],  # BuildHelper looks at all jobs in the end
        project=flexmock(
            service=flexmock(),
            namespace="packit",
            repo="testing_package",
            default_branch="main",
        ),
        metadata=flexmock(pr_id=None, identifier=None),
        db_project_event=flexmock()
        .should_receive("get_project_event_object")
        .and_return(
            flexmock(
                job_config_trigger_type=job_type[0],
                project_event_model_type=job_type[1],
                name="main",
            ),
        )
        .mock(),
    ).configured_tests_targets == {"opensuse-tumbleweed-x86_64"}


@pytest.mark.parametrize(
    "jobs,job_config_trigger_type,build_targets_override,"
    "tests_targets_override,build_targets,build_targets_for_job,tests_targets_for_job",
    [
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=STABLE_VERSIONS,
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"packages": CommonPackageConfig()},
                ),
            ],
            JobConfigTriggerType.pull_request,
            {("fedora-32-x86_64", None)},
            None,
            {"fedora-32-x86_64"},
            [{"fedora-32-x86_64"}],
            [{"fedora-32-x86_64"}],
            id="target_in_config_for_both",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=STABLE_VERSIONS,
                        ),
                    },
                ),
            ],
            JobConfigTriggerType.pull_request,
            {("fedora-32-x86_64", None)},
            None,
            {"fedora-32-x86_64"},
            [{"fedora-32-x86_64"}],
            None,
            id="target_in_config",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=STABLE_VERSIONS,
                        ),
                    },
                ),
            ],
            JobConfigTriggerType.pull_request,
            {("fedora-33-x86_64", None)},
            None,
            set(),
            [set()],
            None,
            id="target_not_in_config",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets={
                                "epel-7-x86_64": {"distros": ["centos-7", "rhel-7"]},
                            },
                        ),
                    },
                ),
            ],
            JobConfigTriggerType.pull_request,
            None,
            {("centos-7-x86_64", None)},
            {"epel-7-x86_64"},
            None,
            [{"centos-7-x86_64"}],
            id="build_test_mapping_test_overrides",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets={
                                "epel-7-x86_64": {"distros": ["centos-7", "rhel-7"]},
                            },
                        ),
                    },
                ),
            ],
            JobConfigTriggerType.pull_request,
            {("epel-7-x86_64", None)},
            None,
            {"epel-7-x86_64"},
            None,
            [{"centos-7-x86_64", "rhel-7-x86_64"}],
            id="build_test_mapping_build_overrides",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=["centos-stream-8"],
                        ),
                    },
                ),
            ],
            JobConfigTriggerType.pull_request,
            None,
            {("centos-stream-8-x86_64", None)},
            {"centos-stream-8-x86_64"},
            None,
            [{"centos-stream-8-x86_64"}],
            id="targets_in_tests_no_mapping",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=["centos-stream-8"],
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"packages": CommonPackageConfig()},
                ),
            ],
            JobConfigTriggerType.pull_request,
            None,
            {("centos-stream-8-x86_64", None)},
            {"centos-stream-8-x86_64"},
            [{"centos-stream-8-x86_64"}],
            [{"centos-stream-8-x86_64"}],
            id="targets_in_build_no_mapping",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=["epel-7-x86_64"],
                        ),
                    },
                ),
            ],
            JobConfigTriggerType.pull_request,
            {("epel-7-x86_64", None)},
            None,
            {"epel-7-x86_64"},
            None,
            [{"centos-7-x86_64"}],
            id="default_mapping_build_override",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=["epel-7-x86_64"],
                        ),
                    },
                ),
            ],
            JobConfigTriggerType.pull_request,
            None,
            {("centos-7-x86_64", None)},
            {"epel-7-x86_64"},
            None,
            [{"centos-7-x86_64"}],
            id="default_mapping_test_override",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=["epel-7-ppc64le"],
                        ),
                    },
                ),
            ],
            JobConfigTriggerType.pull_request,
            {("epel-7-ppc64le", None)},
            None,
            {"epel-7-ppc64le"},
            None,
            [{"centos-7-ppc64le"}],
            id="default_mapping_build_override_different_arch",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=["epel-7-ppc64le"],
                        ),
                    },
                ),
            ],
            JobConfigTriggerType.pull_request,
            None,
            {("centos-7-ppc64le", None)},
            {"epel-7-ppc64le"},
            None,
            [{"centos-7-ppc64le"}],
            id="default_mapping_test_override_different_arch",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=["fedora-41-x86_64"], identifier="latest"
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=["fedora-rawhide-x86_64"],
                        ),
                    },
                ),
            ],
            JobConfigTriggerType.pull_request,
            {("fedora-rawhide-x86_64", None)},
            None,
            {"fedora-rawhide-x86_64"},
            None,
            [{"fedora-rawhide-x86_64"}, set()],
            id="rebuild_default_job_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=["fedora-41-x86_64"], identifier="latest"
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=["fedora-rawhide-x86_64"],
                        ),
                    },
                ),
            ],
            JobConfigTriggerType.pull_request,
            {("fedora-41-x86_64", "latest")},
            None,
            {"fedora-41-x86_64"},
            None,
            [set(), {"fedora-41-x86_64"}],
            id="rebuild_latest_job_targets",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=["fedora-41-x86_64", "fedora-rawhide-x86_64"],
                            identifier="latest",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=["fedora-rawhide-x86_64"],
                        ),
                    },
                ),
            ],
            JobConfigTriggerType.pull_request,
            {("fedora-41-x86_64", "latest")},
            None,
            {"fedora-41-x86_64"},
            None,
            [set(), {"fedora-41-x86_64"}],
            id="rebuild_latest_job_targets_for_job_with_identifier",
        ),
    ],
)
def test_build_targets_overrides(
    jobs,
    job_config_trigger_type,
    build_targets_override,
    tests_targets_override,
    build_targets,
    build_targets_for_job,
    tests_targets_for_job,
):
    copr_build_helper = CoprBuildJobHelper(
        service_config=ServiceConfig.get_service_config(),
        package_config=PackageConfig(
            jobs=jobs,
            packages={"package": CommonPackageConfig()},
        ),
        job_config=jobs[-1],  # BuildHelper looks at all jobs in the end
        project=flexmock(),
        metadata=flexmock(pr_id=None),
        db_project_event=flexmock()
        .should_receive("get_project_event_object")
        .and_return(flexmock(job_config_trigger_type=job_config_trigger_type))
        .mock(),
        build_targets_override=build_targets_override,
        tests_targets_override=tests_targets_override,
    )
    flexmock(CoprHelper).should_receive("get_valid_build_targets").with_args(
        "fedora-31",
        "fedora-32",
        default=None,
    ).and_return(STABLE_CHROOTS)
    flexmock(CoprHelper).should_receive("get_valid_build_targets").with_args(
        "fedora-32",
        "fedora-31",
        default=None,
    ).and_return(STABLE_CHROOTS)
    flexmock(CoprHelper).should_receive("get_valid_build_targets").with_args(
        default=None,
    ).and_return(set())
    flexmock(CoprHelper).should_receive("get_valid_build_targets").with_args(
        "epel-7-x86_64",
        default=None,
    ).and_return({"epel-7-x86_64"})
    flexmock(CoprHelper).should_receive("get_valid_build_targets").with_args(
        "epel-7-ppc64le",
        default=None,
    ).and_return({"epel-7-ppc64le"})
    flexmock(CoprHelper).should_receive("get_valid_build_targets").with_args(
        "centos-stream-8",
        default=None,
    ).and_return({"centos-stream-8-x86_64"})
    flexmock(CoprHelper).should_receive("get_valid_build_targets").with_args(
        "fedora-rawhide-x86_64",
        "fedora-41-x86_64",
        default=None,
    ).and_return({"fedora-rawhide-x86_64", "fedora-41-x86_64"})
    flexmock(CoprHelper).should_receive("get_valid_build_targets").with_args(
        "fedora-41-x86_64",
        default=None,
    ).and_return({"fedora-41-x86_64"})
    flexmock(CoprHelper).should_receive("get_valid_build_targets").with_args(
        "fedora-rawhide-x86_64",
        default=None,
    ).and_return({"fedora-rawhide-x86_64"})
    flexmock(CoprHelper).should_receive("get_valid_build_targets").with_args(
        "fedora-41-x86_64",
        "fedora-rawhide-x86_64",
        default=None,
    ).and_return({"fedora-rawhide-x86_64", "fedora-41-x86_64"})

    assert copr_build_helper.build_targets == build_targets
    for job in [job for job in jobs if job.type == JobType.copr_build]:
        assert copr_build_helper.build_targets_for_test_job(job) == build_targets_for_job.pop()
    for job in [job for job in jobs if job.type == JobType.tests]:
        assert copr_build_helper.tests_targets_for_test_job(job) == tests_targets_for_job.pop()


@pytest.mark.parametrize(
    "jobs,job_config_trigger_type,build_targets_override,tests_targets_override,test_targets",
    [
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=STABLE_VERSIONS,
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            JobConfigTriggerType.pull_request,
            {("fedora-32-x86_64", None)},
            None,
            {"fedora-32-x86_64"},
            id="target_in_config_for_both",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets={
                                "epel-7-x86_64": {"distros": ["centos-7", "rhel-7"]},
                            },
                        ),
                    },
                ),
            ],
            JobConfigTriggerType.pull_request,
            None,
            {("centos-7-x86_64", None)},
            {"centos-7-x86_64"},
            id="build_test_mapping_test_overrides",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets={
                                "epel-7-x86_64": {"distros": ["centos-7", "rhel-7"]},
                            },
                        ),
                    },
                ),
            ],
            JobConfigTriggerType.pull_request,
            {("epel-7-x86_64", None)},
            None,
            {"centos-7-x86_64", "rhel-7-x86_64"},
            id="build_test_mapping_build_overrides",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=["centos-stream-8"],
                        ),
                    },
                ),
            ],
            JobConfigTriggerType.pull_request,
            None,
            {("centos-stream-8-x86_64", None)},
            {"centos-stream-8-x86_64"},
            id="targets_in_tests_no_mapping",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=["centos-stream-8"],
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            JobConfigTriggerType.pull_request,
            None,
            {("centos-stream-8-x86_64", None)},
            {"centos-stream-8-x86_64"},
            id="targets_in_build_no_mapping",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=["epel-7-x86_64"],
                        ),
                    },
                ),
            ],
            JobConfigTriggerType.pull_request,
            {("epel-7-x86_64", None)},
            None,
            {"centos-7-x86_64"},
            id="default_mapping_build_override",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=["epel-7-x86_64"],
                        ),
                    },
                ),
            ],
            JobConfigTriggerType.pull_request,
            None,
            {("centos-7-x86_64", None)},
            {"centos-7-x86_64"},
            id="default_mapping_test_override",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=["epel-7-ppc64le"],
                        ),
                    },
                ),
            ],
            JobConfigTriggerType.pull_request,
            {("epel-7-ppc64le", None)},
            None,
            {"centos-7-ppc64le"},
            id="default_mapping_build_override_different_arch",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=["epel-7-ppc64le"],
                        ),
                    },
                ),
            ],
            JobConfigTriggerType.pull_request,
            None,
            {("centos-7-ppc64le", None)},
            {"centos-7-ppc64le"},
            id="default_mapping_test_override_different_arch",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=["epel-7-x86_64"],
                        ),
                    },
                ),
            ],
            JobConfigTriggerType.pull_request,
            {("fedora-rawhide-x86_64", None)},
            None,
            set(),
            id="build-target-not-in-test",
        ),
    ],
)
def test_tests_targets_overrides(
    jobs,
    job_config_trigger_type,
    build_targets_override,
    tests_targets_override,
    test_targets,
):
    testing_farm_helper = TestingFarmJobHelper(
        service_config=ServiceConfig.get_service_config(),
        package_config=PackageConfig(
            jobs=jobs,
            packages={"package": CommonPackageConfig()},
        ),
        job_config=jobs[-1],  # BuildHelper looks at all jobs in the end
        project=flexmock(),
        metadata=flexmock(pr_id=None),
        db_project_event=flexmock()
        .should_receive("get_project_event_object")
        .and_return(flexmock(job_config_trigger_type=job_config_trigger_type))
        .mock(),
        build_targets_override=build_targets_override,
        tests_targets_override=tests_targets_override,
    )
    flexmock(CoprHelper).should_receive("get_valid_build_targets").with_args(
        "fedora-31",
        "fedora-32",
        default=None,
    ).and_return(STABLE_CHROOTS)
    flexmock(CoprHelper).should_receive("get_valid_build_targets").with_args(
        "fedora-32",
        "fedora-31",
        default=None,
    ).and_return(STABLE_CHROOTS)
    flexmock(CoprHelper).should_receive("get_valid_build_targets").with_args(
        default=None,
    ).and_return(set())
    flexmock(CoprHelper).should_receive("get_valid_build_targets").with_args(
        "epel-7-x86_64",
        default=None,
    ).and_return({"epel-7-x86_64"})
    flexmock(CoprHelper).should_receive("get_valid_build_targets").with_args(
        "epel-7-ppc64le",
        default=None,
    ).and_return({"epel-7-ppc64le"})
    flexmock(CoprHelper).should_receive("get_valid_build_targets").with_args(
        "centos-stream-8",
        default=None,
    ).and_return({"centos-stream-8-x86_64"})
    assert testing_farm_helper.tests_targets == test_targets


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
    configured_targets,
    use_internal_tf,
    build_target,
    test_targets,
):
    jobs = [
        JobConfig(
            type=JobType.tests,
            trigger=JobConfigTriggerType.pull_request,
            packages={
                "package": CommonPackageConfig(
                    _targets=configured_targets,
                    use_internal_tf=use_internal_tf,
                ),
            },
        ),
    ]
    copr_build_helper = CoprBuildJobHelper(
        service_config=ServiceConfig.get_service_config(),
        package_config=PackageConfig(
            jobs=jobs,
            packages={"package": CommonPackageConfig()},
        ),
        job_config=jobs[0],
        project=flexmock(),
        metadata=flexmock(pr_id=None),
        db_project_event=flexmock()
        .should_receive("get_project_event_object")
        .and_return(flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request))
        .mock(),
    )
    flexmock(CoprHelper, get_valid_build_targets=get_build_targets)
    assert (
        copr_build_helper.build_target2test_targets_for_test_job(build_target, jobs[0])
        == test_targets
    )


def test_copr_build_and_test_targets_both_jobs_defined():
    jobs = [
        JobConfig(
            type=JobType.tests,
            trigger=JobConfigTriggerType.pull_request,
            packages={
                "package": CommonPackageConfig(
                    _targets={
                        "epel-8-x86_64": {},
                        "fedora-35-x86_64": {"distros": ["fedora-35", "fedora-36"]},
                    },
                ),
            },
        ),
        JobConfig(
            type=JobType.copr_build,
            trigger=JobConfigTriggerType.pull_request,
            packages={
                "package": CommonPackageConfig(
                    _targets=["fedora-35", "fedora-36", "epel-8"],
                ),
            },
        ),
    ]
    flexmock(CoprHelper, get_valid_build_targets=get_build_targets)
    for i in [0, 1]:
        helper = CoprBuildJobHelper if jobs[i].type == JobType.copr_build else TestingFarmJobHelper
        helper = helper(
            service_config=ServiceConfig.get_service_config(),
            package_config=PackageConfig(
                jobs=jobs,
                packages={"package": CommonPackageConfig()},
            ),
            job_config=jobs[i],
            project=flexmock(),
            metadata=flexmock(pr_id=None),
            db_project_event=flexmock()
            .should_receive("get_project_event_object")
            .and_return(
                flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            )
            .mock(),
        )
        assert helper.build_target2test_targets_for_test_job(
            "fedora-35-x86_64",
            jobs[0],
        ) == {
            "fedora-35-x86_64",
            "fedora-36-x86_64",
        }
        assert helper.build_target2test_targets_for_test_job("fedora-36-x86_64", jobs[0]) == set()
        assert helper.build_target2test_targets_for_test_job(
            "epel-8-x86_64",
            jobs[0],
        ) == {"centos-stream-8-x86_64"}
        assert helper.build_targets == {
            "fedora-35-x86_64",
            "fedora-36-x86_64",
            "epel-8-x86_64",
        }
        if isinstance(helper, TestingFarmJobHelper):
            assert helper.build_targets_for_tests == {
                "fedora-35-x86_64",
                "epel-8-x86_64",
            }
            assert helper.tests_targets == {
                "fedora-35-x86_64",
                "fedora-36-x86_64",
                "centos-stream-8-x86_64",
            }


@pytest.mark.parametrize(
    "job_config,test_target,build_target",
    [
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=STABLE_VERSIONS,
                        ),
                    },
                ),
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
                    packages={
                        "package": CommonPackageConfig(
                            _targets={
                                "epel-7-x86_64": {"distros": ["centos-7", "rhel-7"]},
                            },
                        ),
                    },
                ),
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
                    packages={
                        "package": CommonPackageConfig(
                            _targets={
                                "epel-7-x86_64": {"distros": ["centos-7", "rhel-7"]},
                            },
                        ),
                    },
                ),
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
                    packages={
                        "package": CommonPackageConfig(
                            _targets={
                                "epel-7-x86_64": {"distros": ["centos-7", "rhel-7"]},
                            },
                        ),
                    },
                ),
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
                    packages={
                        "package": CommonPackageConfig(
                            _targets=["epel-7-x86_64"],
                        ),
                    },
                ),
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
                    packages={
                        "package": CommonPackageConfig(
                            _targets=["epel-7-x86_64"],
                            use_internal_tf=True,
                        ),
                    },
                ),
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
                    packages={
                        "package": CommonPackageConfig(
                            _targets=["centos-stream-9-x86_64"],
                        ),
                    },
                ),
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
                    packages={"packages": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=["centos-stream-9-x86_64"],
                        ),
                    },
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
                    packages={
                        "package": CommonPackageConfig(
                            _targets=["centos-stream-9-x86_64"],
                            use_internal_tf=True,
                        ),
                    },
                ),
            ],
            "centos-stream-9-x86_64",
            "centos-stream-9-x86_64",
            id="no_mapping_internal_tf",
        ),
    ],
)
def test_copr_test_target2build_target(job_config, test_target, build_target):
    jobs = job_config
    testing_farm_helper = TestingFarmJobHelper(
        service_config=ServiceConfig.get_service_config(),
        package_config=PackageConfig(
            jobs=jobs,
            packages={"package": CommonPackageConfig()},
        ),
        job_config=jobs[0],
        project=flexmock(),
        metadata=flexmock(pr_id=None),
        db_project_event=flexmock()
        .should_receive("get_project_event_object")
        .and_return(flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request))
        .mock(),
    )
    flexmock(CoprHelper).should_receive("get_valid_build_targets").with_args(
        "fedora-31",
        "fedora-32",
        default=None,
    ).and_return(STABLE_CHROOTS)
    flexmock(CoprHelper).should_receive("get_valid_build_targets").with_args(
        "fedora-32",
        "fedora-31",
        default=None,
    ).and_return(STABLE_CHROOTS)
    flexmock(CoprHelper).should_receive("get_valid_build_targets").with_args(
        "centos-stream-9-x86_64",
        default=None,
    ).and_return({"centos-stream-9-x86_64"})
    flexmock(CoprHelper).should_receive("get_valid_build_targets").with_args(
        "epel-7-x86_64",
        default=None,
    ).and_return({"epel-7-x86_64"})
    assert testing_farm_helper.test_target2build_target(test_target) == build_target


@pytest.mark.parametrize(
    "jobs,job_config_trigger_type,targets_override,build_targets",
    [
        pytest.param(
            [
                JobConfig(
                    type=JobType.upstream_koji_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=STABLE_VERSIONS,
                        ),
                    },
                ),
            ],
            JobConfigTriggerType.pull_request,
            {("f32", None)},
            {"f32"},
            id="target_in_config",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.upstream_koji_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=STABLE_VERSIONS,
                        ),
                    },
                ),
            ],
            JobConfigTriggerType.pull_request,
            {("f33", None)},
            set(),
            id="target_not_in_config",
        ),
    ],
)
def test_koji_targets_overrides(
    jobs,
    job_config_trigger_type,
    targets_override,
    build_targets,
):
    koji_build_helper = KojiBuildJobHelper(
        service_config=ServiceConfig.get_service_config(),
        package_config=PackageConfig(
            jobs=jobs,
            packages={"package": CommonPackageConfig()},
        ),
        job_config=jobs[0],
        project=flexmock(),
        metadata=flexmock(),
        db_project_event=flexmock()
        .should_receive("get_project_event_object")
        .and_return(flexmock(job_config_trigger_type=job_config_trigger_type))
        .mock(),
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
                    packages={"packages": CommonPackageConfig()},
                ),
            ],
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={"packages": CommonPackageConfig()},
            ),
            JobConfigTriggerType.pull_request,
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={"packages": CommonPackageConfig()},
            ),
            None,
            id="copr_build&pull_request",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"packages": CommonPackageConfig()},
                ),
            ],
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={"packages": CommonPackageConfig()},
            ),
            JobConfigTriggerType.pull_request,
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={"packages": CommonPackageConfig()},
            ),
            None,
            id="build&pull_request",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"packages": CommonPackageConfig()},
                ),
            ],
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={"packages": CommonPackageConfig()},
            ),
            JobConfigTriggerType.pull_request,
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={"packages": CommonPackageConfig()},
            ),
            None,
            id="copr_build&pr_comment",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    packages={"packages": CommonPackageConfig()},
                ),
            ],
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.release,
                packages={"packages": CommonPackageConfig()},
            ),
            JobConfigTriggerType.release,
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.release,
                packages={"packages": CommonPackageConfig()},
            ),
            None,
            id="copr_build&release",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"packages": CommonPackageConfig()},
                ),
            ],
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.commit,
                packages={"packages": CommonPackageConfig()},
            ),
            JobConfigTriggerType.commit,
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.commit,
                packages={"packages": CommonPackageConfig()},
            ),
            None,
            id="copr_build&push",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"packages": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"packages": CommonPackageConfig()},
                ),
            ],
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={"packages": CommonPackageConfig()},
            ),
            JobConfigTriggerType.pull_request,
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={"packages": CommonPackageConfig()},
            ),
            None,
            id="copr_build[pr+commit]&pull_request",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"packages": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"packages": CommonPackageConfig()},
                ),
            ],
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={"packages": CommonPackageConfig()},
            ),
            JobConfigTriggerType.pull_request,
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={"packages": CommonPackageConfig()},
            ),
            None,
            id="copr_build[commit+pr]&pull_request",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"packages": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"packages": CommonPackageConfig()},
                ),
            ],
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.commit,
                packages={"packages": CommonPackageConfig()},
            ),
            JobConfigTriggerType.commit,
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.commit,
                packages={"packages": CommonPackageConfig()},
            ),
            None,
            id="copr_build[pr+commit]&push",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"packages": CommonPackageConfig()},
                ),
            ],
            JobConfig(
                type=JobType.tests,
                trigger=JobConfigTriggerType.pull_request,
                packages={"packages": CommonPackageConfig()},
            ),
            JobConfigTriggerType.pull_request,
            None,
            JobConfig(
                type=JobType.tests,
                trigger=JobConfigTriggerType.pull_request,
                packages={"packages": CommonPackageConfig()},
            ),
            id="test&pr",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"packages": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"packages": CommonPackageConfig()},
                ),
            ],
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={"packages": CommonPackageConfig()},
            ),
            JobConfigTriggerType.pull_request,
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={"packages": CommonPackageConfig()},
            ),
            JobConfig(
                type=JobType.tests,
                trigger=JobConfigTriggerType.pull_request,
                packages={"packages": CommonPackageConfig()},
            ),
            id="copr_build+test&pr",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"packages": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"packages": CommonPackageConfig()},
                ),
            ],
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={"packages": CommonPackageConfig()},
            ),
            JobConfigTriggerType.pull_request,
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={"packages": CommonPackageConfig()},
            ),
            JobConfig(
                type=JobType.tests,
                trigger=JobConfigTriggerType.pull_request,
                packages={"packages": CommonPackageConfig()},
            ),
            id="build+test&pr",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"packages": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"packages": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"packages": CommonPackageConfig()},
                ),
            ],
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={"packages": CommonPackageConfig()},
            ),
            JobConfigTriggerType.pull_request,
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={"packages": CommonPackageConfig()},
            ),
            JobConfig(
                type=JobType.tests,
                trigger=JobConfigTriggerType.pull_request,
                packages={"packages": CommonPackageConfig()},
            ),
            id="copr_build[pr+commit]+test[pr]&pr",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"packages": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"packages": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"packages": CommonPackageConfig()},
                ),
            ],
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.commit,
                packages={"packages": CommonPackageConfig()},
            ),
            JobConfigTriggerType.commit,
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.commit,
                packages={"packages": CommonPackageConfig()},
            ),
            None,
            id="copr_build[pr+commit]+test[pr]&commit",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"packages": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"packages": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"packages": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    packages={"packages": CommonPackageConfig()},
                ),
            ],
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.release,
                packages={"packages": CommonPackageConfig()},
            ),
            JobConfigTriggerType.release,
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.release,
                packages={"packages": CommonPackageConfig()},
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
    db_project_object = flexmock(
        job_config_trigger_type=job_config_trigger_type,
    )
    if job_config_trigger_type == JobConfigTriggerType.commit:
        db_project_object.name = "main"

    copr_build_helper = CoprBuildJobHelper(
        service_config=ServiceConfig.get_service_config(),
        package_config=PackageConfig(
            jobs=jobs,
            packages={"package": CommonPackageConfig()},
        ),
        job_config=init_job,
        project=flexmock(default_branch="main"),
        metadata=flexmock(pr_id=None),
        db_project_event=flexmock()
        .should_receive("get_project_event_object")
        .and_return(db_project_object)
        .mock(),
    )

    assert copr_build_helper.package_config.jobs
    assert [j.type for j in copr_build_helper.package_config.jobs]

    assert copr_build_helper.job_build == result_job_build


@pytest.mark.parametrize(
    "jobs,job_config_trigger_type,project_event_model_type,tag_name,job_owner,job_project",
    [
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"packages": CommonPackageConfig()},
                ),
            ],
            JobConfigTriggerType.pull_request,
            ProjectEventModelType.pull_request,
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
                    packages={
                        "package": CommonPackageConfig(
                            owner="custom-owner",
                        ),
                    },
                ),
            ],
            JobConfigTriggerType.pull_request,
            ProjectEventModelType.pull_request,
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
                    packages={
                        "package": CommonPackageConfig(
                            project="custom-project",
                        ),
                    },
                ),
            ],
            JobConfigTriggerType.pull_request,
            ProjectEventModelType.pull_request,
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
                    packages={
                        "package": CommonPackageConfig(
                            owner="custom-owner",
                            project="custom-project",
                        ),
                    },
                ),
            ],
            JobConfigTriggerType.pull_request,
            ProjectEventModelType.pull_request,
            None,
            "custom-owner",
            "custom-project",
            id="custom-owner&custom-project",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            owner="custom-owner",
                            project="custom-project",
                        ),
                    },
                ),
            ],
            JobConfigTriggerType.pull_request,
            ProjectEventModelType.pull_request,
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
                    packages={"packages": CommonPackageConfig()},
                ),
            ],
            JobConfigTriggerType.commit,
            ProjectEventModelType.branch_push,
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
                    packages={"packages": CommonPackageConfig()},
                ),
            ],
            JobConfigTriggerType.release,
            ProjectEventModelType.release,
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
                    packages={"packages": CommonPackageConfig()},
                ),
            ],
            JobConfigTriggerType.release,
            ProjectEventModelType.release,
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
                    packages={
                        "package": CommonPackageConfig(
                            owner="commit-owner",
                            project="commit-project",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            owner="pr-owner",
                            project="pr-project",
                        ),
                    },
                ),
            ],
            JobConfigTriggerType.pull_request,
            ProjectEventModelType.pull_request,
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
                    packages={"packages": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"packages": CommonPackageConfig()},
                ),
            ],
            JobConfigTriggerType.pull_request,
            ProjectEventModelType.pull_request,
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
                    packages={
                        "package": CommonPackageConfig(
                            owner="custom-owner",
                            project="custom-project",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"packages": CommonPackageConfig()},
                ),
            ],
            JobConfigTriggerType.pull_request,
            ProjectEventModelType.pull_request,
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
                    packages={"packages": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            owner="custom-owner",
                            project="custom-project",
                        ),
                    },
                ),
            ],
            JobConfigTriggerType.pull_request,
            ProjectEventModelType.pull_request,
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
                    packages={
                        "package": CommonPackageConfig(
                            owner="pr-owner",
                            project="pr-project",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"packages": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            owner="commit-owner",
                            project="commit-project",
                        ),
                    },
                ),
            ],
            JobConfigTriggerType.pull_request,
            ProjectEventModelType.pull_request,
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
                    packages={
                        "package": CommonPackageConfig(
                            owner="pr-owner",
                            project="pr-project",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"packages": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            owner="commit-owner",
                            project="commit-project",
                        ),
                    },
                ),
            ],
            JobConfigTriggerType.commit,
            ProjectEventModelType.branch_push,
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
    project_event_model_type,
    tag_name,
    job_owner,
    job_project,
):
    copr_build_helper = CoprBuildJobHelper(
        service_config=ServiceConfig.get_service_config(),
        package_config=PackageConfig(
            jobs=jobs,
            packages={"package": CommonPackageConfig()},
        ),
        job_config=jobs[0],  # BuildHelper looks at all jobs in the end
        project=flexmock(
            namespace="the/example/namespace",
            repo="the-example-repo",
            service=flexmock(instance_url="https://git.instance.io"),
            default_branch="main",
        ),
        metadata=flexmock(
            pr_id=None,
            identifier="the-event-identifier",
            tag_name=tag_name,
        ),
        db_project_event=flexmock()
        .should_receive("get_project_event_object")
        .and_return(
            flexmock(
                job_config_trigger_type=job_config_trigger_type,
                project_event_model_type=project_event_model_type,
                name="main",
            ),
        )
        .mock(),
    )
    copr_build_helper._api = flexmock(
        copr_helper=flexmock(copr_client=flexmock(config={"username": "nobody"})),
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
                packages={
                    "package": CommonPackageConfig(
                        owner="the-owner",
                        project="the-project",
                    ),
                },
            ),
            "",
            False,
            id="empty",
        ),
        pytest.param(
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={
                    "package": CommonPackageConfig(
                        owner="the-owner",
                        project="the-project",
                    ),
                },
            ),
            "something/different",
            False,
            id="not-present",
        ),
        pytest.param(
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={
                    "package": CommonPackageConfig(
                        owner="the-owner",
                        project="the-project",
                    ),
                },
            ),
            "git.instance.io/the/example/namespace/the-example-repo",
            True,
            id="present",
        ),
        pytest.param(
            JobConfig(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.pull_request,
                packages={
                    "package": CommonPackageConfig(
                        owner="the-owner",
                        project="the-project",
                    ),
                },
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
        package_config=PackageConfig(
            jobs=[job],
            packages={"package": CommonPackageConfig()},
        ),
        job_config=job,  # BuildHelper looks at all jobs in the end
        project=flexmock(
            namespace="the/example/namespace",
            repo="the-example-repo",
            service=flexmock(
                instance_url="https://git.instance.io",
                hostname="git.instance.io",
            ),
        ),
        metadata=flexmock(pr_id=None, identifier="the-event-identifier", tag_name=None),
        db_project_event=flexmock()
        .should_receive("get_project_event_object")
        .and_return(
            flexmock(
                job_config_trigger_type=JobConfigTriggerType.pull_request,
                project_event_model_type=ProjectEventModelType.pull_request,
            ),
        )
        .mock(),
    )
    copr_helper = flexmock(
        copr_client=flexmock(
            config={"username": "nobody"},
            project_proxy=flexmock(
                get=lambda owner, project: {
                    "packit_forge_projects_allowed": git_forge_allowed_list,
                },
            ),
        ),
    )
    copr_helper.should_receive("get_copr_settings_url").with_args(
        "the-owner",
        "the-project",
    ).and_return().times(0 if allowed else 1)
    copr_build_helper._api = flexmock(copr_helper=copr_helper)
    flexmock(CoprBuildJobHelper).should_receive("report_status_to_build").times(
        0 if allowed else 1,
    )
    assert copr_build_helper.check_if_custom_copr_can_be_used_and_report() is allowed


@pytest.mark.parametrize(
    "jobs,job_config_trigger_type,build_targets,koji_targets",
    [
        pytest.param(
            [
                JobConfig(
                    type=JobType.upstream_koji_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=STABLE_VERSIONS,
                        ),
                    },
                ),
            ],
            JobConfigTriggerType.pull_request,
            set(STABLE_VERSIONS),
            STABLE_KOJI_TARGETS,
            id="koji_build_with_targets_for_pr",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.upstream_koji_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=STABLE_VERSIONS,
                            branch="build-branch",
                        ),
                    },
                ),
            ],
            JobConfigTriggerType.commit,
            set(STABLE_VERSIONS),
            STABLE_KOJI_TARGETS,
            id="koji_build_with_targets_for_commit",
        ),
        pytest.param(
            [
                JobConfig(
                    type=JobType.upstream_koji_build,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=STABLE_VERSIONS,
                            branch="build-branch",
                        ),
                    },
                ),
            ],
            JobConfigTriggerType.release,
            set(STABLE_VERSIONS),
            STABLE_KOJI_TARGETS,
            id="koji_build_with_targets_for_release",
        ),
    ],
)
def test_targets_for_koji_build(
    jobs,
    job_config_trigger_type,
    build_targets,
    koji_targets,
):
    pr_id = 41 if job_config_trigger_type == JobConfigTriggerType.pull_request else None
    koji_build_helper = KojiBuildJobHelper(
        service_config=ServiceConfig.get_service_config(),
        package_config=PackageConfig(
            jobs=jobs,
            packages={"package": CommonPackageConfig()},
        ),
        job_config=jobs[0],
        project=flexmock(),
        metadata=flexmock(pr_id=pr_id),
        db_project_event=flexmock()
        .should_receive("get_project_event_object")
        .and_return(
            flexmock(
                job_config_trigger_type=job_config_trigger_type,
                name="build-branch",
            ),
        )
        .mock(),
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

    copr_build_helper = KojiBuildJobHelper(
        service_config=service_config,
        package_config=PackageConfig(
            jobs=[
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=STABLE_VERSIONS,
                        ),
                    },
                ),
            ],
            packages={"package": CommonPackageConfig()},
        ),
        job_config=JobConfig(
            type=JobType.copr_build,
            trigger=JobConfigTriggerType.pull_request,
            packages={
                "package": CommonPackageConfig(
                    _targets=STABLE_VERSIONS,
                ),
            },
        ),
        project=flexmock(
            service=flexmock(),
            get_git_urls=lambda: {
                "git": "https://github.com/some-namespace/some-repo.git",
            },
            repo=flexmock(),
            namespace=flexmock(),
        ),
        metadata=flexmock(pr_id=None, git_ref=flexmock()),
        db_project_event=flexmock()
        .should_receive("get_project_event_object")
        .and_return(flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request))
        .mock(),
    )

    flexmock(RepositoryCache).should_call("__init__").once()
    flexmock(RepositoryCache).should_receive("get_repo").with_args(
        "https://github.com/some-namespace/some-repo.git",
        directory="/tmp/some-dir",
    ).and_return(
        flexmock(
            git=flexmock().should_receive("checkout").and_return().mock(),
            commit=lambda: "commit",
        ),
    ).once()
    assert copr_build_helper.local_project


def test_local_project_not_called_when_initializing_api():
    jobs = [
        JobConfig(
            type=JobType.copr_build,
            trigger=JobConfigTriggerType.pull_request,
            packages={"packages": CommonPackageConfig()},
        ),
    ]
    copr_build_helper = CoprBuildJobHelper(
        service_config=ServiceConfig.get_service_config(),
        package_config=PackageConfig(
            jobs=jobs,
            packages={"package": CommonPackageConfig()},
        ),
        job_config=jobs[0],
        project=flexmock(),
        metadata=flexmock(pr_id=1),
        db_project_event=flexmock()
        .should_receive("get_project_event_object")
        .and_return(flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request))
        .mock(),
    )
    flexmock(LocalProject).should_receive("__init__").never()
    assert copr_build_helper.api
    assert copr_build_helper.api.copr_helper


@pytest.mark.parametrize(
    "failure_comment,kwargs,result_comment",
    [
        pytest.param(
            ("One of the Copr builds failed for commit {commit_sha}, ping @admin"),
            {},
            "One of the Copr builds failed for commit 123, ping @admin",
            id="only commit_sha",
        ),
        pytest.param(
            (
                "One of the Copr builds failed for "
                "commit {commit_sha}, ping @admin, copr build logs {logs_url}"
            ),
            {"logs_url": "jghfkgjfd"},
            "One of the Copr builds failed for commit 123, ping @admin, copr build logs jghfkgjfd",
            id="commit_sha and logs url",
        ),
        pytest.param(
            (
                "One of the Copr builds failed for "
                "commit {commit_sha}, ping @admin, copr build logs {logs_url} "
                "and {packit_dashboard_url}"
            ),
            {},
            (
                "One of the Copr builds failed for commit 123, ping @admin, "
                "copr build logs {no entry for logs_url} and "
                "{no entry for packit_dashboard_url}"
            ),
            id="commit_sha and no logs and packit dashboard url",
        ),
        pytest.param(
            (
                "One of the Copr builds failed for "
                "commit {commit_sha}, ping @admin, copr build logs {logs_url}"
            ),
            {
                "logs_url": "jghfkgjfd",
                "packit_dashboard_url": "jghfkgjfd",
            },
            "One of the Copr builds failed for commit 123, ping @admin, copr build logs jghfkgjfd",
            id="commit_sha, copr build logs url and packit dashboard url",
        ),
    ],
)
def test_notify_about_failure_if_configured(failure_comment, kwargs, result_comment):
    jobs = [
        JobConfig(
            type=JobType.copr_build,
            trigger=JobConfigTriggerType.pull_request,
            packages={
                "packages": CommonPackageConfig(
                    notifications=NotificationsConfig(
                        failure_comment=FailureCommentNotificationsConfig(
                            failure_comment,
                        ),
                    ),
                ),
            },
        ),
    ]
    copr_build_helper = CoprBuildJobHelper(
        service_config=ServiceConfig.get_service_config(),
        package_config=PackageConfig(
            jobs=jobs,
            packages={"package": CommonPackageConfig()},
        ),
        job_config=jobs[0],
        project=flexmock(),
        metadata=flexmock(pr_id=1, commit_sha="123"),
        db_project_event=flexmock(id=12, commit_sha="123")
        .should_receive("get_project_event_object")
        .and_return(flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request))
        .mock(),
    )

    flexmock(StatusReporter).should_receive("comment").with_args(
        result_comment,
        duplicate_check=DuplicateCheckMode.check_last_comment,
    )
    copr_build_helper.notify_about_failure_if_configured(**kwargs)
