# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
import contextlib
from datetime import datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError, ProgrammingError

from packit_service.models import (
    BodhiUpdateTargetModel,
    BuildStatus,
    CoprBuildGroupModel,
    CoprBuildTargetModel,
    GitBranchModel,
    GithubInstallationModel,
    GitProjectModel,
    KojiBuildGroupModel,
    KojiBuildTargetModel,
    OSHScanModel,
    PipelineModel,
    ProjectAuthenticationIssueModel,
    ProjectEventModel,
    ProjectEventModelType,
    ProjectReleaseModel,
    PullRequestModel,
    Session,
    SourceGitPRDistGitPRModel,
    SRPMBuildModel,
    SyncReleaseJobType,
    SyncReleaseModel,
    SyncReleaseStatus,
    SyncReleaseTargetModel,
    SyncReleaseTargetStatus,
    TestingFarmResult,
    TFTTestRunGroupModel,
    TFTTestRunTargetModel,
    sa_session_transaction,
)
from tests_openshift.conftest import SampleValues


def test_create_pr_model(clean_before_and_after, pr_model):
    assert isinstance(pr_model, PullRequestModel)
    assert pr_model.pr_id == 342
    assert pr_model.project


def test_create_release_model(clean_before_and_after, release_model):
    assert isinstance(release_model, ProjectReleaseModel)
    assert release_model.tag_name == "v1.0.2"
    assert release_model.commit_hash == "80201a74d96c"
    assert release_model.project


def test_create_branch_model(clean_before_and_after, branch_model):
    assert isinstance(branch_model, GitBranchModel)
    assert branch_model.name == "build-branch"
    assert branch_model.project


def test_create_pr_project_event_model(clean_before_and_after, pr_project_event_model):
    assert pr_project_event_model.type == ProjectEventModelType.pull_request
    pr = pr_project_event_model.get_project_event_object()
    assert isinstance(pr, PullRequestModel)
    assert pr.pr_id == 342


def test_create_release_project_event_model(
    clean_before_and_after,
    release_project_event_model,
):
    assert release_project_event_model.type == ProjectEventModelType.release
    release_model = release_project_event_model.get_project_event_object()
    assert isinstance(release_model, ProjectReleaseModel)
    assert release_model.tag_name == "v1.0.2"


def test_create_branch_trigger_model(
    clean_before_and_after,
    branch_project_event_model,
):
    assert branch_project_event_model.type == ProjectEventModelType.branch_push
    branch = branch_project_event_model.get_project_event_object()
    assert isinstance(branch, GitBranchModel)
    assert branch.name == "build-branch"


def test_create_copr_build(clean_before_and_after, a_copr_build_for_pr):
    assert a_copr_build_for_pr.build_id == "123456"
    assert a_copr_build_for_pr.commit_sha == "80201a74d96c"
    assert a_copr_build_for_pr.project_name == "the-project-name"
    assert a_copr_build_for_pr.owner == "the-owner"
    assert a_copr_build_for_pr.web_url == "https://copr.something.somewhere/123456"
    assert a_copr_build_for_pr.get_srpm_build().logs == "some\nboring\nlogs"
    assert a_copr_build_for_pr.target == "fedora-42-x86_64"
    assert a_copr_build_for_pr.status == BuildStatus.pending
    # Since datetime.utcnow() will return different results in every time its called,
    # we will check if a_copr_build has build_submitted_time value that's within the past hour
    time_last_hour = datetime.utcnow() - timedelta(hours=1)
    assert a_copr_build_for_pr.build_submitted_time > time_last_hour
    a_copr_build_for_pr.set_end_time(None)
    assert a_copr_build_for_pr.build_finished_time is None


def test_copr_build_get_pr_id(
    clean_before_and_after,
    copr_builds_with_different_triggers,
):
    assert copr_builds_with_different_triggers[0].get_pr_id() == 342
    assert not copr_builds_with_different_triggers[1].get_pr_id()
    assert not copr_builds_with_different_triggers[2].get_pr_id()


def test_copr_build_get_branch(
    clean_before_and_after,
    copr_builds_with_different_triggers,
):
    assert not copr_builds_with_different_triggers[0].get_branch_name()
    assert copr_builds_with_different_triggers[1].get_branch_name() == "build-branch"
    assert not copr_builds_with_different_triggers[2].get_branch_name()


def test_get_merged_chroots(clean_before_and_after, too_many_copr_builds):
    # fetch 10 merged groups of builds
    builds_list = list(CoprBuildTargetModel.get_merged_chroots(10, 20))
    assert len(builds_list) == 10
    # two merged chroots so two statuses
    assert len(builds_list[0].status) == 2
    assert len(builds_list[0].target) == 2

    # check that IDs are different
    assert builds_list[0].packit_id_per_chroot[0][0] != builds_list[0].packit_id_per_chroot[1][0]

    assert builds_list[1].status[0][0] == "success"
    assert ["fedora-42-x86_64"] in builds_list[2].target
    assert ["fedora-43-x86_64"] in builds_list[2].target


def test_get_copr_build(clean_before_and_after, a_copr_build_for_pr):
    assert a_copr_build_for_pr.id

    # pass in a build_id and a target
    b = CoprBuildTargetModel.get_by_build_id(
        a_copr_build_for_pr.build_id,
        SampleValues.target,
    )
    assert b.id == a_copr_build_for_pr.id
    # let's make sure passing int works as well
    b2 = CoprBuildTargetModel.get_by_build_id(
        int(a_copr_build_for_pr.build_id),
        SampleValues.target,
    )
    assert b2.id == a_copr_build_for_pr.id

    # pass in a build_id and without a target
    b3 = CoprBuildTargetModel.get_by_build_id(a_copr_build_for_pr.build_id, None)
    assert b3.commit_sha == a_copr_build_for_pr.commit_sha

    b4 = CoprBuildTargetModel.get_by_id(b.id)
    assert b4.id == a_copr_build_for_pr.id


