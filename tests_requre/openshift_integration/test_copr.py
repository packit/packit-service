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
import unittest

from tests_requre.openshift_integration.base import PackitServiceTestCase, DATA_DIR


def pr_event():
    return json.loads(
        (DATA_DIR / "webhooks" / "copr_build" / "pr_synchronize.json").read_text()
    )


def pr_comment_event():
    return json.loads(
        (DATA_DIR / "webhooks" / "github_pr_comment_copr_build.json").read_text()
    )


def pr_comment_event_not_collaborator():
    return json.loads(
        (
            DATA_DIR / "webhooks" / "copr_build" / "pr_comment_not_collaborator.json"
        ).read_text()
    )


class Copr(PackitServiceTestCase):
    @unittest.skipIf(True, "troubles with whitelisting")
    def test_submit_copr_build_pr_event(self):
        result = self.steve.process_message(pr_event())
        self.assertTrue(result)
        self.assertIn("copr_build", result["jobs"])
        self.assertTrue(result["jobs"]["copr_build"]["success"])

    @unittest.skipIf(True, "We can't obtain installation ID, I give up.")
    def test_submit_copr_build_pr_comment(self):
        # flexmock(GithubIntegration).should_receive("get_installation").and_return(
        #     Installation(requester=None, headers={}, attributes={}, completed=True)
        # )
        # flexmock(GithubIntegration).should_receive("get_access_token").and_return(
        #     InstallationAuthorization(
        #         requester=None, headers={}, attributes={}, completed=True
        #     )
        # )
        result = self.steve.process_message(pr_comment_event())
        self.assertTrue(result)
        self.assertIn("pull_request_action", result["jobs"])
        self.assertTrue(result["jobs"]["pull_request_action"]["success"])

    @unittest.skipIf(True, "We can't obtain installation ID, I give up.")
    def test_not_collaborator(self):
        # flexmock(GithubIntegration).should_receive("get_installation").and_return(
        #     Installation(requester=None, headers={}, attributes={}, completed=True)
        # )
        # flexmock(GithubIntegration).should_receive("get_access_token").and_return(
        #     InstallationAuthorization(
        #         requester=None, headers={}, attributes={}, completed=True
        #     )
        # )
        result = self.steve.process_message(pr_comment_event_not_collaborator())
        action = result["jobs"]["pull_request_action"]
        self.assertEqual(action["details"]["msg"], "Account is not whitelisted!")
