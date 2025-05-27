# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

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

import datetime
from typing import ClassVar

import pytest
from ogr import GithubService, GitlabService, PagureService

from packit_service.config import ServiceConfig
from packit_service.events import github
from packit_service.models import (
    AllowlistModel,
    BodhiUpdateGroupModel,
    BodhiUpdateTargetModel,
    BuildStatus,
    CoprBuildGroupModel,
    CoprBuildTargetModel,
    GitBranchModel,
    GithubInstallationModel,
    GitProjectModel,
    IssueModel,
    KojiBuildGroupModel,
    KojiBuildTargetModel,
    KojiTagRequestGroupModel,
    KojiTagRequestTargetModel,
    OSHScanModel,
    OSHScanStatus,
    PipelineModel,
    ProjectAuthenticationIssueModel,
    ProjectEventModel,
    ProjectEventModelType,
    ProjectReleaseModel,
    PullRequestModel,
    SourceGitPRDistGitPRModel,
    SRPMBuildModel,
    SyncReleaseJobType,
    SyncReleaseModel,
    SyncReleasePullRequestModel,
    SyncReleaseStatus,
    SyncReleaseTargetModel,
    SyncReleaseTargetStatus,
    TestingFarmResult,
    TFTTestRunGroupModel,
    TFTTestRunTargetModel,
    sa_session_transaction,
    sync_release_pr_association_table,
)


class SampleValues:
    testing_farm_url = (
        "https://console-testing-farm.apps.ci.centos.org/"
        "pipeline/02271aa8-2917-4741-a39e-78d8706c56c1"
    )
    repo_namespace = "the-namespace"
    repo_name = "the-repo-name"
    different_project_name = "different-project-name"
    project_url = "https://github.com/the-namespace/the-repo-name"
    downstream_pr_url = "propose-downstream-pr-url"
    https_url = "https://github.com/the-namespace/the-repo-name.git"
    pagure_project_url = "https://git.stg.centos.org/the-namespace/the-repo-name"
    project = "the-project-name"
    owner = "the-owner"
    ref = "80201a74d96c"
    different_ref = "123456789012"
    branch = "build-branch"
    different_branch = "different-branch"
    commit_sha = "80201a74d96c"
    different_commit_sha = "687abc76d67d"
    pr_id = 342
    pagure_pr_id = 343
    tag_name = "v1.0.2"
    different_tag_name = "v1.2.3"
    package_name = "a-package-name"
    downstream_pr_id = 34
    downstream_namespace = "the-namespace"
    downstream_repo = "the-repo-name"
    downstream_project_url = "https://src.fedoraproject.org/the-namespace/the-repo-name"

    # gitlab
    mr_id = 2
    gitlab_repo_namespace = "the-namespace"
    gitlab_repo_name = "repo-name"
    gitlab_project_url = "https://gitlab.com/the-namespace/repo-name"
    gitlab_commit_sha = "80201a74d96c"

    # build
    build_id = "123456"
    different_build_id = "987654"
    another_different_build_id = "78912"
    status_success = BuildStatus.success
    status_pending = BuildStatus.pending
    status_error = BuildStatus.error
    status_failed = BuildStatus.failure
    status_waiting_for_srpm = BuildStatus.waiting_for_srpm
    target = "fedora-42-x86_64"
    different_target = "fedora-43-x86_64"
    chroots: ClassVar[list[str]] = ["fedora-43-x86_64", "fedora-42-x86_64"]
    status_per_chroot: ClassVar[dict[str, str]] = {
        "fedora-43-x86_64": "success",
        "fedora-42-x86_64": "pending",
    }
    copr_web_url = "https://copr.something.somewhere/123456"
    koji_web_url = "https://koji.something.somewhere/123456"
    srpm_logs = "some\nboring\nlogs"

    # TFT
    pipeline_id = "123456"
    different_pipeline_id = "123457"
    another_different_pipeline_id = "98765"

    # Allowlist
    account_name = "github.com/Rayquaza"
    different_account_name = "gitlab.com/Deoxys"
    another_different_acount_name = "gitlab.com/Solgaleo"
    yet_another_different_acount_name = "github.com/Zacian"

    # Issues
    issue_id = 2020
    different_issue_id = 987
    built_packages: ClassVar[list[dict]] = [
        {
            "arch": "noarch",
            "epoch": 0,
            "name": "python3-packit",
            "release": "1.20210930124525726166.main.0.g0b7b36b.fc36",
            "version": "0.38.0",
        },
        {
            "arch": "src",
            "epoch": 0,
            "name": "packit",
            "release": "1.20210930124525726166.main.0.g0b7b36b.fc36",
            "version": "0.38.0",
        },
        {
            "arch": "noarch",
            "epoch": 0,
            "name": "packit",
            "release": "1.20210930124525726166.main.0.g0b7b36b.fc36",
            "version": "0.38.0",
        },
    ]

    # dist-git
    nvr = "packit-0.43.0-1.fc39"
    dist_git_branch = "f39"
    different_nvr = "packit-0.40.0-1.fc38"
    different_dist_git_branch = "f38"
    alias = "FEDORA-123"
    bodhi_url = "https://bodhi.fedoraproject.org/FEDORA-123"
    sidetag = "f39-build-side-12345"

    # anitya
    anitya_project_id = 12345
    anitya_project_name = "packit-anitya"

    # scan
    task_id = 123
    scan_url = "https://scan-url"
    issues_added_count = 3
    issues_added_url = "https://issues-added-url"
    issues_fixed_url = "https://issues-fixed-url"
    scan_results_url = "https://scan-results-url"
    scan_status_success = OSHScanStatus.succeeded


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
    service_config.github_requests_log_path = "/path"
    service_config.server_name = "localhost"
    ServiceConfig.service_config = service_config


def clean_db():
    with sa_session_transaction() as session:
        session.query(SourceGitPRDistGitPRModel).delete()
        session.query(OSHScanModel).delete()

        session.query(AllowlistModel).delete()
        session.query(GithubInstallationModel).delete()

        session.query(PipelineModel).delete()
        session.query(ProjectEventModel).delete()

        session.query(TFTTestRunTargetModel).delete()
        session.query(TFTTestRunGroupModel).delete()
        session.query(CoprBuildTargetModel).delete()
        session.query(CoprBuildGroupModel).delete()
        session.query(KojiBuildTargetModel).delete()
        session.query(KojiTagRequestTargetModel).delete()
        session.query(BodhiUpdateTargetModel).delete()
        session.query(sync_release_pr_association_table).delete()
        session.query(SRPMBuildModel).delete()
        session.query(SyncReleaseTargetModel).delete()
        session.query(SyncReleaseModel).delete()
        session.query(SyncReleasePullRequestModel).delete()

        session.query(GitBranchModel).delete()
        session.query(ProjectReleaseModel).delete()
        session.query(PullRequestModel).delete()
        session.query(IssueModel).delete()
        session.query(ProjectAuthenticationIssueModel).delete()

        session.query(GitProjectModel).delete()


@pytest.fixture()
def clean_before_and_after():
    clean_db()
    yield
    clean_db()


@pytest.fixture()
def pr_project_event_model():
    _, event = ProjectEventModel.add_pull_request_event(
        pr_id=SampleValues.pr_id,
        namespace=SampleValues.repo_namespace,
        repo_name=SampleValues.repo_name,
        project_url=SampleValues.project_url,
        commit_sha=SampleValues.commit_sha,
    )
    yield event


