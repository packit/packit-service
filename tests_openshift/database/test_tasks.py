# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
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

from packit_service.config import Deployment
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
from packit_service.utils import load_package_config
from packit_service.worker.handlers import TestingFarmHandler
from packit_service.worker.handlers.copr import CoprBuildHandler
from packit_service.worker.helpers.build import babysit
from packit_service.worker.helpers.build.babysit import check_copr_build
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.tasks import run_copr_build_end_handler

BUILD_ID = 1300329

logger = logging.getLogger(__name__)


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


def test_copr_build_race_condition_concurrent_packages(
    clean_before_and_after,
):
    """
    Test that three concurrent copr_build_handler tasks for different packages
    (containers-common-fedora, containers-common-eln, containers-common-centos)
    with the same commit_sha trigger a race condition when creating CoprBuildGroups.

    This test simulates the race condition where multiple handlers try to create
    a CoprBuildGroup for the same pipeline concurrently. The reverted code checks
    run_model.copr_build_group (in-memory attribute) instead of the database state,
    so all threads see None and try to set it, causing a race condition.

    To trigger this, we create a shared pipeline upfront and mock
    SRPMBuildModel.create_with_new_run()
    to return the same pipeline object for all threads, ensuring they all try to modify
    the same pipeline's copr_build_group attribute concurrently.
    """
    # Setup: Create a PR and project event with the same commit_sha for all packages
    commit_sha = "3826238a9f0c530ee7d064c50907ef54255b0add"
    pr = PullRequestModel.get_or_create(
        pr_id=475,
        namespace="containers",
        repo_name="container-libs",
        project_url="https://github.com/containers/container-libs",
    )
    project_event = ProjectEventModel.get_or_create(
        type=ProjectEventModelType.pull_request,
        event_id=pr.id,
        commit_sha=commit_sha,
    )
    # Store the ID to avoid thread-safety issues with SQLAlchemy sessions
    project_event_id = project_event.id

    # Package configurations - each package will get a different build_id when submitted
    packages = [
        ("containers-common-fedora", "fedora-rawhide-x86_64"),
        ("containers-common-eln", "fedora-rawhide-x86_64"),
        ("containers-common-centos", "centos-stream-9-x86_64"),
    ]

    # Create a SHARED pipeline upfront that all threads will use
    # This is critical to trigger the race condition: all threads will try to
    # create a CoprBuildGroup for the same pipeline at the same time
    from packit_service.models import PipelineModel, SRPMBuildModel

    with sa_session_transaction(commit=True) as session:
        shared_srpm, shared_pipeline = SRPMBuildModel.create_with_new_run(
            project_event_model=project_event,
            package_name=None,  # No package_name to make it shared
        )
        shared_pipeline_id = shared_pipeline.id
        shared_srpm_id = shared_srpm.id
        # Store only IDs - each thread will query fresh objects from its own session
        # This ensures all threads work on the same database row (same pipeline_id)
        # but each has its own session-bound object, which is necessary for SQLAlchemy

    builds_data = {}
    for package_name, target in packages:
        builds_data[package_name] = {
            "target": target,
            "build_id": None,  # Will be set by submit_copr_build mock
        }

    # Results storage for threads
    results = {}
    errors = {}

    # Shared storage for mock clients - each thread will register its client here
    # Key: package_name, Value: mock_copr_client
    mock_clients_by_package = {}

    # Shared storage for thread ID -> package_name mapping
    thread_packages = {}

    # Create a callable class that returns the correct client based on thread ID
    # This will be called when CoprClient.create_from_config_file() is invoked
    class MockClientFactory:
        """Factory that returns the correct mock client based on thread ID"""

        def __call__(self, *args, **kwargs):
            import threading

            thread_id = threading.current_thread().ident
            current_package = thread_packages.get(thread_id)
            if current_package and current_package in mock_clients_by_package:
                return mock_clients_by_package[current_package]
            # Fallback: return the first available client (shouldn't happen)
            return next(iter(mock_clients_by_package.values())) if mock_clients_by_package else None

    mock_factory = MockClientFactory()

    # Set up the mock once before threads start
    # Note: flexmock's and_return() evaluates immediately, so we need to use a callable
    # that will be invoked when the method is called. However, flexmock doesn't support
    # and_call(), so we'll need to patch the method directly.

    from copr.v3 import Client as CoprClient

    # Patch the method to use our factory
    # create_from_config_file is a staticmethod, so it doesn't receive cls
    def patched_create_from_config_file(*args, **kwargs):
        return mock_factory(*args, **kwargs)

    # Replace the method - check if it's a classmethod or staticmethod first
    original_method = CoprClient.create_from_config_file
    # Unwrap if it's a descriptor (classmethod/staticmethod)
    if isinstance(original_method, (classmethod, staticmethod)):
        # For staticmethod, we can just replace it with our function
        CoprClient.create_from_config_file = staticmethod(patched_create_from_config_file)
    else:
        # It's a regular method or already unwrapped
        CoprClient.create_from_config_file = staticmethod(patched_create_from_config_file)

    # Use a barrier to synchronize all three threads to start at exactly the same time
    # This ensures they execute concurrently and increases the chance of a race condition
    barrier = threading.Barrier(3, timeout=30)

    def run_copr_build_handler_for_package(package_name: str):
        """Helper function to run copr_build_handler in a thread"""
        try:
            build_data = builds_data[package_name]
            target = build_data["target"]
            # Use specific build IDs as provided by the user
            build_id_mapping = {
                "containers-common-fedora": 9805644,
                "containers-common-centos": 9805646,
                "containers-common-eln": 9805645,
            }
            mock_build_id = build_id_mapping[package_name]

            # Create event dict for CoprBuildHandler (PR event)
            event_dict = {
                "event_type": "PullRequestEvent",
                "action": "opened",
                "project_url": "https://github.com/containers/container-libs",
                "actor": "packit",
                "event_id": project_event_id,
                "pr_id": pr.id,
                "commit_sha": commit_sha,
                "git_ref": commit_sha,
            }

            # Create package_config and job_config as dictionaries
            # Set the target explicitly so build_targets will include it
            package_config_dict = {
                "packages": {package_name: {"specfile_path": f"{package_name}.spec"}},
                "jobs": [
                    {
                        "job": JobType.copr_build.value,
                        "trigger": JobConfigTriggerType.pull_request.value,
                        "packages": {package_name: {"specfile_path": f"{package_name}.spec"}},
                        "targets": [target],  # Set targets at the job level
                    },
                ],
            }

            # Mock necessary dependencies for CoprBuildHandler
            flexmock(Pushgateway).should_receive("push").and_return()

            # Mock service config to use prod deployment to avoid memory profiling
            from packit_service.config import ServiceConfig

            service_config = ServiceConfig.get_service_config()
            service_config.deployment = Deployment.prod

            # Query project_event fresh in this thread BEFORE the barrier
            with sa_session_transaction() as session:
                fresh_project_event = (
                    session.query(ProjectEventModel).filter_by(id=project_event_id).first()
                )
                assert fresh_project_event is not None, "Project event should exist"

            # Load package_config and use the job_config from it
            # This ensures job_config is the same object as in package_config.jobs
            package_config = load_package_config(package_config_dict)
            job_config = package_config.jobs[0]  # Use the job from package_config

            # Create the handler instance BEFORE the barrier to avoid concurrent initialization
            handler = CoprBuildHandler(
                package_config=package_config,
                job_config=job_config,
                event=event_dict,
                celery_task=flexmock(request=flexmock(retries=0)),
            )

            # Create real GithubProject instance and only mock what's needed
            from ogr.services.github.pull_request import GithubPullRequest
            from ogr.services.github.service import GithubService

            mock_service = flexmock(
                GithubService(),
                instance_url="https://github.com",
            )
            mock_project = flexmock(
                GithubProject(
                    repo="container-libs",
                    service=mock_service,
                    namespace="containers",
                )
            )
            # Mock only the methods that need specific behavior
            mock_project.should_receive("is_private").and_return(False)
            mock_project.should_receive("get_web_url").and_return(
                "https://github.com/containers/container-libs"
            )
            mock_project.should_receive("get_git_urls").and_return(
                {
                    "git": "https://github.com/containers/container-libs.git",
                    "ssh": "git@github.com:containers/container-libs.git",
                }
            )

            # Create a real GithubPullRequest instance and only mock what's needed
            mock_raw_pr = flexmock(
                base=flexmock(ref="main"),
                head=flexmock(sha=commit_sha),
            )
            mock_pr = flexmock(
                GithubPullRequest(
                    raw_pr=mock_raw_pr,
                    project=mock_project,
                )
            )
            # Mock only the properties that need specific values
            mock_pr._source_project = mock_project
            mock_pr.should_receive("target_branch").and_return("main")
            mock_pr.should_receive("head_commit").and_return(commit_sha)

            mock_project.should_receive("get_pr").and_return(mock_pr)
            handler._project = mock_project
            handler.data.project_url = "https://github.com/containers/container-libs"
            # CRITICAL: Set db_project_event before accessing copr_build_helper
            # The copr_build_helper property needs it to create the helper
            handler.data._db_project_event = fresh_project_event

            # Mock only external API calls to let the real CoprHelper code run
            # This allows us to test the race condition in CoprHelper methods

            # Mock CoprClient creation to avoid needing a real config file
            # Each thread needs its own mock_copr_client with the correct build_id
            from copr.v3 import Client as CoprClient

            # Create a mock project object once and reuse it
            mock_copr_project = flexmock(
                chroot_repos={
                    target: None,
                    "fedora-rawhide-x86_64": None,
                    "centos-stream-9-x86_64": None,
                },
                additional_repos=[],
                module_hotfixes=False,
                bootstrap=None,
                unlisted_on_hp=True,
                delete_after_days=60,
            )

            # Use a real CoprClient instance and only mock what's needed
            mock_copr_client = flexmock(
                CoprClient(
                    config={"username": "packit", "copr_url": "https://copr.fedorainfracloud.org"}
                ),
            )
            # Mock get_aliases to return a fixed latest stable Fedora version
            # This ensures the test is deterministic and
            # won't break when Fedora releases new versions
            # expand_aliases requires both "fedora-all" and "epel-all"
            # to be present
            from packit.config import aliases
            from packit.config.aliases import Distro

            fixed_latest_stable = "fedora-41"
            fixed_latest_stable_chroot = f"{fixed_latest_stable}-x86_64"

            # Create the aliases dict with the fixed latest stable
            mock_aliases = {
                "fedora-stable": [
                    Distro("fedora-40", "f40"),
                    Distro(fixed_latest_stable, "f41"),
                ],
                "fedora-all": [
                    Distro("fedora-40", "f40"),
                    Distro(fixed_latest_stable, "f41"),
                    Distro("fedora-rawhide", "rawhide"),
                ],
                "epel-all": [
                    Distro("epel-8", "epel8"),
                    Distro("epel-9", "epel9"),
                ],
            }
            flexmock(aliases).should_receive("get_aliases").and_return(mock_aliases)

            # Mock get_valid_build_targets to return the targets we need
            # This is called by get_latest_fedora_stable_chroot and build_targets_all
            from packit.copr_helper import CoprHelper

            def mock_get_valid_build_targets(self, *args, **kwargs):
                # get_latest_fedora_stable_chroot calls: get_valid_build_targets("fedora-41")
                # It expects exactly one value: {"fedora-41-x86_64"}
                # Check if called with exactly one argument that matches fixed_latest_stable
                if len(args) == 1 and args[0] == fixed_latest_stable:
                    return {fixed_latest_stable_chroot}
                # build_targets_all calls with configured_build_targets
                # (e.g., ["fedora-rawhide-x86_64"])
                # Return only the target that matches our configured target
                result = set()
                for arg in args:
                    if arg == target:
                        result.add(target)
                # If no matches, return the target to ensure we have valid targets
                if not result:
                    result = {target}
                return result

            flexmock(CoprHelper, get_valid_build_targets=mock_get_valid_build_targets)

            # Still need to mock mock_chroot_proxy for other code paths
            fedora_chroots = {
                fixed_latest_stable_chroot: None,
                target: None,
                "fedora-rawhide-x86_64": None,
                "centos-stream-9-x86_64": None,
            }
            mock_copr_client.mock_chroot_proxy = flexmock(get_list=lambda: fedora_chroots)
            mock_copr_client.project_proxy = flexmock(
                add=lambda **kwargs: mock_copr_project,
                get=lambda **kwargs: mock_copr_project,
                edit=lambda **kwargs: None,
                request_permissions=lambda **kwargs: None,
            )
            mock_copr_client.build_proxy = flexmock(
                create_from_custom=lambda **kwargs: flexmock(id=mock_build_id),
            )
            # Store this thread's mock client in the shared dictionary
            mock_clients_by_package[package_name] = mock_copr_client

            # Store thread ID -> package_name mapping so we can identify which thread is calling
            import threading

            thread_packages[threading.current_thread().ident] = package_name

            # Mock status reporting to avoid external API calls
            flexmock(handler.copr_build_helper).should_receive(
                "report_running_build_and_test_on_build_submission"
            )
            flexmock(handler.copr_build_helper).should_receive("report_status_to_all_for_chroot")
            flexmock(handler.copr_build_helper).should_receive("report_status_to_build_for_chroot")
            flexmock(handler.copr_build_helper).should_receive("monitor_not_submitted_copr_builds")
            flexmock(handler.copr_build_helper.status_reporter).should_receive("comment")

            # Mock srpm_path to avoid needing actual SRPM file
            flexmock(handler.copr_build_helper).should_receive("srpm_path").and_return(
                "/tmp/test.srpm"
            )

            # CRITICAL: Mock SRPMBuildModel.create_with_new_run() to return the shared pipeline
            # This ensures all threads use the same pipeline, triggering the race condition
            # when they all try to create a CoprBuildGroup for it concurrently

            from packit_service.models import SRPMBuildModel

            def mock_create_with_new_run(cls, *args, **kwargs):
                # Query fresh objects from this thread's session to avoid SQLAlchemy
                # "already attached to session" errors. All threads will get objects
                # representing the same database row (same pipeline_id), which is what
                # we need to trigger the race condition.
                #
                # The race condition happens because:
                # 1. All threads query the same pipeline (same pipeline_id)
                # 2. Each thread checks run_model.copr_build_group (in-memory attribute)
                # 3. All see None (because they're checking their own session's object state)
                # 4. All try to set run_model.copr_build_group = build_group
                # 5. Only the last commit wins, causing the race condition
                #
                # The issue: CoprBuildGroupModel.create() checks run_model.copr_build_group
                # before merging the object into its session. If the object is detached,
                # this triggers a lazy load which fails. We need to ensure the relationship
                # is accessible without lazy loading. We'll use getattr with a default to
                # avoid triggering lazy loads, or we can ensure the object is properly merged.
                #
                # Actually, the best approach is to query the object fresh in each thread's
                # session, commit it (so it's persisted), then return it.
                # When CoprBuildGroupModel.create()
                # uses it, it will merge it into its own session. But we need to ensure the
                # relationship check doesn't trigger a lazy load.
                #
                # Solution: Query with the relationship already loaded (eager load), then
                # after commit and expunge, the relationship value is already in the object's
                # __dict__, so accessing it won't trigger a lazy load.
                with sa_session_transaction(commit=True) as session:
                    # Query with eager loading to populate all relationships in __dict__
                    # This prevents lazy load errors when the object is detached
                    from sqlalchemy.orm import joinedload

                    shared_srpm = session.query(SRPMBuildModel).filter_by(id=shared_srpm_id).first()
                    shared_pipeline = (
                        session.query(PipelineModel)
                        .options(
                            joinedload(PipelineModel.copr_build_group),
                            joinedload(PipelineModel.project_event),
                            joinedload(PipelineModel.srpm_build),
                        )
                        .filter_by(id=shared_pipeline_id)
                        .first()
                    )
                    # Update package_name to match this thread's package
                    # (This simulates different packages trying to use the same pipeline)
                    # Note: Since all threads update the same shared pipeline, the last one wins
                    shared_pipeline.package_name = package_name
                    session.add(shared_pipeline)
                    session.commit()
                    # Access all relationships to ensure they're loaded into __dict__
                    # This prevents lazy load errors when the object is detached
                    _ = shared_pipeline.copr_build_group  # Load it now
                    _ = shared_pipeline.project_event  # Load it now (needed for cloning)
                    _ = shared_pipeline.srpm_build  # Load it now (needed for cloning)
                    # Expunge to detach - but the relationship values are in __dict__
                    session.expunge(shared_srpm)
                    session.expunge(shared_pipeline)
                    return shared_srpm, shared_pipeline

            # Patch the classmethod directly (similar to
            # how CoprClient.create_from_config_file is patched)
            # create_with_new_run is a classmethod, so wrap it properly
            # Intentional patching for testing - ignore mypy errors
            SRPMBuildModel.create_with_new_run = classmethod(mock_create_with_new_run)  # type: ignore

            # Mock celery_app.send_task to prevent actual task sending
            from packit_service.celerizer import celery_app

            flexmock(celery_app).should_receive("send_task")

            # Wait for all threads to be ready before calling the handler
            # This ensures they all execute run() concurrently and create
            # pipelines/builds at the same time
            barrier.wait()

            # Now call run() - this will create pipelines and builds concurrently
            # The race condition occurs here when multiple handlers try to create
            # pipelines and builds for the same project_event but different packages
            result = handler.run()

            # Verify the handler succeeded
            assert result.get("success", False), (
                f"Handler failed for {package_name}: {result.get('details', {})}"
            )

            # Store the build_id for verification later
            builds_data[package_name]["build_id"] = str(mock_build_id)

            # Query the database to verify the build and pipeline were created
            # Use a fresh session and expire all cached objects to ensure we see committed data
            with sa_session_transaction() as session:
                # Expire all cached objects to ensure we see the latest committed data
                session.expire_all()

                # Query directly by commit_sha and package_name to find the builds
                # This avoids relying on cached relationships
                all_builds = (
                    session.query(CoprBuildTargetModel)
                    .join(CoprBuildGroupModel)
                    .join(PipelineModel)
                    .join(ProjectEventModel)
                    .filter(ProjectEventModel.commit_sha == commit_sha)
                    .filter(PipelineModel.package_name == package_name)
                    .filter(CoprBuildTargetModel.target == target)
                    .all()
                )
                logger.info(
                    f"Found {len(all_builds)} builds for {package_name} with target {target} "
                    f"and commit_sha {commit_sha}"
                )
                for b in all_builds:
                    logger.info(
                        f"  Build id={b.id}, build_id={b.build_id}, target={b.target}, "
                        f" group_id={b.copr_build_group_id}"
                    )

                # Now check for the specific build_id
                created_build = (
                    session.query(CoprBuildTargetModel)
                    .filter_by(build_id=str(mock_build_id))
                    .first()
                )
                if created_build is None:
                    # Check if there's a build with build_id=None that should have been updated
                    build_without_id = (
                        session.query(CoprBuildTargetModel)
                        .join(CoprBuildGroupModel)
                        .join(PipelineModel)
                        .join(ProjectEventModel)
                        .filter(ProjectEventModel.commit_sha == commit_sha)
                        .filter(PipelineModel.package_name == package_name)
                        .filter(CoprBuildTargetModel.target == target)
                        .filter(CoprBuildTargetModel.build_id.is_(None))
                        .first()
                    )
                    if build_without_id:
                        raise AssertionError(
                            f"Build exists for {package_name} but build_id is None "
                            f"(should be {mock_build_id}). "
                            f"This suggests handle_rpm_build_start() didn't set the build_id. "
                            f"Group has "
                            f"{len(build_without_id.group_of_targets.copr_build_targets)} builds."
                        )
                assert created_build is not None, (
                    f"Build {mock_build_id} should exist for {package_name}"
                )

                # Verify pipeline was created
                pipeline = (
                    session.query(PipelineModel)
                    .join(CoprBuildGroupModel)
                    .join(CoprBuildTargetModel)
                    .filter(CoprBuildTargetModel.id == created_build.id)
                    .first()
                )
                assert pipeline is not None, f"Pipeline should exist for {package_name}"
                # Note: Since all threads use the same shared pipeline and update its package_name,
                # the last thread to update it will win. So we can't assert a specific package_name.
                # Instead, we just verify that the pipeline exists and is linked to the build.
                assert pipeline.package_name in [
                    "containers-common-fedora",
                    "containers-common-eln",
                    "containers-common-centos",
                ], (
                    f"Pipeline package_name should be one of the test packages,"
                    f"got {pipeline.package_name}"
                )

                results[package_name] = {
                    "build_id": str(mock_build_id),
                    "target": target,
                    "build": created_build,
                    "group_id": created_build.copr_build_group_id,
                    "pipeline_id": pipeline.id,
                }
        except Exception as e:
            errors[package_name] = e
            raise

    # Create three threads that will execute concurrently
    thread_fedora = threading.Thread(
        target=run_copr_build_handler_for_package,
        args=("containers-common-fedora",),
    )
    thread_eln = threading.Thread(
        target=run_copr_build_handler_for_package,
        args=("containers-common-eln",),
    )
    thread_centos = threading.Thread(
        target=run_copr_build_handler_for_package,
        args=("containers-common-centos",),
    )

    # Start all three threads at the same time
    thread_fedora.start()
    thread_eln.start()
    thread_centos.start()

    # Wait for all threads to complete
    thread_fedora.join()
    thread_eln.join()
    thread_centos.join()

    # Verify no errors occurred
    assert not errors, f"Errors occurred: {errors}"

    # Verify all three handlers succeeded
    assert "containers-common-fedora" in results
    assert "containers-common-eln" in results
    assert "containers-common-centos" in results

    # Verify all three builds and pipelines exist and were created
    with sa_session_transaction() as session:
        # Get build_ids from results
        build_id_fedora = results["containers-common-fedora"]["build_id"]
        build_id_eln = results["containers-common-eln"]["build_id"]
        build_id_centos = results["containers-common-centos"]["build_id"]

        # Verify that all three copr_build_targets exist
        # Query by build_id only since build_id is unique
        build_fedora = (
            session.query(CoprBuildTargetModel).filter_by(build_id=build_id_fedora).first()
        )
        build_eln = session.query(CoprBuildTargetModel).filter_by(build_id=build_id_eln).first()
        build_centos = (
            session.query(CoprBuildTargetModel).filter_by(build_id=build_id_centos).first()
        )

        assert build_fedora is not None, "containers-common-fedora build should exist"
        assert build_eln is not None, "containers-common-eln build should exist"
        assert build_centos is not None, "containers-common-centos build should exist"

        # Verify all three copr_build_groups exist
        group_fedora_id = build_fedora.copr_build_group_id
        group_eln_id = build_eln.copr_build_group_id
        group_centos_id = build_centos.copr_build_group_id

        group_fedora = session.query(CoprBuildGroupModel).filter_by(id=group_fedora_id).first()
        group_eln = session.query(CoprBuildGroupModel).filter_by(id=group_eln_id).first()
        group_centos = session.query(CoprBuildGroupModel).filter_by(id=group_centos_id).first()

        assert group_fedora is not None, "containers-common-fedora group should exist"
        assert group_eln is not None, "containers-common-eln group should exist"
        assert group_centos is not None, "containers-common-centos group should exist"

        # Check if race condition occurred: all three builds should have different group IDs
        # Since all threads use the same pipeline,
        # the race condition in CoprBuildGroupModel.create()
        # will cause multiple threads to try to set copr_build_group on the same pipeline.
        # With the reverted code (checking in-memory attribute instead of DB), all threads see None
        # and try to set it, but only the last commit wins. This may cause builds to share groups
        # or pipelines to have incorrect group assignments.
        unique_group_ids = {group_fedora_id, group_eln_id, group_centos_id}

        # Log group information for debugging
        print("\n*** Group IDs for each package ***")
        print(f"  containers-common-fedora: group_id={group_fedora_id}")
        print(f"  containers-common-eln: group_id={group_eln_id}")
        print(f"  containers-common-centos: group_id={group_centos_id}")
        print(f"  Unique group IDs: {unique_group_ids} (count: {len(unique_group_ids)})\n")

        if len(unique_group_ids) < 3:
            # Race condition detected: multiple packages share the same group
            print(
                f"*** RACE CONDITION DETECTED ***\n"
                f"Expected 3 different group IDs, but got {len(unique_group_ids)}. "
                f"This indicates that multiple handlers created builds "
                f"in the same CoprBuildGroupModel.\n"
            )
            # Count how many builds are in each group and show package names
            for group_id in unique_group_ids:
                builds_in_group = (
                    session.query(CoprBuildTargetModel)
                    .filter_by(copr_build_group_id=group_id)
                    .all()
                )
                package_names = []
                for _ in builds_in_group:
                    # Get package name from the pipeline
                    pipeline = (
                        session.query(PipelineModel).filter_by(copr_build_group_id=group_id).first()
                    )
                    if pipeline:
                        package_names.append(pipeline.package_name)
                print(
                    f"  Group {group_id} has {len(builds_in_group)} "
                    f"builds for packages: {set(package_names)}"
                )

            # Fail the test to indicate the race condition was reproduced
            pytest.fail(
                f"Race condition reproduced: {len(unique_group_ids)} unique group(s) instead of 3."
                f"Group IDs: fedora={group_fedora_id}, eln={group_eln_id}, "
                f"centos={group_centos_id}."
                f"This indicates that CoprBuildGroupModel.create() has a race condition."
            )

        # With the race condition, the reverted code may cause issues:
        # - Multiple threads try to set copr_build_group on the same pipeline
        # - Only the last commit wins, so some groups might be lost
        # - The pipeline's copr_build_group_id will be set to the last group that committed
        #
        # Verify that all three groups were created (even if the race condition caused issues)
        assert group_fedora_id != group_eln_id, "Each package should have its own group"
        assert group_fedora_id != group_centos_id, "Each package should have its own group"
        assert group_eln_id != group_centos_id, "Each package should have its own group"

        # Since all threads use the same shared pipeline, there should be only one pipeline
        # (or cloned pipelines if the code handles the race condition correctly)
        shared_pipeline = session.query(PipelineModel).filter_by(id=shared_pipeline_id).first()
        assert shared_pipeline is not None, "The shared pipeline should exist"

        # The pipeline's copr_build_group_id will be set to whichever group committed last
        # due to the race condition in the reverted code
        assert shared_pipeline.copr_build_group_id is not None, (
            "The pipeline should have a copr_build_group_id set (by the last thread to commit)"
        )
        assert shared_pipeline.copr_build_group_id in unique_group_ids, (
            "The pipeline's copr_build_group_id should match one of the created groups"
        )

        # Verify the pipeline is linked to the project_event
        assert shared_pipeline.project_event_id == project_event_id

        # The package_name will be set to whichever thread set it last
        # (this is a side effect of the mock, not the race condition itself)
        assert shared_pipeline.package_name in [
            "containers-common-fedora",
            "containers-common-eln",
            "containers-common-centos",
        ], (
            f"Pipeline package_name should be one of the test packages,"
            f"got {shared_pipeline.package_name}"
        )

        # Verify that all three builds were created in the same second
        # This ensures the race condition test is actually testing concurrent execution
        group_fedora_second = group_fedora.submitted_time.replace(microsecond=0)
        group_eln_second = group_eln.submitted_time.replace(microsecond=0)
        group_centos_second = group_centos.submitted_time.replace(microsecond=0)
        assert group_fedora_second == group_eln_second == group_centos_second, (
            f"All groups should be created in the same second. "
            f"fedora: {group_fedora.submitted_time}, "
            f"eln: {group_eln.submitted_time}, "
            f"centos: {group_centos.submitted_time}"
        )