def test_copr_build_set_status(clean_before_and_after, a_copr_build_for_pr):
    assert a_copr_build_for_pr.status == BuildStatus.pending
    a_copr_build_for_pr.set_status(BuildStatus.success)
    assert a_copr_build_for_pr.status == BuildStatus.success
    b = CoprBuildTargetModel.get_by_build_id(
        a_copr_build_for_pr.build_id,
        SampleValues.target,
    )
    assert b.status == BuildStatus.success


def test_copr_build_set_build_logs_url(clean_before_and_after, a_copr_build_for_pr):
    url = "https://copr.fp.o/logs/12456/build.log"
    a_copr_build_for_pr.set_build_logs_url(url)
    assert a_copr_build_for_pr.build_logs_url == url
    b = CoprBuildTargetModel.get_by_build_id(
        a_copr_build_for_pr.build_id,
        SampleValues.target,
    )
    assert b.build_logs_url == url


def test_create_koji_build(clean_before_and_after, a_koji_build_for_pr):
    assert a_koji_build_for_pr.task_id == "123456"
    assert a_koji_build_for_pr.commit_sha == "80201a74d96c"
    assert a_koji_build_for_pr.web_url == "https://koji.something.somewhere/123456"
    assert a_koji_build_for_pr.get_srpm_build().logs == "some\nboring\nlogs"
    assert a_koji_build_for_pr.target == "fedora-42-x86_64"
    assert a_koji_build_for_pr.status == "pending"
    # Since datetime.utcnow() will return different results in every time its called,
    # we will check if a_koji_build has build_submitted_time value that's within the past hour
    time_last_hour = datetime.utcnow() - timedelta(hours=1)
    assert a_koji_build_for_pr.build_submitted_time > time_last_hour


def test_get_koji_build(clean_before_and_after, a_koji_build_for_pr):
    assert a_koji_build_for_pr.id
    b = KojiBuildTargetModel.get_by_task_id(
        a_koji_build_for_pr.task_id,
        SampleValues.target,
    )
    assert b.id == a_koji_build_for_pr.id
    # let's make sure passing int works as well
    b = KojiBuildTargetModel.get_by_task_id(
        int(a_koji_build_for_pr.task_id),
        SampleValues.target,
    )
    assert b.id == a_koji_build_for_pr.id
    b2 = KojiBuildTargetModel.get_by_id(b.id)
    assert b2.id == a_koji_build_for_pr.id


def test_koji_build_set_status(clean_before_and_after, a_koji_build_for_pr):
    assert a_koji_build_for_pr.status == "pending"
    a_koji_build_for_pr.set_status("awesome")
    assert a_koji_build_for_pr.status == "awesome"
    b = KojiBuildTargetModel.get_by_task_id(
        a_koji_build_for_pr.task_id,
        SampleValues.target,
    )
    assert b.status == "awesome"


def test_koji_build_set_build_logs_urls(clean_before_and_after, a_koji_build_for_pr):
    urls = {
        "x86_64": "https://kojipkgs.fedoraproject.org//"
        "packages/python-ogr/0.11.0/1.fc30/data/logs/noarch/build.log",
    }
    a_koji_build_for_pr.set_build_logs_urls(urls)
    assert a_koji_build_for_pr.build_logs_urls == urls
    b = KojiBuildTargetModel.get_by_task_id(
        a_koji_build_for_pr.task_id,
        SampleValues.target,
    )
    assert b.build_logs_urls == urls


def test_get_or_create_pr(clean_before_and_after):
    with sa_session_transaction() as session:
        expected_pr = PullRequestModel.get_or_create(
            pr_id=42,
            namespace="clapton",
            repo_name="layla",
            project_url="https://github.com/clapton/layla",
        )
        actual_pr = PullRequestModel.get_or_create(
            pr_id=42,
            namespace="clapton",
            repo_name="layla",
            project_url="https://github.com/clapton/layla",
        )

        assert session.query(PullRequestModel).count() == 1
        assert expected_pr.project_id == actual_pr.project_id

        expected_pr = PullRequestModel.get_or_create(
            pr_id=42,
            namespace="clapton",
            repo_name="cocaine",
            project_url="https://github.com/clapton/layla",
        )
        actual_pr = PullRequestModel.get_or_create(
            pr_id=42,
            namespace="clapton",
            repo_name="cocaine",
            project_url="https://github.com/clapton/layla",
        )

        assert session.query(PullRequestModel).count() == 2
        assert expected_pr.project_id == actual_pr.project_id


def test_errors_while_doing_db(clean_before_and_after):
    with sa_session_transaction() as session:
        with contextlib.suppress(ProgrammingError):
            PullRequestModel.get_or_create(
                pr_id="nope",
                namespace="",
                repo_name=False,
                project_url="https://github.com/the-namespace/the-repo",
            )
        assert len(session.query(PullRequestModel).all()) == 0
        PullRequestModel.get_or_create(
            pr_id=111,
            namespace="asd",
            repo_name="qwe",
            project_url="https://github.com/asd/qwe",
        )
        assert len(session.query(PullRequestModel).all()) == 1


def test_get_srpm_builds_in_give_range(
    clean_before_and_after,
    srpm_build_model_with_new_run_for_pr,
):
    builds_list = list(SRPMBuildModel.get_range(0, 10))
    assert len(builds_list) == 1
    assert builds_list[0].status == "success"


