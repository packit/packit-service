# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from datetime import datetime, timedelta

from sqlalchemy.exc import ProgrammingError

from packit_service.models import (
    BugzillaModel,
    CoprBuildModel,
    GitBranchModel,
    GitProjectModel,
    InstallationModel,
    JobTriggerModelType,
    KojiBuildModel,
    ProjectAuthenticationIssueModel,
    ProjectReleaseModel,
    PullRequestModel,
    SRPMBuildModel,
    TFTTestRunModel,
    TestingFarmResult,
    get_sa_session,
    PipelineModel,
)
from tests_requre.conftest import SampleValues


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


def test_create_pr_trigger_model(clean_before_and_after, pr_trigger_model):
    assert pr_trigger_model.type == JobTriggerModelType.pull_request
    pr = pr_trigger_model.get_trigger_object()
    assert isinstance(pr, PullRequestModel)
    assert pr.pr_id == 342


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


def test_create_copr_build(clean_before_and_after, a_copr_build_for_pr):
    assert a_copr_build_for_pr.build_id == "123456"
    assert a_copr_build_for_pr.commit_sha == "80201a74d96c"
    assert a_copr_build_for_pr.project_name == "the-project-name"
    assert a_copr_build_for_pr.owner == "the-owner"
    assert a_copr_build_for_pr.web_url == "https://copr.something.somewhere/123456"
    assert a_copr_build_for_pr.get_srpm_build().logs == "some\nboring\nlogs"
    assert a_copr_build_for_pr.target == "fedora-42-x86_64"
    assert a_copr_build_for_pr.status == "pending"
    # Since datetime.utcnow() will return different results in every time its called,
    # we will check if a_copr_build has build_submitted_time value that's within the past hour
    time_last_hour = datetime.utcnow() - timedelta(hours=1)
    assert a_copr_build_for_pr.build_submitted_time > time_last_hour
    a_copr_build_for_pr.set_end_time(None)
    assert a_copr_build_for_pr.build_finished_time is None


def test_copr_build_get_pr_id(
    clean_before_and_after, copr_builds_with_different_triggers
):
    assert copr_builds_with_different_triggers[0].get_pr_id() == 342
    assert not copr_builds_with_different_triggers[1].get_pr_id()
    assert not copr_builds_with_different_triggers[2].get_pr_id()


def test_copr_build_get_branch(
    clean_before_and_after, copr_builds_with_different_triggers
):
    assert not copr_builds_with_different_triggers[0].get_branch_name()
    assert copr_builds_with_different_triggers[1].get_branch_name() == "build-branch"
    assert not copr_builds_with_different_triggers[2].get_branch_name()


def test_get_merged_chroots(clean_before_and_after, too_many_copr_builds):
    # fetch 10 merged groups of builds
    builds_list = list(CoprBuildModel.get_merged_chroots(10, 20))
    assert len(builds_list) == 10
    # two merged chroots so two statuses
    assert len(builds_list[0].status) == 2
    assert len(builds_list[0].target) == 2

    # check that IDs are different
    assert (
        builds_list[0].packit_id_per_chroot[0][0]
        != builds_list[0].packit_id_per_chroot[1][0]
    )

    assert builds_list[1].status[0][0] == "success"
    assert builds_list[2].target[0][0] == "fedora-42-x86_64"


def test_get_copr_build(clean_before_and_after, a_copr_build_for_pr):
    assert a_copr_build_for_pr.id

    # pass in a build_id and a target
    b = CoprBuildModel.get_by_build_id(
        a_copr_build_for_pr.build_id, SampleValues.target
    )
    assert b.id == a_copr_build_for_pr.id
    # let's make sure passing int works as well
    b2 = CoprBuildModel.get_by_build_id(
        int(a_copr_build_for_pr.build_id), SampleValues.target
    )
    assert b2.id == a_copr_build_for_pr.id

    # pass in a build_id and without a target
    b3 = CoprBuildModel.get_by_build_id(a_copr_build_for_pr.build_id, None)
    assert b3.commit_sha == a_copr_build_for_pr.commit_sha

    b4 = CoprBuildModel.get_by_id(b.id)
    assert b4.id == a_copr_build_for_pr.id