@pytest.fixture()
def pr_model(pr_project_event_model):
    pr = pr_project_event_model.get_project_event_object()
    yield pr


@pytest.fixture()
def mr_model():
    pr, _ = ProjectEventModel.add_pull_request_event(
        pr_id=SampleValues.mr_id,
        namespace=SampleValues.gitlab_repo_namespace,
        repo_name=SampleValues.gitlab_repo_name,
        project_url=SampleValues.gitlab_project_url,
        commit_sha=SampleValues.gitlab_commit_sha,
    )
    yield pr


@pytest.fixture()
def different_pr_model():
    pr, _ = ProjectEventModel.add_pull_request_event(
        pr_id=4,
        namespace=SampleValues.repo_namespace,
        repo_name=SampleValues.repo_name,
        project_url=SampleValues.project_url,
        commit_sha=SampleValues.different_commit_sha,
    )
    yield pr


@pytest.fixture()
def pagure_pr_model():
    pr, _ = ProjectEventModel.add_pull_request_event(
        pr_id=SampleValues.pagure_pr_id,
        namespace=SampleValues.repo_namespace,
        repo_name=SampleValues.repo_name,
        project_url=SampleValues.pagure_project_url,
        commit_sha=SampleValues.commit_sha,
    )
    yield pr


@pytest.fixture()
def release_model():
    release, _ = ProjectEventModel.add_release_event(
        tag_name=SampleValues.tag_name,
        commit_hash=SampleValues.commit_sha,
        namespace=SampleValues.repo_namespace,
        repo_name=SampleValues.repo_name,
        project_url=SampleValues.project_url,
    )
    yield release


@pytest.fixture()
def anitya_version_model():
    release, _ = ProjectEventModel.add_anitya_version_event(
        version=SampleValues.tag_name,
        project_name=SampleValues.anitya_project_name,
        project_id=SampleValues.anitya_project_id,
        package=SampleValues.package_name,
    )
    yield release


@pytest.fixture()
def different_release_model():
    release, _ = ProjectEventModel.add_release_event(
        tag_name=SampleValues.different_tag_name,
        commit_hash=SampleValues.different_commit_sha,
        namespace=SampleValues.repo_namespace,
        repo_name=SampleValues.repo_name,
        project_url=SampleValues.project_url,
    )
    yield release


@pytest.fixture()
def branch_project_event_model():
    _, event = ProjectEventModel.add_branch_push_event(
        branch_name=SampleValues.branch,
        namespace=SampleValues.repo_namespace,
        repo_name=SampleValues.repo_name,
        project_url=SampleValues.project_url,
        commit_sha=SampleValues.commit_sha,
    )
    yield event


@pytest.fixture()
def branch_model(branch_project_event_model):
    yield branch_project_event_model.get_project_event_object()


@pytest.fixture()
def branch_model_gitlab():
    branch, _ = ProjectEventModel.add_branch_push_event(
        branch_name=SampleValues.branch,
        namespace=SampleValues.gitlab_repo_namespace,
        repo_name=SampleValues.gitlab_repo_name,
        project_url=SampleValues.gitlab_project_url,
        commit_sha=SampleValues.gitlab_commit_sha,
    )
    yield branch


@pytest.fixture()
def propose_model():
    yield SyncReleaseTargetModel.create(
        status=SyncReleaseTargetStatus.running,
        branch=SampleValues.branch,
    )


@pytest.fixture()
def propose_downstream_model_release(release_project_event_model):
    propose_downstream_model, _ = SyncReleaseModel.create_with_new_run(
        status=SyncReleaseStatus.running,
        project_event_model=release_project_event_model,
        job_type=SyncReleaseJobType.propose_downstream,
    )
    yield propose_downstream_model


@pytest.fixture()
def pull_from_upstream_target_model(release_project_event_model):
    pull_from_upstream_model, _ = SyncReleaseModel.create_with_new_run(
        status=SyncReleaseStatus.running,
        project_event_model=release_project_event_model,
        job_type=SyncReleaseJobType.pull_from_upstream,
    )

    target_model = SyncReleaseTargetModel.create(
        status=SyncReleaseTargetStatus.submitted,
        branch=SampleValues.branch,
    )
    sync_release_pull_request_model = SyncReleasePullRequestModel.get_or_create(
        SampleValues.downstream_pr_id,
        SampleValues.downstream_namespace,
        SampleValues.downstream_repo,
        SampleValues.downstream_project_url,
        SampleValues.branch,
        SampleValues.downstream_pr_url,
    )
    target_model.set_downstream_pr_url(downstream_pr_url=SampleValues.downstream_pr_url)
    target_model.set_downstream_prs([sync_release_pull_request_model])
    target_model.set_finished_time(finished_time=datetime.datetime.utcnow())
    target_model.set_logs(logs="random logs")

    pull_from_upstream_model.sync_release_targets.append(target_model)
    yield target_model


@pytest.fixture()
def pull_from_upstream_target_model_non_git(anitya_version_project_event_model):
    pull_from_upstream_model, _ = SyncReleaseModel.create_with_new_run(
        status=SyncReleaseStatus.running,
        project_event_model=anitya_version_project_event_model,
        job_type=SyncReleaseJobType.pull_from_upstream,
    )

    target_model = SyncReleaseTargetModel.create(
        status=SyncReleaseTargetStatus.submitted,
        branch=SampleValues.branch,
    )
    sync_release_pull_request_model = SyncReleasePullRequestModel.get_or_create(
        SampleValues.downstream_pr_id,
        SampleValues.downstream_namespace,
        SampleValues.downstream_repo,
        SampleValues.downstream_project_url,
        SampleValues.branch,
        SampleValues.downstream_pr_url,
    )
    target_model.set_downstream_pr_url(downstream_pr_url=SampleValues.downstream_pr_url)
    target_model.set_downstream_prs([sync_release_pull_request_model])
    target_model.set_finished_time(finished_time=datetime.datetime.utcnow())
    target_model.set_logs(logs="random logs")

    pull_from_upstream_model.sync_release_targets.append(target_model)
    yield target_model


@pytest.fixture()
def pull_from_upstream_target_model_without_pr_model(release_project_event_model):
    pull_from_upstream_model, _ = SyncReleaseModel.create_with_new_run(
        status=SyncReleaseStatus.running,
        project_event_model=release_project_event_model,
        job_type=SyncReleaseJobType.pull_from_upstream,
    )

    target_model = SyncReleaseTargetModel.create(
        status=SyncReleaseTargetStatus.submitted,
        branch=SampleValues.branch,
    )
    target_model.set_finished_time(finished_time=datetime.datetime.utcnow())
    target_model.set_logs(logs="random logs")

    pull_from_upstream_model.sync_release_targets.append(target_model)
    yield target_model


@pytest.fixture()
def propose_downstream_model_issue(an_issue_project_event_model):
    propose_downstream_model, _ = SyncReleaseModel.create_with_new_run(
        status=SyncReleaseStatus.running,
        project_event_model=an_issue_project_event_model,
        job_type=SyncReleaseJobType.propose_downstream,
    )
    yield propose_downstream_model