def test_get_all_builds(clean_before_and_after, multiple_copr_builds):
    builds_list = list(CoprBuildTargetModel.get_all())
    assert len({builds_list[i].id for i in range(4)})
    # All builds has to have exactly one PipelineModel connected
    assert all(len(build.group_of_targets.runs) == 1 for build in builds_list)
    # All build groups must have a different PipelineModel connected.
    assert len({build.group_of_targets.runs[0] for build in builds_list}) == 3


def test_get_all_build_id(clean_before_and_after, multiple_copr_builds):
    builds_list = list(CoprBuildTargetModel.get_all_by_build_id(str(123456)))
    assert len(builds_list) == 2
    # both should have the same project_name
    assert builds_list[1].project_name == builds_list[0].project_name
    assert builds_list[1].project_name == "the-project-name"


# returns the first copr build with given build id and target
def test_get_by_build_id(clean_before_and_after, multiple_copr_builds):
    # these are not iterable and thus should be accessible directly
    build_a = CoprBuildTargetModel.get_by_build_id(
        SampleValues.build_id,
        SampleValues.target,
    )
    assert build_a.project_name == "the-project-name"
    assert build_a.target == "fedora-42-x86_64"

    build_b = CoprBuildTargetModel.get_by_build_id(
        SampleValues.build_id,
        SampleValues.different_target,
    )
    assert build_b.project_name == "the-project-name"
    assert build_b.target == "fedora-43-x86_64"

    build_c = CoprBuildTargetModel.get_by_build_id(
        SampleValues.another_different_build_id,
        SampleValues.target,
    )
    assert build_c.project_name == "different-project-name"


def test_copr_get_all_by_owner_project_commit_target(
    clean_before_and_after,
    multiple_copr_builds,
):
    builds_list = list(
        CoprBuildTargetModel.get_all_by(
            owner=SampleValues.owner,
            project_name=SampleValues.project,
            target=SampleValues.target,
            commit_sha=SampleValues.ref,
        ),
    )
    assert len(builds_list) == 2
    # both should have the same project_name
    assert builds_list[1].project_name == builds_list[0].project_name == SampleValues.project

    # test without target and owner
    builds_list_without_target = list(
        CoprBuildTargetModel.get_all_by(
            project_name=SampleValues.project,
            commit_sha=SampleValues.ref,
        ),
    )
    assert len(builds_list_without_target) == 3
    assert (
        builds_list_without_target[0].commit_sha
        == builds_list_without_target[1].commit_sha
        == builds_list_without_target[2].commit_sha
        == SampleValues.ref
    )


def test_copr_get_all_by_commit(clean_before_and_after, multiple_copr_builds):
    builds_list = list(
        CoprBuildTargetModel.get_all_by_commit(commit_sha=SampleValues.ref),
    )
    assert len(builds_list) == 3
    # they should have the same project_name
    assert (
        builds_list[0].project_name
        == builds_list[1].project_name
        == builds_list[2].project_name
        == SampleValues.project
    )


def test_multiple_pr_models(clean_before_and_after):
    pr1 = PullRequestModel.get_or_create(
        pr_id=1,
        namespace="the-namespace",
        repo_name="the-repo-name",
        project_url="https://github.com/the-namespace/the-repo-name",
    )
    pr1_second = PullRequestModel.get_or_create(
        pr_id=1,
        namespace="the-namespace",
        repo_name="the-repo-name",
        project_url="https://github.com/the-namespace/the-repo-name",
    )
    assert pr1.id == pr1_second.id
    assert pr1.project.id == pr1_second.project.id


def test_multiple_different_pr_models(clean_before_and_after):
    pr1 = PullRequestModel.get_or_create(
        pr_id=1,
        namespace="the-namespace",
        repo_name="the-repo-name",
        project_url="https://github.com/the-namespace/the-repo-name",
    )
    pr2 = PullRequestModel.get_or_create(
        pr_id=2,
        namespace="the-namespace",
        repo_name="the-repo-name",
        project_url="https://github.com/the-namespace/the-repo-name",
    )
    assert pr1.id != pr2.id
    assert pr1.project.id == pr2.project.id


def test_copr_and_koji_build_for_one_trigger(clean_before_and_after):
    pr1 = PullRequestModel.get_or_create(
        pr_id=1,
        namespace="the-namespace",
        repo_name="the-repo-name",
        project_url="https://github.com/the-namespace/the-repo-name",
    )
    project_event = ProjectEventModel.get_or_create(
        type=ProjectEventModelType.pull_request,
        event_id=pr1.id,
        commit_sha="abcdef",
    )
    # SRPMBuildModel is (sadly) not shared between Koji and Copr builds.
    srpm_build_for_copr, run_model_for_copr = SRPMBuildModel.create_with_new_run(
        project_event_model=project_event,
    )
    copr_group = CoprBuildGroupModel.create(run_model_for_copr)
    srpm_build_for_copr.set_logs("asd\nqwe\n")
    srpm_build_for_copr.set_status(BuildStatus.success)

    srpm_build_for_koji, run_model_for_koji = SRPMBuildModel.create_with_new_run(
        project_event_model=project_event,
    )
    koji_group = KojiBuildGroupModel.create(run_model_for_koji)
    srpm_build_for_copr.set_logs("asd\nqwe\n")
    srpm_build_for_copr.set_status(BuildStatus.success)

    copr_build = CoprBuildTargetModel.create(
        build_id="123456",
        project_name="SomeUser-hello-world-9",
        owner="packit",
        web_url="https://copr.something.somewhere/123456",
        target=SampleValues.target,
        status=BuildStatus.pending,
        copr_build_group=copr_group,
    )
    koji_build = KojiBuildTargetModel.create(
        task_id="987654",
        web_url="https://copr.something.somewhere/123456",
        target=SampleValues.target,
        status="pending",
        scratch=True,
        koji_build_group=koji_group,
    )

    assert copr_build in pr1.get_copr_builds()
    assert koji_build in pr1.get_koji_builds()

    assert srpm_build_for_copr in pr1.get_srpm_builds()
    assert srpm_build_for_koji in pr1.get_srpm_builds()

    assert copr_build.get_project_event_model() == koji_build.get_project_event_model()

    assert srpm_build_for_copr.get_project_event_object() == pr1
    assert srpm_build_for_koji.get_project_event_object() == pr1
    assert copr_build.get_project_event_object() == pr1
    assert koji_build.get_project_event_object() == pr1

    assert len(koji_build.group_of_targets.runs) == 1
    assert koji_build.group_of_targets.runs[0] == run_model_for_koji
    assert len(copr_build.group_of_targets.runs) == 1
    assert copr_build.group_of_targets.runs[0] == run_model_for_copr


