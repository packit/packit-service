# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json
import pytest

from packit_service.worker.events import OpenScanHubTaskFinishEvent
from packit_service.worker.parser import Parser
from tests.spellbook import DATA_DIR


@pytest.fixture()
def openscanhub_task_finish_event():
    with open(DATA_DIR / "fedmsg" / "open_scan_hub_task_finish.json") as outfile:
        return json.load(outfile)


def test_parse_openscanhub_task_finish(openscanhub_task_finish_event):
    event_object = Parser.parse_event(openscanhub_task_finish_event)

    assert isinstance(event_object, OpenScanHubTaskFinishEvent)
    assert event_object.task_id == 15649
    assert (
        event_object.issues_added_url
        == "http://openscanhub.fedoraproject.org/task/15649/log/added.js?format=raw"
    )
    assert (
        event_object.issues_fixed_url
        == "http://openscanhub.fedoraproject.org/task/15649/log/fixed.js?format=raw"
    )
    assert event_object.scan_results_url == (
        "http://openscanhub.fedoraproject.org/task/15649/log/gvisor-tap-vsock"
        "-0.7.5-1.20241007054606793155.pr405.23.g829aafd6/scan-results.js?format=raw"
    )
