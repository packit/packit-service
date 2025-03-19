# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import datetime
import json

import pytest
from celery.canvas import group as celery_group
from flexmock import flexmock
from packit.api import PackitAPI
from packit.config import (
    CommonPackageConfig,
    JobConfig,
    JobConfigTriggerType,
    JobType,
    PackageConfig,
)

from packit_service.events import (
    copr,
    openscanhub,
)
from packit_service.models import (
    BuildStatus,
    CoprBuildTargetModel,
    OSHScanModel,
    ProjectEventModelType,
)
from packit_service.worker.handlers import CoprOpenScanHubTaskFinishedHandler
from packit_service.worker.handlers.copr import CoprOpenScanHubHelper
from packit_service.worker.helpers import open_scan_hub
from packit_service.worker.helpers.build import CoprBuildJobHelper
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.reporting import BaseCommitStatus
from packit_service.worker.tasks import (
    run_openscanhub_task_finished_handler,
    run_openscanhub_task_started_handler,
)
from tests.spellbook import DATA_DIR, get_parameters_from_results


@pytest.fixture()
def openscanhub_task_finished_event():
    with open(DATA_DIR / "fedmsg" / "open_scan_hub_task_finished.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def openscanhub_task_started_event():
    with open(DATA_DIR / "fedmsg" / "open_scan_hub_task_started.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def prepare_openscanhub_db_and_handler(
    add_pull_request_event_with_sha_123456,
):
    db_project_object, db_project_event = add_pull_request_event_with_sha_123456
    db_build = (
        flexmock(
            build_id="55",
            identifier=None,
            status="success",
            build_submitted_time=datetime.datetime.utcnow(),
            target="the-target",
            owner="the-owner",
            project_name="the-namespace-repo_name-5",
            commit_sha="123456",
            project_event=flexmock(),
            srpm_build=flexmock(url=None)
            .should_receive("set_url")
            .with_args("https://some.host/my.srpm")
            .mock(),
        )
        .should_receive("get_project_event_object")
        .and_return(db_project_object)
        .mock()
        .should_receive("get_project_event_model")
        .and_return(db_project_event)
        .mock()
    )

    flexmock(celery_group).should_receive("apply_async")
    scan_mock = flexmock(
        id=123,
        copr_build_target=db_build,
        url="https://openscanhub.fedoraproject.org/task/17514/",
        set_issues_added_url=lambda _: None,
        set_issues_fixed_url=lambda _: None,
        set_scan_results_url=lambda _: None,
    )
    flexmock(OSHScanModel).should_receive("get_by_task_id").and_return(scan_mock)
    flexmock(Pushgateway).should_receive("push").and_return()
    yield scan_mock


@pytest.mark.parametrize(
    "build_models",
    [
        [
            (
                "abcdef",
                [flexmock(identifier=None, get_srpm_build=lambda: flexmock(url="base-srpm-url"))],
            )
        ],
        [
            ("abcdef", []),
            (
                "fedcba",
                [flexmock(identifier=None, get_srpm_build=lambda: flexmock(url="base-srpm-url"))],
            ),
        ],
    ],
)
def test_handle_scan(build_models):
    srpm_mock = flexmock(url="https://some-url/my-srpm.src.rpm")
    flexmock(copr.CoprBuild).should_receive("from_event_dict").and_return(
        flexmock(chroot="fedora-rawhide-x86_64", build_id="123", pr_id=12),
    )
    flexmock(open_scan_hub).should_receive("download_file").twice().and_return(True)

    for commit_sha, models in build_models:
        flexmock(CoprBuildTargetModel).should_receive("get_all_by").with_args(
            commit_sha=commit_sha,
            project_name="commit-project",
            owner="user-123",
            target="fedora-rawhide-x86_64",
            status=BuildStatus.success,
        ).and_return(models).once()

    flexmock(PackitAPI).should_receive("run_osh_build").once().and_return(
        'some\nmultiline\noutput\n{"id": 123}\nand\nmore\n{"url": "scan-url"}\n',
    )

    flexmock(CoprBuildJobHelper).should_receive("_report")
    package_config = flexmock(
        get_job_views=lambda: [
            flexmock(
                type=JobType.copr_build,
                trigger=JobConfigTriggerType.commit,
                branch="main",
                project="commit-project",
                owner="user-123",
                identifier=None,
            ),
        ],
    )

    project = flexmock(
        get_pr=lambda pr_id: flexmock(
            target_branch="main",
            target_branch_head_commit="abcdef",
        ),
        get_commits=lambda ref: ["abcdef", "fedcba"],
    )

    CoprOpenScanHubHelper(
        build=flexmock(
            id=1,
            get_srpm_build=lambda: srpm_mock,
            target="fedora-rawhide-x86_64",
            scan=None,
            get_project_event_model=lambda: flexmock(
                type=ProjectEventModelType.pull_request,
                get_project_event_object=lambda: flexmock(),
            ),
        )
        .should_receive("add_scan_transaction")
        .once()
        .and_return(flexmock())
        .mock(),
        copr_build_helper=CoprBuildJobHelper(
            service_config=flexmock(),
            package_config=package_config,
            project=project,
            metadata=flexmock(pr_id=12),
            db_project_event=flexmock(get_project_event_object=lambda: None),
            job_config=flexmock(identifier=None),
        ),
    ).handle_scan()


@pytest.mark.parametrize(
    "job_config_type,job_config_trigger,job_config_targets,scan_status,num_of_handlers",
    [
        (
            JobType.copr_build,
            JobConfigTriggerType.commit,
            ["fedora-rawhide-x86_64"],
            openscanhub.task.Status.success,
            0,
        ),
        (
            JobType.copr_build,
            JobConfigTriggerType.pull_request,
            ["fedora-stable"],
            openscanhub.task.Status.success,
            0,
        ),
        (
            JobType.copr_build,
            JobConfigTriggerType.pull_request,
            ["fedora-rawhide-x86_64"],
            openscanhub.task.Status.success,
            1,
        ),
        (
            JobType.copr_build,
            JobConfigTriggerType.pull_request,
            ["fedora-rawhide-x86_64"],
            openscanhub.task.Status.fail,
            1,
        ),
        (
            JobType.copr_build,
            JobConfigTriggerType.pull_request,
            ["fedora-rawhide-x86_64"],
            openscanhub.task.Status.cancel,
            1,
        ),
        (
            JobType.copr_build,
            JobConfigTriggerType.commit,
            ["fedora-rawhide-x86_64"],
            openscanhub.task.Status.interrupt,
            0,
        ),
    ],
)
def test_handle_scan_task_finished(
    openscanhub_task_finished_event,
    prepare_openscanhub_db_and_handler,
    job_config_type,
    job_config_trigger,
    job_config_targets,
    scan_status,
    num_of_handlers,
):
    flexmock(openscanhub.task.Finished).should_receive(
        "get_packages_config",
    ).and_return(
        PackageConfig(
            jobs=[
                JobConfig(
                    type=job_config_type,
                    trigger=job_config_trigger,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=job_config_targets,
                            specfile_path="test.spec",
                        ),
                    },
                ),
            ],
            packages={"package": CommonPackageConfig()},
        ),
    )

    scan_mock = prepare_openscanhub_db_and_handler
    openscanhub_task_finished_event["status"] = scan_status
    processing_results = SteveJobs().process_message(openscanhub_task_finished_event)
    assert len(processing_results) == num_of_handlers

    if processing_results:
        links_to_external_services = {
            "OpenScanHub task": "https://openscanhub.fedoraproject.org/task/17514/"
        }
        if scan_status == openscanhub.task.Status.success:
            state = BaseCommitStatus.success
            description = "Scan in OpenScanHub is finished. 2 new findings identified."
            flexmock(scan_mock).should_receive("set_status").with_args(
                "succeeded",
            ).once()
            flexmock(scan_mock).should_receive("set_issues_added_count").with_args(2).once()
            flexmock(CoprOpenScanHubTaskFinishedHandler).should_receive(
                "get_number_of_new_findings_identified"
            ).and_return(2)
            links_to_external_services.update(
                {
                    "Added issues": (
                        "https://openscanhub.fedoraproject.org/task/15649/log/added.html"
                    ),
                }
            )
        elif scan_status == openscanhub.task.Status.cancel:
            state = BaseCommitStatus.neutral
            description = f"Scan in OpenScanHub is finished in a {scan_status} state."
            flexmock(scan_mock).should_receive("set_status").with_args(
                "canceled",
            ).once()
        else:
            state = BaseCommitStatus.neutral
            description = f"Scan in OpenScanHub is finished in a {scan_status} state."
            flexmock(scan_mock).should_receive("set_status").with_args("failed").once()
        if num_of_handlers == 1:
            # one handler is always skipped because it is for fedora-stable ->
            # no rawhide build
            flexmock(CoprOpenScanHubHelper).should_receive("report").with_args(
                state=state,
                description=description,
                url="/jobs/openscanhub/123",
                links_to_external_services=links_to_external_services,
            ).once().and_return()

        for sub_results in processing_results:
            event_dict, job, job_config, package_config = get_parameters_from_results(
                [sub_results],
            )
            assert json.dumps(event_dict)

            run_openscanhub_task_finished_handler(
                package_config=package_config,
                event=event_dict,
                job_config=job_config,
            )