def test_tmt_test_run(clean_before_and_after, a_new_test_run_pr):
    assert a_new_test_run_pr.pipeline_id == "123456"
    assert a_new_test_run_pr.commit_sha == "80201a74d96c"
    assert (
        a_new_test_run_pr.web_url == "https://console-testing-farm.apps.ci.centos.org/"
        "pipeline/02271aa8-2917-4741-a39e-78d8706c56c1"
    )
    assert a_new_test_run_pr.target == "fedora-42-x86_64"
    assert a_new_test_run_pr.status == TestingFarmResult.new

    b = TFTTestRunTargetModel.get_by_pipeline_id(a_new_test_run_pr.pipeline_id)
    assert b
    assert b.id == a_new_test_run_pr.id


def test_tmt_test_multiple_runs(clean_before_and_after, multiple_new_test_runs):
    assert multiple_new_test_runs
    assert multiple_new_test_runs[0].pipeline_id == SampleValues.pipeline_id
    assert multiple_new_test_runs[1].pipeline_id == SampleValues.different_pipeline_id

    test_runs = Session().query(TFTTestRunTargetModel).all()
    assert len(test_runs) == 4
    # Separate PipelineModel for each TFTTestRunGroupModel
    assert len({m.group_of_targets.runs[0] for m in multiple_new_test_runs}) == 3
    # Exactly one PipelineModel for each TFTTestRunTargetModel
    assert all(len(m.group_of_targets.runs) == 1 for m in multiple_new_test_runs)
    # Two ProjectEventModel:
    assert len({m.get_project_event_object() for m in multiple_new_test_runs}) == 2


def test_tmt_test_run_set_status(clean_before_and_after, a_new_test_run_pr):
    assert a_new_test_run_pr.status == TestingFarmResult.new
    a_new_test_run_pr.set_status(TestingFarmResult.running)
    assert a_new_test_run_pr.status == TestingFarmResult.running

    b = TFTTestRunTargetModel.get_by_pipeline_id(a_new_test_run_pr.pipeline_id)
    assert b
    assert b.status == TestingFarmResult.running


def test_tmt_test_run_get_project(clean_before_and_after, a_new_test_run_pr):
    assert a_new_test_run_pr.status == TestingFarmResult.new
    assert a_new_test_run_pr.get_project().namespace == "the-namespace"
    assert a_new_test_run_pr.get_project().repo_name == "the-repo-name"


def test_tmt_test_run_get_copr_build(
    clean_before_and_after,
    a_copr_build_for_pr,
    a_new_test_run_pr,
):
    assert len(a_new_test_run_pr.group_of_targets.runs) == 1
    assert (
        a_new_test_run_pr.group_of_targets.runs[0].copr_build_group.grouped_targets[0]
        == a_copr_build_for_pr
    )


def test_tmt_test_run_get_pr_id(clean_before_and_after, a_new_test_run_pr):
    assert a_new_test_run_pr.status == TestingFarmResult.new
    assert a_new_test_run_pr.get_pr_id() == 342


def test_tmt_test_run_set_web_url(
    clean_before_and_after,
    srpm_build_model_with_new_run_for_pr,
):
    _, run_model = srpm_build_model_with_new_run_for_pr
    group = TFTTestRunGroupModel.create(run_models=[run_model], ranch="public")
    test_run_model = TFTTestRunTargetModel.create(
        pipeline_id="123456",
        target=SampleValues.target,
        status=TestingFarmResult.new,
        test_run_group=group,
    )
    assert not test_run_model.web_url
    new_url = (
        "https://console-testing-farm.apps.ci.centos.org/"
        "pipeline/02271aa8-2917-4741-a39e-78d8706c56c1"
    )
    test_run_model.set_web_url(new_url)
    assert test_run_model.web_url == new_url

    test_run_for_pipeline_id = TFTTestRunTargetModel.get_by_pipeline_id(
        test_run_model.pipeline_id,
    )
    assert test_run_for_pipeline_id
    assert test_run_for_pipeline_id.web_url == new_url


def test_tmt_test_get_by_pipeline_id_pr(
    clean_before_and_after,
    pr_model,
    srpm_build_model_with_new_run_for_pr,
):
    _, run_model = srpm_build_model_with_new_run_for_pr
    group = TFTTestRunGroupModel.create(run_models=[run_model], ranch="public")
    test_run_model = TFTTestRunTargetModel.create(
        pipeline_id="123456",
        target=SampleValues.target,
        status=TestingFarmResult.new,
        test_run_group=group,
    )

    test_run_for_pipeline_id = TFTTestRunTargetModel.get_by_pipeline_id(
        test_run_model.pipeline_id,
    )
    assert test_run_for_pipeline_id
    assert test_run_for_pipeline_id.get_project_event_object() == pr_model


def test_tmt_test_get_range(clean_before_and_after, multiple_new_test_runs):
    assert multiple_new_test_runs
    results = TFTTestRunTargetModel.get_range(0, 10)
    assert len(list(results)) == 4


