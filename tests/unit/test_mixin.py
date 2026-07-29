# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import time
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
    _PACKAGER_CACHE,
    ConfigFromDistGitUrlMixin,
    ConfigFromEventMixin,
    GetBranchesFromIssueMixin,
    PackitAPIWithDownstreamMixin,
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


class TestIsPackager:
    """Tests for PackitAPIWithDownstreamMixin.is_packager()

    Verifies retry logic for transient FASJSON errors and
    TTL caching of successful lookups.
    """

    @pytest.fixture(autouse=True)
    def _clear_packager_cache(self):
        """Ensure every test starts with a clean cache."""
        _PACKAGER_CACHE.clear()
        yield
        _PACKAGER_CACHE.clear()

    @staticmethod
    def _make_mixin():
        """Create a minimal PackitAPIWithDownstreamMixin instance with
        mocked dependencies so we can call is_packager() directly."""
        from fasjson_client import Client as FasjsonClient

        class _ConcreteDownstreamMixin(PackitAPIWithDownstreamMixin):
            """Concrete subclass that stubs the abstract properties
            inherited from Config so the mixin can be instantiated."""

            @property
            def project(self):
                return None

            @property
            def service_config(self):
                return None

            @property
            def project_url(self):
                return ""

        def init(*args):
            pass

        FasjsonClient.__init__ = init

        flexmock(_ConcreteDownstreamMixin).should_receive(
            "packit_api",
        ).and_return(
            flexmock(init_kerberos_ticket=lambda: None),
        )

        return _ConcreteDownstreamMixin()

    def test_successful_lookup_is_cached(self):
        """A successful FASJSON response is cached; subsequent calls
        do not hit the API again."""
        from fasjson_client import Client as FasjsonClient

        flexmock(time).should_receive("sleep").never()
        mixin = self._make_mixin()

        # First call returns packager groups
        mock_client = flexmock()
        mock_client.should_receive("list_user_groups").with_args(
            username="testuser",
        ).and_return(
            flexmock(result=[{"groupname": "packager"}]),
        ).once()  # Must be called exactly once

        flexmock(FasjsonClient).new_instances(mock_client)

        assert mixin.is_packager("testuser") is True

        # Second call should use cache (no new client created)
        assert mixin.is_packager("testuser") is True

    def test_retry_on_transient_error_then_success(self):
        """Transient 5xx errors are retried; a subsequent success returns
        True and caches the result."""
        from fasjson_client import Client as FasjsonClient
        from fasjson_client.errors import APIError

        flexmock(time).should_receive("sleep").and_return(None)
        mixin = self._make_mixin()

        mock_client = flexmock()
        mock_client.should_receive("list_user_groups").with_args(
            username="retryuser",
        ).and_raise(
            APIError,
            "Service Unavailable",
            502,
        ).and_return(
            flexmock(result=[{"groupname": "packager"}]),
        ).twice()

        flexmock(FasjsonClient).new_instances(mock_client)

        assert mixin.is_packager("retryuser") is True
        # Verify the result was cached
        assert "retryuser" in _PACKAGER_CACHE
        assert _PACKAGER_CACHE["retryuser"] is True

    def test_cache_hit_during_outage(self):
        """A previously cached packager remains authorized even when
        FASJSON is down."""
        from fasjson_client import Client as FasjsonClient

        flexmock(time).should_receive("sleep").never()
        mixin = self._make_mixin()

        # Populate cache via a successful call
        mock_client = flexmock()
        mock_client.should_receive("list_user_groups").with_args(
            username="cacheduser",
        ).and_return(
            flexmock(result=[{"groupname": "packager"}]),
        ).once()

        flexmock(FasjsonClient).new_instances(mock_client)
        assert mixin.is_packager("cacheduser") is True

        # Now the API is "down" — but we should still get True from cache
        # (no new API call should be made)
        assert mixin.is_packager("cacheduser") is True

    def test_non_transient_error_not_retried(self):
        """A 404 (user not found) error is not retried and returns False."""
        from fasjson_client import Client as FasjsonClient
        from fasjson_client.errors import APIError

        flexmock(time).should_receive("sleep").never()
        mixin = self._make_mixin()

        mock_client = flexmock()
        mock_client.should_receive("list_user_groups").with_args(
            username="unknownuser",
        ).and_raise(
            APIError,
            "User not found",
            404,
        ).once()  # Must be called exactly once — no retries

        flexmock(FasjsonClient).new_instances(mock_client)

        assert mixin.is_packager("unknownuser") is False

    def test_exhausted_retries_returns_false(self):
        """When all retries are exhausted on transient errors,
        is_packager() returns False."""
        from fasjson_client import Client as FasjsonClient
        from fasjson_client.errors import APIError

        flexmock(time).should_receive("sleep").and_return(None)
        mixin = self._make_mixin()

        mock_client = flexmock()
        mock_client.should_receive("list_user_groups").with_args(
            username="unluckyuser",
        ).and_raise(
            APIError,
            "Internal Server Error",
            500,
        ).times(3)  # _FASJSON_RETRY_COUNT = 3

        flexmock(FasjsonClient).new_instances(mock_client)

        assert mixin.is_packager("unluckyuser") is False

    def test_non_packager_user_returns_false(self):
        """A user who is not in the 'packager' group returns False
        and is NOT cached (only positive results are cached)."""
        from fasjson_client import Client as FasjsonClient

        flexmock(time).should_receive("sleep").never()
        mixin = self._make_mixin()

        mock_client = flexmock()
        mock_client.should_receive("list_user_groups").with_args(
            username="nonpackager",
        ).and_return(
            flexmock(result=[{"groupname": "users"}]),
        ).once()

        flexmock(FasjsonClient).new_instances(mock_client)

        assert mixin.is_packager("nonpackager") is False
        # Only positive results are cached to avoid stale-negative denial
        assert "nonpackager" not in _PACKAGER_CACHE
