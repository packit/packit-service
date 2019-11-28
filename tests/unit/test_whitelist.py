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
import pytest
from fedora.client import AuthError, FedoraServiceError
from fedora.client.fas2 import AccountSystem
from flexmock import flexmock
from ogr.abstract import GitProject, GitService
from ogr.services.github import GithubProject, GithubService

from packit_service.service.events import (
    ReleaseEvent,
    PullRequestEvent,
    PullRequestCommentEvent,
    PullRequestAction,
    PullRequestCommentAction,
)
from packit_service.service.events import WhitelistStatus
from packit_service.worker.whitelist import Whitelist


class GracefulDict(dict):
    def __getitem__(self, item):
        try:
            return super().__getitem__(item)
        except KeyError:
            return None


@pytest.fixture()
def db():
    return GracefulDict(
        {
            "fero": {"status": WhitelistStatus.approved_manually.value},
            "lojzo": {"status": str(WhitelistStatus.approved_automatically)},
            "konipas": {"status": WhitelistStatus.waiting.value},
        }
    )


@pytest.fixture()
def whitelist(db):
    w = Whitelist()
    w.db = db
    return w


@pytest.mark.parametrize(
    "account_name,is_dict", (("lojzo", True), ("fero", True), ("krasomila", False))
)
def test_get_account(whitelist, account_name, is_dict):
    a = whitelist.get_account(account_name)
    assert isinstance(a, dict) == is_dict


@pytest.mark.parametrize(
    "account_name,is_approved",
    (("lojzo", True), ("fero", True), ("konipas", False), ("krasomila", False)),
)
def test_is_approved(whitelist, account_name, is_approved):
    assert whitelist.is_approved(account_name) == is_approved


@pytest.mark.parametrize(
    "account_name,person_object,raises,is_packager",
    [
        (
            "me",
            {
                "memberships": [
                    {"name": "unicorns"},
                    {"name": "packager"},
                    {"name": "builder"},
                ]
            },
            None,
            True,
        ),
        ("you", {"memberships": [{"name": "packagers"}]}, None, False),
        ("they", {}, None, False),
        ("parrot", {"some": "data"}, None, False),
        ("we", None, AuthError, False),
        ("bear", None, FedoraServiceError, False),
    ],
)
def test_is_packager(whitelist, account_name, person_object, raises, is_packager):
    fas = (
        flexmock(AccountSystem)
        .should_receive("person_by_username")
        .with_args(account_name)
        .once()
    )
    if person_object is not None:
        fas.and_return(person_object)
    if raises is not None:
        fas.and_raise(raises)

    assert whitelist._is_packager(account_name) == is_packager


def test_check_and_report_pr_comment_reject(whitelist):
    event = PullRequestCommentEvent(
        PullRequestAction["opened"], 0, "", "", "", "", "", "rakosnicek", ""
    )
    gp = GitProject("", GitService(), "")
    flexmock(gp).should_receive("pr_comment").with_args(
        0, "Namespace rakosnicek is not whitelisted!"
    ).once()
    assert not whitelist.check_and_report(event, gp)


def test_check_and_report_pr_comment_approve(whitelist):
    event = PullRequestCommentEvent(
        PullRequestAction["opened"], 0, "", "", "", "", "", "lojzo", ""
    )
    gp = GitProject("", GitService(), "")
    flexmock(gp).should_receive("pr_comment").with_args(
        0, "Account is not whitelisted!"
    ).never()
    assert whitelist.check_and_report(event, gp)


@pytest.mark.parametrize(
    "event,should_pass",
    (
        (ReleaseEvent("yoda", "", "", ""), True),
        (ReleaseEvent("vader", "", "", ""), False),
        (
            PullRequestEvent(
                PullRequestAction.opened,
                1,
                "death-star",
                "",
                "",
                "",
                "",
                "",
                "darth-criplicus",
            ),
            False,
        ),
        (
            PullRequestEvent(
                PullRequestAction.opened,
                1,
                "naboo",
                "",
                "",
                "",
                "",
                "",
                "darth-criplicus",
            ),
            True,
        ),
        (
            PullRequestEvent(
                PullRequestAction.opened, 1, "death-star", "", "", "", "", "", "anakin"
            ),
            True,
        ),
        (
            PullRequestEvent(
                PullRequestAction.opened, 1, "naboo", "", "", "", "", "", "anakin"
            ),
            True,
        ),
        (
            PullRequestCommentEvent(
                PullRequestCommentAction.created,
                1,
                "death-star",
                "",
                "",
                "",
                "",
                "darth-criplicus",
                "",
            ),
            False,
        ),
        (
            PullRequestCommentEvent(
                PullRequestCommentAction.created,
                1,
                "naboo",
                "",
                "",
                "",
                "",
                "darth-criplicus",
                "",
            ),
            False,
        ),
        (
            PullRequestCommentEvent(
                PullRequestCommentAction.created,
                1,
                "death-star",
                "",
                "",
                "",
                "",
                "anakin",
                "",
            ),
            True,
        ),
        (
            PullRequestCommentEvent(
                PullRequestCommentAction.created,
                1,
                "naboo",
                "",
                "",
                "",
                "",
                "anakin",
                "",
            ),
            True,
        ),
    ),
)
def test_check_and_report(event, should_pass):
    w = Whitelist()
    w.db = {
        "anakin": {"status": "approved_manually"},
        "yoda": {"status": "approved_manually"},
        "naboo": {"status": "approved_manually"},
    }

    flexmock(
        GithubProject,
        pr_comment=lambda *args, **kwargs: None,
        set_commit_status=lambda *args, **kwargs: None,
        issue_comment=lambda *args, **kwargs: None,
    )
    git_project = GithubProject("", GithubService(), "")
    assert w.check_and_report(event, git_project) == should_pass