def test_tmt_test_get_by_pipeline_id_branch_push(
    clean_before_and_after,
    branch_model,
    srpm_build_model_with_new_run_and_tf_for_branch,
    a_copr_build_for_branch_push,
):
    _, tf_group_model, run_model = srpm_build_model_with_new_run_and_tf_for_branch
    test_run_model = TFTTestRunTargetModel.create(
        pipeline_id="123456",
        target=SampleValues.target,
        status=TestingFarmResult.new,
        test_run_group=tf_group_model,
    )

    test_run = TFTTestRunTargetModel.get_by_pipeline_id(test_run_model.pipeline_id)
    assert test_run
    assert test_run.get_project_event_object() == branch_model


def test_tmt_test_get_by_pipeline_id_release(
    clean_before_and_after,
    release_model,
    srpm_build_model_with_new_run_and_tf_for_release,
    a_copr_build_for_release,
):
    _, tf_group_model, run_model = srpm_build_model_with_new_run_and_tf_for_release
    test_run_model = TFTTestRunTargetModel.create(
        pipeline_id="123456",
        target=SampleValues.target,
        status=TestingFarmResult.new,
        test_run_group=tf_group_model,
    )

    test_run = TFTTestRunTargetModel.get_by_pipeline_id(test_run_model.pipeline_id)
    assert test_run
    assert test_run.get_project_event_object() == release_model


def test_pr_id_property_for_srpm_build(srpm_build_model_with_new_run_for_pr):
    srpm_build, _ = srpm_build_model_with_new_run_for_pr
    project_pr = srpm_build.get_pr_id()
    assert isinstance(project_pr, int)


def test_package_name_for_srpm_build(srpm_build_model_with_new_run_for_pr):
    srpm_build, _ = srpm_build_model_with_new_run_for_pr
    assert srpm_build.get_package_name() == "a-package-name"


def test_project_property_for_srpm_build(srpm_build_model_with_new_run_for_pr):
    srpm_build, _ = srpm_build_model_with_new_run_for_pr
    project = srpm_build.get_project()
    assert isinstance(project, GitProjectModel)
    assert project.namespace == "the-namespace"
    assert project.repo_name == "the-repo-name"


def test_package_name_for_copr_build(a_copr_build_for_pr):
    assert a_copr_build_for_pr.get_package_name() == "a-package-name"


def test_project_property_for_copr_build(a_copr_build_for_pr):
    project = a_copr_build_for_pr.get_project()
    assert isinstance(project, GitProjectModel)
    assert project.namespace == "the-namespace"
    assert project.repo_name == "the-repo-name"


def test_get_projects(clean_before_and_after, a_copr_build_for_pr):
    projects = GitProjectModel.get_range(0, 10)
    assert isinstance(projects[0], GitProjectModel)
    assert projects[0].namespace == "the-namespace"
    assert projects[0].repo_name == "the-repo-name"
    assert projects[0].project_url == "https://github.com/the-namespace/the-repo-name"


def test_get_project(clean_before_and_after, a_copr_build_for_pr):
    project = GitProjectModel.get_project(
        "github.com",
        "the-namespace",
        "the-repo-name",
    )
    assert project.namespace == "the-namespace"
    assert project.repo_name == "the-repo-name"
    assert project.project_url == "https://github.com/the-namespace/the-repo-name"


def test_get_by_forge(clean_before_and_after, multiple_forge_projects):
    projects = list(GitProjectModel.get_by_forge(0, 10, "github.com"))
    assert projects
    assert len(projects) == 2

    projects = list(GitProjectModel.get_by_forge(0, 10, "gitlab.com"))
    assert len(projects) == 1

    projects = list(GitProjectModel.get_by_forge(0, 10, "git.stg.centos.org"))
    assert len(projects) == 1


def test_get_by_forge_namespace(clean_before_and_after, multiple_copr_builds):
    projects = list(
        GitProjectModel.get_by_forge_namespace(0, 10, "github.com", "the-namespace"),
    )
    assert projects[0].namespace == "the-namespace"
    assert projects[0].repo_name == "the-repo-name"


def test_get_project_prs(clean_before_and_after, a_copr_build_for_pr):
    prs_a = list(
        GitProjectModel.get_project_prs(
            0,
            10,
            "github.com",
            "the-namespace",
            "the-repo-name",
        ),
    )
    assert prs_a
    assert len(prs_a) == 1
    assert prs_a[0].id is not None  # cant explicitly check because its random like
    prs_b = list(
        GitProjectModel.get_project_prs(
            0,
            10,
            "gitlab.com",
            "the-namespace",
            "the-repo-name",
        ),
    )
    assert prs_b == []
    prs_c = list(
        GitProjectModel.get_project_prs(
            0,
            10,
            "github",
            "the-namespace",
            "the-repo-name",
        ),
    )
    assert prs_c == []


def test_get_project_branch(clean_before_and_after, a_copr_build_for_branch_push):
    branches_list = list(
        GitProjectModel.get_project_branches(
            0,
            10,
            "github.com",
            "the-namespace",
            "the-repo-name",
        ),
    )
    assert len(branches_list) == 1
    assert branches_list[0].name == "build-branch"


def test_get_project_issues(clean_before_and_after, an_issue_model):
    issues_list = list(
        GitProjectModel.get_project_issues(
            0,
            10,
            "github.com",
            "the-namespace",
            "the-repo-name",
        ),
    )
    assert len(issues_list) == 1
    assert issues_list[0].issue_id == 2020


def test_get_project_releases(clean_before_and_after, release_model):
    releases = list(
        GitProjectModel.get_project_releases(
            0,
            10,
            "github.com",
            "the-namespace",
            "the-repo-name",
        ),
    )
    assert releases[0].tag_name == "v1.0.2"
    assert releases[0].commit_hash == "80201a74d96c"
    assert len(releases) == 1


