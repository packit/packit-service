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

from ogr import GithubService, GitlabService, PagureService
from packit_service.config import ServiceConfig
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
    BugzillaModel,
)
from packit_service.service.events import InstallationEvent


class SampleValues:
    testing_farm_url = (
        "https://console-testing-farm.apps.ci.centos.org/"
        "pipeline/02271aa8-2917-4741-a39e-78d8706c56c1"
    )
    repo_namespace = "the-namespace"
    repo_name = "the-repo-name"
    different_project_name = "different-project-name"
    project_url = "https://github.com/the-namespace/the-repo-name"
    https_url = "https://github.com/the-namespace/the-repo-name.git"
    pagure_project_url = "https://git.stg.centos.org/the-namespace/the-repo-name"
    project = "the-project-name"
    owner = "the-owner"
    ref = "80201a74d96c"
    branch = "build-branch"
    commit_sha = "80201a74d96c"
    different_commit_sha = "687abc76d67d"
    pr_id = 342
    tag_name = "v1.0.2"

    # gitlab
    mr_id = 2
    gitlab_repo_namespace = "the-namespace"
    gitlab_repo_name = "repo-name"
    gitlab_project_url = "https://gitlab.com/the-namespace/repo-name"

    # build
    build_id = "123456"
    different_build_id = "987654"
    another_different_build_id = "78912"
    status_success = "success"
    status_pending = "pending"
    target = "fedora-42-x86_64"
    different_target = "fedora-43-x86_64"
    chroots = ["fedora-43-x86_64", "fedora-42-x86_64"]
    status_per_chroot = {"fedora-43-x86_64": "success", "fedora-42-x86_64": "pending"}
    copr_web_url = "https://copr.something.somewhere/123456"
    koji_web_url = "https://koji.something.somewhere/123456"
    srpm_logs = "some\nboring\nlogs"

    # TFT
    pipeline_id = "123456"
    different_pipeline_id = "123457"
    another_different_pipeline_id = "98765"

    # Whitelist
    account_name = "Rayquaza"
    different_account_name = "Deoxys"
    another_different_acount_name = "Solgaleo"
    yet_another_different_acount_name = "Zacian"

    # Bugzilla
    bug_id = 123456
    bug_url = f"https://partner-bugzilla.redhat.com/show_bug.cgi?id={bug_id}"


@pytest.fixture(scope="session", autouse=True)
def global_service_config():
    """
    This config will be used instead of the one loaded from the local config file.

    You can still mock/overwrite the service config content in your tests
    but this one will be used by default.
    """
    service_config = ServiceConfig()
    service_config.services = {
        GithubService(token="token"),
        GitlabService(token="token"),
        PagureService(token="token", instance_url="https://git.stg.centos.org"),
    }
    service_config.dry_run = False
    service_config.github_requests_log_path = "/path"
    service_config.server_name = "localhost"
    ServiceConfig.service_config = service_config


def clean_db():
    with get_sa_session() as session:
        session.query(CoprBuildModel).delete()
        session.query(KojiBuildModel).delete()
        session.query(SRPMBuildModel).delete()
        session.query(TFTTestRunModel).delete()
        session.query(TaskResultModel).delete()

        session.query(WhitelistModel).delete()
        session.query(InstallationModel).delete()
        session.query(BugzillaModel).delete()

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
        pr_id=SampleValues.pr_id,
        namespace=SampleValues.repo_namespace,
        repo_name=SampleValues.repo_name,
        project_url=SampleValues.project_url,
    )


@pytest.fixture()
def mr_model():
    yield PullRequestModel.get_or_create(
        pr_id=SampleValues.mr_id,
        namespace=SampleValues.gitlab_repo_namespace,
        repo_name=SampleValues.gitlab_repo_name,
        project_url=SampleValues.gitlab_project_url,
    )


@pytest.fixture()
def different_pr_model():
    yield PullRequestModel.get_or_create(
        pr_id=4,
        namespace=SampleValues.repo_namespace,
        repo_name=SampleValues.repo_name,
        project_url=SampleValues.project_url,
    )


@pytest.fixture()
def pagure_pr_model():
    yield PullRequestModel.get_or_create(
        pr_id=SampleValues.pr_id,
        namespace=SampleValues.repo_namespace,
        repo_name=SampleValues.repo_name,
        project_url=SampleValues.pagure_project_url,
    )