@pytest.fixture()
def propose_model_submitted():
    propose_downstream_target = SyncReleaseTargetModel.create(
        status=SyncReleaseTargetStatus.submitted,
        branch=SampleValues.branch,
    )
    sync_release_pull_request_model = SyncReleasePullRequestModel.get_or_create(
        SampleValues.downstream_pr_id,
        SampleValues.downstream_namespace,
        SampleValues.downstream_repo,
        SampleValues.downstream_project_url,
        SampleValues.branch,
        SampleValues.downstream_pr_url,
    )
    propose_downstream_target.set_downstream_pr_url(
        downstream_pr_url=SampleValues.downstream_pr_url,
    )
    propose_downstream_target.set_downstream_prs([sync_release_pull_request_model])
    propose_downstream_target.set_finished_time(
        finished_time=datetime.datetime.utcnow(),
    )
    propose_downstream_target.set_logs(logs="random logs")

    yield propose_downstream_target


@pytest.fixture()
def propose_model_submitted_release(
    propose_downstream_model_release,
    propose_model_submitted,
):
    propose_downstream = propose_downstream_model_release
    propose_downstream_target = propose_model_submitted
    propose_downstream.sync_release_targets.append(propose_downstream_target)
    yield propose_downstream_target


@pytest.fixture()
def propose_model_submitted_issue(
    propose_downstream_model_issue,
    propose_model_submitted,
):
    propose_downstream = propose_downstream_model_issue
    propose_downstream_target = propose_model_submitted
    propose_downstream.sync_release_targets.append(propose_downstream_target)
    yield propose_downstream_target


@pytest.fixture()
def different_pr_project_event_model(different_pr_model):
    yield ProjectEventModel.get_or_create(
        type=ProjectEventModelType.pull_request,
        event_id=different_pr_model.id,
        commit_sha=SampleValues.different_commit_sha,
    )


@pytest.fixture()
def release_project_event_model(release_model):
    yield ProjectEventModel.get_or_create(
        type=ProjectEventModelType.release,
        event_id=release_model.id,
        commit_sha=SampleValues.commit_sha,
    )


@pytest.fixture()
def anitya_version_project_event_model(anitya_version_model):
    yield ProjectEventModel.get_or_create(
        type=ProjectEventModelType.anitya_version,
        event_id=anitya_version_model.id,
        commit_sha=None,
    )


@pytest.fixture()
def different_release_project_event_model(different_release_model):
    yield ProjectEventModel.get_or_create(
        type=ProjectEventModelType.release,
        event_id=different_release_model.id,
        commit_sha=SampleValues.different_commit_sha,
    )


@pytest.fixture()
def srpm_build_model_with_new_run_and_tf_for_pr(srpm_build_model_with_new_run_for_pr):
    srpm_model, run_model = srpm_build_model_with_new_run_for_pr
    tf_group_model = TFTTestRunGroupModel.create([run_model], ranch="public")
    yield srpm_model, tf_group_model, run_model


@pytest.fixture()
def srpm_build_model_with_new_run_for_pr(pr_project_event_model):
    srpm_model, run_model = SRPMBuildModel.create_with_new_run(
        project_event_model=pr_project_event_model,
        package_name=SampleValues.package_name,
    )
    srpm_model.set_logs(SampleValues.srpm_logs)
    srpm_model.set_status(BuildStatus.success)
    yield srpm_model, run_model


@pytest.fixture()
def srpm_build_model_with_new_run_for_pr_different_commit(pr_project_event_model):
    _, event = ProjectEventModel.add_pull_request_event(
        pr_id=SampleValues.pr_id,
        namespace=SampleValues.repo_namespace,
        repo_name=SampleValues.repo_name,
        project_url=SampleValues.project_url,
        commit_sha="different-sha",
    )
    srpm_model, run_model = SRPMBuildModel.create_with_new_run(
        project_event_model=event,
        package_name=SampleValues.package_name,
    )
    srpm_model.set_logs(SampleValues.srpm_logs)
    srpm_model.set_status(BuildStatus.success)
    yield srpm_model, run_model


@pytest.fixture()
def srpm_build_model_with_new_run_and_tf_for_branch(
    srpm_build_model_with_new_run_for_branch,
):
    srpm_model, run_model = srpm_build_model_with_new_run_for_branch
    tf_group_model = TFTTestRunGroupModel.create([run_model], ranch="public")
    yield srpm_model, tf_group_model, run_model


@pytest.fixture()
def srpm_build_model_with_new_run_for_branch(branch_project_event_model):
    srpm_model, run_model = SRPMBuildModel.create_with_new_run(
        project_event_model=branch_project_event_model,
    )
    srpm_model.set_logs(SampleValues.srpm_logs)
    srpm_model.set_status(BuildStatus.success)
    yield srpm_model, run_model


@pytest.fixture()
def srpm_build_model_with_new_run_and_tf_for_release(
    srpm_build_model_with_new_run_for_release,
):
    srpm_model, run_model = srpm_build_model_with_new_run_for_release
    tf_group_model = TFTTestRunGroupModel.create([run_model], ranch="public")
    yield srpm_model, tf_group_model, run_model


@pytest.fixture()
def srpm_build_model_with_new_run_for_release(release_project_event_model):
    srpm_model, run_model = SRPMBuildModel.create_with_new_run(
        project_event_model=release_project_event_model,
    )
    srpm_model.set_logs(SampleValues.srpm_logs)
    srpm_model.set_status(BuildStatus.success)
    yield srpm_model, run_model


@pytest.fixture()
def srpm_build_in_copr_model(pr_project_event_model):
    srpm_model, run_model = SRPMBuildModel.create_with_new_run(
        project_event_model=pr_project_event_model,
        copr_build_id="123",
        copr_web_url="example-url",
    )
    srpm_model.set_status(BuildStatus.success)
    yield srpm_model, run_model


@pytest.fixture()
def an_issue_project_event_model():
    _, event = ProjectEventModel.add_issue_event(
        issue_id=SampleValues.issue_id,
        namespace=SampleValues.repo_namespace,
        repo_name=SampleValues.repo_name,
        project_url=SampleValues.project_url,
    )
    yield event


@pytest.fixture()
def an_issue_model(an_issue_project_event_model):
    return an_issue_project_event_model.get_project_event_object()


@pytest.fixture()
def different_issue_project_event_model():
    _, event = ProjectEventModel.add_issue_event(
        issue_id=SampleValues.different_issue_id,
        namespace=SampleValues.repo_namespace,
        repo_name=SampleValues.repo_name,
        project_url=SampleValues.project_url,
    )
    yield event


@pytest.fixture()
def different_issue_model(different_issue_project_event_model):
    return different_issue_project_event_model.get_project_event_object()


@pytest.fixture()
def a_copr_build_for_pr(srpm_build_model_with_new_run_for_pr):
    _, run_model = srpm_build_model_with_new_run_for_pr
    group = CoprBuildGroupModel.create(run_model)
    copr_build_model = CoprBuildTargetModel.create(
        build_id=SampleValues.build_id,
        project_name=SampleValues.project,
        owner=SampleValues.owner,
        web_url=SampleValues.copr_web_url,
        target=SampleValues.target,
        status=SampleValues.status_pending,
        copr_build_group=group,
    )
    copr_build_model.set_build_logs_url(
        "https://copr.somewhere/results/owner/package/target/build.logs",
    )
    copr_build_model.set_built_packages(SampleValues.built_packages)
    yield copr_build_model


