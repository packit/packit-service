# MIT License
#
# Copyright (c) 2018-2019 Red Hat, Inc.

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
import json
import flexmock

import pytest

from packit_service.worker.handler import BuildStatusReporter
from packit_service.worker.jobs import SteveJobs
from tests.spellbook import DATA_DIR


@pytest.fixture()
def copr_build_start():
    return json.loads((DATA_DIR / "fedmsg" / "copr_build_start").read_text())


@pytest.fixture()
def copr_build_end():
    return json.loads((DATA_DIR / "fedmsg" / "copr_build_end").read_text())


def test_copr_build_end(copr_build_end):
    steve = SteveJobs()
    flexmock(SteveJobs, _is_private=False)
    results = steve.process_message(copr_build_end)

    url = (
        f"https://copr.fedorainfracloud.org/coprs/packit/"
        f"packit-service-hello-world-24-stg/build/1044215/"
    )

    flexmock(BuildStatusReporter).should_receive("report").with_args(
        "success", "RPMs were built successfully.", url, "packit-stg/rpm-build"
    ).once()

    assert results.get("jobs", {})


def test_copr_build_start(copr_build_start):
    steve = SteveJobs()
    flexmock(SteveJobs, _is_private=False)
    results = steve.process_message(copr_build_start)

    url = (
        f"https://copr.fedorainfracloud.org/coprs/packit/"
        f"packit-service-hello-world-24-stg/build/1044215/"
    )

    flexmock(BuildStatusReporter).should_receive("report").with_args(
        "pending", "RPM build has started...", url, "packit-stg/rpm-build"
    ).once()

    assert results.get("jobs", {})