def test_copr_build_set_status(clean_before_and_after, a_copr_build_for_pr):
    assert a_copr_build_for_pr.status == "pending"
    a_copr_build_for_pr.set_status("awesome")
    assert a_copr_build_for_pr.status == "awesome"
    b = CoprBuildModel.get_by_build_id(
        a_copr_build_for_pr.build_id, SampleValues.target
    )
    assert b.status == "awesome"


def test_copr_build_set_build_logs_url(clean_before_and_after, a_copr_build_for_pr):
    url = "https://copr.fp.o/logs/12456/build.log"
    a_copr_build_for_pr.set_build_logs_url(url)
    assert a_copr_build_for_pr.build_logs_url == url
    b = CoprBuildModel.get_by_build_id(
        a_copr_build_for_pr.build_id, SampleValues.target
    )
    assert b.build_logs_url == url


def test_create_koji_build(clean_before_and_after, a_koji_build_for_pr):
    assert a_koji_build_for_pr.build_id == "123456"
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
    b = KojiBuildModel.get_by_build_id(
        a_koji_build_for_pr.build_id, SampleValues.target
    )
    assert b.id == a_koji_build_for_pr.id
    # let's make sure passing int works as well
    b = KojiBuildModel.get_by_build_id(
        int(a_koji_build_for_pr.build_id), SampleValues.target
    )
    assert b.id == a_koji_build_for_pr.id
    b2 = KojiBuildModel.get_by_id(b.id)
    assert b2.id == a_koji_build_for_pr.id


def test_koji_build_set_status(clean_before_and_after, a_koji_build_for_pr):
    assert a_koji_build_for_pr.status == "pending"
    a_koji_build_for_pr.set_status("awesome")
    assert a_koji_build_for_pr.status == "awesome"
    b = KojiBuildModel.get_by_build_id(
        a_koji_build_for_pr.build_id, SampleValues.target
    )
    assert b.status == "awesome"


def test_koji_build_set_build_logs_url(clean_before_and_after, a_koji_build_for_pr):
    url = (
        "https://kojipkgs.fedoraproject.org//"
        "packages/python-ogr/0.11.0/1.fc30/data/logs/noarch/build.log"
    )
    a_koji_build_for_pr.set_build_logs_url(url)
    assert a_koji_build_for_pr.build_logs_url == url
    b = KojiBuildModel.get_by_build_id(
        a_koji_build_for_pr.build_id, SampleValues.target
    )
    assert b.build_logs_url == url


def test_get_or_create_pr(clean_before_and_after):
    with get_sa_session() as session:
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
    with get_sa_session() as session:
        try:
            PullRequestModel.get_or_create(
                pr_id="nope",
                namespace="",
                repo_name=False,
                project_url="https://github.com/the-namespace/the-repo",
            )
        except ProgrammingError:
            pass
        assert len(session.query(PullRequestModel).all()) == 0
        PullRequestModel.get_or_create(
            pr_id=111,
            namespace="asd",
            repo_name="qwe",
            project_url="https://github.com/asd/qwe",
        )
        assert len(session.query(PullRequestModel).all()) == 1


def test_get_srpm_builds_in_give_range(
    clean_before_and_after, srpm_build_model_with_new_run_for_pr
):
    builds_list = list(SRPMBuildModel.get(0, 10))
    assert len(builds_list) == 1
    assert builds_list[0].status == "success"


def test_get_all_builds(clean_before_and_after, multiple_copr_builds):
    builds_list = list(CoprBuildModel.get_all())
    assert len({builds_list[i].id for i in range(4)})
    # All builds has to have exactly one PipelineModel connected
    assert all(len(build.runs) == 1 for build in builds_list)
    # All builds has to have a different PipelineModel connected.
    assert len({build.runs[0] for build in builds_list}) == 4


def test_get_all_build_id(clean_before_and_after, multiple_copr_builds):
    builds_list = list(CoprBuildModel.get_all_by_build_id(str(123456)))
    assert len(builds_list) == 2
    # both should have the same project_name
    assert builds_list[1].project_name == builds_list[0].project_name
    assert builds_list[1].project_name == "the-project-name"