@pytest.fixture()
def a_copr_build_for_pr_different_commit(
    srpm_build_model_with_new_run_for_pr_different_commit,
):
    _, run_model = srpm_build_model_with_new_run_for_pr_different_commit
    group = CoprBuildGroupModel.create(run_model)
    copr_build_model = CoprBuildTargetModel.create(
        build_id=SampleValues.build_id,
        project_name=SampleValues.project,
        owner=SampleValues.owner,
        web_url=SampleValues.copr_web_url,
        target=SampleValues.target,
        status=SampleValues.status_pending,
        copr_build_group=group,
    )
    copr_build_model.set_build_logs_url(
        "https://copr.somewhere/results/owner/package/target/build.logs",
    )
    copr_build_model.set_built_packages(SampleValues.built_packages)
    yield copr_build_model


@pytest.fixture()
def a_copr_build_for_branch_push(srpm_build_model_with_new_run_for_branch):
    _, run_model = srpm_build_model_with_new_run_for_branch
    group = CoprBuildGroupModel.create(run_model)
    copr_build_model = CoprBuildTargetModel.create(
        build_id=SampleValues.build_id,
        project_name=SampleValues.project,
        owner=SampleValues.owner,
        web_url=SampleValues.copr_web_url,
        target=SampleValues.target,
        status=SampleValues.status_pending,
        copr_build_group=group,
    )
    copr_build_model.set_build_logs_url(
        "https://copr.somewhere/results/owner/package/target/build.logs",
    )
    yield copr_build_model


@pytest.fixture()
def a_copr_build_for_release(srpm_build_model_with_new_run_for_release):
    _, run_model = srpm_build_model_with_new_run_for_release
    group = CoprBuildGroupModel.create(run_model)
    copr_build_model = CoprBuildTargetModel.create(
        build_id=SampleValues.build_id,
        project_name=SampleValues.project,
        owner=SampleValues.owner,
        web_url=SampleValues.copr_web_url,
        target=SampleValues.target,
        status=SampleValues.status_pending,
        copr_build_group=group,
    )
    copr_build_model.set_build_logs_url(
        "https://copr.somewhere/results/owner/package/target/build.logs",
    )
    yield copr_build_model


@pytest.fixture()
def a_copr_build_waiting_for_srpm(srpm_build_in_copr_model):
    _, run_model = srpm_build_in_copr_model
    group = CoprBuildGroupModel.create(run_model)
    copr_build_model = CoprBuildTargetModel.create(
        build_id=SampleValues.build_id,
        project_name=SampleValues.project,
        owner=SampleValues.owner,
        web_url=SampleValues.copr_web_url,
        target=SampleValues.target,
        status=SampleValues.status_waiting_for_srpm,
        copr_build_group=group,
    )
    copr_build_model.set_build_logs_url(
        "https://copr.somewhere/results/owner/package/target/build.logs",
    )
    copr_build_model.set_built_packages(SampleValues.built_packages)
    yield copr_build_model


@pytest.fixture()
def multiple_copr_builds(pr_project_event_model, different_pr_project_event_model):
    _, run_model_for_pr = SRPMBuildModel.create_with_new_run(
        project_event_model=pr_project_event_model,
    )
    group_for_pr = CoprBuildGroupModel.create(run_model_for_pr)
    _, run_model_for_same_pr = SRPMBuildModel.create_with_new_run(
        project_event_model=pr_project_event_model,
    )
    group_for_same_pr = CoprBuildGroupModel.create(run_model_for_same_pr)
    _, run_model_for_a_different_pr = SRPMBuildModel.create_with_new_run(
        project_event_model=different_pr_project_event_model,
    )
    group_for_a_different_pr = CoprBuildGroupModel.create(run_model_for_a_different_pr)

    yield [
        # Two chroots for one run model
        CoprBuildTargetModel.create(
            build_id=SampleValues.build_id,
            project_name=SampleValues.project,
            owner=SampleValues.owner,
            web_url=SampleValues.copr_web_url,
            target=SampleValues.target,
            status=SampleValues.status_success,
            copr_build_group=group_for_pr,
        ),
        CoprBuildTargetModel.create(
            build_id=SampleValues.build_id,
            project_name=SampleValues.project,
            owner=SampleValues.owner,
            web_url=SampleValues.copr_web_url,
            target=SampleValues.different_target,
            status=SampleValues.status_pending,
            copr_build_group=group_for_pr,
        ),
        # Same PR, same ref, but different run model
        CoprBuildTargetModel.create(
            build_id=SampleValues.different_build_id,
            project_name=SampleValues.project,
            owner=SampleValues.owner,
            web_url=SampleValues.copr_web_url,
            target=SampleValues.target,
            status=SampleValues.status_success,
            copr_build_group=group_for_same_pr,
        ),
        # Different PR
        CoprBuildTargetModel.create(
            build_id=SampleValues.another_different_build_id,
            project_name=SampleValues.different_project_name,
            owner=SampleValues.owner,
            web_url=SampleValues.copr_web_url,
            target=SampleValues.target,
            status=SampleValues.status_success,
            copr_build_group=group_for_a_different_pr,
        ),
    ]


@pytest.fixture()
def too_many_copr_builds(pr_project_event_model, different_pr_project_event_model):
    """Don't use for testing anything other than pagination, use multiple_copr_builds."""
    builds_list = []
    for i in range(20):
        _, run_model_for_pr = SRPMBuildModel.create_with_new_run(
            project_event_model=pr_project_event_model,
        )
        group_for_pr = CoprBuildGroupModel.create(run_model_for_pr)
        _, run_model_for_same_pr = SRPMBuildModel.create_with_new_run(
            project_event_model=pr_project_event_model,
        )
        group_for_same_pr = CoprBuildGroupModel.create(run_model_for_same_pr)
        _, run_model_for_a_different_pr = SRPMBuildModel.create_with_new_run(
            project_event_model=different_pr_project_event_model,
        )
        group_for_a_different_pr = CoprBuildGroupModel.create(
            run_model_for_a_different_pr,
        )

        builds_list += [
            # The following two are similar, except for target, status
            CoprBuildTargetModel.create(
                build_id=SampleValues.build_id + str(i),
                project_name=SampleValues.project,
                owner=SampleValues.owner,
                web_url=SampleValues.copr_web_url + str(i),
                target=SampleValues.target,
                status=SampleValues.status_success,
                copr_build_group=group_for_pr,
            ),
            CoprBuildTargetModel.create(
                build_id=SampleValues.build_id + str(i),
                project_name=SampleValues.project,
                owner=SampleValues.owner,
                web_url=SampleValues.copr_web_url + str(i),
                target=SampleValues.different_target,
                status=SampleValues.status_pending,
                copr_build_group=group_for_pr,
            ),
            # Same PR, different run model
            CoprBuildTargetModel.create(
                build_id=SampleValues.different_build_id + str(i),
                project_name=SampleValues.different_project_name,
                owner=SampleValues.owner,
                web_url=SampleValues.copr_web_url + str(i),
                target=SampleValues.target,
                status=SampleValues.status_success,
                copr_build_group=group_for_same_pr,
            ),
            # Different PR:
            CoprBuildTargetModel.create(
                build_id=SampleValues.different_build_id + str(i),
                project_name=SampleValues.different_project_name,
                owner=SampleValues.owner,
                web_url=SampleValues.copr_web_url + str(i),
                target=SampleValues.different_target,
                status=SampleValues.status_success,
                copr_build_group=group_for_a_different_pr,
            ),
        ]
    yield builds_list


