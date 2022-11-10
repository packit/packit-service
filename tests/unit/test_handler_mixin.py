# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from flexmock import flexmock
from packit.utils.koji_helper import KojiHelper
from packit_service.constants import KojiBuildState
from packit_service.worker.handlers.mixin import (
    GetKojiBuildDataFromKojiServiceMixin,
    GetKojiBuildDataFromKojiBuildEventMixin,
)


def test_GetKojiBuildDataFromKojiServiceMixin():
    class Test(GetKojiBuildDataFromKojiServiceMixin):
        def __init__(self):
            self._project = flexmock(
                repo="a_repo", get_pr=lambda pr_id: flexmock(target_branch="a_branch")
            )
            self.data = flexmock(pr_id="123")

    flexmock(KojiHelper).should_receive("get_latest_build_in_tag").with_args(
        package="a_repo", tag="a_branch"
    ).and_return({"nvr": "1.0.0", "state": 1, "build_id": 123})
    mixin = Test()
    assert mixin.nvr == "1.0.0"
    assert mixin.build_id == 123
    assert mixin.state == KojiBuildState.complete
    assert mixin.dist_git_branch == "a_branch"


def test_GetKojiBuildDataFromKojiBuildEventMixin():
    class Test(GetKojiBuildDataFromKojiBuildEventMixin):
        def __init__(self):
            self.data = flexmock(pr_id="123")

        @property
        def koji_build_event(self):
            return flexmock(
                nvr="1.0.0",
                state=KojiBuildState.complete,
                build_id=123,
                git_ref="a_branch",
            )

    mixin = Test()
    assert mixin.nvr == "1.0.0"
    assert mixin.build_id == 123
    assert mixin.state == KojiBuildState.complete
    assert mixin.dist_git_branch == "a_branch"
