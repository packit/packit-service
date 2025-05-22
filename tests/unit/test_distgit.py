# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
import json

import pytest
from fasjson_client import Client
from flexmock import flexmock
from ogr.parsing import parse_git_repo
from ogr.services.github import GithubService
from packit.api import PackitAPI
from packit.config.notifications import NotificationsConfig

from packit_service.events.event_data import EventData
from packit_service.worker.checker.run_condition import IsRunConditionSatisfied
from packit_service.worker.handlers.distgit import (
    AbstractSyncReleaseHandler,
    DownstreamKojiBuildHandler,
    DownstreamKojiScratchBuildHandler,
    ProposeDownstreamHandler,
    PullFromUpstreamHandler,
)
from packit_service.worker.reporting import utils


def test_create_one_issue_for_pr():
    flexmock(EventData).should_receive("from_event_dict").and_return(
        flexmock(
            event_type="a type",
            actor="an actor",
            event_id=1,
            project_url="a project url",
            tag_name="056",
        ),
    )
    project = (
        flexmock()
        .should_receive("create_issue")
        .once()
        .and_return(flexmock(id="1", url="an url"))
        .mock()
    )
    project.should_receive("get_issue_list").twice().and_return([]).and_return(
        [
            flexmock(
                title="[packit] Propose downstream failed for release 056",
                id=1,
                url="a url",
                get_comments=lambda *args, **kwargs: [],
            )
            .should_receive("comment")
            .once()
            .mock(),
        ],
    )
    flexmock(ProposeDownstreamHandler).should_receive("project").and_return(project)
    handler = ProposeDownstreamHandler(
        None,
        flexmock(notifications=NotificationsConfig()),
        {},
        flexmock(),
    )
    handler._report_errors_for_each_branch(
        {
            "f34": "Propose downstream failed for release 056",
            "f35": "Propose downstream failed for release 056",
        },
    )
    handler._report_errors_for_each_branch(
        {
            "f34": "Propose downstream failed for release 056",
            "f35": "Propose downstream failed for release 056",
        },
    )


PAGURE_PULL_REQUEST_COMMENT_PROCESSED = '{"created_at": 1658228337, "project_url": "https://src.fedoraproject.org/rpms/python-teamcity-messages", "_pr_id": 36, "fail_when_config_file_missing": true, "actor": null, "_package_config_searched": true, "git_ref": null, "identifier": "36", "comment": "/packit koji-build", "comment_id": 110401, "_commit_sha": "beaf90bcecc51968a46663f8d6f092bfdc92e682", "action": "created", "base_repo_namespace": "rpms", "base_repo_name": "python-teamcity-messages", "base_repo_owner": "mmassari", "base_ref": null, "target_repo": "python-teamcity-messages", "user_login": "mmassari", "event_type": "pagure.pr.Comment", "event_id": null, "task_accepted_time": null, "commit_sha": "beaf90bcecc51968a46663f8d6f092bfdc92e682"}'  # noqa


@pytest.mark.parametrize(
    "user_groups,data,check_passed",
    [
        pytest.param(
            flexmock(result=[{"groupname": "somegroup"}, {"groupname": "packager"}]),
            PAGURE_PULL_REQUEST_COMMENT_PROCESSED,
            True,
        ),
        pytest.param(
            flexmock(result=[{"groupname": "somegroup"}]),
            PAGURE_PULL_REQUEST_COMMENT_PROCESSED,
            False,
        ),
    ],
)
def test_retrigger_downstream_koji_build_pre_check(user_groups, data, check_passed):
    data_dict = json.loads(data)
    flexmock(PackitAPI).should_receive("init_kerberos_ticket").and_return(None)
    flexmock(Client).should_receive("__getattr__").with_args(
        "list_user_groups",
    ).and_return(lambda username: user_groups)

    flexmock(DownstreamKojiBuildHandler).should_receive("service_config").and_return(
        flexmock(),
    )
    if not check_passed:
        flexmock(utils).should_receive("create_issue_if_needed").once()

    flexmock(IsRunConditionSatisfied).should_receive("pre_check").and_return(True)

    result = DownstreamKojiBuildHandler.pre_check(
        None,
        flexmock(issue_repository=flexmock()),
        data_dict,
    )
    assert result == check_passed


def test_downstream_handler_init_order():
    class Test(AbstractSyncReleaseHandler):
        pass

    handler = Test(None, None, {"event_type": "unknown"}, None)
    assert handler.local_project


def test_upstream_local_project_is_used():
    class Test(AbstractSyncReleaseHandler):
        pass

    handler = Test(None, None, {"event_type": "unknown"}, None)
    assert handler.packit_api
    assert not handler.packit_api.downstream_local_project
    assert handler.packit_api.upstream_local_project


def test_pull_from_upstream_auth_method():
    class Test(PullFromUpstreamHandler):
        pass

    handler = Test(
        None,
        flexmock(upstream_project_url="url"),
        {"event_type": "unknown"},
        None,
    )
    flexmock(GithubService).should_receive("set_auth_method").once()
    flexmock(AbstractSyncReleaseHandler).should_receive("run").once()
    flexmock(GithubService).should_receive("reset_auth_method").once()
    handler.run()


@pytest.mark.parametrize(
    "src, git_ref, expected",
    (
        pytest.param(
            "https://src.fedoraproject.org/fork/packit/rpms/packit",
            "deadbeef",
            "git+https://src.fedoraproject.org/forks/packit/rpms/packit.git#deadbeef",
            id="source repo with namespace, forked; owner == repo name",
        ),
        pytest.param(
            "https://src.fedoraproject.org/fork/lbalhar/rpms/marshalparser",
            "f2f041328d629719c5ff31a08e800638d5df497f",
            "git+https://src.fedoraproject.org/forks/lbalhar/rpms/marshalparser.git#f2f041328d629719c5ff31a08e800638d5df497f",
            id="source repo with namespace, forked",
        ),
        pytest.param(
            "https://pagure.io/fedora-kiwi-descriptions",
            "914e61919e3a3a82c0ef7b6bd3a73f74e2de36e7",
            "git+https://pagure.io/fedora-kiwi-descriptions.git#914e61919e3a3a82c0ef7b6bd3a73f74e2de36e7",
            id="source repo without namespace, not forked",
        ),
        pytest.param(
            "https://src.fedoraproject.org/fork/lbalhar/marshalparser",
            "f2f041328d629719c5ff31a08e800638d5df497f",
            "git+https://src.fedoraproject.org/forks/lbalhar/marshalparser.git#f2f041328d629719c5ff31a08e800638d5df497f",
            id="source repo without namespace, forked",
        ),
    ),
)
def test__repo_url_with_git_ref(src, git_ref, expected):
    assert (
        DownstreamKojiScratchBuildHandler._repo_url_with_git_ref(parse_git_repo(src), git_ref)
        == expected
    )