@pytest.fixture()
def copr_builds_with_different_triggers(
    srpm_build_model_with_new_run_for_pr,
    srpm_build_model_with_new_run_for_branch,
    srpm_build_model_with_new_run_for_release,
):
    _, run_model_for_pr = srpm_build_model_with_new_run_for_pr
    _, run_model_for_branch = srpm_build_model_with_new_run_for_branch
    _, run_model_for_release = srpm_build_model_with_new_run_for_release
    group_for_pr = CoprBuildGroupModel.create(run_model_for_pr)
    group_for_branch = CoprBuildGroupModel.create(run_model_for_branch)
    group_for_release = CoprBuildGroupModel.create(run_model_for_release)

    yield [
        # pull request trigger
        CoprBuildTargetModel.create(
            build_id=SampleValues.build_id,
            project_name=SampleValues.project,
            owner=SampleValues.owner,
            web_url=SampleValues.copr_web_url,
            target=SampleValues.target,
            status=SampleValues.status_success,
            copr_build_group=group_for_pr,
        ),
        # branch push trigger
        CoprBuildTargetModel.create(
            build_id=SampleValues.different_build_id,
            project_name=SampleValues.project,
            owner=SampleValues.owner,
            web_url=SampleValues.copr_web_url,
            target=SampleValues.target,
            status=SampleValues.status_success,
            copr_build_group=group_for_branch,
        ),
        # release trigger
        CoprBuildTargetModel.create(
            build_id=SampleValues.another_different_build_id,
            project_name=SampleValues.project,
            owner=SampleValues.owner,
            web_url=SampleValues.copr_web_url,
            target=SampleValues.target,
            status=SampleValues.status_success,
            copr_build_group=group_for_release,
        ),
    ]


@pytest.fixture()
def a_koji_build_for_pr(srpm_build_model_with_new_run_for_pr):
    _, run_model = srpm_build_model_with_new_run_for_pr
    group = KojiBuildGroupModel.create(run_model)
    koji_build_model = KojiBuildTargetModel.create(
        task_id=SampleValues.build_id,
        web_url=SampleValues.koji_web_url,
        target=SampleValues.target,
        status=SampleValues.status_pending,
        scratch=True,
        koji_build_group=group,
    )
    koji_build_model.set_build_logs_urls(
        {"x86_64": "https://koji.somewhere/results/owner/package/target/build.logs"},
    )
    yield koji_build_model


@pytest.fixture()
def a_koji_build_for_pr_non_scratch(branch_project_event_model):
    group_for_nonscratch_build = KojiBuildGroupModel.create(
        run_model=PipelineModel.create(project_event=branch_project_event_model),
    )
    koji_build_model = KojiBuildTargetModel.create(
        task_id=SampleValues.build_id,
        web_url=SampleValues.koji_web_url,
        target=SampleValues.target,
        status=SampleValues.status_pending,
        scratch=False,
        koji_build_group=group_for_nonscratch_build,
    )
    koji_build_model.set_build_logs_urls(
        {"x86_64": "https://koji.somewhere/results/owner/package/target/build.logs"},
    )
    yield koji_build_model


@pytest.fixture()
def a_koji_build_for_branch_push(srpm_build_model_with_new_run_for_branch):
    _, run_model = srpm_build_model_with_new_run_for_branch
    group = KojiBuildGroupModel.create(run_model)

    yield KojiBuildTargetModel.create(
        task_id=SampleValues.build_id,
        web_url=SampleValues.koji_web_url,
        target=SampleValues.target,
        status=SampleValues.status_pending,
        scratch=True,
        koji_build_group=group,
    )


@pytest.fixture()
def a_koji_build_for_release(srpm_build_model_with_new_run_for_release):
    _, run_model = srpm_build_model_with_new_run_for_release
    group = KojiBuildGroupModel.create(run_model)

    yield KojiBuildTargetModel.create(
        task_id=SampleValues.build_id,
        web_url=SampleValues.koji_web_url,
        target=SampleValues.target,
        status=SampleValues.status_pending,
        scratch=True,
        koji_build_group=group,
    )


@pytest.fixture()
def multiple_koji_builds(pr_project_event_model, different_pr_project_event_model):
    _, run_model_for_pr = SRPMBuildModel.create_with_new_run(
        project_event_model=pr_project_event_model,
    )
    group_for_pr = KojiBuildGroupModel.create(run_model_for_pr)
    _, run_model_for_same_pr = SRPMBuildModel.create_with_new_run(
        project_event_model=pr_project_event_model,
    )
    group_for_same_pr = KojiBuildGroupModel.create(run_model_for_same_pr)
    _, run_model_for_a_different_pr = SRPMBuildModel.create_with_new_run(
        project_event_model=different_pr_project_event_model,
    )
    group_for_a_different_pr = KojiBuildGroupModel.create(run_model_for_a_different_pr)

    group_for_nonscratch_build = KojiBuildGroupModel.create(
        run_model=PipelineModel.create(project_event=different_pr_project_event_model),
    )

    yield [
        # Non-scratch build
        KojiBuildTargetModel.create(
            task_id="1",
            web_url=SampleValues.koji_web_url,
            target=SampleValues.target,
            status=SampleValues.status_pending,
            scratch=False,
            koji_build_group=group_for_nonscratch_build,
        ),
        # Two builds for same run
        KojiBuildTargetModel.create(
            task_id=SampleValues.build_id,
            web_url=SampleValues.koji_web_url,
            target=SampleValues.target,
            status=SampleValues.status_pending,
            scratch=True,
            koji_build_group=group_for_pr,
        ),
        KojiBuildTargetModel.create(
            task_id=SampleValues.different_build_id,
            web_url=SampleValues.koji_web_url,
            target=SampleValues.different_target,
            status=SampleValues.status_pending,
            scratch=True,
            koji_build_group=group_for_pr,
        ),
        # Same PR, different run
        KojiBuildTargetModel.create(
            task_id=SampleValues.different_build_id,
            web_url=SampleValues.koji_web_url,
            target=SampleValues.different_target,
            status=SampleValues.status_pending,
            scratch=True,
            koji_build_group=group_for_same_pr,
        ),
        # Completely different build
        KojiBuildTargetModel.create(
            task_id=SampleValues.another_different_build_id,
            web_url=SampleValues.koji_web_url,
            target=SampleValues.target,
            status=SampleValues.status_pending,
            scratch=True,
            koji_build_group=group_for_a_different_pr,
        ),
    ]


@pytest.fixture()
def a_new_test_run_pr(srpm_build_model_with_new_run_for_pr, a_copr_build_for_pr):
    _, run_model = srpm_build_model_with_new_run_for_pr
    group = TFTTestRunGroupModel.create([run_model], ranch="public")
    yield TFTTestRunTargetModel.create(
        pipeline_id=SampleValues.pipeline_id,
        web_url=SampleValues.testing_farm_url,
        target=SampleValues.target,
        status=TestingFarmResult.new,
        test_run_group=group,
    )


@pytest.fixture()
def a_new_test_run_branch_push(
    srpm_build_model_with_new_run_for_branch,
    a_copr_build_for_branch_push,
):
    _, run_model = srpm_build_model_with_new_run_for_branch
    group = TFTTestRunGroupModel.create([run_model], ranch="public")
    yield TFTTestRunTargetModel.create(
        pipeline_id=SampleValues.pipeline_id,
        web_url=SampleValues.testing_farm_url,
        target=SampleValues.target,
        status=TestingFarmResult.new,
        test_run_group=group,
    )


