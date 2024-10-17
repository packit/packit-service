# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import datetime
import pytest
import json
from flexmock import flexmock
from celery.canvas import group as celery_group

from packit.api import PackitAPI
from packit.config import (
    JobType,
    JobConfigTriggerType,
    PackageConfig,
    JobConfig,
    CommonPackageConfig,
)
from packit_service.models import (
    CoprBuildTargetModel,
    ProjectEventModelType,
    BuildStatus,
    ScanModel,
)
from packit_service.worker.tasks import run_openscanhub_task_finish_handler
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.reporting import BaseCommitStatus

from packit_service.worker.events import (
    AbstractCoprBuildEvent,
    OpenScanHubTaskFinishEvent,
)
from packit_service.worker.helpers import scan
from packit_service.worker.handlers.copr import ScanHelper
from packit_service.worker.helpers.build import CoprBuildJobHelper

from tests.spellbook import DATA_DIR, get_parameters_from_results


@pytest.fixture()
def openscanhub_task_finish_event():
    with open(DATA_DIR / "fedmsg" / "open_scan_hub_task_finish.json") as outfile:
        return json.load(outfile)


@pytest.mark.parametrize(
    "build_models",
    [
        [("abcdef", [flexmock(get_srpm_build=lambda: flexmock(url="base-srpm-url"))])],
        [
            ("abcdef", []),
            (
                "fedcba",
                [flexmock(get_srpm_build=lambda: flexmock(url="base-srpm-url"))],
            ),
        ],
    ],
)
def test_handle_scan(build_models):
    srpm_mock = flexmock(url="https://some-url/my-srpm.src.rpm")
    flexmock(AbstractCoprBuildEvent).should_receive("from_event_dict").and_return(
        flexmock(chroot="fedora-rawhide-x86_64", build_id="123", pr_id=12)
    )
    flexmock(scan).should_receive("download_file").twice().and_return(True)

    for commit_sha, models in build_models:
        flexmock(CoprBuildTargetModel).should_receive("get_all_by").with_args(
            commit_sha=commit_sha,
            project_name="commit-project",
            owner="user-123",
            target="fedora-rawhide-x86_64",
            status=BuildStatus.success,
        ).and_return(models).once()

    flexmock(PackitAPI).should_receive("run_osh_build").once().and_return(
        'some\nmultiline\noutput\n{"id": 123}\nand\nmore\n{"url": "scan-url"}\n'
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
            )
        ]
    )

    project = flexmock(
        get_pr=lambda pr_id: flexmock(
            target_branch="main", target_branch_head_commit="abcdef"
        ),
        get_commits=lambda ref: ["abcdef", "fedcba"],
    )

    ScanHelper(
        build=flexmock(
            id=1,
            get_srpm_build=lambda: srpm_mock,
            target="fedora-rawhide-x86_64",
            get_project_event_model=lambda: flexmock(
                type=ProjectEventModelType.pull_request,
                get_project_event_object=lambda: flexmock(),
            ),
        ),
        copr_build_helper=CoprBuildJobHelper(
            service_config=flexmock(),
            package_config=package_config,
            project=project,
            metadata=flexmock(pr_id=12),
            db_project_event=flexmock(get_project_event_object=lambda: None),
            job_config=flexmock(),
        ),
    ).handle_scan()


@pytest.mark.parametrize(
    "job_config_type,job_config_trigger,job_config_targets,copr_build_state,num_of_handlers",
    [
        (
            JobType.copr_build,
            JobConfigTriggerType.commit,
            ["fedora-rawhide-x86_64"],
            "success",
            1,
        ),
        (
            JobType.copr_build,
            JobConfigTriggerType.pull_request,
            ["fedora-rawhide-x86_64"],
            "success",
            2,
        ),
        (
            JobType.copr_build,
            JobConfigTriggerType.pull_request,
            ["fedora-rawhide-x86_64"],
            "failed",
            2,
        ),
    ],
)
def test_handle_scan_task_finish(
    openscanhub_task_finish_event,
    add_pull_request_event_with_sha_123456,
    job_config_type,
    job_config_trigger,
    job_config_targets,
    copr_build_state,
    num_of_handlers,
):
    db_project_object, db_project_event = add_pull_request_event_with_sha_123456
    db_build = (
        flexmock(
            build_id="55",
            status=copr_build_state,
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
    flexmock(OpenScanHubTaskFinishEvent).should_receive(
        "get_packages_config"
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
                        )
                    },
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            _targets=["fedora-stable"],
                            specfile_path="test.spec",
                        )
                    },
                ),
            ],
            packages={"package": CommonPackageConfig()},
        )
    )
    flexmock(celery_group).should_receive("apply_async")
    flexmock(ScanModel).should_receive("get_by_task_id").and_return(
        flexmock(copr_build_target=db_build)
    )
    flexmock(Pushgateway).should_receive("push").and_return()

    processing_results = SteveJobs().process_message(openscanhub_task_finish_event)
    assert len(processing_results) == num_of_handlers

    if processing_results:
        url = (
            "http://openscanhub.fedoraproject.org/task/15649/log/gvisor-tap-vsock-0.7.5-1."
            "20241007054606793155.pr405.23.g829aafd6/scan-results.js?format=raw"
        )
        links_to_external_services = {
            "Added issues": (
                "http://openscanhub.fedoraproject.org/task/15649/log/added.js"
                "?format=raw"
            ),
            "Fixed issues": (
                "http://openscanhub.fedoraproject.org/task/15649/log/fixed.js"
                "?format=raw"
            ),
            "Scan results": (
                "http://openscanhub.fedoraproject.org/task/15649/log/gvisor-tap-vsock-"
                "0.7.5-1.20241007054606793155.pr405.23.g829aafd6/scan-results.js?format=raw"
            ),
        }
        if copr_build_state == "success":
            state = BaseCommitStatus.success
            description = (
                "Scan in OpenScanHub is finished. Check the URL for more details."
            )
        else:
            state = BaseCommitStatus.neutral
            description = (
                "Scan in OpenScanHub is finished but the build did not finish yet"
                " or did not succeed."
            )
        if num_of_handlers > 1:
            # one handler is always skipped because it is for fedora-stable ->
            # no rawhide build
            flexmock(ScanHelper).should_receive("report").with_args(
                state=state,
                description=description,
                url=url,
                links_to_external_services=links_to_external_services,
            ).once().and_return()

        for sub_results in processing_results:
            event_dict, job, job_config, package_config = get_parameters_from_results(
                [sub_results]
            )
            assert json.dumps(event_dict)

            run_openscanhub_task_finish_handler(
                package_config=package_config,
                event=event_dict,
                job_config=job_config,
            )
