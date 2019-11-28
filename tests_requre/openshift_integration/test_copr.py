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
import flexmock


from packit_service.service.models import CoprBuild
from packit_service.worker.copr_build import CoprBuildHandler
from packit_service.worker.copr_db import CoprBuildDB
from packit_service.worker.whitelist import Whitelist
from tests_requre.openshift_integration.base import PackitServiceTestCase, DATA_DIR


def pr_event():
    return json.loads(
        (DATA_DIR / "webhooks" / "copr_build" / "pr_synchronize.json").read_text()
    )


def pr_comment_event():
    return json.loads(
        (DATA_DIR / "webhooks" / "copr_build" / "pr_comment.json").read_text()
    )


def pr_comment_event_not_collaborator():
    return json.loads(
        (
            DATA_DIR / "webhooks" / "copr_build" / "pr_comment_not_collaborator.json"
        ).read_text()
    )


class Copr(PackitServiceTestCase):
    def test_submit_copr_build_pr_event(self):

        # turn off interactions with redis
        flexmock(CoprBuildDB).should_receive("add_build")
        flexmock(CoprBuild).should_receive("create")
        flexmock(CoprBuildHandler).should_receive("copr_build_model").and_return(
            CoprBuild()
        )
        flexmock(CoprBuild).should_receive("save")
        flexmock(Whitelist, check_and_report=True)

        result = self.steve.process_message(pr_event())
        self.assertTrue(result)
        self.assertIn("copr_build", result["jobs"])
        self.assertTrue(result["jobs"]["copr_build"]["success"])

    def test_submit_copr_build_pr_comment(self):

        # turn off interactions with redis
        flexmock(CoprBuildDB).should_receive("add_build")
        flexmock(CoprBuild).should_receive("create")
        flexmock(CoprBuildHandler).should_receive("copr_build_model").and_return(
            CoprBuild()
        )
        flexmock(CoprBuild).should_receive("save")

        result = self.steve.process_message(pr_comment_event())
        self.assertTrue(result)
        self.assertIn("pull_request_action", result["jobs"])
        self.assertTrue(result["jobs"]["pull_request_action"]["success"])

    def test_not_collaborator(self):
        result = self.steve.process_message(pr_comment_event_not_collaborator())
        action = result["jobs"]["pull_request_action"]
        self.assertEqual(action["details"]["msg"], "Account is not whitelisted!")
