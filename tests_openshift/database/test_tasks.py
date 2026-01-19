# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import threading

import pytest
from celery.canvas import Signature
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
    PipelineModel,
    ProjectEventModel,
    ProjectEventModelType,
    PullRequestModel,
    SRPMBuildModel,
    TFTTestRunGroupModel,
    TFTTestRunTargetModel,
    sa_session_transaction,
)
from packit_service.worker.handlers import TestingFarmHandler
from packit_service.worker.helpers.build import babysit
from packit_service.worker.helpers.build.babysit import check_copr_build
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.tasks import run_copr_build_end_handler

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
    _, pr_event = ProjectEventModel.add_pull_request_event(
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
    assert packit_build_752.status == BuildStatus.pending
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
    flexmock(CoprBuild).should_receive("get_packages_config").and_return(
        PackageConfig(
            jobs=[
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig(specfile_path="some.spec")},
                ),
            ],
            packages={"package": CommonPackageConfig(specfile_path="some.spec")},
        ),
    )

    # Define the mock execution
    def celery_run_async_stub(signatures, handlers) -> None:
        assert isinstance(signatures, list)
        results = []
        handler = handlers.pop(0)
        for sig in signatures:
            assert isinstance(sig, Signature)
            event_dict = sig.kwargs["event"]
            job_config = sig.kwargs["job_config"]
            package_config = sig.kwargs["package_config"]

            result = handler(
                package_config=package_config,
                event=event_dict,
                job_config=job_config,
            )
            results.append(result)

    flexmock(
        babysit,
        celery_run_async=lambda signatures: celery_run_async_stub(
            signatures, [run_copr_build_end_handler]
        ),
    )

    assert check_copr_build(BUILD_ID)
    build = CoprBuildTargetModel.get_by_id(packit_build_752.id)
    assert build is not None
    assert build.status == BuildStatus.success