@pytest.mark.parametrize(
    "job_config_type,job_config_trigger,job_config_targets,num_of_handlers",
    [
        (
            JobType.copr_build,
            JobConfigTriggerType.commit,
            ["fedora-rawhide-x86_64"],
            0,
        ),
        (
            JobType.copr_build,
            JobConfigTriggerType.pull_request,
            ["fedora-stable"],
            0,
        ),
        (
            JobType.copr_build,
            JobConfigTriggerType.pull_request,
            ["fedora-rawhide-x86_64"],
            1,
        ),
    ],
)
def test_handle_scan_task_started(
    openscanhub_task_started_event,
    prepare_openscanhub_db_and_handler,
    job_config_type,
    job_config_trigger,
    job_config_targets,
    num_of_handlers,
):
    flexmock(openscanhub.task.Started).should_receive(
        "get_packages_config",
    ).and_return(
        PackageConfig(
            jobs=[
                JobConfig(
                    type=job_config_type,
                    trigger=job_config_trigger,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=job_config_targets,
                            specfile_path="test.spec",
                        ),
                    },
                ),
            ],
            packages={"package": CommonPackageConfig()},
        ),
    )

    scan_mock = prepare_openscanhub_db_and_handler
    processing_results = SteveJobs().process_message(openscanhub_task_started_event)
    assert len(processing_results) == num_of_handlers

    if processing_results:
        if num_of_handlers == 1:
            flexmock(scan_mock).should_receive("set_status").with_args("running").once()
            flexmock(CoprOpenScanHubHelper).should_receive("report").with_args(
                state=BaseCommitStatus.running,
                description="Scan in OpenScanHub has started.",
                url="https://openscanhub.fedoraproject.org/task/17514/",
            ).once().and_return()

        for sub_results in processing_results:
            event_dict, job, job_config, package_config = get_parameters_from_results(
                [sub_results],
            )
            assert json.dumps(event_dict)

            run_openscanhub_task_started_handler(
                package_config=package_config,
                event=event_dict,
                job_config=job_config,
            )
