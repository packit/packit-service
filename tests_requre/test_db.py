# MIT License
#
# Copyright (c) 2018-2020 Red Hat, Inc.

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
"""
These tests require a psql database with a schema:
```
export POSTGRESQL_USER=packit
export POSTGRESQL_PASSWORD=secret-password
export POSTGRESQL_DATABASE=packit
export POSTGRESQL_SERVICE_HOST=0.0.0.0
$ docker-compose -d postgres
$ alembic upgrade head
```
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy.exc import ProgrammingError

from packit_service.models import (
    CoprBuild,
    get_sa_session,
    SRPMBuild,
    PullRequest,
    GitProject,
    Whitelist,
    TaskResultModel,
)

TARGET = "fedora-42-x86_64"


def clean_db():
    with get_sa_session() as session:
        session.query(CoprBuild).delete()
        session.query(PullRequest).delete()
        session.query(GitProject).delete()
        session.query(Whitelist).delete()
        session.query(TaskResultModel).delete()


# Create a single build
@pytest.fixture()
def a_copr_build():
    with get_sa_session() as session:
        session.query(CoprBuild).delete()
        srpm_build = SRPMBuild.create("asd\nqwe\n")
        yield CoprBuild.get_or_create(
            pr_id=1,
            build_id="123456",
            commit_sha="687abc76d67d",
            repo_name="lithium",
            namespace="nirvana",
            project_name="SomeUser-hello-world-9",
            owner="packit",
            web_url="https://copr.something.somewhere/123456",
            target=TARGET,
            status="pending",
            srpm_build=srpm_build,
        )
    clean_db()


# Create multiple builds
# Used for testing querys
@pytest.fixture()
def multiple_copr_builds():
    with get_sa_session() as session:
        session.query(CoprBuild).delete()
        srpm_build = SRPMBuild.create("asd\nqwe\n")
        yield [
            CoprBuild.get_or_create(
                pr_id=1,
                build_id="123456",
                commit_sha="687abc76d67d",
                repo_name="lithium",
                namespace="nirvana",
                project_name="SomeUser-hello-world-9",
                owner="packit",
                web_url="https://copr.something.somewhere/123456",
                target="fedora-42-x86_64",
                status="pending",
                srpm_build=srpm_build,
            ),
            # Same build_id but different chroot
            CoprBuild.get_or_create(
                pr_id=1,
                build_id="123456",
                commit_sha="687abc76d67d",
                repo_name="lithium",
                namespace="nirvana",
                project_name="SomeUser-hello-world-9",
                owner="packit",
                web_url="https://copr.something.somewhere/123456",
                target="fedora-43-x86_64",
                status="pending",
                srpm_build=srpm_build,
            ),
            # Completely different build
            CoprBuild.get_or_create(
                pr_id=4,
                build_id="987654",
                commit_sha="987def76d67e",
                repo_name="cockpit-project",
                namespace="cockpit",
                project_name="SomeUser-random-text-7",
                owner="cockpit-project",
                web_url="https://copr.something.somewhere/987654",
                target="fedora-43-x86_64",
                status="pending",
                srpm_build=srpm_build,
            ),
        ]

    clean_db()


# Create multiple whitelist entries
@pytest.fixture()
def multiple_whitelist_entries():
    with get_sa_session() as session:
        session.query(Whitelist).delete()
        yield [
            Whitelist.add_account(account_name="Rayquaza", status="approved_manually"),
            Whitelist.add_account(account_name="Deoxys", status="approved_manually"),
            # Not a typo, account_name repeated intentionally to check behaviour
            Whitelist.add_account(account_name="Deoxys", status="waiting"),
            Whitelist.add_account(account_name="Solgaleo", status="waiting"),
            Whitelist.add_account(account_name="Zacian", status="approved_manually"),
        ]
    clean_db()


# Create new whitelist entry
@pytest.fixture()
def new_whitelist_entry():
    with get_sa_session() as session:
        session.query(Whitelist).delete()
        yield Whitelist.add_account(account_name="Rayquaza", status="approved_manually")
    clean_db()


@pytest.fixture()
def task_results():
    return [
        {
            "jobs": {
                "copr_build": {
                    "success": True,
                    "details": {
                        "msg": "Only users with write or admin permissions to the "
                        "repository can trigger Packit-as-a-Service"
                    },
                }
            },
            "event": {
                "trigger": "pull_request",
                "created_at": "2020-03-26T07:39:18",
                "project_url": "https://github.com/nmstate/nmstate",
                "git_ref": None,
                "identifier": "934",
                "action": "synchronize",
                "pr_id": 934,
                "base_repo_namespace": "nmstate",
                "base_repo_name": "nmstate",
                "base_ref": "f483003f13f0fee585f5cc0b970f4cd21eca7c9d",
                "target_repo": "nmstate/nmstate",
                "commit_sha": "f483003f13f0fee585f5cc0b970f4cd21eca7c9d",
                "github_login": "adwait-thattey",
            },
        },
        {
            "jobs": {"tests": {"success": True, "details": {}}},
            "event": {
                "trigger": "testing_farm_results",
                "created_at": "2020-03-25T16:56:39",
                "project_url": "https://github.com/psss/tmt.git",
                "git_ref": "4c584245ef53062eb15afc7f8daa6433da0a95a7",
                "identifier": "4c584245ef53062eb15afc7f8daa6433da0a95a7",
                "pipeline_id": "c9a88c3d-801f-44e4-a206-2e1b6081446a",
                "result": "passed",
                "environment": "Fedora-Cloud-Base-30-20200325.0.x86_64.qcow2",
                "message": "All tests passed",
                "log_url": "https://console-testing-farm.apps.ci.centos.org/pipeline"
                "/c9a88c3d-801f-44e4-a206-2e1b6081446a",
                "copr_repo_name": "packit/psss-tmt-178",
                "copr_chroot": "fedora-30-x86_64",
                "tests": [
                    {"name": "/plans/smoke", "result": "passed", "log_url": None},
                    {"name": "/plans/basic", "result": "passed", "log_url": None},
                ],
                "repo_name": "tmt",
                "repo_namespace": "psss",
                "commit_sha": "4c584245ef53062eb15afc7f8daa6433da0a95a7",
            },
        },
    ]


@pytest.fixture()
def multiple_task_results_entries(task_results):
    with get_sa_session() as session:
        session.query(TaskResultModel).delete()
        yield [
            TaskResultModel.add_task_result(
                task_id="ab1", task_result_dict=task_results[0]
            ),
            TaskResultModel.add_task_result(
                task_id="ab2", task_result_dict=task_results[1]
            ),
        ]
    clean_db()


def test_create_copr_build(a_copr_build):
    assert a_copr_build.pr_id == a_copr_build.pr.id
    assert a_copr_build.pr.pr_id == 1
    assert a_copr_build.build_id == "123456"
    assert a_copr_build.commit_sha == "687abc76d67d"
    assert a_copr_build.pr.project.namespace == "nirvana"
    assert a_copr_build.pr.project.repo_name == "lithium"
    assert a_copr_build.project_name == "SomeUser-hello-world-9"
    assert a_copr_build.owner == "packit"
    assert a_copr_build.web_url == "https://copr.something.somewhere/123456"
    assert a_copr_build.srpm_build.logs == "asd\nqwe\n"
    assert a_copr_build.target == TARGET
    assert a_copr_build.status == "pending"
    # Since datetime.utcnow() will return different results in every time its called,
    # we will check if a_copr_build has build_submitted_time value thats within the past hour
    time_last_hour = datetime.utcnow() - timedelta(hours=1)
    assert a_copr_build.build_submitted_time > time_last_hour


def test_get_copr_build(a_copr_build):
    assert a_copr_build.id
    b = CoprBuild.get_by_build_id(a_copr_build.build_id, TARGET)
    assert b.id == a_copr_build.id
    # let's make sure passing int works as well
    b = CoprBuild.get_by_build_id(int(a_copr_build.build_id), TARGET)
    assert b.id == a_copr_build.id
    b2 = CoprBuild.get_by_id(b.id)
    assert b2.id == a_copr_build.id


def test_copr_build_set_status(a_copr_build):
    assert a_copr_build.status == "pending"
    a_copr_build.set_status("awesome")
    assert a_copr_build.status == "awesome"
    b = CoprBuild.get_by_build_id(a_copr_build.build_id, TARGET)
    assert b.status == "awesome"


def test_copr_build_set_build_logs_url(a_copr_build):
    url = "https://copr.fp.o/logs/12456/build.log"
    a_copr_build.set_build_logs_url(url)
    assert a_copr_build.build_logs_url == url
    b = CoprBuild.get_by_build_id(a_copr_build.build_id, TARGET)
    assert b.build_logs_url == url


def test_get_or_create_pr():
    clean_db()
    with get_sa_session() as session:
        try:
            expected_pr = PullRequest.get_or_create(
                pr_id=42, namespace="clapton", repo_name="layla"
            )
            actual_pr = PullRequest.get_or_create(
                pr_id=42, namespace="clapton", repo_name="layla"
            )

            assert session.query(PullRequest).count() == 1
            assert expected_pr.project_id == actual_pr.project_id

            expected_pr = PullRequest.get_or_create(
                pr_id=42, namespace="clapton", repo_name="cocaine"
            )
            actual_pr = PullRequest.get_or_create(
                pr_id=42, namespace="clapton", repo_name="cocaine"
            )

            assert session.query(PullRequest).count() == 2
            assert expected_pr.project_id == actual_pr.project_id
        finally:
            clean_db()


def test_errors_while_doing_db():
    with get_sa_session() as session:
        try:
            try:
                PullRequest.get_or_create(pr_id="nope", namespace="", repo_name=False)
            except ProgrammingError:
                pass
            assert len(session.query(PullRequest).all()) == 0
            PullRequest.get_or_create(pr_id=111, namespace="asd", repo_name="qwe")
            assert len(session.query(PullRequest).all()) == 1
        finally:
            clean_db()


# return all builds in table
def test_get_all(multiple_copr_builds):
    builds_list = CoprBuild.get_all()
    assert len(builds_list) == 3
    # we just wanna check if result is iterable
    # order doesn't matter, so all of them are set to pending in supplied data
    assert builds_list[1].status == "pending"


# return all builds with given build_id
def test_get_all_build_id(multiple_copr_builds):
    builds_list = CoprBuild.get_all_by_build_id(str(123456))
    assert len(list(builds_list)) == 2
    # both should have the same project_name
    assert builds_list[1].project_name == builds_list[0].project_name
    assert builds_list[1].project_name == "SomeUser-hello-world-9"


# returns the first build with given build id and target
def test_get_by_build_id(multiple_copr_builds):
    # these are not iterable and thus should be accessible directly
    build_a = CoprBuild.get_by_build_id(str(123456), "fedora-42-x86_64")
    assert build_a.project_name == "SomeUser-hello-world-9"
    assert build_a.target == "fedora-42-x86_64"
    build_b = CoprBuild.get_by_build_id(str(123456), "fedora-43-x86_64")
    assert build_b.project_name == "SomeUser-hello-world-9"
    assert build_b.target == "fedora-43-x86_64"
    build_c = CoprBuild.get_by_build_id(str(987654), "fedora-43-x86_64")
    assert build_c.project_name == "SomeUser-random-text-7"


def test_add_account(new_whitelist_entry):
    assert new_whitelist_entry.status == "approved_manually"
    assert new_whitelist_entry.account_name == "Rayquaza"


def test_get_account(multiple_whitelist_entries):
    assert Whitelist.get_account("Rayquaza").status == "approved_manually"
    assert Whitelist.get_account("Rayquaza").account_name == "Rayquaza"
    assert Whitelist.get_account("Deoxys").status == "waiting"
    assert Whitelist.get_account("Deoxys").account_name == "Deoxys"
    assert Whitelist.get_account("Solgaleo").status == "waiting"
    assert Whitelist.get_account("Solgaleo").account_name == "Solgaleo"


def test_get_accounts_by_status(multiple_whitelist_entries):
    a = Whitelist.get_accounts_by_status("waiting")
    assert len(list(a)) == 2
    b = Whitelist.get_accounts_by_status("approved_manually")
    assert len(list(b)) == 2


def test_remove_account(multiple_whitelist_entries):
    assert Whitelist.get_account("Rayquaza").account_name == "Rayquaza"
    Whitelist.remove_account("Rayquaza")
    assert Whitelist.get_account("Rayquaza") is None


def test_get_task_results(multiple_task_results_entries):
    results = TaskResultModel.get_all()
    assert len(results) == 2
    assert results[0].task_id == "ab1"
    assert results[1].task_id == "ab2"


def test_get_task_result_by_id(multiple_task_results_entries, task_results):
    assert TaskResultModel.get_by_id("ab1").jobs == task_results[0].get("jobs")
    assert TaskResultModel.get_by_id("ab1").event == task_results[0].get("event")
    assert TaskResultModel.get_by_id("ab2").jobs == task_results[1].get("jobs")
    assert TaskResultModel.get_by_id("ab2").event == task_results[1].get("event")
