# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
import json
import pytest

from flexmock import flexmock
from fasjson_client import Client

from packit.api import PackitAPI
from packit_service.worker.handlers.distgit import (
    ProposeDownstreamHandler,
    DownstreamKojiBuildHandler,
)
from packit_service.worker.events.event import EventData


def test_create_one_issue_for_pr():
    flexmock(EventData).should_receive("from_event_dict").and_return(
        flexmock(
            event_type="a type",
            actor="an actor",
            trigger_id=1,
            project_url="a project url",
            tag_name="056",
        )
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
            )
            .should_receive("comment")
            .once()
            .mock()
        ]
    )
    flexmock(ProposeDownstreamHandler).should_receive("project").and_return(project)
    handler = ProposeDownstreamHandler(None, None, {})
    handler._report_errors_for_each_branch(
        {
            "f34": "Propose downstream failed for release 056",
            "f35": "Propose downstream failed for release 056",
        }
    )
    handler._report_errors_for_each_branch(
        {
            "f34": "Propose downstream failed for release 056",
            "f35": "Propose downstream failed for release 056",
        }
    )


PAGURE_PULL_REQUEST_COMMENT_PROCESSED = '{"created_at": 1658228337, "project_url": "https://src.fedoraproject.org/rpms/python-teamcity-messages", "_pr_id": 36, "fail_when_config_file_missing": true, "actor": null, "_package_config_searched": true, "git_ref": null, "identifier": "36", "comment": "/packit koji-build", "comment_id": 110401, "_commit_sha": "beaf90bcecc51968a46663f8d6f092bfdc92e682", "action": "created", "base_repo_namespace": "rpms", "base_repo_name": "python-teamcity-messages", "base_repo_owner": "mmassari", "base_ref": null, "target_repo": "python-teamcity-messages", "user_login": "mmassari", "event_type": "PullRequestCommentPagureEvent", "trigger_id": null, "task_accepted_time": null, "commit_sha": "beaf90bcecc51968a46663f8d6f092bfdc92e682"}'  # noqa


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
        "list_user_groups"
    ).and_return(lambda username: user_groups)

    result = DownstreamKojiBuildHandler.pre_check(None, None, data_dict)
    assert result == check_passed
