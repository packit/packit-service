# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

# import re
import logging
from dataclasses import dataclass
from typing import Optional

# from flexmock import flexmock
import pytest

from packit_service.worker.helpers.build.build_helper import BaseBuildJobHelper
from packit_service.worker.helpers.build.copr_build import CoprBuildJobHelper
from packit_service.worker.helpers.testing_farm import TestingFarmJobHelper

logger = logging.getLogger(__name__)


@dataclass
class StatusNameTestcase:
    job_name: Optional[str] = None
    chroot: Optional[str] = None
    event: Optional[str] = None
    identifier: Optional[str] = None
    package: Optional[str] = None
    template: Optional[str] = None
    expected: str = None


@pytest.mark.parametrize(
    "testcase",
    [
        pytest.param(
            StatusNameTestcase(
                job_name="copr",
                chroot="centos-10",
                event="pr-42",
                identifier=None,
                package="packit",
                template=None,
                expected="copr:pr-42:centos-10",
            ),
            id="default template",
        ),
        pytest.param(
            StatusNameTestcase(
                job_name="copr",
                chroot="centos-10",
                event="pr-42",
                identifier=None,
                package="packit",
                template="packit:{job_name}:{event}:{chroot}",
                expected="packit:copr:pr-42:centos-10",
            ),
            id="custom template",
        ),
        pytest.param(
            StatusNameTestcase(
                job_name="copr",
                chroot="fedora-40",
                event="stable",
                identifier="custom-build",
                package="special-package",
                template="packit:rpm-build:{package}:{identifier}:{chroot}",
                expected="packit:rpm-build:special-package:custom-build:fedora-40",
            ),
            id="custom template #2",
        ),
    ],
)
def test_get_check_cls(testcase):
    assert (
        BaseBuildJobHelper.get_check_cls(
            job_name=testcase.job_name,
            chroot=testcase.chroot,
            project_event_identifier=testcase.event,
            identifier=testcase.identifier,
            package=testcase.package,
            template=testcase.template,
        )
        == testcase.expected
    )


@pytest.mark.parametrize(
    "testcase",
    [
        pytest.param(
            StatusNameTestcase(
                chroot="centos-10",
                event="stable",
                identifier="release-build",
                expected="rpm-build:stable:centos-10:release-build",
            ),
            id="default template",
        ),
        pytest.param(
            StatusNameTestcase(
                chroot="fedora-40-x86_64",
                event="pr-42069",
                identifier="release-build",
                template="copr-build:pr:{chroot}:{identifier}",
                expected="copr-build:pr:fedora-40-x86_64:release-build",
            ),
            id="custom template",
        ),
    ],
)
def test_get_copr_build_check_cls(testcase):
    assert (
        CoprBuildJobHelper.get_build_check_cls(
            chroot=testcase.chroot,
            project_event_identifier=testcase.event,
            identifier=testcase.identifier,
            template=testcase.template,
        )
        == testcase.expected
    )


@pytest.mark.parametrize(
    "testcase",
    [
        pytest.param(
            StatusNameTestcase(
                chroot="centos-10",
                event="stable",
                identifier="release-test",
                expected="testing-farm:stable:centos-10:release-test",
            ),
            id="default template",
        ),
        pytest.param(
            StatusNameTestcase(
                chroot="fedora-40-x86_64",
                event="pr-42069",
                identifier="revdep-on-release-build",
                template="tests:pr:{chroot}:{identifier}",
                expected="tests:pr:fedora-40-x86_64:revdep-on-release-build",
            ),
            id="custom template",
        ),
    ],
)
def test_get_copr_test_check_cls(testcase):
    assert (
        TestingFarmJobHelper.get_test_check_cls(
            chroot=testcase.chroot,
            project_event_identifier=testcase.event,
            identifier=testcase.identifier,
            template=testcase.template,
        )
        == testcase.expected
    )
