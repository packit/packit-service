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

import pytest

from packit_service.models import (
    CoprBuildModel,
    get_sa_session,
    SRPMBuildModel,
    PullRequestModel,
    GitProjectModel,
    WhitelistModel,
    GitBranchModel,
    ProjectReleaseModel,
    IssueModel,
    JobTriggerModel,
    JobTriggerModelType,
    KojiBuildModel,
    TFTTestRunModel,
    TestingFarmResult,
    TaskResultModel,
    InstallationModel,
)
from packit_service.service.events import InstallationEvent

TARGET = "fedora-42-x86_64"


def clean_db():
    with get_sa_session() as session:
        session.query(CoprBuildModel).delete()
        session.query(KojiBuildModel).delete()
        session.query(SRPMBuildModel).delete()
        session.query(TFTTestRunModel).delete()
        session.query(TaskResultModel).delete()

        session.query(WhitelistModel).delete()
        session.query(InstallationModel).delete()

        session.query(JobTriggerModel).delete()

        session.query(GitBranchModel).delete()
        session.query(ProjectReleaseModel).delete()
        session.query(PullRequestModel).delete()
        session.query(IssueModel).delete()

        session.query(GitProjectModel).delete()


@pytest.fixture()
def clean_before_and_after():
    clean_db()
    yield
    clean_db()


@pytest.fixture()
def pr_model():
    yield PullRequestModel.get_or_create(
        pr_id=342, namespace="the-namespace", repo_name="the-repo-name"
    )


@pytest.fixture()
def different_pr_model():
    yield PullRequestModel.get_or_create(
        pr_id=4, namespace="the-namespace", repo_name="the-repo-name"
    )


@pytest.fixture()
def release_model():
    yield ProjectReleaseModel.get_or_create(
        tag_name="v1.0.2",
        commit_hash="aksjdaksjdla",
        namespace="the-namespace",
        repo_name="the-repo-name",
    )


@pytest.fixture()
def branch_model():
    yield GitBranchModel.get_or_create(
        branch_name="build-branch",
        namespace="the-namespace",
        repo_name="the-repo-name",
    )


@pytest.fixture()
def pr_trigger_model(pr_model):
    yield JobTriggerModel.get_or_create(
        type=JobTriggerModelType.pull_request, trigger_id=pr_model.id
    )


@pytest.fixture()
def different_pr_trigger_model(different_pr_model):
    yield JobTriggerModel.get_or_create(
        type=JobTriggerModelType.pull_request, trigger_id=different_pr_model.id
    )


@pytest.fixture()
def release_trigger_model(release_model):
    yield JobTriggerModel.get_or_create(
        type=JobTriggerModelType.release, trigger_id=release_model.id
    )


@pytest.fixture()
def branch_trigger_model(branch_model):
    yield JobTriggerModel.get_or_create(
        type=JobTriggerModelType.branch_push, trigger_id=branch_model.id
    )


@pytest.fixture()
def a_copr_build_for_pr(pr_model):
    srpm_build = SRPMBuildModel.create("asd\nqwe\n")
    yield CoprBuildModel.get_or_create(
        build_id="123456",
        commit_sha="687abc76d67d",
        project_name="SomeUser-hello-world-9",
        owner="packit",
        web_url="https://copr.something.somewhere/123456",
        target=TARGET,
        status="pending",
        srpm_build=srpm_build,
        trigger_model=pr_model,
    )


@pytest.fixture()
def a_copr_build_for_branch_push(branch_model):
    srpm_build = SRPMBuildModel.create("asd\nqwe\n")
    yield CoprBuildModel.get_or_create(
        build_id="123456",
        commit_sha="687abc76d67d",
        project_name="SomeUser-hello-world-9",
        owner="packit",
        web_url="https://copr.something.somewhere/123456",
        target=TARGET,
        status="pending",
        srpm_build=srpm_build,
        trigger_model=branch_model,
    )


@pytest.fixture()
def a_copr_build_for_release(release_model):
    srpm_build = SRPMBuildModel.create("asd\nqwe\n")
    yield CoprBuildModel.get_or_create(
        build_id="123456",
        commit_sha="687abc76d67d",
        project_name="SomeUser-hello-world-9",
        owner="packit",
        web_url="https://copr.something.somewhere/123456",
        target=TARGET,
        status="pending",
        srpm_build=srpm_build,
        trigger_model=release_model,
    )


