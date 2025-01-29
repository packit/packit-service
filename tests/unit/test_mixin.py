# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from typing import Optional

import pytest
from flexmock import flexmock
from ogr.abstract import GitProject
from packit.vm_image_build import ImageBuilder

from packit_service.config import ServiceConfig
from packit_service.events.abstract.comment import Issue as AbstractIssueCommentEvent
from packit_service.events.event_data import EventData
from packit_service.worker.handlers.mixin import (
    GetCoprBuildJobHelperMixin,
    GetVMImageBuilderMixin,
    GetVMImageDataMixin,
)
from packit_service.worker.mixin import (
    ConfigFromDistGitUrlMixin,
    ConfigFromEventMixin,
    GetBranchesFromIssueMixin,
)


def test_GetVMImageBuilderMixin():
    class Test(ConfigFromEventMixin, GetVMImageBuilderMixin): ...

    flexmock(ImageBuilder).should_receive("_get_access_token").and_return("token")
    mixin = Test()
    assert isinstance(mixin.vm_image_builder, ImageBuilder)


def test_GetVMImageDataMixin(fake_package_config_job_config_project_db_trigger):
    class Test(ConfigFromEventMixin, GetCoprBuildJobHelperMixin, GetVMImageDataMixin):
        def __init__(self) -> None:
            super().__init__()
            (
                package_config,
                job_config,
                project,
                _,
            ) = fake_package_config_job_config_project_db_trigger
            self.package_config = package_config
            self.job_config = job_config
            self._project = project

    mixin = Test()
    assert mixin.chroot == "fedora-36-x86_64"
    assert mixin.identifier == ""
    assert mixin.owner == "mmassari"
    assert mixin.project_name == "knx-stack"
    assert mixin.image_distribution == "fedora-36"
    assert mixin.image_request == {
        "architecture": "x86_64",
        "image_type": "aws",
        "upload_request": {"type": "aws", "options": {}},
    }
    assert mixin.image_customizations == {"packages": ["python-knx-stack"]}


@pytest.mark.parametrize(
    "desc,comments,branches",
    [
        (
            """
        | dist-git branch | error |
        | --------------- | ----- |
        | `f37` | `` |
        | `f38` | `` |
            """,
            [],
            ["f37", "f38"],
        ),
        (
            """
| dist-git branch | error |
| --------------- | ----- |
| `f37` | `` |
| `f38` | `` |
            """,
            [],
            ["f37", "f38"],
        ),
        (
            """
        | dist-git branch | error |
        | --------------- | ----- |
        | `f37` | `` |
            """,
            [
                """
    | dist-git branch | error |
    | --------------- | ----- |
    | `f38` | `` |
                """,
                "random comment",
            ],
            ["f37", "f38"],
        ),
        (
            "",
            [],
            [],
        ),
    ],
)
def test_GetBranchesFromIssueMixin(desc, comments, branches):
    class Test(GetBranchesFromIssueMixin):
        def __init__(self) -> None:
            project = (
                flexmock()
                .should_receive("get_issue")
                .and_return(
                    flexmock(
                        description=desc,
                        get_comments=lambda: [flexmock(body=c) for c in comments],
                    ),
                )
                .mock()
            )
            self.data = flexmock(project=project, issue_id=1)

        @property
        def service_config(self) -> ServiceConfig:
            return flexmock(ServiceConfig)

        @property
        def project(self) -> Optional[GitProject]:
            return None

        @property
        def project_url(self) -> str:
            return ""

    mixin = Test()
    assert set(mixin.branches) == set(branches)


def test_ConfigFromDistGitUrlMixin():
    class Test(ConfigFromDistGitUrlMixin):
        def __init__(self) -> None:
            event = AbstractIssueCommentEvent(
                issue_id=1,
                repo_namespace="a namespace",
                repo_name="a repo name",
                project_url="upstream project url",
                comment="probably an issue opened by the propose downstream",
                comment_id=1,
            )
            event.dist_git_project_url = "url to distgit"
            self.data = EventData.from_event_dict(
                flexmock(event, tag_name="a tag", commit_sha="aebdf").get_dict(),
            )

    mixin = Test()
    assert mixin.project_url == "url to distgit"