def test_project_property_for_koji_build(a_koji_build_for_pr):
    project = a_koji_build_for_pr.get_project()
    assert isinstance(project, GitProjectModel)
    assert project.namespace == "the-namespace"
    assert project.repo_name == "the-repo-name"


def test_get_installations(clean_before_and_after, multiple_installation_entries):
    results = list(GithubInstallationModel.get_all())
    assert len(results) == 2


def test_get_installation_by_account(
    clean_before_and_after,
    multiple_installation_entries,
):
    assert GithubInstallationModel.get_by_account_login("teg").sender_login == "teg"
    assert GithubInstallationModel.get_by_account_login("Pac23").sender_login == "Pac23"


def test_pr_get_copr_builds(
    clean_before_and_after,
    a_copr_build_for_pr,
    different_pr_model,
    a_copr_build_for_branch_push,
):
    pr_model = a_copr_build_for_pr.get_project_event_object()
    copr_builds = pr_model.get_copr_builds()
    assert a_copr_build_for_pr in copr_builds
    assert len(copr_builds) == 1
    assert not different_pr_model.get_copr_builds()


def test_pr_multiple_commits_copr_builds(
    clean_before_and_after,
    a_copr_build_for_pr,
    a_copr_build_for_pr_different_commit,
):
    pr_model = a_copr_build_for_pr_different_commit.get_project_event_object()
    copr_builds = pr_model.get_copr_builds()
    assert a_copr_build_for_pr in copr_builds
    assert a_copr_build_for_pr_different_commit in copr_builds
    assert len(copr_builds) == 2


def test_pr_get_koji_builds(
    clean_before_and_after,
    a_koji_build_for_pr,
    different_pr_model,
):
    pr_model = a_koji_build_for_pr.get_project_event_object()
    assert a_koji_build_for_pr in pr_model.get_koji_builds()
    assert not different_pr_model.get_koji_builds()


def test_pr_get_srpm_builds(
    clean_before_and_after,
    srpm_build_model_with_new_run_for_pr,
    a_copr_build_for_pr,
):
    srpm_build_model, _ = srpm_build_model_with_new_run_for_pr
    pr_model = a_copr_build_for_pr.get_project_event_object()
    assert srpm_build_model in pr_model.get_srpm_builds()


def test_project_token_model(clean_before_and_after):
    namespace = "the-namespace"
    repo = "repo-name"
    http_url = "https://gitlab.com/the-namespace/repo-name"

    actual = ProjectAuthenticationIssueModel.create(
        namespace=namespace,
        repo_name=repo,
        project_url=http_url,
        issue_created=True,
    )
    expected = ProjectAuthenticationIssueModel.get_project(
        namespace=namespace,
        repo_name=repo,
        project_url=http_url,
    )
    assert actual.issue_created == expected.issue_created


def test_merged_runs(clean_before_and_after, few_runs):
    for _i, run_id in enumerate(few_runs, 1):
        merged_run = PipelineModel.get_merged_run(run_id)
        srpm_build_id = merged_run.srpm_build_id

        # Since the introduction of build groups, the builds are grouped
        assert len(merged_run.copr_build_group_id) == 1
        assert len(merged_run.copr_build_group_id[0]) == 1
        build_group = CoprBuildGroupModel.get_by_id(
            merged_run.copr_build_group_id[0][0],
        )

        for copr_build in build_group.grouped_targets:
            assert copr_build.get_srpm_build().id == srpm_build_id

        assert len(merged_run.test_run_group_id) == 1


def test_merged_chroots_on_tests_without_build(
    clean_before_and_after,
    runs_without_build,
):
    result = list(PipelineModel.get_merged_chroots(0, 10))
    assert len(result) == 2
    for item in result:
        assert len(item.test_run_group_id[0]) == 1


def test_tf_get_all_by_commit_target(clean_before_and_after, multiple_new_test_runs):
    test_list = list(
        TFTTestRunTargetModel.get_all_by_commit_target(
            commit_sha=SampleValues.commit_sha,
            target=SampleValues.target,
        ),
    )
    assert len(test_list) == 1
    assert test_list[0].commit_sha == SampleValues.commit_sha

    # test without target
    test_list = list(
        TFTTestRunTargetModel.get_all_by_commit_target(
            commit_sha=SampleValues.commit_sha,
        ),
    )
    assert len(test_list) == 3
    assert (
        test_list[0].commit_sha
        == test_list[1].commit_sha
        == test_list[2].commit_sha
        == SampleValues.commit_sha
    )


def test_create_propose_model(clean_before_and_after, propose_model):
    assert propose_model.status == SyncReleaseTargetStatus.running
    # test if submitted time is something - datetime
    assert isinstance(propose_model.submitted_time, datetime)


def test_set_propose_model_attributes(clean_before_and_after, propose_model):
    propose_model.set_status(status=SyncReleaseTargetStatus.submitted)
    assert propose_model.status == SyncReleaseTargetStatus.submitted

    propose_model.set_downstream_pr_url(downstream_pr_url="not_for_kids")
    assert propose_model.downstream_pr_url == "not_for_kids"

    now = datetime.utcnow()
    propose_model.set_finished_time(finished_time=now)
    assert propose_model.finished_time == now

    propose_model.set_start_time(start_time=now)
    assert propose_model.start_time == now

    propose_model.set_logs(logs="omg secret logs! don't read this!")
    assert propose_model.logs == "omg secret logs! don't read this!"


def test_propose_model_get_by_id(clean_before_and_after, propose_model):
    assert propose_model.id

    model = SyncReleaseTargetModel.get_by_id(id_=propose_model.id)
    assert model.id == propose_model.id