# Create multiple builds
# Used for testing queries
@pytest.fixture()
def multiple_copr_builds(pr_model, different_pr_model):
    srpm_build = SRPMBuildModel.create("asd\nqwe\n")
    yield [
        CoprBuildModel.get_or_create(
            build_id="123456",
            commit_sha="687abc76d67d",
            project_name="SomeUser-hello-world-9",
            owner="packit",
            web_url="https://copr.something.somewhere/123456",
            target="fedora-42-x86_64",
            status="pending",
            srpm_build=srpm_build,
            trigger_model=pr_model,
        ),
        # Same build_id but different chroot
        CoprBuildModel.get_or_create(
            build_id="123456",
            commit_sha="687abc76d67d",
            project_name="SomeUser-hello-world-9",
            owner="packit",
            web_url="https://copr.something.somewhere/123456",
            target="fedora-43-x86_64",
            status="pending",
            srpm_build=srpm_build,
            trigger_model=pr_model,
        ),
        # Completely different build
        CoprBuildModel.get_or_create(
            build_id="987654",
            commit_sha="987def76d67e",
            project_name="SomeUser-random-text-7",
            owner="cockpit-project",
            web_url="https://copr.something.somewhere/987654",
            target="fedora-43-x86_64",
            status="pending",
            srpm_build=srpm_build,
            trigger_model=different_pr_model,
        ),
    ]


@pytest.fixture()
def copr_builds_with_different_triggers(pr_model, branch_model, release_model):
    srpm_build = SRPMBuildModel.create("asd\nqwe\n")
    yield [
        # pull request trigger
        CoprBuildModel.get_or_create(
            build_id="123456",
            commit_sha="687abc76d67d",
            project_name="SomeUser-hello-world-9",
            owner="packit",
            web_url="https://copr.something.somewhere/123456",
            target="fedora-42-x86_64",
            status="pending",
            srpm_build=srpm_build,
            trigger_model=pr_model,
        ),
        # branch push trigger
        CoprBuildModel.get_or_create(
            build_id="123456",
            commit_sha="687abc76d67d",
            project_name="SomeUser-hello-world-9",
            owner="packit",
            web_url="https://copr.something.somewhere/123456",
            target="fedora-43-x86_64",
            status="pending",
            srpm_build=srpm_build,
            trigger_model=branch_model,
        ),
        # release trigger
        CoprBuildModel.get_or_create(
            build_id="987654",
            commit_sha="987def76d67e",
            project_name="SomeUser-random-text-7",
            owner="cockpit-project",
            web_url="https://copr.something.somewhere/987654",
            target="fedora-43-x86_64",
            status="pending",
            srpm_build=srpm_build,
            trigger_model=release_model,
        ),
    ]


# Create a single build
@pytest.fixture()
def a_koji_build(pr_model):
    srpm_build = SRPMBuildModel.create("asd\nqwe\n")
    yield KojiBuildModel.get_or_create(
        build_id="123456",
        commit_sha="687abc76d67d",
        web_url="https://copr.something.somewhere/123456",
        target=TARGET,
        status="pending",
        srpm_build=srpm_build,
        trigger_model=pr_model,
    )


# Create multiple builds
# Used for testing queries
@pytest.fixture()
def multiple_koji_builds(pr_trigger_model, different_pr_trigger_model):
    srpm_build = SRPMBuildModel.create("asd\nqwe\n")
    yield [
        KojiBuildModel.get_or_create(
            build_id="123456",
            commit_sha="687abc76d67d",
            web_url="https://copr.something.somewhere/123456",
            target="fedora-42-x86_64",
            status="pending",
            srpm_build=srpm_build,
            trigger_model=pr_trigger_model,
        ),
        # Same build_id but different chroot
        KojiBuildModel.get_or_create(
            build_id="123456",
            commit_sha="687abc76d67d",
            web_url="https://copr.something.somewhere/123456",
            target="fedora-43-x86_64",
            status="pending",
            srpm_build=srpm_build,
            trigger_model=pr_trigger_model,
        ),
        # Completely different build
        KojiBuildModel.get_or_create(
            build_id="987654",
            commit_sha="987def76d67e",
            web_url="https://copr.something.somewhere/987654",
            target="fedora-43-x86_64",
            status="pending",
            srpm_build=srpm_build,
            trigger_model=different_pr_trigger_model,
        ),
    ]


