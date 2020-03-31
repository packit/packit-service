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
from datetime import datetime, timedelta

from sqlalchemy.exc import ProgrammingError

from packit_service.models import (
    ProjectReleaseModel,
    PullRequestModel,
    JobTriggerModelType,
    GitBranchModel,
    CoprBuildModel,
    get_sa_session,
    KojiBuildModel,
    SRPMBuildModel,
    JobTriggerModel,
    TestingFarmResult,
    TFTTestRunModel,
    TaskResultModel,
)
from tests_requre.database.conftest import TARGET


def test_create_pr_model(clean_before_and_after, pr_model):
    assert isinstance(pr_model, PullRequestModel)
    assert pr_model.pr_id == 1
    assert pr_model.project


def test_create_release_model(clean_before_and_after, release_model):
    assert isinstance(release_model, ProjectReleaseModel)
    assert release_model.tag_name == "v1.0.2"
    assert release_model.commit_hash == "aksjdaksjdla"
    assert release_model.project


def test_create_branch_model(clean_before_and_after, branch_model):
    assert isinstance(branch_model, GitBranchModel)
    assert branch_model.name == "build-branch"
    assert branch_model.project


def test_create_pr_trigger_model(clean_before_and_after, pr_trigger_model):
    assert pr_trigger_model.type == JobTriggerModelType.pull_request
    pr = pr_trigger_model.get_trigger_object()
    assert isinstance(pr, PullRequestModel)
    assert pr.pr_id == 1


def test_create_release_trigger_model(clean_before_and_after, release_trigger_model):
    assert release_trigger_model.type == JobTriggerModelType.release
    release = release_trigger_model.get_trigger_object()
    assert isinstance(release, ProjectReleaseModel)
    assert release.tag_name == "v1.0.2"


def test_create_branch_trigger_model(clean_before_and_after, branch_trigger_model):
    assert branch_trigger_model.type == JobTriggerModelType.branch_push
    branch = branch_trigger_model.get_trigger_object()
    assert isinstance(branch, GitBranchModel)
    assert branch.name == "build-branch"


def test_create_copr_build(clean_before_and_after, a_copr_build):
    assert a_copr_build.build_id == "123456"
    assert a_copr_build.commit_sha == "687abc76d67d"
    assert a_copr_build.project_name == "SomeUser-hello-world-9"
    assert a_copr_build.owner == "packit"
    assert a_copr_build.web_url == "https://copr.something.somewhere/123456"
    assert a_copr_build.srpm_build.logs == "asd\nqwe\n"
    assert a_copr_build.target == TARGET
    assert a_copr_build.status == "pending"
    # Since datetime.utcnow() will return different results in every time its called,
    # we will check if a_copr_build has build_submitted_time value that's within the past hour
    time_last_hour = datetime.utcnow() - timedelta(hours=1)
    assert a_copr_build.build_submitted_time > time_last_hour


def test_get_copr_build(clean_before_and_after, a_copr_build):
    assert a_copr_build.id
    b = CoprBuildModel.get_by_build_id(a_copr_build.build_id, TARGET)
    assert b.id == a_copr_build.id
    # let's make sure passing int works as well
    b = CoprBuildModel.get_by_build_id(int(a_copr_build.build_id), TARGET)
    assert b.id == a_copr_build.id
    b2 = CoprBuildModel.get_by_id(b.id)
    assert b2.id == a_copr_build.id


def test_copr_build_set_status(clean_before_and_after, a_copr_build):
    assert a_copr_build.status == "pending"
    a_copr_build.set_status("awesome")
    assert a_copr_build.status == "awesome"
    b = CoprBuildModel.get_by_build_id(a_copr_build.build_id, TARGET)
    assert b.status == "awesome"


def test_copr_build_set_build_logs_url(clean_before_and_after, a_copr_build):
    url = "https://copr.fp.o/logs/12456/build.log"
    a_copr_build.set_build_logs_url(url)
    assert a_copr_build.build_logs_url == url
    b = CoprBuildModel.get_by_build_id(a_copr_build.build_id, TARGET)
    assert b.build_logs_url == url


def test_create_koji_build(clean_before_and_after, a_koji_build):
    assert a_koji_build.build_id == "123456"
    assert a_koji_build.commit_sha == "687abc76d67d"
    assert a_koji_build.web_url == "https://copr.something.somewhere/123456"
    assert a_koji_build.srpm_build.logs == "asd\nqwe\n"
    assert a_koji_build.target == TARGET
    assert a_koji_build.status == "pending"
    # Since datetime.utcnow() will return different results in every time its called,
    # we will check if a_koji_build has build_submitted_time value that's within the past hour
    time_last_hour = datetime.utcnow() - timedelta(hours=1)
    assert a_koji_build.build_submitted_time > time_last_hour


