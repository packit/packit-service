# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from packit.api import PackitAPI
from packit_service.models import CoprBuildTargetModel
from packit_service.worker.handlers import CoprBuildEndHandler
from packit_service.worker.handlers import copr
from packit_service.worker.events import AbstractCoprBuildEvent
from flexmock import flexmock


def test_handle_scan():
    srpm_mock = flexmock(url="https://some-url/my-srpm.src.rpm")
    flexmock(CoprBuildTargetModel).should_receive("get_by_build_id").and_return(
        flexmock(
            get_srpm_build=lambda: srpm_mock,
            target="fedora-rawhide-x86_64",
            get_project_event_model=lambda: None,
        )
    )
    flexmock(AbstractCoprBuildEvent).should_receive("from_event_dict").and_return(
        flexmock(chroot="fedora-rawhide-x86_64", build_id="123")
    )

    flexmock(copr).should_receive("download_file").once().and_return(True)
    flexmock(PackitAPI).should_receive("run_osh_build").once()

    CoprBuildEndHandler(
        package_config=flexmock(),
        job_config=flexmock(osh_diff_scan_after_copr_build=True),
        event={},
    ).handle_scan()