# Create a single test run
@pytest.fixture()
def a_new_test_run_pr(pr_model):
    yield TFTTestRunModel.create(
        pipeline_id="123456",
        commit_sha="687abc76d67d",
        web_url="https://console-testing-farm.apps.ci.centos.org/"
        "pipeline/02271aa8-2917-4741-a39e-78d8706c56c1",
        target=TARGET,
        status=TestingFarmResult.new,
        trigger_model=pr_model,
    )


# Create a single test run
@pytest.fixture()
def a_new_test_run_branch_push(branch_model):
    yield TFTTestRunModel.create(
        pipeline_id="123456",
        commit_sha="687abc76d67d",
        web_url="https://console-testing-farm.apps.ci.centos.org/"
        "pipeline/02271aa8-2917-4741-a39e-78d8706c56c1",
        target=TARGET,
        status=TestingFarmResult.new,
        trigger_model=branch_model,
    )


# Create multiple builds
# Used for testing queries
@pytest.fixture()
def multiple_new_test_runs(pr_model, different_pr_model):
    yield [
        TFTTestRunModel.create(
            pipeline_id="123456",
            commit_sha="687abc76d67d",
            web_url="https://console-testing-farm.apps.ci.centos.org/"
            "pipeline/02271aa8-2917-4741-a39e-78d8706c56c1",
            target="fedora-42-x86_64",
            status=TestingFarmResult.new,
            trigger_model=pr_model,
        ),
        # Same commit_sha but different chroot and pipeline_id
        TFTTestRunModel.create(
            pipeline_id="123457",
            commit_sha="687abc76d67d",
            web_url="https://console-testing-farm.apps.ci.centos.org/"
            "pipeline/02271aa8-2917-4741-a39e-78d8706c56c2",
            target="fedora-43-x86_64",
            status=TestingFarmResult.new,
            trigger_model=pr_model,
        ),
        # Completely different build
        TFTTestRunModel.create(
            pipeline_id="987654",
            commit_sha="987def76d67e",
            web_url="https://console-testing-farm.apps.ci.centos.org/"
            "pipeline/12272ba8-2918-4751-a40e-78d8706c56d4",
            target="fedora-43-x86_64",
            status=TestingFarmResult.running,
            trigger_model=different_pr_model,
        ),
    ]


# Create multiple whitelist entries
@pytest.fixture()
def multiple_whitelist_entries():
    yield [
        WhitelistModel.add_account(account_name="Rayquaza", status="approved_manually"),
        WhitelistModel.add_account(account_name="Deoxys", status="approved_manually"),
        # Not a typo, account_name repeated intentionally to check behaviour
        WhitelistModel.add_account(account_name="Deoxys", status="waiting"),
        WhitelistModel.add_account(account_name="Solgaleo", status="waiting"),
        WhitelistModel.add_account(account_name="Zacian", status="approved_manually"),
    ]


# Create new whitelist entry
@pytest.fixture()
def new_whitelist_entry(clean_before_and_after):
    yield WhitelistModel.add_account(
        account_name="Rayquaza", status="approved_manually"
    )


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
                "user_login": "adwait-thattey",
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


@pytest.fixture()
def installation_events():
    return [
        InstallationEvent(
            installation_id=3767734,
            account_login="teg",
            account_id=5409,
            account_url="https://api.github.com/users/teg",
            account_type="User",
            created_at="2020-03-31T10:06:38Z",
            repositories=[],
            sender_id=5409,
            sender_login="teg",
        ),
        InstallationEvent(
            installation_id=6813698,
            account_login="Pac23",
            account_id=11048203,
            account_url="https://api.github.com/users/Pac23",
            account_type="User",
            created_at="2020-03-31T10:06:38Z",
            repositories=["Pac23/awesome-piracy"],
            sender_id=11048203,
            sender_login="Pac23",
        ),
    ]