@pytest.fixture()
def multiple_new_test_runs(pr_project_event_model, different_pr_project_event_model):
    _, run_model_for_pr = SRPMBuildModel.create_with_new_run(
        project_event_model=pr_project_event_model,
    )
    test_group_for_pr = TFTTestRunGroupModel.create([run_model_for_pr], ranch="public")
    build_group_for_pr = CoprBuildGroupModel.create(run_model_for_pr)
    _, run_model_for_same_pr = SRPMBuildModel.create_with_new_run(
        project_event_model=pr_project_event_model,
    )
    test_group_for_same_pr = TFTTestRunGroupModel.create([run_model_for_same_pr], ranch="public")
    build_group_for_same_pr = CoprBuildGroupModel.create(run_model_for_same_pr)
    _, run_model_for_a_different_pr = SRPMBuildModel.create_with_new_run(
        project_event_model=different_pr_project_event_model,
    )
    build_group_for_a_different_pr = CoprBuildGroupModel.create(
        run_model_for_a_different_pr,
    )
    test_group_for_different_pr = TFTTestRunGroupModel.create(
        [run_model_for_a_different_pr], ranch="public"
    )

    CoprBuildTargetModel.create(
        build_id=SampleValues.build_id,
        project_name=SampleValues.project,
        owner=SampleValues.owner,
        web_url=SampleValues.copr_web_url,
        target=SampleValues.target,
        status=SampleValues.status_success,
        copr_build_group=build_group_for_pr,
    )

    # Same PR, same ref, but different run model
    CoprBuildTargetModel.create(
        build_id=SampleValues.different_build_id,
        project_name=SampleValues.project,
        owner=SampleValues.owner,
        web_url=SampleValues.copr_web_url,
        target=SampleValues.target,
        status=SampleValues.status_success,
        copr_build_group=build_group_for_same_pr,
    )

    # Different PR
    CoprBuildTargetModel.create(
        build_id=SampleValues.another_different_build_id,
        project_name=SampleValues.different_project_name,
        owner=SampleValues.owner,
        web_url=SampleValues.copr_web_url,
        target=SampleValues.target,
        status=SampleValues.status_success,
        copr_build_group=build_group_for_a_different_pr,
    )

    yield [
        TFTTestRunTargetModel.create(
            pipeline_id=SampleValues.pipeline_id,
            web_url=SampleValues.testing_farm_url,
            target=SampleValues.target,
            status=TestingFarmResult.new,
            test_run_group=test_group_for_pr,
        ),
        # Same commit_sha but different chroot and pipeline_id
        TFTTestRunTargetModel.create(
            pipeline_id=SampleValues.different_pipeline_id,
            web_url=SampleValues.testing_farm_url,
            target=SampleValues.different_target,
            status=TestingFarmResult.new,
            test_run_group=test_group_for_pr,
        ),
        # Same PR, different run model
        TFTTestRunTargetModel.create(
            pipeline_id=SampleValues.different_pipeline_id,
            web_url=SampleValues.testing_farm_url,
            target=SampleValues.different_target,
            status=TestingFarmResult.new,
            test_run_group=test_group_for_same_pr,
        ),
        # Completely different build
        TFTTestRunTargetModel.create(
            pipeline_id=SampleValues.another_different_pipeline_id,
            web_url=SampleValues.testing_farm_url,
            target=SampleValues.different_target,
            status=TestingFarmResult.running,
            test_run_group=test_group_for_different_pr,
        ),
    ]


@pytest.fixture()
def multiple_propose_downstream_runs_release_trigger(
    release_project_event_model,
    different_release_project_event_model,
):
    propose_downstream_model1, _ = SyncReleaseModel.create_with_new_run(
        status=SyncReleaseStatus.running,
        project_event_model=release_project_event_model,
        job_type=SyncReleaseJobType.propose_downstream,
    )
    propose_downstream_model2, _ = SyncReleaseModel.create_with_new_run(
        status=SyncReleaseStatus.error,
        project_event_model=release_project_event_model,
        job_type=SyncReleaseJobType.propose_downstream,
    )
    propose_downstream_model3, _ = SyncReleaseModel.create_with_new_run(
        status=SyncReleaseStatus.running,
        project_event_model=different_release_project_event_model,
        job_type=SyncReleaseJobType.propose_downstream,
    )
    propose_downstream_model4, _ = SyncReleaseModel.create_with_new_run(
        status=SyncReleaseStatus.finished,
        project_event_model=different_release_project_event_model,
        job_type=SyncReleaseJobType.propose_downstream,
    )

    yield [
        propose_downstream_model1,
        propose_downstream_model2,
        propose_downstream_model3,
        propose_downstream_model4,
    ]


@pytest.fixture()
def multiple_pull_from_upstream_runs(
    release_project_event_model,
    different_release_project_event_model,
):
    pull_from_upstream_model1, _ = SyncReleaseModel.create_with_new_run(
        status=SyncReleaseStatus.running,
        project_event_model=release_project_event_model,
        job_type=SyncReleaseJobType.pull_from_upstream,
    )
    pull_from_upstream_model2, _ = SyncReleaseModel.create_with_new_run(
        status=SyncReleaseStatus.error,
        project_event_model=release_project_event_model,
        job_type=SyncReleaseJobType.pull_from_upstream,
    )
    pull_from_upstream_model3, _ = SyncReleaseModel.create_with_new_run(
        status=SyncReleaseStatus.running,
        project_event_model=different_release_project_event_model,
        job_type=SyncReleaseJobType.pull_from_upstream,
    )
    pull_from_upstream_model4, _ = SyncReleaseModel.create_with_new_run(
        status=SyncReleaseStatus.finished,
        project_event_model=different_release_project_event_model,
        job_type=SyncReleaseJobType.pull_from_upstream,
    )

    yield [
        pull_from_upstream_model1,
        pull_from_upstream_model2,
        pull_from_upstream_model3,
        pull_from_upstream_model4,
    ]


@pytest.fixture()
def multiple_propose_downstream_runs_issue_trigger(
    an_issue_project_event_model,
    different_issue_project_event_model,
):
    propose_downstream_model1, _ = SyncReleaseModel.create_with_new_run(
        status=SyncReleaseStatus.running,
        project_event_model=an_issue_project_event_model,
        job_type=SyncReleaseJobType.propose_downstream,
    )
    propose_downstream_model2, _ = SyncReleaseModel.create_with_new_run(
        status=SyncReleaseStatus.error,
        project_event_model=an_issue_project_event_model,
        job_type=SyncReleaseJobType.propose_downstream,
    )
    propose_downstream_model3, _ = SyncReleaseModel.create_with_new_run(
        status=SyncReleaseStatus.running,
        project_event_model=different_issue_project_event_model,
        job_type=SyncReleaseJobType.propose_downstream,
    )
    propose_downstream_model4, _ = SyncReleaseModel.create_with_new_run(
        status=SyncReleaseStatus.finished,
        project_event_model=different_issue_project_event_model,
        job_type=SyncReleaseJobType.propose_downstream,
    )

    yield [
        propose_downstream_model1,
        propose_downstream_model2,
        propose_downstream_model3,
        propose_downstream_model4,
    ]


