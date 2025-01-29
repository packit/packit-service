# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import pytest
from copr.v3 import BuildChrootProxy, BuildProxy, Client
from flexmock import flexmock
from munch import Munch
from ogr.services.github import GithubProject
from packit.config import (
    CommonPackageConfig,
    JobConfig,
    JobConfigTriggerType,
    JobType,
    PackageConfig,
)
from packit.copr_helper import CoprHelper

from packit_service.events.copr import CoprBuild
from packit_service.models import (
    BuildStatus,
    CoprBuildGroupModel,
    CoprBuildTargetModel,
    ProjectEventModel,
    SRPMBuildModel,
)
from packit_service.worker.helpers.build.babysit import check_copr_build
from packit_service.worker.monitoring import Pushgateway

BUILD_ID = 1300329


# FIXME: I tried but couldn't make it work
# @pytest.fixture()
# def requre_setup():
#     upgrade_import_system() \
#         .decorate(
#             where="^packit_service",
#             what="BuildProxy.get",
#             decorator=Simple.decorator_plain,
#         )
#
#     TEST_DATA_DIR = "test_data"
#     PERSISTENT_DATA_PREFIX = Path(__file__).parent.parent / TEST_DATA_DIR
#
#     test_file_name = os.path.basename(__file__).rsplit(
#         ".", 1
#     )[0]
#     testdata_dirname = PERSISTENT_DATA_PREFIX / str(test_file_name)
#     testdata_dirname.mkdir(mode=0o777, exist_ok=True)
#
#     PersistentObjectStorage().storage_file = testdata_dirname / "packit_build_752"
#
#     yield
#     PersistentObjectStorage().dump()


@pytest.fixture()
def packit_build_752():
    pr_model, pr_event = ProjectEventModel.add_pull_request_event(
        pr_id=752,
        namespace="packit-service",
        repo_name="packit",
        project_url="https://github.com/packit-service/packit",
        commit_sha="abcdef",
    )

    srpm_build, run_model = SRPMBuildModel.create_with_new_run(
        project_event_model=pr_event,
    )
    group = CoprBuildGroupModel.create(run_model)
    srpm_build.set_logs("asd\nqwe\n")
    srpm_build.set_status("success")
    yield CoprBuildTargetModel.create(
        build_id=str(BUILD_ID),
        project_name="packit-service-packit-752",
        owner="packit",
        web_url=(
            "https://download.copr.fedorainfracloud.org/results/packit/packit-service-packit-752"
        ),
        target="fedora-rawhide-x86_64",
        status=BuildStatus.pending,
        copr_build_group=group,
    )


def test_check_copr_build(clean_before_and_after, packit_build_752):
    flexmock(Client).should_receive("create_from_config_file").and_return(
        Client(
            config={
                "username": "packit",
                "copr_url": "https://copr.fedorainfracloud.org/",
            },
        ),
    )
    flexmock(CoprBuild).should_receive("get_packages_config").and_return(
        PackageConfig(
            packages={"packit": CommonPackageConfig()},
            jobs=[
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "packit": CommonPackageConfig(
                            _targets=[
                                "fedora-30-x86_64",
                                "fedora-rawhide-x86_64",
                                "fedora-31-x86_64",
                                "fedora-32-x86_64",
                            ],
                        ),
                    },
                ),
            ],
        ),
    )
    coprs_response = Munch(
        {
            "chroots": [
                "fedora-30-x86_64",
                "fedora-rawhide-x86_64",
                "fedora-31-x86_64",
                "fedora-32-x86_64",
            ],
            "ended_on": 1583916564,
            "id": 1300329,
            "ownername": "packit",
            "project_dirname": "packit-service-packit-752",
            "projectname": "packit-service-packit-752",
            "repo_url": (
                "https://download.copr.fedorainfracloud.org/"
                "results/packit/packit-service-packit-752"
            ),
            "source_package": {
                "name": "packit",
                "url": (
                    "https://download.copr.fedorainfracloud.org/"
                    "results/packit/packit-service-packit-752/"
                    "srpm-builds/01300329/packit-0.8.2.dev122g64ebb47-1.fc31.src.rpm"
                ),
                "version": "0.8.2.dev122+g64ebb47-1.fc31",
            },
            "started_on": 1583916315,
            "state": "succeeded",
            "submitted_on": 1583916261,
            "submitter": "packit",
        },
    )
    flexmock(BuildProxy).should_receive("get").and_return(coprs_response)

    copr_response_built_packages = Munch(
        {
            "packages": [
                {
                    "arch": "noarch",
                    "epoch": 0,
                    "name": "python3-packit",
                    "release": "1.20210930124525726166.main.0.g0b7b36b.fc36",
                    "version": "0.38.0",
                },
                {
                    "arch": "src",
                    "epoch": 0,
                    "name": "packit",
                    "release": "1.20210930124525726166.main.0.g0b7b36b.fc36",
                    "version": "0.38.0",
                },
                {
                    "arch": "noarch",
                    "epoch": 0,
                    "name": "packit",
                    "release": "1.20210930124525726166.main.0.g0b7b36b.fc36",
                    "version": "0.38.0",
                },
            ],
        },
    )
    flexmock(BuildChrootProxy).should_receive("get_built_packages").with_args(
        BUILD_ID,
        "fedora-rawhide-x86_64",
    ).and_return(copr_response_built_packages)

    chroot_response = Munch(
        {
            "ended_on": 1583916564,
            "name": "fedora-rawhide-x86_64",
            "result_url": "https://download.copr.fedorainfracloud.org/"
            "results/packit/packit-service-packit-752/fedora-rawhide-x86_64/"
            "01300329-packit/",
            "started_on": 1583916315,
            "state": "succeeded",
        },
    )
    flexmock(BuildChrootProxy).should_receive("get").with_args(
        BUILD_ID,
        "fedora-rawhide-x86_64",
    ).and_return(chroot_response)

    pr = flexmock(source_project=flexmock(), target_branch="main")
    pr.should_receive("get_comments").and_return([])
    pr.should_receive("comment").and_return()

    # Reporting
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    flexmock(GithubProject).should_receive("create_check_run").and_return().once()
    flexmock(GithubProject).should_receive("get_git_urls").and_return(
        {"git": "https://github.com/packit-service/packit.git"},
    )
    flexmock(CoprHelper).should_receive("get_valid_build_targets").and_return(
        {
            "fedora-33-x86_64",
            "fedora-32-x86_64",
            "fedora-31-x86_64",
            "fedora-rawhide-x86_64",
        },
    )
    flexmock(Pushgateway).should_receive("push").once().and_return()

    check_copr_build(BUILD_ID)
    assert packit_build_752.status == BuildStatus.success