@pytest.fixture()
def multiple_installation_entries(installation_events):
    with get_sa_session() as session:
        session.query(InstallationModel).delete()
        yield [
            InstallationModel.create(event=installation_events[0],),
            InstallationModel.create(event=installation_events[1],),
        ]
    clean_db()


@pytest.fixture()
def release_event_dict():
    """
    Cleared version of the release webhook content.
    """
    return {
        "action": "published",
        "release": {
            "html_url": "https://github.com/the-namespace/the-repo-name/"
            "releases/tag/v1.0.2",
            "tag_name": "v1.0.2",
            "target_commitish": "master",
            "name": "test",
            "draft": False,
            "author": {
                "login": "lbarcziova",
                "url": "https://api.github.com/users/lbarcziova",
                "html_url": "https://github.com/lbarcziova",
                "type": "User",
            },
            "prerelease": False,
            "created_at": "2019-06-28T11:26:06Z",
            "published_at": "2019-07-11T13:51:51Z",
            "assets": [],
            "tarball_url": "https://api.github.com/repos/the-namespace/the-repo-name/"
            "tarball/v1.0.2",
            "zipball_url": "https://api.github.com/repos/the-namespace/the-repo-name/"
            "zipball/v1.0.2",
            "body": "testing release",
        },
        "repository": {
            "name": "the-repo-name",
            "full_name": "the-namespace/the-repo-name",
            "owner": {
                "login": "the-namespace",
                "url": "https://api.github.com/users/the-namespace",
                "html_url": "https://github.com/the-namespace",
                "type": "Organization",
            },
            "html_url": "https://github.com/the-namespace/the-repo-name",
            "created_at": "2019-05-02T18:54:46Z",
            "updated_at": "2019-06-28T11:26:09Z",
            "pushed_at": "2019-07-11T13:51:51Z",
        },
        "organization": {
            "login": "the-namespace",
            "url": "https://api.github.com/orgs/the-namespace",
        },
    }


@pytest.fixture()
def push_branch_event_dict():
    """
    Cleared version of the push webhook content.
    """
    return {
        "ref": "refs/heads/build-branch",
        "before": "0000000000000000000000000000000000000000",
        "after": "04885ff850b0fa0e206cd09db73565703d48f99b",
        "repository": {
            "name": "the-repo-name",
            "full_name": "the-namespace/the-repo-name",
            "private": False,
            "owner": {
                "name": "the-namespace",
                "login": "the-namespace",
                "url": "https://api.github.com/users/the-namespace",
                "html_url": "https://github.com/the-namespace",
            },
            "html_url": "https://github.com/the-namespace/the-repo-name",
            "description": "The most progresive command-line tool in the world.",
            "created_at": 1556823286,
            "updated_at": "2019-12-13T14:05:07Z",
            "pushed_at": 1583325578,
            "organization": "the-namespace",
        },
        "pusher": {"name": "lachmanfrantisek", "email": "lachmanfrantisek@gmail.com"},
        "organization": {"login": "the-namespace"},
        "sender": {"login": "lachmanfrantisek"},
        "created": True,
        "deleted": False,
        "forced": False,
        "base_ref": None,
        "compare": "https://github.com/the-namespace/the-repo-name/commit/04885ff850b0",
        "commits": [
            {
                "id": "04885ff850b0fa0e206cd09db73565703d48f99b",
                "message": "Add builds for branch\n\n"
                "Signed-off-by: Frantisek Lachman <flachman@redhat.com>",
                "timestamp": "2020-03-04T13:32:31+01:00",
                "url": "https://github.com/the-namespace/the-repo-name/"
                "commit/04885ff850b0fa0e206cd09db73565703d48f99b",
                "author": {
                    "name": "Frantisek Lachman",
                    "email": "flachman@redhat.com",
                    "username": "lachmanfrantisek",
                },
                "committer": {
                    "name": "Frantisek Lachman",
                    "email": "flachman@redhat.com",
                    "username": "lachmanfrantisek",
                },
                "added": [],
                "removed": [],
                "modified": [".packit.yaml"],
            }
        ],
        "head_commit": {
            "id": "04885ff850b0fa0e206cd09db73565703d48f99b",
            "message": "Add builds for branch\n\n"
            "Signed-off-by: Frantisek Lachman <flachman@redhat.com>",
            "timestamp": "2020-03-04T13:32:31+01:00",
            "url": "https://github.com/the-namespace/the-repo-name/"
            "commit/04885ff850b0fa0e206cd09db73565703d48f99b",
            "author": {
                "name": "Frantisek Lachman",
                "email": "flachman@redhat.com",
                "username": "lachmanfrantisek",
            },
            "committer": {
                "name": "Frantisek Lachman",
                "email": "flachman@redhat.com",
                "username": "lachmanfrantisek",
            },
            "added": [],
            "removed": [],
            "modified": [".packit.yaml"],
        },
    }