@pytest.fixture()
def multiple_propose_downstream_runs_with_propose_downstream_targets_release_trigger(
    multiple_propose_downstream_runs_release_trigger,
):
    propose_downstream_models_release = multiple_propose_downstream_runs_release_trigger
    propose_downstream_models_release[0].sync_release_targets.append(
        SyncReleaseTargetModel.create(
            status=SyncReleaseTargetStatus.queued,
            branch=SampleValues.different_branch,
        ),
    )

    propose_downstream_target = SyncReleaseTargetModel.create(
        status=SyncReleaseTargetStatus.running,
        branch=SampleValues.branch,
    )
    propose_downstream_models_release[0].sync_release_targets.append(
        propose_downstream_target,
    )

    yield [
        propose_downstream_models_release[0],
        propose_downstream_models_release[1],
        propose_downstream_models_release[2],
        propose_downstream_models_release[3],
    ]


@pytest.fixture()
def multiple_pull_from_upstream_runs_with_targets_release_trigger(
    multiple_pull_from_upstream_runs,
):
    pull_from_upstream_runs = multiple_pull_from_upstream_runs
    pull_from_upstream_runs[0].sync_release_targets.append(
        SyncReleaseTargetModel.create(
            status=SyncReleaseTargetStatus.queued,
            branch=SampleValues.different_branch,
        ),
    )

    propose_downstream_target = SyncReleaseTargetModel.create(
        status=SyncReleaseTargetStatus.running,
        branch=SampleValues.branch,
    )
    pull_from_upstream_runs[0].sync_release_targets.append(propose_downstream_target)

    yield [
        pull_from_upstream_runs[0],
        pull_from_upstream_runs[1],
        pull_from_upstream_runs[2],
        pull_from_upstream_runs[3],
    ]


@pytest.fixture()
def multiple_propose_downstream_runs_with_propose_downstream_targets_issue_trigger(
    multiple_propose_downstream_runs_issue_trigger,
):
    propose_downstream_models_issue = multiple_propose_downstream_runs_issue_trigger
    propose_downstream_target = SyncReleaseTargetModel.create(
        status=SyncReleaseTargetStatus.retry,
        branch=SampleValues.branch,
    )
    propose_downstream_models_issue[0].sync_release_targets.append(
        propose_downstream_target,
    )

    propose_downstream_target = SyncReleaseTargetModel.create(
        status=SyncReleaseTargetStatus.error,
        branch=SampleValues.different_branch,
    )
    propose_downstream_models_issue[0].sync_release_targets.append(
        propose_downstream_target,
    )

    yield [
        propose_downstream_models_issue[0],
        propose_downstream_models_issue[1],
        propose_downstream_models_issue[2],
        propose_downstream_models_issue[3],
    ]


@pytest.fixture()
def multiple_allowlist_entries():
    yield [
        AllowlistModel.add_namespace(
            namespace=SampleValues.account_name,
            status="approved_manually",
        ),
        AllowlistModel.add_namespace(
            namespace=SampleValues.different_account_name,
            status="approved_manually",
        ),
        # Not a typo, account_name repeated intentionally to check behaviour
        AllowlistModel.add_namespace(
            namespace=SampleValues.different_account_name,
            status="waiting",
        ),
        AllowlistModel.add_namespace(
            namespace=SampleValues.another_different_acount_name,
            status="waiting",
        ),
        AllowlistModel.add_namespace(
            namespace=SampleValues.yet_another_different_acount_name,
            status="approved_manually",
        ),
    ]


@pytest.fixture()
def new_allowlist_entry(clean_before_and_after):
    yield AllowlistModel.add_namespace(
        namespace=SampleValues.account_name,
        status="approved_manually",
    )


