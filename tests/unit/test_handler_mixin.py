# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from typing import Optional

from flexmock import flexmock
from ogr.abstract import GitProject
from packit.utils.koji_helper import KojiHelper

from packit_service.config import ServiceConfig
from packit_service.constants import KojiBuildState
from packit_service.worker.handlers.mixin import (
    GetKojiBuildDataFromKojiBuildEventMixin,
    GetKojiBuildDataFromKojiServiceMixin,
    GetKojiBuildDataFromKojiServiceMultipleBranches,
)


def test_GetKojiBuildDataFromKojiServiceMixin():
    class Test(GetKojiBuildDataFromKojiServiceMixin):
        def __init__(self):
            self._project = flexmock(
                repo="a_repo",
                get_pr=lambda pr_id: flexmock(target_branch="a_branch"),
            )
            self.data = flexmock(pr_id="123")

    flexmock(KojiHelper).should_receive("get_latest_candidate_build").with_args(
        "a_repo",
        "a_branch",
    ).and_return({"nvr": "1.0.0", "state": 1, "build_id": 123, "task_id": 321})
    mixin = Test()
    data = []
    for koji_build_data in mixin:
        data.append(koji_build_data)
        assert koji_build_data.nvr == "1.0.0"
        assert koji_build_data.build_id == 123
        assert koji_build_data.state == KojiBuildState.complete
        assert koji_build_data.dist_git_branch == "a_branch"
        assert koji_build_data.task_id == 321
    assert mixin.num_of_branches == 1
    assert len(data) == 1


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
                task_id=321,
            )

    mixin = Test()
    data = []
    for koji_build_data in mixin:
        data.append(koji_build_data)
        assert koji_build_data.nvr == "1.0.0"
        assert koji_build_data.build_id == 123
        assert koji_build_data.state == KojiBuildState.complete
        assert koji_build_data.dist_git_branch == "a_branch"
        assert koji_build_data.task_id == 321
    assert mixin.num_of_branches == 1
    assert len(data) == 1


def test_GetKojiBuildDataFromKojiServiceMultipleBranches():
    class Test(GetKojiBuildDataFromKojiServiceMultipleBranches):
        @property
        def service_config(self) -> ServiceConfig:
            return flexmock(ServiceConfig)

        @property
        def project(self) -> Optional[GitProject]:
            return flexmock(repo="a repo")

        @property
        def project_url(self) -> str:
            return ""

        @property
        def branches(self):
            return ["f37", "f38"]

    flexmock(KojiHelper).should_receive("get_latest_candidate_build").with_args(
        "a repo",
        "f37",
    ).and_return({"nvr": "1.0.1", "state": 1, "build_id": 123, "task_id": 321})
    flexmock(KojiHelper).should_receive("get_latest_candidate_build").with_args(
        "a repo",
        "f38",
    ).and_return({"nvr": "1.0.2", "state": 1, "build_id": 1234, "task_id": 4321})

    mixin = Test()
    data = []
    for koji_build_data in mixin:
        data.append(koji_build_data)
        assert koji_build_data.nvr in ("1.0.1", "1.0.2")
        assert koji_build_data.build_id in (123, 1234)
        assert koji_build_data.state == KojiBuildState.complete
        assert koji_build_data.dist_git_branch in ("f37", "f38")
    assert mixin.num_of_branches == 2
    assert len(data) == 2