@pytest.fixture()
def pr_event_dict():
    """
    Cleared version of the pr webhook content.
    """
    return {
        "action": "opened",
        "number": 342,
        "pull_request": {
            "url": "https://api.github.com/repos/the-namespace/the-repo-name/pulls/342",
            "html_url": "https://github.com/the-namespace/the-repo-name/pull/342",
            "number": 342,
            "state": "open",
            "title": "better exception - issue 339",
            "user": {
                "login": "lbarcziova",
                "html_url": "https://github.com/lbarcziova",
            },
            "body": "I created better exception when the token is not supplied",
            "created_at": "2019-05-21T14:30:50Z",
            "updated_at": "2019-05-21T14:30:50Z",
            "head": {
                "label": "lbarcziova:master",
                "ref": "master",
                "sha": "528b803be6f93e19ca4130bf4976f2800a3004c4",
                "user": {
                    "login": "lbarcziova",
                    "html_url": "https://github.com/lbarcziova",
                },
                "repo": {
                    "name": "the-repo-name",
                    "full_name": "lbarcziova/the-repo-name",
                    "private": False,
                    "owner": {
                        "login": "lbarcziova",
                        "html_url": "https://github.com/lbarcziova",
                    },
                    "html_url": "https://github.com/lbarcziova/the-repo-name",
                    "description": "Upstream project ← → Downstream distribution",
                    "fork": True,
                    "url": "https://api.github.com/repos/lbarcziova/the-repo-name",
                },
            },
            "base": {
                "label": "the-namespace:master",
                "ref": "master",
                "sha": "724acc54471a720f8403c0ba0769640c88ae3cc0",
                "user": {
                    "login": "the-namespace",
                    "html_url": "https://github.com/the-namespace",
                },
                "repo": {
                    "name": "the-repo-name",
                    "full_name": "the-namespace/the-repo-name",
                    "private": False,
                    "owner": {
                        "login": "the-namespace",
                        "html_url": "https://github.com/the-namespace",
                    },
                    "html_url": "https://github.com/the-namespace/the-repo-name",
                    "url": "https://api.github.com/repos/the-namespace/the-repo-name",
                    "created_at": "2018-11-06T10:24:40Z",
                    "updated_at": "2019-05-21T13:41:13Z",
                    "pushed_at": "2019-05-21T13:58:51Z",
                },
            },
            "author_association": "CONTRIBUTOR",
            "commits": 1,
        },
        "repository": {
            "name": "the-repo-name",
            "full_name": "the-namespace/the-repo-name",
            "private": False,
            "owner": {
                "login": "the-namespace",
                "url": "https://api.github.com/users/the-namespace",
                "html_url": "https://github.com/the-namespace",
            },
            "html_url": "https://github.com/the-namespace/the-repo-name",
            "created_at": "2018-11-06T10:24:40Z",
            "updated_at": "2019-05-21T13:41:13Z",
            "pushed_at": "2019-05-21T13:58:51Z",
        },
        "organization": {"login": "the-namespace"},
        "sender": {"login": "lbarcziova"},
    }


