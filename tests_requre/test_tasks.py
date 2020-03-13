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

import pytest
from copr.v3 import Client, BuildProxy
from flexmock import flexmock
from munch import Munch
from packit.config import PackageConfig

from packit_service.constants import PG_COPR_BUILD_STATUS_SUCCESS
from packit_service.models import (
    get_sa_session,
    CoprBuild,
    SRPMBuild,
)
from packit_service.service.events import CoprBuildEvent
from packit_service.worker.tasks import babysit_copr_build
from tests_requre.test_db import clean_db

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
    with get_sa_session() as session:
        session.query(CoprBuild).delete()
        srpm_build = SRPMBuild.create("asd\nqwe\n")
        yield CoprBuild.get_or_create(
            pr_id=752,
            build_id=str(BUILD_ID),
            commit_sha="687abc76d67d",
            repo_name="packit",
            namespace="packit-service",
            project_name="packit-service-packit-752",
            owner="packit",
            web_url=(
                "https://download.copr.fedorainfracloud.org/"
                "results/packit/packit-service-packit-752"
            ),
            target="fedora-rawhide-x86_64",
            status="pending",
            srpm_build=srpm_build,
        )
    clean_db()


def test_babysit_copr_build(packit_build_752):
    flexmock(Client).should_receive("create_from_config_file").and_return(Client(None))
    flexmock(CoprBuildEvent).should_receive("get_package_config").and_return(
        PackageConfig()
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
        }
    )
    flexmock(BuildProxy).should_receive("get").and_return(coprs_response)

    babysit_copr_build(BUILD_ID)
    assert packit_build_752.status == PG_COPR_BUILD_STATUS_SUCCESS
