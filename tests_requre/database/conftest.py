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
)

TARGET = "fedora-42-x86_64"


def clean_db():
    with get_sa_session() as session:
        session.query(CoprBuildModel).delete()
        session.query(KojiBuildModel).delete()
        session.query(SRPMBuildModel).delete()
        session.query(TFTTestRunModel).delete()

        session.query(WhitelistModel).delete()

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
        pr_id=1, namespace="the-namespace", repo_name="the-repo-name"
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


# Create a single build
@pytest.fixture()
def a_copr_build(pr_model):
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
def a_new_test_run(pr_model):
    yield TFTTestRunModel.create(
        pipeline_id="123456",
        commit_sha="687abc76d67d",
        web_url="https://console-testing-farm.apps.ci.centos.org/"
        "pipeline/02271aa8-2917-4741-a39e-78d8706c56c1",
        target=TARGET,
        status=TestingFarmResult.new,
        trigger_model=pr_model,
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