@pytest.fixture()
def pr_comment_event_dict_packit_build():
    """
    Cleared version of the pr webhook content.
    """
    return {
        "action": "created",
        "issue": {
            "url": "https://api.github.com/repos/the-namespace/the-repo-name/issues/342",
            "repository_url": "https://api.github.com/repos/the-namespace/the-repo-name",
            "html_url": "https://github.com/the-namespace/the-repo-name/pull/342",
            "number": 342,
            "title": "WIP Testing collaborators - DO NOT MERGE",
            "user": {"login": "phracek", "html_url": "https://github.com/phracek"},
            "labels": [],
            "state": "open",
            "comments": 6,
            "created_at": "2019-07-19T13:50:33Z",
            "updated_at": "2019-08-08T15:22:24Z",
            "closed_at": None,
            "author_association": "NONE",
            "pull_request": {
                "url": "https://api.github.com/repos/the-namespace/the-repo-name/pulls/342",
            },
            "body": 'Signed-off-by: Petr "Stone" Hracek <phracek@redhat.com>\r\n\r\n'
            "This pull request is used for testing collaborators. \r\nDO NOT MERGE IT.",
        },
        "comment": {
            "url": "https://api.github.com/repos/the-namespace/the-repo-name/"
            "issues/comments/519565264",
            "html_url": "https://github.com/the-namespace/the-repo-name/pull/342"
            "#issuecomment-519565264",
            "issue_url": "https://api.github.com/repos/the-namespace/the-repo-name/issues/342",
            "id": 519565264,
            "user": {"login": "phracek", "html_url": "https://github.com/phracek"},
            "created_at": "2019-08-08T15:22:24Z",
            "updated_at": "2019-08-08T15:22:24Z",
            "author_association": "NONE",
            "body": "/packit build",
        },
        "repository": {
            "name": "the-repo-name",
            "full_name": "the-namespace/the-repo-name",
            "private": False,
            "owner": {
                "login": "the-namespace",
                "html_url": "https://github.com/the-namespace",
            },
            "html_url": "https://github.com/the-namespace/the-repo-name",
            "description": "The most progresive command-line tool in the world.",
            "fork": False,
            "created_at": "2019-05-02T18:54:46Z",
            "updated_at": "2019-06-28T11:26:09Z",
            "pushed_at": "2019-08-08T11:36:54Z",
        },
        "organization": {"login": "the-namespace"},
        "sender": {"login": "phracek", "html_url": "https://github.com/phracek"},
    }


@pytest.fixture()
def pr_comment_event_dict_packit_copr_build(pr_comment_event_dict_packit_build):
    copied_response = pr_comment_event_dict_packit_build.copy()
    copied_response["comment"]["body"] = "/packit copr-build"


@pytest.fixture()
def pr_comment_event_dict_packit_test(pr_comment_event_dict_packit_build):
    copied_response = pr_comment_event_dict_packit_build.copy()
    copied_response["comment"]["body"] = "/packit test"


@pytest.fixture()
def tf_result_dict_pr():
    return {
        "artifact": {
            "commit-sha": "687abc76d67d",
            "copr-chroot": "fedora-31-x86_64",
            "copr-repo-name": "packit/the-namespace-the-repo-name-79-stg",
            "git-ref": "687abc76d67d",
            "git-url": "https://github.com/packit-service/hello-world.git",
            "repo-name": "the-repo-name",
            "repo-namespace": "the-namespace",
        },
        "environment": {"image": "Fedora-Cloud-Base-31-20200403.0.x86_64.qcow2"},
        "message": "All tests passed",
        "pipeline": {"id": "123456"},
        "result": "passed",
        "tests": [{"name": "/ci/test/build/smoke", "result": "passed"}],
        "token": "XXXXXXXXXXXXXXXXXXXXXXXXXX",
        "url": "https://console-testing-farm.apps.ci.centos.org/pipeline/123456",
    }


@pytest.fixture()
def tf_result_dict_branch_push():
    return {
        "artifact": {
            "commit-sha": "687abc76d67d",
            "copr-chroot": "fedora-30-x86_64",
            "copr-repo-name": "packit/the-namespace-the-repo-name-build-branch-stg",
            "git-ref": "687abc76d67d",
            "git-url": "https://github.com/the-namespace/the-repo-name.git",
            "repo-name": "the-repo-name",
            "repo-namespace": "the-namespace",
        },
        "environment": {"image": "Fedora-Cloud-Base-30-20200401.0.x86_64.qcow2"},
        "message": "All tests passed",
        "pipeline": {"id": "123456"},
        "result": "passed",
        "tests": [{"name": "/ci/test/build/smoke", "result": "passed"}],
        "token": "XXXXXXXXXXXXXXXXXXXXXXXXXX",
        "url": "https://console-testing-farm.apps.ci.centos.org/" "pipeline/123456",
    }