def test_testing_farm_race_condition_concurrent_identifiers(
    clean_before_and_after,
):
    """
    Test that two concurrent testing_farm tasks with different identifiers
    (test_c and test_d) for the same copr build create separate pipelines
    without race conditions.

    This test simulates the race condition where two handlers both try to
    create a test_run_group for the same pipeline at the same time.
    """
    # Setup: Create a PR and project event
    pr = PullRequestModel.get_or_create(
        pr_id=1,
        namespace="test-namespace",
        repo_name="test-repo",
        project_url="https://github.com/test-namespace/test-repo",
    )
    project_event = ProjectEventModel.get_or_create(
        type=ProjectEventModelType.pull_request,
        event_id=pr.id,
        commit_sha="4f4403b44107aae0b820f2a940623d3fa54dfcb6",
    )
    # Store the ID to avoid thread-safety issues with SQLAlchemy sessions
    project_event_id = project_event.id

    # Create SRPM build and pipeline
    _, run_model = SRPMBuildModel.create_with_new_run(
        project_event_model=project_event,
    )
    copr_build_group = CoprBuildGroupModel.create(run_model)

    # Create copr build with specific build_id
    # The commit_sha is already set in the project_event we created above
    _ = CoprBuildTargetModel.create(
        build_id="10034382",
        project_name="test-project",
        owner="test-owner",
        web_url="https://copr.fedorainfracloud.org/coprs/build/10034382/",
        target="fedora-rawhide-x86_64",
        status=BuildStatus.success,
        copr_build_group=copr_build_group,
    )

    # Results storage for threads
    results = {}
    errors = {}

    # Use a barrier to synchronize both threads to start at exactly the same time
    # This ensures they execute concurrently and increases the chance of a race condition
    barrier = threading.Barrier(2, timeout=30)

    def create_test_run_group(identifier: str):
        """Helper function to create test run group in a thread"""
        try:
            # Query the copr build fresh in this thread
            fresh_copr_build = CoprBuildTargetModel.get_by_build_id(
                build_id="10034382",
                target="fedora-rawhide-x86_64",
            )
            assert fresh_copr_build is not None, "Copr build should exist"

            handler_instance = TestingFarmHandler(
                package_config=flexmock(),
                job_config=JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(identifier=identifier),
                    },
                ),
                event={},
                celery_task=flexmock(request=flexmock(retries=0)),
            )
            # Query project_event fresh in this thread to avoid thread-safety issues
            with sa_session_transaction() as session:
                fresh_project_event = (
                    session.query(ProjectEventModel).filter_by(id=project_event_id).first()
                )
                assert fresh_project_event is not None, "Project event should exist"

            # Set up the handler's required attributes
            handler_instance._db_project_event = fresh_project_event
            handler_instance.data = flexmock(
                db_project_event=fresh_project_event,
                commit_sha="4f4403b44107aae0b820f2a940623d3fa54dfcb6",
            )
            handler_instance._project = flexmock(
                get_web_url=lambda: "https://github.com/test-namespace/test-repo"
            )

            # Mock the testing_farm_job_helper
            handler_instance._testing_farm_job_helper = flexmock(
                skip_build=False,
                tft_client=flexmock(default_ranch="public"),
                tests_targets={"fedora-rawhide-x86_64"},
                test_target2build_target=lambda target: target,
                get_latest_copr_build=lambda target, commit_sha: fresh_copr_build,
                run_testing_farm=lambda test_run, build: {"success": True, "details": {}},
                build_required=lambda: False,  # Build not required, proceed with tests
                job_owner="test-owner",
                job_project="test-project",
            )

            # Wait for both threads to be ready before calling run()
            barrier.wait()

            # Call run() which will exercise the full logic including _get_or_create_group
            result = handler_instance.run()
            assert result["success"], (
                f"Handler run() failed for {identifier}: {result.get('details', {})}"
            )

            # Query the database to get the created group
            with sa_session_transaction() as session:
                test_run = (
                    session.query(TFTTestRunTargetModel)
                    .join(TFTTestRunGroupModel)
                    .join(PipelineModel)
                    .filter(PipelineModel.project_event_id == project_event_id)
                    .filter(TFTTestRunTargetModel.identifier == identifier)
                    .order_by(TFTTestRunTargetModel.id.desc())
                    .first()
                )
                assert test_run is not None, f"Test run should have been created for {identifier}"
                group_id = test_run.group_of_targets.id
                results[identifier] = (group_id, [test_run])
        except Exception as e:
            errors[identifier] = e
            raise

    # Create two threads that will execute concurrently
    thread_c = threading.Thread(target=create_test_run_group, args=("test_c",))
    thread_d = threading.Thread(target=create_test_run_group, args=("test_d",))

    # Start both threads at the same time
    thread_c.start()
    thread_d.start()

    # Wait for both threads to complete
    thread_c.join()
    thread_d.join()

    # Verify no errors occurred
    assert not errors, f"Errors occurred: {errors}"

    # Verify both test_run_groups were created
    assert "test_c" in results
    assert "test_d" in results

    group_c_id, _ = results["test_c"]
    group_d_id, _ = results["test_d"]

    # Verify both groups have different IDs
    assert group_c_id != group_d_id, "Each identifier should have its own group"

    # Verify both groups and test run targets exist
    with sa_session_transaction() as session:
        # Verify that both test_run_groups were created in the same second
        # This ensures the race condition test is actually testing concurrent creation
        group_c = session.query(TFTTestRunGroupModel).filter_by(id=group_c_id).first()
        group_d = session.query(TFTTestRunGroupModel).filter_by(id=group_d_id).first()

        assert group_c is not None, "test_c group should exist"
        assert group_d is not None, "test_d group should exist"
        assert group_c.submitted_time is not None, "group_c should have a submitted_time"
        assert group_d.submitted_time is not None, "group_d should have a submitted_time"

        # Verify that there exists a tft_test_run_targets row for every identifier
        test_run_target_c = (
            session.query(TFTTestRunTargetModel)
            .filter_by(identifier="test_c")
            .filter_by(tft_test_run_group_id=group_c_id)
            .first()
        )
        test_run_target_d = (
            session.query(TFTTestRunTargetModel)
            .filter_by(identifier="test_d")
            .filter_by(tft_test_run_group_id=group_d_id)
            .first()
        )

        assert test_run_target_c is not None, "test_c should have a test_run_target row"
        assert test_run_target_d is not None, "test_d should have a test_run_target row"

        # Verify that both test_run_targets are linked to their respective groups
        assert test_run_target_c.tft_test_run_group_id == group_c_id
        assert test_run_target_d.tft_test_run_group_id == group_d_id

        # Verify both groups have pipelines linked to them (check last)
        pipeline_c = session.query(PipelineModel).filter_by(test_run_group_id=group_c_id).first()
        pipeline_d = session.query(PipelineModel).filter_by(test_run_group_id=group_d_id).first()

        assert pipeline_c is not None, "test_c group should have a pipeline"
        assert pipeline_d is not None, "test_d group should have a pipeline"
        assert pipeline_c.id != pipeline_d.id, "Each group should have its own pipeline"

        # Verify the pipelines are linked to the same project_event
        assert pipeline_c.project_event_id == project_event_id
        assert pipeline_d.project_event_id == project_event_id

        # Truncate to seconds and compare
        group_c_second = group_c.submitted_time.replace(microsecond=0)
        group_d_second = group_d.submitted_time.replace(microsecond=0)
        assert group_c_second == group_d_second, (
            f"Test run groups should be created in the same second. "
            f"group_c: {group_c.submitted_time}, group_d: {group_d.submitted_time}"
        )