def test_get_koji_build(clean_before_and_after, a_koji_build):
    assert a_koji_build.id
    b = KojiBuildModel.get_by_build_id(a_koji_build.build_id, TARGET)
    assert b.id == a_koji_build.id
    # let's make sure passing int works as well
    b = KojiBuildModel.get_by_build_id(int(a_koji_build.build_id), TARGET)
    assert b.id == a_koji_build.id
    b2 = KojiBuildModel.get_by_id(b.id)
    assert b2.id == a_koji_build.id


def test_koji_build_set_status(clean_before_and_after, a_koji_build):
    assert a_koji_build.status == "pending"
    a_koji_build.set_status("awesome")
    assert a_koji_build.status == "awesome"
    b = KojiBuildModel.get_by_build_id(a_koji_build.build_id, TARGET)
    assert b.status == "awesome"


def test_koji_build_set_build_logs_url(clean_before_and_after, a_koji_build):
    url = (
        "https://kojipkgs.fedoraproject.org//"
        "packages/python-ogr/0.11.0/1.fc30/data/logs/noarch/build.log"
    )
    a_koji_build.set_build_logs_url(url)
    assert a_koji_build.build_logs_url == url
    b = KojiBuildModel.get_by_build_id(a_koji_build.build_id, TARGET)
    assert b.build_logs_url == url


def test_get_or_create_pr(clean_before_and_after):
    with get_sa_session() as session:
        expected_pr = PullRequestModel.get_or_create(
            pr_id=42, namespace="clapton", repo_name="layla"
        )
        actual_pr = PullRequestModel.get_or_create(
            pr_id=42, namespace="clapton", repo_name="layla"
        )

        assert session.query(PullRequestModel).count() == 1
        assert expected_pr.project_id == actual_pr.project_id

        expected_pr = PullRequestModel.get_or_create(
            pr_id=42, namespace="clapton", repo_name="cocaine"
        )
        actual_pr = PullRequestModel.get_or_create(
            pr_id=42, namespace="clapton", repo_name="cocaine"
        )

        assert session.query(PullRequestModel).count() == 2
        assert expected_pr.project_id == actual_pr.project_id


def test_errors_while_doing_db(clean_before_and_after):
    with get_sa_session() as session:
        try:
            PullRequestModel.get_or_create(pr_id="nope", namespace="", repo_name=False)
        except ProgrammingError:
            pass
        assert len(session.query(PullRequestModel).all()) == 0
        PullRequestModel.get_or_create(pr_id=111, namespace="asd", repo_name="qwe")
        assert len(session.query(PullRequestModel).all()) == 1


# return all builds in table
def test_get_all(clean_before_and_after, multiple_copr_builds):
    builds_list = CoprBuildModel.get_all()
    assert len(builds_list) == 3
    # we just wanna check if result is iterable
    # order doesn't matter, so all of them are set to pending in supplied data
    assert builds_list[1].status == "pending"


# return all builds with given build_id
def test_get_all_build_id(clean_before_and_after, multiple_copr_builds):
    builds_list = CoprBuildModel.get_all_by_build_id(str(123456))
    assert len(list(builds_list)) == 2
    # both should have the same project_name
    assert builds_list[1].project_name == builds_list[0].project_name
    assert builds_list[1].project_name == "SomeUser-hello-world-9"


# returns the first build with given build id and target
def test_get_by_build_id(clean_before_and_after, multiple_copr_builds):
    # these are not iterable and thus should be accessible directly
    build_a = CoprBuildModel.get_by_build_id(str(123456), "fedora-42-x86_64")
    assert build_a.project_name == "SomeUser-hello-world-9"
    assert build_a.target == "fedora-42-x86_64"
    build_b = CoprBuildModel.get_by_build_id(str(123456), "fedora-43-x86_64")
    assert build_b.project_name == "SomeUser-hello-world-9"
    assert build_b.target == "fedora-43-x86_64"
    build_c = CoprBuildModel.get_by_build_id(str(987654), "fedora-43-x86_64")
    assert build_c.project_name == "SomeUser-random-text-7"


def test_multiple_pr_models(clean_before_and_after):
    pr1 = PullRequestModel.get_or_create(
        pr_id=1, namespace="the-namespace", repo_name="the-repo-name"
    )
    pr1_second = PullRequestModel.get_or_create(
        pr_id=1, namespace="the-namespace", repo_name="the-repo-name"
    )
    assert pr1.id == pr1_second.id
    assert pr1.project.id == pr1_second.project.id