@pytest.fixture()
def release_model():
    yield ProjectReleaseModel.get_or_create(
        tag_name=SampleValues.tag_name,
        commit_hash=SampleValues.commit_sha,
        namespace=SampleValues.repo_namespace,
        repo_name=SampleValues.repo_name,
        project_url=SampleValues.project_url,
    )


@pytest.fixture()
def branch_model():
    yield GitBranchModel.get_or_create(
        branch_name=SampleValues.branch,
        namespace=SampleValues.repo_namespace,
        repo_name=SampleValues.repo_name,
        project_url=SampleValues.project_url,
    )


@pytest.fixture()
def branch_model_gitlab():
    yield GitBranchModel.get_or_create(
        branch_name=SampleValues.branch,
        namespace=SampleValues.gitlab_repo_namespace,
        repo_name=SampleValues.gitlab_repo_name,
        project_url=SampleValues.gitlab_project_url,
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
def srpm_build_model():
    yield SRPMBuildModel.create(SampleValues.srpm_logs, success=True)


@pytest.fixture()
def bugzilla_model():
    yield BugzillaModel.get_or_create(
        pr_id=SampleValues.pr_id,
        namespace=SampleValues.repo_namespace,
        repo_name=SampleValues.repo_name,
        project_url=SampleValues.project_url,
        bug_id=SampleValues.bug_id,
        bug_url=SampleValues.bug_url,
    )


@pytest.fixture()
def a_copr_build_for_pr(pr_model, srpm_build_model):
    model = CoprBuildModel.get_or_create(
        build_id=SampleValues.build_id,
        commit_sha=SampleValues.commit_sha,
        project_name=SampleValues.project,
        owner=SampleValues.owner,
        web_url=SampleValues.copr_web_url,
        target=SampleValues.target,
        status=SampleValues.status_pending,
        srpm_build=srpm_build_model,
        trigger_model=pr_model,
    )
    model.set_build_logs_url(
        "https://copr.somewhere/results/owner/package/target/build.logs"
    )
    yield model


@pytest.fixture()
def a_copr_build_for_branch_push(branch_model, srpm_build_model):
    model = CoprBuildModel.get_or_create(
        build_id=SampleValues.build_id,
        commit_sha=SampleValues.commit_sha,
        project_name=SampleValues.project,
        owner=SampleValues.owner,
        web_url=SampleValues.copr_web_url,
        target=SampleValues.target,
        status=SampleValues.status_pending,
        srpm_build=srpm_build_model,
        trigger_model=branch_model,
    )
    model.set_build_logs_url(
        "https://copr.somewhere/results/owner/package/target/build.logs"
    )
    yield model


@pytest.fixture()
def a_copr_build_for_release(release_model, srpm_build_model):
    model = CoprBuildModel.get_or_create(
        build_id=SampleValues.build_id,
        commit_sha=SampleValues.commit_sha,
        project_name=SampleValues.project,
        owner=SampleValues.owner,
        web_url=SampleValues.copr_web_url,
        target=SampleValues.target,
        status=SampleValues.status_pending,
        srpm_build=srpm_build_model,
        trigger_model=release_model,
    )
    model.set_build_logs_url(
        "https://copr.somewhere/results/owner/package/target/build.logs"
    )
    yield model


@pytest.fixture()
def multiple_copr_builds(pr_model, different_pr_model, srpm_build_model):
    yield [
        CoprBuildModel.get_or_create(
            build_id=SampleValues.build_id,
            commit_sha=SampleValues.ref,
            project_name=SampleValues.project,
            owner=SampleValues.owner,
            web_url=SampleValues.copr_web_url,
            target=SampleValues.target,
            status=SampleValues.status_success,
            srpm_build=srpm_build_model,
            trigger_model=pr_model,
        ),
        CoprBuildModel.get_or_create(
            build_id=SampleValues.build_id,
            commit_sha=SampleValues.ref,
            project_name=SampleValues.project,
            owner=SampleValues.owner,
            web_url=SampleValues.copr_web_url,
            target=SampleValues.different_target,
            status=SampleValues.status_pending,
            srpm_build=srpm_build_model,
            trigger_model=pr_model,
        ),
        CoprBuildModel.get_or_create(
            build_id=SampleValues.different_build_id,
            commit_sha=SampleValues.different_commit_sha,
            project_name=SampleValues.different_project_name,
            owner=SampleValues.owner,
            web_url=SampleValues.copr_web_url,
            target=SampleValues.target,
            status=SampleValues.status_success,
            srpm_build=srpm_build_model,
            trigger_model=different_pr_model,
        ),
    ]


@pytest.fixture()
def too_many_copr_builds(pr_model, different_pr_model, srpm_build_model):
    """Don't use for testing anything other than pagination, use multiple_copr_builds."""
    for i in range(20):

        # The following two are similar, except for target, status
        CoprBuildModel.get_or_create(
            build_id=SampleValues.build_id + str(i),
            commit_sha=SampleValues.ref,
            project_name=SampleValues.project,
            owner=SampleValues.owner,
            web_url=SampleValues.copr_web_url + str(i),
            target=SampleValues.target,
            status=SampleValues.status_success,
            srpm_build=srpm_build_model,
            trigger_model=pr_model,
        )
        CoprBuildModel.get_or_create(
            build_id=SampleValues.build_id + str(i),
            commit_sha=SampleValues.ref,
            project_name=SampleValues.project,
            owner=SampleValues.owner,
            web_url=SampleValues.copr_web_url + str(i),
            target=SampleValues.different_target,
            status=SampleValues.status_pending,
            srpm_build=srpm_build_model,
            trigger_model=pr_model,
        )
        # The following two are similar, except for target, status
        CoprBuildModel.get_or_create(
            build_id=SampleValues.different_build_id + str(i),
            commit_sha=SampleValues.different_commit_sha,
            project_name=SampleValues.different_project_name,
            owner=SampleValues.owner,
            web_url=SampleValues.copr_web_url + str(i),
            target=SampleValues.target,
            status=SampleValues.status_success,
            srpm_build=srpm_build_model,
            trigger_model=different_pr_model,
        )
        CoprBuildModel.get_or_create(
            build_id=SampleValues.different_build_id + str(i),
            commit_sha=SampleValues.different_commit_sha,
            project_name=SampleValues.different_project_name,
            owner=SampleValues.owner,
            web_url=SampleValues.copr_web_url + str(i),
            target=SampleValues.different_target,
            status=SampleValues.status_success,
            srpm_build=srpm_build_model,
            trigger_model=different_pr_model,
        )


@pytest.fixture()
def copr_builds_with_different_triggers(
    pr_model, branch_model, release_model, srpm_build_model
):
    yield [
        # pull request trigger
        CoprBuildModel.get_or_create(
            build_id=SampleValues.build_id,
            commit_sha=SampleValues.ref,
            project_name=SampleValues.project,
            owner=SampleValues.owner,
            web_url=SampleValues.copr_web_url,
            target=SampleValues.target,
            status=SampleValues.status_success,
            srpm_build=srpm_build_model,
            trigger_model=pr_model,
        ),
        # branch push trigger
        CoprBuildModel.get_or_create(
            build_id=SampleValues.different_build_id,
            commit_sha=SampleValues.ref,
            project_name=SampleValues.project,
            owner=SampleValues.owner,
            web_url=SampleValues.copr_web_url,
            target=SampleValues.target,
            status=SampleValues.status_success,
            srpm_build=srpm_build_model,
            trigger_model=branch_model,
        ),
        # release trigger
        CoprBuildModel.get_or_create(
            build_id=SampleValues.another_different_build_id,
            commit_sha=SampleValues.ref,
            project_name=SampleValues.project,
            owner=SampleValues.owner,
            web_url=SampleValues.copr_web_url,
            target=SampleValues.target,
            status=SampleValues.status_success,
            srpm_build=srpm_build_model,
            trigger_model=release_model,
        ),
    ]


@pytest.fixture()
def a_koji_build_for_pr(pr_model, srpm_build_model):
    build = KojiBuildModel.get_or_create(
        build_id=SampleValues.build_id,
        commit_sha=SampleValues.commit_sha,
        web_url=SampleValues.koji_web_url,
        target=SampleValues.target,
        status=SampleValues.status_pending,
        srpm_build=srpm_build_model,
        trigger_model=pr_model,
    )
    build.set_build_logs_url(
        "https://koji.somewhere/results/owner/package/target/build.logs"
    )
    yield build


@pytest.fixture()
def a_koji_build_for_branch_push(branch_model, srpm_build_model):
    yield KojiBuildModel.get_or_create(
        build_id=SampleValues.build_id,
        commit_sha=SampleValues.commit_sha,
        web_url=SampleValues.koji_web_url,
        target=SampleValues.target,
        status=SampleValues.status_pending,
        srpm_build=srpm_build_model,
        trigger_model=branch_model,
    )


@pytest.fixture()
def a_koji_build_for_release(release_model, srpm_build_model):
    yield KojiBuildModel.get_or_create(
        build_id=SampleValues.build_id,
        commit_sha=SampleValues.commit_sha,
        web_url=SampleValues.koji_web_url,
        target=SampleValues.target,
        status=SampleValues.status_pending,
        srpm_build=srpm_build_model,
        trigger_model=release_model,
    )


@pytest.fixture()
def multiple_koji_builds(pr_model, different_pr_model, srpm_build_model):
    yield [
        KojiBuildModel.get_or_create(
            build_id=SampleValues.build_id,
            commit_sha=SampleValues.commit_sha,
            web_url=SampleValues.koji_web_url,
            target=SampleValues.target,
            status=SampleValues.status_pending,
            srpm_build=srpm_build_model,
            trigger_model=pr_model,
        ),
        KojiBuildModel.get_or_create(
            build_id=SampleValues.different_build_id,
            commit_sha=SampleValues.commit_sha,
            web_url=SampleValues.koji_web_url,
            target=SampleValues.different_target,
            status=SampleValues.status_pending,
            srpm_build=srpm_build_model,
            trigger_model=pr_model,
        ),
        # Completely different build
        KojiBuildModel.get_or_create(
            build_id=SampleValues.another_different_build_id,
            commit_sha=SampleValues.different_commit_sha,
            web_url=SampleValues.koji_web_url,
            target=SampleValues.target,
            status=SampleValues.status_pending,
            srpm_build=srpm_build_model,
            trigger_model=different_pr_model,
        ),
    ]


@pytest.fixture()
def a_new_test_run_pr(pr_model):
    yield TFTTestRunModel.create(
        pipeline_id=SampleValues.pipeline_id,
        commit_sha=SampleValues.commit_sha,
        web_url=SampleValues.testing_farm_url,
        target=SampleValues.target,
        status=TestingFarmResult.new,
        trigger_model=pr_model,
    )


@pytest.fixture()
def a_new_test_run_branch_push(branch_model):
    yield TFTTestRunModel.create(
        pipeline_id=SampleValues.pipeline_id,
        commit_sha=SampleValues.commit_sha,
        web_url=SampleValues.testing_farm_url,
        target=SampleValues.target,
        status=TestingFarmResult.new,
        trigger_model=branch_model,
    )


@pytest.fixture()
def multiple_new_test_runs(pr_model, different_pr_model):
    yield [
        TFTTestRunModel.create(
            pipeline_id=SampleValues.pipeline_id,
            commit_sha=SampleValues.commit_sha,
            web_url=SampleValues.testing_farm_url,
            target=SampleValues.target,
            status=TestingFarmResult.new,
            trigger_model=pr_model,
        ),
        # Same commit_sha but different chroot and pipeline_id
        TFTTestRunModel.create(
            pipeline_id=SampleValues.different_pipeline_id,
            commit_sha=SampleValues.commit_sha,
            web_url=SampleValues.testing_farm_url,
            target=SampleValues.different_target,
            status=TestingFarmResult.new,
            trigger_model=pr_model,
        ),
        # Completely different build
        TFTTestRunModel.create(
            pipeline_id=SampleValues.another_different_pipeline_id,
            commit_sha=SampleValues.different_commit_sha,
            web_url=SampleValues.testing_farm_url,
            target=SampleValues.different_target,
            status=TestingFarmResult.running,
            trigger_model=different_pr_model,
        ),
    ]


@pytest.fixture()
def multiple_whitelist_entries():
    yield [
        WhitelistModel.add_account(
            account_name=SampleValues.account_name, status="approved_manually"
        ),
        WhitelistModel.add_account(
            account_name=SampleValues.different_account_name, status="approved_manually"
        ),
        # Not a typo, account_name repeated intentionally to check behaviour
        WhitelistModel.add_account(
            account_name=SampleValues.different_account_name, status="waiting"
        ),
        WhitelistModel.add_account(
            account_name=SampleValues.another_different_acount_name, status="waiting"
        ),
        WhitelistModel.add_account(
            account_name=SampleValues.yet_another_different_acount_name,
            status="approved_manually",
        ),
    ]


@pytest.fixture()
def new_whitelist_entry(clean_before_and_after):
    yield WhitelistModel.add_account(
        account_name=SampleValues.account_name, status="approved_manually"
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
        ).get_dict(),
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
        ).get_dict(),
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
def mr_comment_event_dict():
    """
    Cleared version of the mr comment webhook content.
    """
    return {
        "object_kind": "note",
        "event_type": "note",
        "user": {
            "name": "Shreyas Papinwar",
            "username": "shreyaspapi",
            "avatar_url": "https://assets.gitlab-static.net/uploads/-/system/"
            "user/avatar/5647360/avatar.png",
            "email": "spapinwar@gmail.com",
        },
        "project_id": 18032222,
        "project": {
            "id": 18032222,
            "name": "Hello there",
            "description": "Hehehehe",
            "web_url": "https://gitlab.com/testing-packit/hello-there",
            "git_ssh_url": "git@gitlab.com:testing-packit/hello-there.git",
            "git_http_url": "https://gitlab.com/testing-packit/hello-there.git",
            "namespace": "Testing packit",
            "path_with_namespace": "testing-packit/hello-there",
            "default_branch": "master",
            "homepage": "https://gitlab.com/testing-packit/hello-there",
            "url": "git@gitlab.com:testing-packit/hello-there.git",
            "ssh_url": "git@gitlab.com:testing-packit/hello-there.git",
            "http_url": "https://gitlab.com/testing-packit/hello-there.git",
        },
        "object_attributes": {
            "author_id": 5647360,
            "created_at": "2020-06-04 20:52:17 UTC",
            "discussion_id": "79a989acbaa824ddfb5a7850228cfe56ac779a96",
            "id": 355648957,
            "note": "must be reopened",
            "noteable_id": 59533079,
            "noteable_type": "MergeRequest",
            "project_id": 18032222,
            "updated_at": "2020-06-04 20:52:17 UTC",
            "description": "must be reopened",
            "url": "https://gitlab.com/testing-packit/hello-there/"
            "-/merge_requests/2#note_355648957",
        },
        "repository": {
            "name": "Hello there",
            "url": "git@gitlab.com:testing-packit/hello-there.git",
            "description": "Hehehehe",
            "homepage": "https://gitlab.com/testing-packit/hello-there",
        },
        "merge_request": {
            "author_id": 5647360,
            "created_at": "2020-05-24 19:45:07 UTC",
            "description": "",
            "id": 59533079,
            "iid": 2,
            "merge_status": "can_be_merged",
            "source_branch": "test1",
            "source_project_id": 18032222,
            "state_id": 1,
            "target_branch": "master",
            "target_project_id": 18032222,
            "time_estimate": 0,
            "title": "Update README.md",
            "updated_at": "2020-06-04 20:52:03 UTC",
            "url": "https://gitlab.com/testing-packit/hello-there/-/merge_requests/2",
            "source": {
                "id": 18032222,
                "name": "Hello there",
                "description": "Hehehehe",
                "web_url": "https://gitlab.com/testing-packit/hello-there",
                "git_ssh_url": "git@gitlab.com:testing-packit/hello-there.git",
                "git_http_url": "https://gitlab.com/testing-packit/hello-there.git",
                "namespace": "Testing packit",
                "visibility_level": 20,
                "path_with_namespace": "testing-packit/hello-there",
                "default_branch": "master",
                "homepage": "https://gitlab.com/testing-packit/hello-there",
                "url": "git@gitlab.com:testing-packit/hello-there.git",
                "ssh_url": "git@gitlab.com:testing-packit/hello-there.git",
                "http_url": "https://gitlab.com/testing-packit/hello-there.git",
            },
            "target": {
                "id": 18032222,
                "name": "Hello there",
                "description": "Hehehehe",
                "web_url": "https://gitlab.com/testing-packit/hello-there",
                "git_ssh_url": "git@gitlab.com:testing-packit/hello-there.git",
                "git_http_url": "https://gitlab.com/testing-packit/hello-there.git",
                "namespace": "Testing packit",
                "visibility_level": 20,
                "path_with_namespace": "testing-packit/hello-there",
                "default_branch": "master",
                "homepage": "https://gitlab.com/testing-packit/hello-there",
                "url": "git@gitlab.com:testing-packit/hello-there.git",
                "ssh_url": "git@gitlab.com:testing-packit/hello-there.git",
                "http_url": "https://gitlab.com/testing-packit/hello-there.git",
            },
            "last_commit": {
                "id": "45e272a57335e4e308f3176df6e9226a9e7805a9",
                "message": "Update README.md",
                "title": "Update README.md",
                "timestamp": "2020-06-01T07:24:37+00:00",
                "url": "https://gitlab.com/testing-packit/hello-there/-/commit"
                "/45e272a57335e4e308f3176df6e9226a9e7805a9",
                "author": {"name": "Shreyas Papinwar", "email": "spapinwar@gmail.com"},
            },
            "state": "opened",
        },
    }