def test_create_propose_downstream_model(
    clean_before_and_after,
    propose_downstream_model_release,
):
    assert propose_downstream_model_release.status == SyncReleaseStatus.running
    # test if submitted time is something - datetime
    assert isinstance(propose_downstream_model_release.submitted_time, datetime)


def test_set_propose_downstream_model_status(
    clean_before_and_after,
    propose_downstream_model_release,
):
    propose_downstream_model_release.set_status(SyncReleaseStatus.finished)
    assert propose_downstream_model_release.status == SyncReleaseStatus.finished


def test_get_propose_downstream_model_by_id(
    clean_before_and_after,
    propose_downstream_model_release,
):
    assert propose_downstream_model_release.id

    model = SyncReleaseModel.get_by_id(id_=propose_downstream_model_release.id)
    assert model.id == propose_downstream_model_release.id


def test_get_propose_downstream_model_by_status(
    clean_before_and_after,
    multiple_propose_downstream_runs_release_trigger,
):
    assert multiple_propose_downstream_runs_release_trigger

    propose_downstream_list = list(
        SyncReleaseModel.get_all_by_status(status=SyncReleaseStatus.running),
    )
    assert len(propose_downstream_list) == 2
    assert (
        propose_downstream_list[0].status
        == propose_downstream_list[1].status
        == SyncReleaseStatus.running
    )


def test_get_propose_downstream_model_range(
    clean_before_and_after,
    multiple_propose_downstream_runs_release_trigger,
):
    assert multiple_propose_downstream_runs_release_trigger

    propose_downstream_list = list(
        SyncReleaseModel.get_range(
            first=0,
            last=10,
            job_type=SyncReleaseJobType.propose_downstream,
        ),
    )
    assert len(propose_downstream_list) == 4


def test_sourcegit_distgit_pr_relationship(clean_before_and_after):
    source_git_pr_id = 8
    source_git_namespace = "mmassari"
    source_git_repo_name = "python-teamcity-messages"
    source_git_project_url = "https://gitlab.com/mmassari/python-teamcity-messages"
    dist_git_pr_id = 31
    dist_git_namespace = "packit/rpms"
    dist_git_repo_name = "python-teamcity-messages"
    dist_git_project_url = "https://src.fedoraproject.org/fork/packit/rpms/python-teamcity-messages"

    created = SourceGitPRDistGitPRModel.get_or_create(
        source_git_pr_id,
        source_git_namespace,
        source_git_repo_name,
        source_git_project_url,
        dist_git_pr_id,
        dist_git_namespace,
        dist_git_repo_name,
        dist_git_project_url,
    )

    found = SourceGitPRDistGitPRModel.get_or_create(
        source_git_pr_id,
        source_git_namespace,
        source_git_repo_name,
        source_git_project_url,
        dist_git_pr_id,
        dist_git_namespace,
        dist_git_repo_name,
        dist_git_project_url,
    )

    assert created.id == found.id

    with pytest.raises(IntegrityError) as _:
        SourceGitPRDistGitPRModel.get_or_create(
            source_git_pr_id + 1,
            source_git_namespace,
            source_git_repo_name,
            source_git_project_url,
            dist_git_pr_id,
            dist_git_namespace,
            dist_git_repo_name,
            dist_git_project_url,
        )


def test_get_source_git_dist_git_pr_relationship(
    clean_before_and_after,
    source_git_dist_git_pr_new_relationship,
):
    assert source_git_dist_git_pr_new_relationship.id
    assert SourceGitPRDistGitPRModel.get_by_id(
        source_git_dist_git_pr_new_relationship.id,
    )


def test_get_by_source_git_id(
    clean_before_and_after,
    source_git_dist_git_pr_new_relationship,
):
    assert source_git_dist_git_pr_new_relationship.source_git_pull_request_id
    assert SourceGitPRDistGitPRModel.get_by_source_git_id(
        source_git_dist_git_pr_new_relationship.source_git_pull_request_id,
    )


def test_get_by_dist_git_id(
    clean_before_and_after,
    source_git_dist_git_pr_new_relationship,
):
    assert source_git_dist_git_pr_new_relationship.dist_git_pull_request_id
    assert SourceGitPRDistGitPRModel.get_by_dist_git_id(
        source_git_dist_git_pr_new_relationship.dist_git_pull_request_id,
    )


def test_get_all_downstream_projects(clean_before_and_after, propose_model_submitted):
    projects = SyncReleaseTargetModel.get_all_downstream_projects()
    assert len(projects) == 1
    assert projects.pop().project_url == SampleValues.downstream_project_url


def test_project_event_get_and_reset_older_than_with_packages_config(
    clean_before_and_after,
    branch_project_event_model,
):
    branch_project_event_model.set_packages_config({"key": "value"})
    run1 = PipelineModel.create(project_event=branch_project_event_model)
    run1.datetime = datetime(2024, 4, 8, 12, 0, 0)

    assert (
        len(
            list(
                ProjectEventModel.get_and_reset_older_than_with_packages_config(
                    timedelta(days=1),
                ),
            ),
        )
        == 1
    )

    # default datetime = now
    PipelineModel.create(project_event=branch_project_event_model)

    assert (
        len(
            list(
                ProjectEventModel.get_and_reset_older_than_with_packages_config(
                    timedelta(days=1),
                ),
            ),
        )
        == 0
    )


def test_create_scan(clean_before_and_after, a_scan):
    assert a_scan.task_id == 123
    assert a_scan.status == "succeeded"
    assert a_scan.url == "https://scan-url"
    assert a_scan.issues_added_url == "https://issues-added-url"
    assert a_scan.issues_fixed_url == "https://issues-fixed-url"
    assert a_scan.scan_results_url == "https://scan-results-url"
    assert a_scan.copr_build_target.build_id == "123456"