# returns the first copr build with given build id and target
def test_get_by_build_id(clean_before_and_after, multiple_copr_builds):
    # these are not iterable and thus should be accessible directly
    build_a = CoprBuildModel.get_by_build_id(SampleValues.build_id, SampleValues.target)
    assert build_a.project_name == "the-project-name"
    assert build_a.target == "fedora-42-x86_64"

    build_b = CoprBuildModel.get_by_build_id(
        SampleValues.build_id, SampleValues.different_target
    )
    assert build_b.project_name == "the-project-name"
    assert build_b.target == "fedora-43-x86_64"

    build_c = CoprBuildModel.get_by_build_id(
        SampleValues.another_different_build_id, SampleValues.target
    )
    assert build_c.project_name == "different-project-name"


def test_copr_get_all_by_owner_project_commit_target(
    clean_before_and_after, multiple_copr_builds
):
    builds_list = list(
        CoprBuildModel.get_all_by(
            owner=SampleValues.owner,
            project_name=SampleValues.project,
            target=SampleValues.target,
            commit_sha=SampleValues.ref,
        )
    )
    assert len(builds_list) == 2
    # both should have the same project_name
    assert (
        builds_list[1].project_name
        == builds_list[0].project_name
        == SampleValues.project
    )

    # test without target and owner
    builds_list_without_target = list(
        CoprBuildModel.get_all_by(
            project_name=SampleValues.project,
            commit_sha=SampleValues.ref,
        )
    )
    assert len(builds_list_without_target) == 3
    assert (
        builds_list_without_target[0].commit_sha
        == builds_list_without_target[1].commit_sha
        == builds_list_without_target[2].commit_sha
        == SampleValues.ref
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
    # SRPMBuildModel is (sadly) not shared between Koji and Copr builds.
    srpm_build_for_copr, run_model_for_copr = SRPMBuildModel.create_with_new_run(
        trigger_model=pr1, commit_sha="687abc76d67d"
    )
    srpm_build_for_copr.set_logs("asd\nqwe\n")
    srpm_build_for_copr.set_status("success")

    srpm_build_for_koji, run_model_for_koji = SRPMBuildModel.create_with_new_run(
        trigger_model=pr1, commit_sha="687abc76d67d"
    )
    srpm_build_for_copr.set_logs("asd\nqwe\n")
    srpm_build_for_copr.set_status("success")

    copr_build = CoprBuildModel.create(
        build_id="123456",
        commit_sha="687abc76d67d",
        project_name="SomeUser-hello-world-9",
        owner="packit",
        web_url="https://copr.something.somewhere/123456",
        target=SampleValues.target,
        status="pending",
        run_model=run_model_for_copr,
    )
    koji_build = KojiBuildModel.create(
        build_id="987654",
        commit_sha="687abc76d67d",
        web_url="https://copr.something.somewhere/123456",
        target=SampleValues.target,
        status="pending",
        run_model=run_model_for_koji,
    )

    assert copr_build in pr1.get_copr_builds()
    assert koji_build in pr1.get_koji_builds()

    assert srpm_build_for_copr in pr1.get_srpm_builds()
    assert srpm_build_for_koji in pr1.get_srpm_builds()

    assert copr_build.get_job_trigger_model() == koji_build.get_job_trigger_model()

    assert srpm_build_for_copr.get_trigger_object() == pr1
    assert srpm_build_for_koji.get_trigger_object() == pr1
    assert copr_build.get_trigger_object() == pr1
    assert koji_build.get_trigger_object() == pr1

    assert len(koji_build.runs) == 1
    assert koji_build.runs[0] == run_model_for_koji
    assert len(copr_build.runs) == 1
    assert copr_build.runs[0] == run_model_for_copr


def test_tmt_test_run(clean_before_and_after, a_new_test_run_pr):
    assert a_new_test_run_pr.pipeline_id == "123456"
    assert a_new_test_run_pr.commit_sha == "80201a74d96c"
    assert (
        a_new_test_run_pr.web_url == "https://console-testing-farm.apps.ci.centos.org/"
        "pipeline/02271aa8-2917-4741-a39e-78d8706c56c1"
    )
    assert a_new_test_run_pr.target == "fedora-42-x86_64"
    assert a_new_test_run_pr.status == TestingFarmResult.new

    b = TFTTestRunModel.get_by_pipeline_id(a_new_test_run_pr.pipeline_id)
    assert b
    assert b.id == a_new_test_run_pr.id


def test_tmt_test_multiple_runs(clean_before_and_after, multiple_new_test_runs):
    assert multiple_new_test_runs
    assert multiple_new_test_runs[0].pipeline_id == SampleValues.pipeline_id
    assert multiple_new_test_runs[1].pipeline_id == SampleValues.different_pipeline_id

    with get_sa_session() as session:
        test_runs = session.query(TFTTestRunModel).all()
        assert len(test_runs) == 4
        # Separate PipelineModel for each TFTTestRunModel
        assert len({m.runs[0] for m in multiple_new_test_runs}) == 4
        # Exactly one PipelineModel for each TFTTestRunModel
        assert all(len(m.runs) == 1 for m in multiple_new_test_runs)
        # Two JobTriggerModels:
        assert len({m.get_trigger_object() for m in multiple_new_test_runs}) == 2


def test_tmt_test_run_set_status(clean_before_and_after, a_new_test_run_pr):
    assert a_new_test_run_pr.status == TestingFarmResult.new
    a_new_test_run_pr.set_status(TestingFarmResult.running)
    assert a_new_test_run_pr.status == TestingFarmResult.running

    b = TFTTestRunModel.get_by_pipeline_id(a_new_test_run_pr.pipeline_id)
    assert b
    assert b.status == TestingFarmResult.running


def test_tmt_test_run_get_project(clean_before_and_after, a_new_test_run_pr):
    assert a_new_test_run_pr.status == TestingFarmResult.new
    assert a_new_test_run_pr.get_project().namespace == "the-namespace"
    assert a_new_test_run_pr.get_project().repo_name == "the-repo-name"


def test_tmt_test_run_get_copr_build(
    clean_before_and_after, a_copr_build_for_pr, a_new_test_run_pr
):
    assert len(a_new_test_run_pr.runs) == 1
    assert a_new_test_run_pr.runs[0].copr_build == a_copr_build_for_pr


def test_tmt_test_run_get_pr_id(clean_before_and_after, a_new_test_run_pr):
    assert a_new_test_run_pr.status == TestingFarmResult.new
    assert a_new_test_run_pr.get_pr_id() == 342


def test_tmt_test_run_set_web_url(
    clean_before_and_after, srpm_build_model_with_new_run_for_pr
):
    _, run_model = srpm_build_model_with_new_run_for_pr
    test_run_model = TFTTestRunModel.create(
        pipeline_id="123456",
        commit_sha="687abc76d67d",
        target=SampleValues.target,
        status=TestingFarmResult.new,
        run_model=run_model,
    )
    assert not test_run_model.web_url
    new_url = (
        "https://console-testing-farm.apps.ci.centos.org/"
        "pipeline/02271aa8-2917-4741-a39e-78d8706c56c1"
    )
    test_run_model.set_web_url(new_url)
    assert test_run_model.web_url == new_url

    test_run_for_pipeline_id = TFTTestRunModel.get_by_pipeline_id(
        test_run_model.pipeline_id
    )
    assert test_run_for_pipeline_id
    assert test_run_for_pipeline_id.web_url == new_url


def test_tmt_test_get_by_pipeline_id_pr(
    clean_before_and_after, pr_model, srpm_build_model_with_new_run_for_pr
):
    _, run_model = srpm_build_model_with_new_run_for_pr
    test_run_model = TFTTestRunModel.create(
        pipeline_id="123456",
        commit_sha="687abc76d67d",
        target=SampleValues.target,
        status=TestingFarmResult.new,
        run_model=run_model,
    )

    test_run_for_pipeline_id = TFTTestRunModel.get_by_pipeline_id(
        test_run_model.pipeline_id
    )
    assert test_run_for_pipeline_id
    assert test_run_for_pipeline_id.get_trigger_object() == pr_model


def test_tmt_test_get_range(clean_before_and_after, multiple_new_test_runs):
    assert multiple_new_test_runs
    results = TFTTestRunModel.get_range(0, 10)
    assert len(list(results)) == 4


def test_tmt_test_get_by_pipeline_id_branch_push(
    clean_before_and_after,
    branch_model,
    srpm_build_model_with_new_run_for_branch,
    a_copr_build_for_branch_push,
):
    _, run_model = srpm_build_model_with_new_run_for_branch
    test_run_model = TFTTestRunModel.create(
        pipeline_id="123456",
        commit_sha="687abc76d67d",
        target=SampleValues.target,
        status=TestingFarmResult.new,
        run_model=run_model,
    )

    test_run = TFTTestRunModel.get_by_pipeline_id(test_run_model.pipeline_id)
    assert test_run
    assert test_run.get_trigger_object() == branch_model


def test_tmt_test_get_by_pipeline_id_release(
    clean_before_and_after,
    release_model,
    srpm_build_model_with_new_run_for_release,
    a_copr_build_for_release,
):
    _, run_model = srpm_build_model_with_new_run_for_release
    test_run_model = TFTTestRunModel.create(
        pipeline_id="123456",
        commit_sha="687abc76d67d",
        target=SampleValues.target,
        status=TestingFarmResult.new,
        run_model=run_model,
    )

    test_run = TFTTestRunModel.get_by_pipeline_id(test_run_model.pipeline_id)
    assert test_run
    assert test_run.get_trigger_object() == release_model


def test_pr_id_property_for_srpm_build(srpm_build_model_with_new_run_for_pr):
    srpm_build, _ = srpm_build_model_with_new_run_for_pr
    project_pr = srpm_build.get_pr_id()
    assert isinstance(project_pr, int)


def test_project_property_for_srpm_build(srpm_build_model_with_new_run_for_pr):
    srpm_build, _ = srpm_build_model_with_new_run_for_pr
    project = srpm_build.get_project()
    assert isinstance(project, GitProjectModel)
    assert project.namespace == "the-namespace"
    assert project.repo_name == "the-repo-name"


def test_project_property_for_copr_build(a_copr_build_for_pr):
    project = a_copr_build_for_pr.get_project()
    assert isinstance(project, GitProjectModel)
    assert project.namespace == "the-namespace"
    assert project.repo_name == "the-repo-name"


def test_get_projects(clean_before_and_after, a_copr_build_for_pr):
    projects = GitProjectModel.get_projects(0, 10)
    assert isinstance(projects[0], GitProjectModel)
    assert projects[0].namespace == "the-namespace"
    assert projects[0].repo_name == "the-repo-name"
    assert projects[0].project_url == "https://github.com/the-namespace/the-repo-name"


def test_get_project(clean_before_and_after, a_copr_build_for_pr):
    project = GitProjectModel.get_project(
        "github.com", "the-namespace", "the-repo-name"
    )
    assert project.namespace == "the-namespace"
    assert project.repo_name == "the-repo-name"
    assert project.project_url == "https://github.com/the-namespace/the-repo-name"


def test_get_forge(clean_before_and_after, multiple_forge_projects):
    projects = list(GitProjectModel.get_forge(0, 10, "github.com"))
    assert projects
    assert len(projects) == 2

    projects = list(GitProjectModel.get_forge(0, 10, "gitlab.com"))
    assert len(projects) == 1

    projects = list(GitProjectModel.get_forge(0, 10, "git.stg.centos.org"))
    assert len(projects) == 1


def test_get_namespace(clean_before_and_after, multiple_copr_builds):
    projects = GitProjectModel.get_namespace("github.com", "the-namespace")
    assert projects[0].namespace == "the-namespace"
    assert projects[0].repo_name == "the-repo-name"


def test_get_project_prs(clean_before_and_after, a_copr_build_for_pr):
    prs_a = list(
        GitProjectModel.get_project_prs(
            0, 10, "github.com", "the-namespace", "the-repo-name"
        )
    )
    assert prs_a
    assert len(prs_a) == 1
    assert prs_a[0].id is not None  # cant explicitly check because its random like
    prs_b = list(
        GitProjectModel.get_project_prs(
            0, 10, "gitlab.com", "the-namespace", "the-repo-name"
        )
    )
    assert prs_b == []
    prs_c = list(
        GitProjectModel.get_project_prs(
            0, 10, "github", "the-namespace", "the-repo-name"
        )
    )
    assert prs_c == []


def test_get_project_branch(clean_before_and_after, a_copr_build_for_branch_push):
    branches_list = GitProjectModel.get_project_branches(
        "github.com", "the-namespace", "the-repo-name"
    )
    assert len(branches_list) == 1
    assert branches_list[0].name == "build-branch"


def test_get_project_issues(clean_before_and_after, an_issue_model):
    issues_list = GitProjectModel.get_project_issues(
        "github.com", "the-namespace", "the-repo-name"
    )
    assert len(issues_list) == 1
    assert issues_list[0].issue_id == 2020


def test_get_project_releases(clean_before_and_after, release_model):
    releases = GitProjectModel.get_project_releases(
        "github.com", "the-namespace", "the-repo-name"
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
    results = list(InstallationModel.get_all())
    assert len(results) == 2


def test_get_installation_by_account(
    clean_before_and_after, multiple_installation_entries
):
    assert InstallationModel.get_by_account_login("teg").sender_login == "teg"
    assert InstallationModel.get_by_account_login("Pac23").sender_login == "Pac23"


def test_pr_get_copr_builds(
    clean_before_and_after,
    a_copr_build_for_pr,
    different_pr_model,
    a_copr_build_for_branch_push,
):
    pr_model = a_copr_build_for_pr.get_trigger_object()
    copr_builds = pr_model.get_copr_builds()
    assert a_copr_build_for_pr in copr_builds
    assert len(copr_builds) == 1
    assert not different_pr_model.get_copr_builds()


def test_pr_get_koji_builds(
    clean_before_and_after, a_koji_build_for_pr, different_pr_model
):
    pr_model = a_koji_build_for_pr.get_trigger_object()
    assert a_koji_build_for_pr in pr_model.get_koji_builds()
    assert not different_pr_model.get_koji_builds()


def test_pr_get_srpm_builds(
    clean_before_and_after, srpm_build_model_with_new_run_for_pr, a_copr_build_for_pr
):
    srpm_build_model, _ = srpm_build_model_with_new_run_for_pr
    pr_model = a_copr_build_for_pr.get_trigger_object()
    assert srpm_build_model in pr_model.get_srpm_builds()


def test_bugzilla_create(clean_before_and_after, bugzilla_model):
    assert isinstance(bugzilla_model, BugzillaModel)
    assert bugzilla_model.bug_id == SampleValues.bug_id
    assert bugzilla_model.bug_url == SampleValues.bug_url


def test_bugzilla_get_by_id(clean_before_and_after, bugzilla_model):
    bz = BugzillaModel.get_by_pr(
        pr_id=SampleValues.pr_id,
        namespace=SampleValues.repo_namespace,
        repo_name=SampleValues.repo_name,
        project_url=SampleValues.project_url,
    )
    assert bz.bug_id == bugzilla_model.bug_id == SampleValues.bug_id
    assert bz.bug_url == bugzilla_model.bug_url == SampleValues.bug_url


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
    for i, run_id in enumerate(few_runs, 1):
        merged_run = PipelineModel.get_merged_run(run_id)
        srpm_build_id = merged_run.srpm_build_id

        # for different_pr (i=1) there are Copr builds twice, since the second
        # run of TFT from the same Copr build produces new row with same SRPM
        # and Copr IDs, but different Testing Farm IDs
        # ^ handled in API by iterating over set of IDs instead of list
        assert len(merged_run.copr_build_id) == 2 * i

        for copr_build in map(
            lambda ids: CoprBuildModel.get_by_id(ids[0]), merged_run.copr_build_id
        ):
            assert copr_build.get_srpm_build().id == srpm_build_id

        assert len(merged_run.test_run_id) == 2 * i


def test_merged_chroots_on_tests_without_build(
    clean_before_and_after, runs_without_build
):
    result = list(PipelineModel.get_merged_chroots(0, 10))
    assert len(result) == 2
    for item in result:
        assert len(item.test_run_id[0]) == 1


def test_tf_get_all_by_commit_target(clean_before_and_after, multiple_new_test_runs):
    test_list = list(
        TFTTestRunModel.get_all_by_commit_target(
            commit_sha=SampleValues.commit_sha, target=SampleValues.target
        )
    )
    assert len(test_list) == 1
    assert test_list[0].commit_sha == SampleValues.commit_sha

    # test without target
    test_list = list(
        TFTTestRunModel.get_all_by_commit_target(
            commit_sha=SampleValues.commit_sha,
        )
    )
    assert len(test_list) == 3
    assert (
        test_list[0].commit_sha
        == test_list[1].commit_sha
        == test_list[2].commit_sha
        == SampleValues.commit_sha
    )