@pytest.fixture()
def push_gitlab_event_dict():
    """
    Cleared version of the push gitlab webhook content.
    """
    return {
        "object_kind": "push",
        "event_name": "push",
        "before": "0e27f070efa4bef2a7c0168f07a0ac36ef90d8cb",
        "after": "cb2859505e101785097e082529dced35bbee0c8f",
        "ref": "refs/heads/build-branch",
        "checkout_sha": "cb2859505e101785097e082529dced35bbee0c8f",
        "user_id": 5647360,
        "user_name": "Shreyas Papinwar",
        "user_username": "shreyaspapi",
        "user_email": "",
        "user_avatar": "https://assets.gitlab-static.net/uploads/-"
        "/system/user/avatar/5647360/avatar.png",
        "project_id": 18032222,
        "project": {
            "id": 18032222,
            "name": "Hello there",
            "description": "Hehehehe",
            "web_url": "https://gitlab.com/the-namespace/repo-name",
            "git_ssh_url": "git@gitlab.com:the-namespace/repo-name.git",
            "git_http_url": "https://gitlab.com/the-namespace/repo-name.git",
            "namespace": "Testing packit",
            "visibility_level": 20,
            "path_with_namespace": "the-namespace/repo-name",
            "default_branch": "master",
            "homepage": "https://gitlab.com/the-namespace/repo-name",
            "url": "git@gitlab.com:the-namespace/repo-name.git",
            "ssh_url": "git@gitlab.com:the-namespace/repo-name.git",
            "http_url": "https://gitlab.com/the-namespace/repo-name.git",
        },
        "commits": [
            {
                "id": "cb2859505e101785097e082529dced35bbee0c8f",
                "message": "Update README.md",
                "title": "Update README.md",
                "timestamp": "2020-06-04T23:14:57+00:00",
                "url": "https://gitlab.com/the-namespace/repo-name/-/commit/"
                "cb2859505e101785097e082529dced35bbee0c8f",
                "author": {"name": "Shreyas Papinwar", "email": "spapinwar@gmail.com"},
                "added": [],
                "modified": ["README.md"],
                "removed": [],
            }
        ],
        "total_commits_count": 1,
        "push_options": {},
        "repository": {
            "name": "Hello there",
            "url": "git@gitlab.com:the-namespace/repo-name.git",
            "description": "Hehehehe",
            "homepage": "https://gitlab.com/the-namespace/repo-name",
            "git_http_url": "https://gitlab.com/the-namespace/repo-name.git",
            "git_ssh_url": "git@gitlab.com:the-namespace/repo-name.git",
            "visibility_level": 20,
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
def mr_event_dict():
    """
    Cleared version of the mr webhook content.
    """
    return {
        "object_kind": "merge_request",
        "event_type": "merge_request",
        "user": {
            "name": "Shreyas Papinwar",
            "username": "shreyaspapi",
            "email": "spapinwar@gmail.com",
        },
        "project": {
            "id": 18032222,
            "name": "Hello there",
            "description": "Hehehehe",
            "web_url": "https://gitlab.com/the-namespace/repo-name",
            "git_ssh_url": "git@gitlab.com:the-namespace/repo-name.git",
            "git_http_url": "https://gitlab.com/the-namespace/repo-name.git",
            "namespace": "Testing packit",
            "visibility_level": 20,
            "path_with_namespace": "the-namespace/repo-name",
            "default_branch": "master",
            "homepage": "https://gitlab.com/the-namespace/repo-name",
            "url": "git@gitlab.com:the-namespace/repo-name.git",
            "ssh_url": "git@gitlab.com:the-namespace/repo-name.git",
            "http_url": "https://gitlab.com/the-namespace/repo-name.git",
        },
        "object_attributes": {
            "author_id": 5647360,
            "created_at": "2020-05-24 19:45:07 UTC",
            "description": "",
            "id": 59533079,
            "iid": 2,
            "merge_status": "unchecked",
            "source_branch": "test1",
            "source_project_id": 18032222,
            "state_id": 1,
            "target_branch": "master",
            "target_project_id": 18032222,
            "time_estimate": 0,
            "title": "Update README.md",
            "updated_at": "2020-06-01 07:24:00 UTC",
            "url": "https://gitlab.com/the-namespace/repo-name/-/merge_requests/2",
            "source": {
                "id": 18032222,
                "name": "Hello there",
                "description": "Hehehehe",
                "web_url": "https://gitlab.com/the-namespace/repo-name",
                "git_ssh_url": "git@gitlab.com:the-namespace/repo-name.git",
                "git_http_url": "https://gitlab.com/the-namespace/repo-name.git",
                "namespace": "Testing packit",
                "visibility_level": 20,
                "path_with_namespace": "the-namespace/repo-name",
                "default_branch": "master",
                "homepage": "https://gitlab.com/the-namespace/repo-name",
                "url": "git@gitlab.com:the-namespace/repo-name.git",
                "ssh_url": "git@gitlab.com:the-namespace/repo-name.git",
                "http_url": "https://gitlab.com/the-namespace/repo-name.git",
            },
            "target": {
                "id": 18032222,
                "name": "Hello there",
                "description": "Hehehehe",
                "web_url": "https://gitlab.com/the-namespace/repo-name",
                "git_ssh_url": "git@gitlab.com:the-namespace/repo-name.git",
                "git_http_url": "https://gitlab.com/the-namespace/repo-name.git",
                "namespace": "Testing packit",
                "visibility_level": 20,
                "path_with_namespace": "the-namespace/repo-name",
                "default_branch": "master",
                "homepage": "https://gitlab.com/the-namespace/repo-name",
                "url": "git@gitlab.com:the-namespace/repo-name.git",
                "ssh_url": "git@gitlab.com:the-namespace/repo-name.git",
                "http_url": "https://gitlab.com/the-namespace/repo-name.git",
            },
            "last_commit": {
                "id": "45e272a57335e4e308f3176df6e9226a9e7805a9",
                "message": "Update README.md",
                "title": "Update README.md",
                "timestamp": "2020-06-01T07:24:37+00:00",
                "url": "https://gitlab.com/the-namespace/repo-name/-/"
                "commit/45e272a57335e4e308f3176df6e9226a9e7805a9",
                "author": {"name": "Shreyas Papinwar", "email": "spapinwar@gmail.com"},
            },
            "assignee_ids": [],
            "state": "opened",
            "action": "update",
            "oldrev": "94ccba9f986629e24b432c11d9c7fd20bb2ea51d",
        },
        "labels": [],
        "repository": {
            "name": "Hello there",
            "url": "git@gitlab.com:the-namespace/repo-name.git",
            "description": "Hehehehe",
            "homepage": "https://gitlab.com/the-namespace/repo-name",
        },
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
def pagure_pr_tag_added():
    """
    Cleared version of the pr webhook content.
    """
    return {
        "project": {
            "name": "the-repo-name",
            "namespace": "the-namespace",
            "user": {"fullname": "Packit Team", "name": "packit"},
            "fullname": "the-namespace/the-repo-name",
            "url_path": "the-namespace/the-repo-name",
            "id": 6843,
            "tags": [],
        },
        "tags": ["accepted"],
        "pull_request": {
            "uid": "34c5be2e95dd4f708b0c6a3acdcc3019",
            "initial_comment": None,
            "commit_stop": "0ec7f861383821218c485a45810d384ca224e357",
            "id": 342,
            "title": "dummy",
            "comments": [],
            "branch": "master",
            "tags": [],
            "user": {"fullname": "Jiri Popelka", "name": "jpopelka"},
            "branch_from": "test-tags",
            "commit_start": "0ec7f861383821218c485a45810d384ca224e357",
            "project": {
                "name": "the-repo-name",
                "namespace": "the-namespace",
                "user": {"fullname": "Packit Team", "name": "packit"},
                "fullname": "the-namespace/the-repo-name",
                "url_path": "the-namespace/the-repo-name",
                "id": 6843,
                "tags": [],
                "description": "packit test repo",
            },
            "repo_from": {
                "name": "the-repo-name",
                "parent": {
                    "name": "the-repo-name",
                    "namespace": "the-namespace",
                    "user": {"fullname": "Packit Team", "name": "packit"},
                    "fullname": "the-namespace/the-repo-name",
                    "url_path": "the-namespace/the-repo-name",
                    "id": 6843,
                    "tags": [],
                    "description": "packit test repo",
                },
                "namespace": "the-namespace",
                "user": {"fullname": "Jiri Popelka", "name": "jpopelka"},
                "fullname": "forks/jpopelka/the-namespace/the-repo-name",
                "url_path": "fork/jpopelka/the-namespace/the-repo-name",
                "id": 6855,
                "tags": [],
                "description": "packit test repo",
            },
        },
        "topic": "git.stg.centos.org/pull-request.tag.added",
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


@pytest.fixture()
def koji_build_scratch_start_dict():
    return {
        "topic": "org.fedoraproject.prod.buildsys.task.state.change",
        "info": {
            "parent": None,
            "completion_time": None,
            "start_time": 1590993047.0,
            "request": [
                "cli-build/1590993046.5615945.hqCGfULV/"
                "hello-0.74-1.20200601063010016064.fc31.src.rpm",
                "rawhide",
                {"scratch": True, "wait_builds": []},
            ],
            "waiting": None,
            "awaited": None,
            "id": SampleValues.build_id,
            "priority": 20,
            "channel_id": 1,
            "state": 1,
            "create_time": 1590993047.0,
            "owner": 4641,
            "host_id": 305,
            "method": "build",
            "label": None,
            "arch": "noarch",
            "children": [],
        },
        "old": "FREE",
        "attribute": "state",
        "id": SampleValues.build_id,
        "instance": "primary",
        "owner": "packit",
        "new": "OPEN",
        "srpm": "hello-0.74-1.20200601063010016064.fc31.src.rpm",
        "method": "build",
    }


@pytest.fixture()
def koji_build_scratch_end_dict():
    return {
        "topic": "org.fedoraproject.prod.buildsys.task.state.change",
        "info": {
            "parent": None,
            "completion_time": 1590993215.0,
            "start_time": 1590993047.0,
            "request": [
                "cli-build/1590993046.5615945.hqCGfULV/"
                "hello-0.74-1.20200601063010016064.fc31.src.rpm",
                "rawhide",
                {"scratch": True, "wait_builds": []},
            ],
            "waiting": False,
            "awaited": None,
            "id": SampleValues.build_id,
            "priority": 20,
            "channel_id": 1,
            "state": 2,
            "create_time": 1590993047.0,
            "result": None,
            "owner": 4641,
            "host_id": 305,
            "method": "build",
            "label": None,
            "arch": "noarch",
            "children": [
                {
                    "parent": SampleValues.build_id,
                    "completion_time": 1590993124.0,
                    "start_time": 1590993048.0,
                    "waiting": None,
                    "awaited": False,
                    "label": "srpm",
                    "priority": 19,
                    "channel_id": 1,
                    "state": 2,
                    "create_time": 1590993047.0,
                    "owner": 4641,
                    "host_id": 303,
                    "method": "rebuildSRPM",
                    "arch": "noarch",
                    "id": 45270171,
                },
                {
                    "parent": SampleValues.build_id,
                    "completion_time": 1590993214.0,
                    "start_time": 1590993131.0,
                    "waiting": None,
                    "awaited": False,
                    "label": "noarch",
                    "priority": 19,
                    "channel_id": 1,
                    "state": 2,
                    "create_time": 1590993131.0,
                    "owner": 4641,
                    "host_id": 305,
                    "method": "buildArch",
                    "arch": "noarch",
                    "id": 45270227,
                },
            ],
        },
        "old": "OPEN",
        "attribute": "state",
        "id": SampleValues.build_id,
        "instance": "primary",
        "owner": "packit",
        "new": "CLOSED",
        "srpm": "hello-0.74-1.20200601063010016064.fc31.src.rpm",
        "method": "build",
    }
