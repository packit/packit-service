import pytest

from flexmock import flexmock

from packit_service.worker.handlers.bodhi import CreateBodhiUpdateHandler


class TestBodhiHandler:
    @pytest.mark.parametrize(
        "event_type, has_write_access, result",
        [
            pytest.param(
                "PullRequestCommentPagureEvent",
                True,
                True,
            ),
            pytest.param(
                "PullRequestCommentPagureEvent",
                False,
                False,
            ),
            pytest.param(
                "TestingFarmHandler",
                True,
                True,
            ),
        ],
    )
    def test_write_access_to_dist_git_repo_is_not_needed_or_satisfied(
        self, event_type: str, has_write_access: bool, result: bool
    ):
        mock_data = flexmock(
            event_type=event_type,
            actor="happy-packit-user",
            pr_id=123,
        )
        mock_project = flexmock(
            has_write_access=lambda user: has_write_access,
            repo="playground-for-pencils",
        )
        mock_self = flexmock(data=mock_data, project=mock_project)

        assert (
            result
            == CreateBodhiUpdateHandler._write_access_to_dist_git_repo_is_not_needed_or_satisfied(
                mock_self
            )
        )
