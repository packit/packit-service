# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
from flexmock import flexmock

from packit_service.worker.handlers.distgit import ProposeDownstreamHandler
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
            .once.mock()
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
