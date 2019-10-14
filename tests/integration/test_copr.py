# MIT License
#
# Copyright (c) 2018-2019 Red Hat, Inc.

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import json
import pytest

from packit_service.worker.jobs import SteveJobs
from tests.spellbook import DATA_DIR


@pytest.fixture()
def pr_event():
    return json.loads(
        (DATA_DIR / "webhooks" / "copr_build" / "pr_synchronize.json").read_text()
    )


@pytest.fixture()
def pr_comment_event():
    return json.loads(
        (DATA_DIR / "webhooks" / "copr_build" / "pr_comment.json").read_text()
    )


@pytest.fixture()
def pr_comment_event_not_collaborator():
    return json.loads(
        (
            DATA_DIR / "webhooks" / "copr_build" / "pr_comment_not_collaborator.json"
        ).read_text()
    )


def test_submit_copr_build_pr_event(pr_event):
    steve = SteveJobs()
    result = steve.process_message(pr_event)

    assert result
    assert "copr_build" in result["jobs"]
    assert result["jobs"]["copr_build"] == "success"


def test_submit_copr_build_pr(pr_comment_event):
    steve = SteveJobs()
    result = steve.process_message(pr_comment_event)

    assert result
    assert "copr_build" in result["jobs"]
    assert result["jobs"]["copr_build"] == "success"


def test_not_collaborator(pr_comment_event_not_collaborator):
    steve = SteveJobs()
    result = steve.process_message(pr_comment_event_not_collaborator)
    copr_build = result["jobs"]["copr_build"]
    assert not copr_build["success"]
    assert (
        copr_build["details"]["msg"]
        == "Only collaborators can trigger Packit-as-a-Service"
    )