def test_add_scan_to_copr_build(clean_before_and_after, a_copr_build_for_pr):
    a_copr_build_for_pr.add_scan(123)
    scan = OSHScanModel.get_by_task_id(123)
    assert scan.task_id == 123


def test_bodhi_model_get_last_successful_by_sidetag(
    clean_before_and_after, successful_bodhi_update_model
):
    assert successful_bodhi_update_model.id

    model = BodhiUpdateTargetModel.get_last_successful_by_sidetag(SampleValues.sidetag)
    assert model.id == successful_bodhi_update_model.id


def test_bodhi_model_get_all_successful_or_in_progress_by_nvrs(
    clean_before_and_after, successful_bodhi_update_model
):
    assert successful_bodhi_update_model.id

    [model] = BodhiUpdateTargetModel.get_all_successful_or_in_progress_by_nvrs(SampleValues.nvr)
    assert model.id == successful_bodhi_update_model.id


def test_create_koji_tag_request(clean_before_and_after, a_koji_tag_request):
    assert a_koji_tag_request.task_id == SampleValues.build_id
    assert a_koji_tag_request.web_url == SampleValues.koji_web_url
    assert a_koji_tag_request.target == SampleValues.target
    assert a_koji_tag_request.sidetag == SampleValues.sidetag
    assert a_koji_tag_request.nvr == SampleValues.nvr
    assert a_koji_tag_request.get_project().project_url == SampleValues.project_url


def test_copr_get_running(clean_before_and_after, pr_model, srpm_build_model_with_new_run_for_pr):
    _, run_model = srpm_build_model_with_new_run_for_pr
    group = CoprBuildGroupModel.create(run_model=run_model)

    for build_id, target, status in (
        ("1", "fedora-rawhide-x86_64", BuildStatus.pending),
        ("2", "fedora-42-aarch64", BuildStatus.waiting_for_srpm),
        ("2", "fedora-42-x86_64", BuildStatus.waiting_for_srpm),
        ("3", "opensuse-tumbleweed-x86_64", BuildStatus.success),
    ):
        CoprBuildTargetModel.create(
            build_id=build_id,
            project_name="something",
            owner="hello",
            web_url=None,
            target=target,
            status=status,
            copr_build_group=group,
        )

    running = list(CoprBuildGroupModel.get_running(commit_sha=SampleValues.commit_sha))
    assert running, "There are some running builds present"
    assert len(running) == 3, "There are exactly 3 builds running"
    assert {build.build_id for (build,) in running} == {"1", "2"}, (
        "Exactly ‹1› and ‹2› are in the running state"
    )


def test_tmt_get_running(clean_before_and_after, pr_model, srpm_build_model_with_new_run_for_pr):
    _, run_model = srpm_build_model_with_new_run_for_pr
    group = TFTTestRunGroupModel.create(run_models=[run_model], ranch="public")

    for pipeline_id, target, status in (
        ("deadbeef", "fedora-rawhide-x86_64", TestingFarmResult.new),
        ("cafe", "fedora-42-aarch64", TestingFarmResult.queued),
        ("42", "opensuse-tumbleweed-x86_64", TestingFarmResult.running),
        ("4269", "opensuse-leap-42.2-x86_64", TestingFarmResult.complete),
    ):
        TFTTestRunTargetModel.create(
            pipeline_id=pipeline_id,
            status=status,
            target=target,
            test_run_group=group,
        )

    running = list(
        TFTTestRunGroupModel.get_running(commit_sha=SampleValues.commit_sha, ranch="public")
    )
    assert running, "There are some running tests present"
    assert len(running) == 2, "There are exactly 2 tests running"
    assert {test_run.pipeline_id for (test_run,) in running} == {"cafe", "42"}, (
        "Test runs created by the test are in the running state"
    )


def test_tmt_get_running_different_ranches(
    clean_before_and_after, pr_model, srpm_build_model_with_new_run_for_pr
):
    _, run_model = srpm_build_model_with_new_run_for_pr

    public_group = TFTTestRunGroupModel.create(run_models=[run_model], ranch="public")
    redhat_group = TFTTestRunGroupModel.create(run_models=[run_model], ranch="redhat")

    # run tests in the public and internal ranch
    for pipeline_id, target, status in (
        ("deadbeef", "fedora-rawhide-x86_64", TestingFarmResult.new),
        ("cafe", "fedora-42-aarch64", TestingFarmResult.queued),
        ("42", "opensuse-tumbleweed-x86_64", TestingFarmResult.running),
        ("4269", "opensuse-leap-42.2-x86_64", TestingFarmResult.complete),
    ):
        TFTTestRunTargetModel.create(
            pipeline_id=pipeline_id,
            status=status,
            target=target,
            test_run_group=public_group,
        )

        TFTTestRunTargetModel.create(
            pipeline_id=f"{pipeline_id}-internal",
            status=status,
            target=target,
            test_run_group=redhat_group,
        )

    running = list(
        TFTTestRunGroupModel.get_running(commit_sha=SampleValues.commit_sha, ranch="public")
    )
    assert running, "There are some running tests present"
    assert len(running) == 2, "There are exactly 2 tests running in the public ranch"
    assert {test_run.pipeline_id for (test_run,) in running} == {"cafe", "42"}, (
        "Test runs created by the test are in the running state"
    )

    running = list(
        TFTTestRunGroupModel.get_running(commit_sha=SampleValues.commit_sha, ranch="redhat")
    )
    assert running, "There are some running tests present"
    assert len(running) == 2, "There are exactly 2 tests running in the redhat ranch"
    assert {test_run.pipeline_id for (test_run,) in running} == {"cafe-internal", "42-internal"}, (
        "Test runs created by the test are in the running state"
    )