@pytest.fixture()
def installation_events():
    return [
        github.installation.Installation(
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
        github.installation.Installation(
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
    with sa_session_transaction() as session:
        session.query(GithubInstallationModel).delete()
        yield [
            GithubInstallationModel.create_or_update(
                event=installation_events[0],
            ),
            GithubInstallationModel.create_or_update(
                event=installation_events[1],
            ),
        ]
    clean_db()


@pytest.fixture()
def multiple_forge_projects():
    yield [
        GitProjectModel.get_or_create(
            "namespace",
            "repo",
            "https://github.com/namespace/repo",
        ),
        GitProjectModel.get_or_create(
            "namespace",
            "different-repo",
            "https://github.com/namespace/different-repo",
        ),
        GitProjectModel.get_or_create(
            "namespace",
            "repo",
            "https://gitlab.com/namespace/repo",
        ),
        GitProjectModel.get_or_create(
            "namespace",
            "repo",
            "https://git.stg.centos.org/namespace/repo",
        ),
    ]


@pytest.fixture()
def full_database(
    multiple_forge_projects,
    a_copr_build_for_pr,
    a_copr_build_for_branch_push,
    a_copr_build_for_release,
    a_copr_build_waiting_for_srpm,
    multiple_copr_builds,
    copr_builds_with_different_triggers,
    multiple_koji_builds,
    a_koji_build_for_pr,
    a_koji_build_for_release,
    a_koji_build_for_branch_push,
    a_new_test_run_pr,
    a_new_test_run_branch_push,
    multiple_new_test_runs,
    multiple_propose_downstream_runs_release_trigger,
    multiple_propose_downstream_runs_issue_trigger,
    multiple_propose_downstream_runs_with_propose_downstream_targets_release_trigger,
    multiple_allowlist_entries,
    multiple_installation_entries,
):
    yield


@pytest.fixture()
def release_event_dict():
    """
    Cleared version of the release webhook content.
    """
    return {
        "action": "published",
        "release": {
            "html_url": "https://github.com/the-namespace/the-repo-name/releases/tag/v1.0.2",
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
            },
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
            },
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
def tf_notification():
    return {
        "request_id": SampleValues.pipeline_id,
        "source": "testing-farm",
    }


@pytest.fixture()
def tf_result():
    return {
        "id": SampleValues.pipeline_id,
        "test": {"fmf": {"ref": SampleValues.different_commit_sha}},
        "result": {"overall": "passed"},
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


@pytest.fixture()
def few_runs(pr_project_event_model, different_pr_project_event_model):
    _, run_model_for_pr = SRPMBuildModel.create_with_new_run(
        project_event_model=pr_project_event_model,
    )
    build_group = CoprBuildGroupModel.create(run_model_for_pr)
    TFTTestRunGroupModel.create([run_model_for_pr], ranch="public")

    for target in (SampleValues.target, SampleValues.different_target):
        copr_build = CoprBuildTargetModel.create(
            build_id=SampleValues.build_id,
            project_name=SampleValues.project,
            owner=SampleValues.owner,
            web_url=SampleValues.copr_web_url,
            target=target,
            status=SampleValues.status_success,
            copr_build_group=build_group,
        )

        TFTTestRunTargetModel.create(
            pipeline_id=SampleValues.pipeline_id,
            web_url=SampleValues.testing_farm_url,
            target=target,
            status=TestingFarmResult.new,
            test_run_group=copr_build.group_of_targets.runs[0].test_run_group,
        )

    _, run_model_for_different_pr = SRPMBuildModel.create_with_new_run(
        project_event_model=different_pr_project_event_model,
    )
    TFTTestRunGroupModel.create([run_model_for_different_pr], ranch="public")
    build_group = CoprBuildGroupModel.create(run_model_for_different_pr)

    runs = []
    for target in (SampleValues.target, SampleValues.different_target):
        copr_build = CoprBuildTargetModel.create(
            build_id=SampleValues.build_id,
            project_name=SampleValues.project,
            owner=SampleValues.owner,
            web_url=SampleValues.copr_web_url,
            target=target,
            status=SampleValues.status_success,
            copr_build_group=build_group,
        )
        runs.append(copr_build.group_of_targets.runs[0])

        TFTTestRunTargetModel.create(
            pipeline_id=SampleValues.pipeline_id,
            web_url=SampleValues.testing_farm_url,
            target=target,
            status=TestingFarmResult.new,
            test_run_group=runs[-1].test_run_group,
        )

    for _i, target in enumerate((SampleValues.target, SampleValues.different_target)):
        TFTTestRunTargetModel.create(
            pipeline_id=SampleValues.pipeline_id,
            web_url=SampleValues.testing_farm_url,
            target=target,
            status=TestingFarmResult.new,
            test_run_group=runs[-1].test_run_group,
        )

    yield run_model_for_pr.id, run_model_for_different_pr.id


@pytest.fixture()
def runs_without_build(pr_project_event_model, branch_project_event_model):
    run_model_for_pr_only_test = PipelineModel.create(
        project_event=pr_project_event_model,
    )
    TFTTestRunGroupModel.create([run_model_for_pr_only_test], ranch="public")
    run_model_for_branch_only_test = PipelineModel.create(
        project_event=branch_project_event_model,
    )
    TFTTestRunGroupModel.create([run_model_for_branch_only_test], ranch="public")

    TFTTestRunTargetModel.create(
        pipeline_id=SampleValues.pipeline_id,
        web_url=SampleValues.testing_farm_url,
        target=SampleValues.target,
        status=TestingFarmResult.new,
        test_run_group=run_model_for_pr_only_test.test_run_group,
    )
    TFTTestRunTargetModel.create(
        pipeline_id=SampleValues.pipeline_id,
        web_url=SampleValues.testing_farm_url,
        target=SampleValues.target,
        status=TestingFarmResult.new,
        test_run_group=run_model_for_branch_only_test.test_run_group,
    )
    yield [run_model_for_pr_only_test, run_model_for_branch_only_test]


@pytest.fixture()
def check_rerun_event_dict_commit():
    """
    Cleared version of the check rerequested webhook content.
    """
    return {
        "action": "rerequested",
        "check_run": {
            "id": 3659360488,
            "name": "testing-farm:fedora-rawhide-x86_64",
            "node_id": "CR_kwDOCwFO9M7aHWjo",
            "head_sha": "0e5d8b51fd5dfa460605e1497d22a76d65c6d7fd",
            "external_id": "123456",
            "url": "https://api.github.com/repos/packit/hello-world/check-runs/3659360488",
            "html_url": "https://github.com/packit/hello-world/runs/3659360488",
            "details_url": "https://dashboard.stg.packit.dev/results/testing-farm/10523",
            "status": "completed",
            "conclusion": "failure",
            "started_at": "2021-09-21T04:37:53Z",
            "completed_at": "2021-09-21T04:37:53Z",
            "output": {
                "title": "Test environment installation failed: reason unknown, please escalate",
                "summary": "",
                "text": None,
            },
            "check_suite": {
                "id": 3359488643,
                "node_id": "MDEwOkNoZWNrU3VpdGUzMzU5NDg4NjQz",
                "head_branch": None,
                "head_sha": "0e5d8b51fd5dfa460605e1497d22a76d65c6d7fd",
                "status": "queued",
                "conclusion": None,
                "url": "https://api.github.com/repos/packit/hello-world/check-suites/3359488643",
                "before": None,
                "after": None,
                "pull_requests": [],
                "app": {
                    "id": 29180,
                    "slug": "packit-as-a-service-stg",
                    "node_id": "MDM6QXBwMjkxODA=",
                    "owner": {
                        "login": "packit",
                    },
                },
                "created_at": "2021-07-29T09:09:27Z",
                "updated_at": "2021-09-21T09:22:45Z",
            },
            "app": {
                "id": 29180,
                "slug": "packit-as-a-service-stg",
            },
            "pull_requests": [],
        },
        "repository": {
            "id": 184635124,
            "node_id": "MDEwOlJlcG9zaXRvcnkxODQ2MzUxMjQ=",
            "name": "hello-world",
            "full_name": "packit/hello-world",
            "private": False,
            "owner": {
                "login": "packit",
            },
            "html_url": "https://github.com/packit/hello-world",
        },
        "organization": {
            "login": "packit",
        },
        "sender": {
            "login": "lbarcziova",
        },
    }


@pytest.fixture()
def source_git_dist_git_pr_new_relationship():
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

    yield created


@pytest.fixture()
def bodhi_update_model(branch_project_event_model):
    group = BodhiUpdateGroupModel.create(
        run_model=PipelineModel.create(project_event=branch_project_event_model),
    )
    model = BodhiUpdateTargetModel.create(
        target=SampleValues.dist_git_branch,
        koji_nvrs=SampleValues.nvr,
        status="queued",
        bodhi_update_group=group,
        sidetag=SampleValues.sidetag,
    )
    model.set_alias(SampleValues.alias)
    model.set_web_url(SampleValues.bodhi_url)
    model.set_status("error")
    yield model


@pytest.fixture()
def successful_bodhi_update_model(branch_project_event_model):
    group = BodhiUpdateGroupModel.create(
        run_model=PipelineModel.create(project_event=branch_project_event_model),
    )
    model = BodhiUpdateTargetModel.create(
        target=SampleValues.dist_git_branch,
        koji_nvrs=SampleValues.nvr,
        status="queued",
        bodhi_update_group=group,
        sidetag=SampleValues.sidetag,
    )
    model.set_alias(SampleValues.alias)
    model.set_web_url(SampleValues.bodhi_url)
    model.set_status("success")
    yield model


@pytest.fixture()
def multiple_bodhi_update_runs(branch_project_event_model):
    group = BodhiUpdateGroupModel.create(
        run_model=PipelineModel.create(project_event=branch_project_event_model),
    )
    yield [
        BodhiUpdateTargetModel.create(
            target=SampleValues.dist_git_branch,
            koji_nvrs=SampleValues.nvr,
            status="queued",
            bodhi_update_group=group,
        ),
        BodhiUpdateTargetModel.create(
            target=SampleValues.different_dist_git_branch,
            koji_nvrs=SampleValues.different_nvr,
            status="queued",
            bodhi_update_group=group,
        ),
    ]


@pytest.fixture()
def a_scan(a_copr_build_for_pr):
    scan = a_copr_build_for_pr.add_scan(SampleValues.task_id)
    scan.status = SampleValues.scan_status_success
    scan.url = SampleValues.scan_url
    scan.issues_added_url = SampleValues.issues_added_url
    scan.issues_fixed_url = SampleValues.issues_fixed_url
    scan.scan_results_url = SampleValues.scan_results_url
    scan.issues_added_count = SampleValues.issues_added_count
    yield scan


@pytest.fixture()
def a_koji_tag_request(branch_project_event_model):
    group = KojiTagRequestGroupModel.create(
        run_model=PipelineModel.create(project_event=branch_project_event_model),
    )
    koji_tag_request_model = KojiTagRequestTargetModel.create(
        task_id=SampleValues.build_id,
        web_url=SampleValues.koji_web_url,
        target=SampleValues.target,
        sidetag=SampleValues.sidetag,
        nvr=SampleValues.nvr,
        koji_tag_request_group=group,
    )
    yield koji_tag_request_model
