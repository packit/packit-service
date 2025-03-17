# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import pytest
from flexmock import flexmock
from packit.utils.koji_helper import KojiHelper

from packit_service.models import SidetagGroupModel
from packit_service.worker.helpers.sidetag import SidetagHelper

pytestmark = pytest.mark.usefixtures("mock_get_aliases")


@pytest.mark.parametrize(
    "branch, expected_branch",
    [
        ("f42", "f42"),
        ("main", "main"),
        ("rawhide", "main"),
    ],
)
def test_get_sidetag(branch, expected_branch):
    flexmock(
        SidetagGroupModel,
        get_or_create=lambda _: flexmock(
            get_sidetag_by_target=lambda t: flexmock(target=t, koji_name="koji_name")
        ),
    )
    flexmock(KojiHelper).should_receive("get_tag_info").and_return(flexmock())
    assert SidetagHelper.get_sidetag("group", branch).dist_git_branch == expected_branch