def test_multiple_different_pr_models(clean_before_and_after):
    pr1 = PullRequestModel.get_or_create(
        pr_id=1, namespace="the-namespace", repo_name="the-repo-name"
    )
    pr2 = PullRequestModel.get_or_create(
        pr_id=2, namespace="the-namespace", repo_name="the-repo-name"
    )
    assert pr1.id != pr2.id
    assert pr1.project.id == pr2.project.id


def test_copr_and_koji_build_for_one_trigger(clean_before_and_after):
    pr1 = PullRequestModel.get_or_create(
        pr_id=1, namespace="the-namespace", repo_name="the-repo-name"
    )
    pr1_trigger = JobTriggerModel.get_or_create(
        type=JobTriggerModelType.pull_request, trigger_id=pr1.id
    )
    srpm_build = SRPMBuildModel.create("asd\nqwe\n")
    copr_build = CoprBuildModel.get_or_create(
        build_id="123456",
        commit_sha="687abc76d67d",
        project_name="SomeUser-hello-world-9",
        owner="packit",
        web_url="https://copr.something.somewhere/123456",
        target=TARGET,
        status="pending",
        srpm_build=srpm_build,
        trigger_model=pr1,
    )
    koji_build = KojiBuildModel.get_or_create(
        build_id="987654",
        commit_sha="687abc76d67d",
        web_url="https://copr.something.somewhere/123456",
        target=TARGET,
        status="pending",
        srpm_build=srpm_build,
        trigger_model=pr1,
    )

    assert copr_build in pr1_trigger.copr_builds
    assert koji_build in pr1_trigger.koji_builds

    assert copr_build.job_trigger.get_trigger_object() == pr1
    assert koji_build.job_trigger.get_trigger_object() == pr1


def test_tmt_test_run(clean_before_and_after, a_new_test_run):
    assert a_new_test_run.pipeline_id == "123456"
    assert a_new_test_run.commit_sha == "687abc76d67d"
    assert (
        a_new_test_run.web_url == "https://console-testing-farm.apps.ci.centos.org/"
        "pipeline/02271aa8-2917-4741-a39e-78d8706c56c1"
    )
    assert a_new_test_run.target == "fedora-42-x86_64"
    assert a_new_test_run.status == TestingFarmResult.new

    b = TFTTestRunModel.get_by_pipeline_id(a_new_test_run.pipeline_id)
    assert b
    assert b.id == a_new_test_run.id


def test_tmt_test_multiple_runs(clean_before_and_after, multiple_new_test_runs):
    assert multiple_new_test_runs
    assert multiple_new_test_runs[1].pipeline_id == "123457"
    assert multiple_new_test_runs[2].pipeline_id == "987654"
    with get_sa_session() as session:
        test_runs = session.query(TFTTestRunModel).all()
        assert len(test_runs) == 3


def test_tmt_test_run_set_status(clean_before_and_after, a_new_test_run):
    assert a_new_test_run.status == TestingFarmResult.new
    a_new_test_run.set_status(TestingFarmResult.running)
    assert a_new_test_run.status == TestingFarmResult.running

    b = TFTTestRunModel.get_by_pipeline_id(a_new_test_run.pipeline_id)
    assert b
    assert b.status == TestingFarmResult.running


def test_tmt_test_run_set_web_url(clean_before_and_after, pr_trigger_model):
    test_run_model = TFTTestRunModel.create(
        pipeline_id="123456",
        commit_sha="687abc76d67d",
        target=TARGET,
        status=TestingFarmResult.new,
        job_trigger=pr_trigger_model,
    )
    assert not test_run_model.web_url
    new_url = (
        "https://console-testing-farm.apps.ci.centos.org/"
        "pipeline/02271aa8-2917-4741-a39e-78d8706c56c1"
    )
    test_run_model.set_web_url(new_url)
    assert test_run_model.web_url == new_url

    b = TFTTestRunModel.get_by_pipeline_id(test_run_model.pipeline_id)
    assert b
    assert b.web_url == new_url


def test_get_task_results(clean_before_and_after, multiple_task_results_entries):
    results = TaskResultModel.get_all()
    assert len(results) == 2
    assert results[0].task_id == "ab1"
    assert results[1].task_id == "ab2"


def test_get_task_result_by_id(
    clean_before_and_after, multiple_task_results_entries, task_results
):
    assert TaskResultModel.get_by_id("ab1").jobs == task_results[0].get("jobs")
    assert TaskResultModel.get_by_id("ab1").event == task_results[0].get("event")
    assert TaskResultModel.get_by_id("ab2").jobs == task_results[1].get("jobs")
    assert TaskResultModel.get_by_id("ab2").event == task_results[1].get("event")
