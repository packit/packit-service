# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json
import unittest

from tests_openshift.openshift_integration.base import DATA_DIR, PackitServiceTestCase


def pr_event():
    return json.loads(
        (DATA_DIR / "webhooks" / "copr_build" / "pr_synchronize.json").read_text(),
    )


def pr_comment_event():
    return json.loads(
        (DATA_DIR / "webhooks" / "github" / "pr_comment_copr_build.json").read_text(),
    )


def pr_comment_event_not_collaborator():
    return json.loads(
        (DATA_DIR / "webhooks" / "copr_build" / "pr_comment_not_collaborator.json").read_text(),
    )


class Copr(PackitServiceTestCase):
    @unittest.skipIf(True, "troubles with allowlisting")
    def test_submit_copr_build_pr_event(self):
        result = self.steve.process_message(pr_event())
        self.assertTrue(result[0]["success"])

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
        self.assertTrue(result[0]["success"])

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
        self.assertEqual(result[0]["details"]["msg"], "Account is not allowlisted!")
