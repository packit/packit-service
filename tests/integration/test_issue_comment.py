# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json
import shutil
from datetime import datetime

import pytest
from celery.canvas import group
from flexmock import flexmock
from ogr.abstract import GitTag, PRStatus
from ogr.read_only import PullRequestReadOnly
from ogr.services.github import GithubProject, GithubRelease
from ogr.services.gitlab import GitlabProject, GitlabRelease
from ogr.services.pagure import PagureProject
from packit.api import PackitAPI
from packit.config import JobConfigTriggerType, PackageConfig
from packit.distgit import DistGit
from packit.exceptions import PackitException
from packit.local_project import LocalProject, LocalProjectBuilder
from packit.utils.koji_helper import KojiHelper

from packit_service.config import ServiceConfig
from packit_service.constants import COMMENT_REACTION, TASK_ACCEPTED
from packit_service.events import github, gitlab
from packit_service.models import (
    BodhiUpdateGroupModel,
    BodhiUpdateTargetModel,
    IssueModel,
    KojiBuildGroupModel,
    KojiBuildTargetModel,
    PipelineModel,
    ProjectEventModel,
    ProjectEventModelType,
    SyncReleaseJobType,
    SyncReleaseModel,
    SyncReleasePullRequestModel,
    SyncReleaseStatus,
    SyncReleaseTargetModel,
    SyncReleaseTargetStatus,
)
from packit_service.package_config_getter import PackageConfigGetter
from packit_service.service.urls import get_propose_downstream_info_url
from packit_service.worker.allowlist import Allowlist
from packit_service.worker.celery_task import CeleryTask
from packit_service.worker.handlers import distgit
from packit_service.worker.handlers.distgit import (
    RetriggerDownstreamKojiBuildHandler,
)
from packit_service.worker.helpers.sync_release.propose_downstream import (
    ProposeDownstreamJobHelper,
)
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.reporting import BaseCommitStatus
from packit_service.worker.tasks import (
    run_issue_comment_retrigger_bodhi_update,
    run_propose_downstream_handler,
    run_retrigger_downstream_koji_build,
)
from tests.spellbook import DATA_DIR, first_dict_value, get_parameters_from_results


def issue_comment_propose_downstream_event(forge):
    return json.loads(
        (DATA_DIR / "webhooks" / forge / "issue_propose_downstream.json").read_text(),
    )


def mock_release_class(release_class, project_class, **kwargs):
    release = release_class(raw_release=flexmock(), project=flexmock(project_class))

    for prop, value in kwargs.items():
        flexmock(release).should_receive(prop).and_return(value)

    return release


@pytest.fixture(scope="module")
def mock_comment(request):
    project_class, release_class, forge, author = request.param

    packit_yaml = """\
downstream_package_name: packit
specfile_path: packit.spec
files_to_sync:
  - packit.spec

jobs:
  - job: propose_downstream
    trigger: release
    dist_git_branches:
      - main
"""
    flexmock(
        project_class,
        get_file_content=lambda path, ref: packit_yaml,
        full_repo_name="packit-service/packit",
        get_web_url=lambda: f"https://{forge}.com/packit-service/packit",
        default_branch="main",
        get_sha_from_tag=lambda tag_name: "123456",
    )
    (flexmock(project_class).should_receive("can_merge_pr").with_args(author).and_return(True))
    issue = flexmock(
        description="Packit failed on creating pull-requests in dist-git "
        "(https://src.fedoraproject.org/rpms/packit.git):",
    )
    flexmock(SteveJobs).should_receive("search_distgit_config_in_issue").never()
    flexmock(PackageConfigGetter).should_call("get_package_config_from_repo").with_args(
        base_project=None,
        project=project_class,
        reference="123456",
        pr_id=None,
        fail_when_missing=True,
    ).and_return(PackageConfig)

    flexmock(project_class).should_receive("get_issue").and_return(issue)
    comment = flexmock()
    flexmock(issue).should_receive("get_comment").and_return(comment)
    flexmock(comment).should_receive("add_reaction").with_args(COMMENT_REACTION).once()
    flexmock(issue).should_receive("close").and_return(issue)
    gr = mock_release_class(
        release_class=release_class,
        project_class=project_class,
        tag_name="0.5.1",
        url="packit-service/packit",
        created_at="",
        tarball_url="https://foo/bar",
        git_tag=flexmock(GitTag),
    )
    flexmock(project_class).should_receive("get_releases").and_return([gr])
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(LocalProjectBuilder, _refresh_the_state=lambda *args: flexmock())
    lp = flexmock(git_project=flexmock(default_branch="main"))
    lp.working_dir = ""
    flexmock(DistGit).should_receive("local_project").and_return(lp)
    flexmock(Allowlist, check_and_report=True)

    yield project_class, issue_comment_propose_downstream_event(forge)


@pytest.mark.parametrize(
    "mock_comment,event_type",
    [
        (
            (GithubProject, GithubRelease, "github", "phracek"),
            github.issue.Comment,
        ),
        (
            (GitlabProject, GitlabRelease, "gitlab", "shreyaspapi"),
            gitlab.issue.Comment,
        ),
    ],
    indirect=[
        "mock_comment",
    ],
)
def test_issue_comment_propose_downstream_handler(
    mock_comment,
    event_type,
):
    project_class, comment_event = mock_comment
    pr = PullRequestReadOnly(
        title="foo",
        description="bar",
        target_branch="baz",
        source_branch="yet",
        id=1,
        status=PRStatus.open,
        url="https://xyz",
        author="me",
        created=datetime.now(),
    )
    target_project = (
        flexmock(namespace="downstream-namespace", repo="downstream-repo")
        .should_receive("get_web_url")
        .and_return("https://src.fedoraproject.org/rpms/downstream-repo")
        .mock()
    )
    pr._target_project = target_project
    flexmock(pr).should_receive("comment")
    flexmock(PackitAPI).should_receive("sync_release").and_return((pr, {}))
    flexmock(
        project_class,
        get_files=lambda ref, recursive: [".packit.yaml", "tox.ini"],
        is_private=lambda: False,
    )

    flexmock(LocalProject).should_receive("git_repo").and_return(
        flexmock(
            head=flexmock()
            .should_receive("reset")
            .with_args("HEAD", index=True, working_tree=True)
            .once()
            .mock(),
            git=flexmock(clear_cache=lambda: None),
            submodules=[
                flexmock()
                .should_receive("update")
                .with_args(init=True, recursive=True, force=True)
                .once()
                .mock()
            ],
        ),
    )

    db_project_object = flexmock(
        id=123,
        job_config_trigger_type=JobConfigTriggerType.release,
        project_event_model_type=ProjectEventModelType.issue,
    )
    flexmock(gitlab.issue.Comment).should_receive("db_project_object").and_return(
        db_project_object,
    )
    db_project_event = (
        flexmock(
            id=123456,
            project_event_model_type=ProjectEventModelType.issue,
        )
        .should_receive("get_project_event_object")
        .and_return(db_project_object)
        .mock()
    )
    flexmock(ProjectEventModel).should_receive("get_or_create").with_args(
        type=ProjectEventModelType.issue,
        event_id=123,
        commit_sha=None,
    ).and_return(db_project_event)
    flexmock(IssueModel).should_receive("get_or_create").and_return(db_project_object)

    run_model = flexmock(PipelineModel)
    propose_downstream_model = flexmock(sync_release_targets=[])
    flexmock(SyncReleaseModel).should_receive("create_with_new_run").with_args(
        status=SyncReleaseStatus.running,
        project_event_model=db_project_event,
        job_type=SyncReleaseJobType.propose_downstream,
        package_name="packit",
    ).and_return(propose_downstream_model, run_model).once()
    sync_release_pr_model = flexmock(sync_release_targets=[flexmock(), flexmock()])
    flexmock(SyncReleasePullRequestModel).should_receive("get_or_create").with_args(
        pr_id=1,
        namespace="downstream-namespace",
        repo_name="downstream-repo",
        project_url="https://src.fedoraproject.org/rpms/downstream-repo",
        target_branch=str,
        url=str,
    ).and_return(sync_release_pr_model)
    model = flexmock(status="queued", id=1234, branch="main")
    flexmock(SyncReleaseTargetModel).should_receive("create").with_args(
        status=SyncReleaseTargetStatus.queued,
        branch="main",
    ).and_return(model).once()
    flexmock(model).should_receive("set_status").with_args(
        status=SyncReleaseTargetStatus.running,
    ).once()
    flexmock(model).should_receive("set_downstream_pr_url").with_args(
        downstream_pr_url="https://xyz",
    )
    flexmock(model).should_receive("set_downstream_prs").with_args(
        downstream_prs=[sync_release_pr_model],
    ).once()
    flexmock(model).should_receive("set_status").with_args(
        status=SyncReleaseTargetStatus.submitted,
    ).once()
    flexmock(model).should_receive("set_start_time").once()
    flexmock(model).should_receive("set_finished_time").once()
    flexmock(model).should_receive("set_logs").once()
    flexmock(propose_downstream_model).should_receive("set_status").with_args(
        status=SyncReleaseStatus.finished,
    ).once()

    flexmock(group).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    flexmock(ProposeDownstreamJobHelper).should_receive(
        "report_status_to_all",
    ).with_args(
        description=TASK_ACCEPTED,
        state=BaseCommitStatus.pending,
        url="",
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()
    flexmock(shutil).should_receive("rmtree").with_args("")

    url = get_propose_downstream_info_url(model.id)
    flexmock(ProposeDownstreamJobHelper).should_receive(
        "report_status_for_branch",
    ).with_args(
        branch="main",
        description="Starting propose downstream...",
        state=BaseCommitStatus.running,
        url=url,
    )

    flexmock(ProposeDownstreamJobHelper).should_receive(
        "report_status_for_branch",
    ).with_args(
        branch="main",
        description="Propose downstream finished successfully.",
        state=BaseCommitStatus.success,
        url=url,
    )

    processing_results = SteveJobs().process_message(comment_event)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    results = run_propose_downstream_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]


@pytest.fixture()
def mock_repository_issue_retriggering():
    flexmock(GithubProject).should_receive("is_private").and_return(False)

    issue = flexmock(
        description="\n".join(
            (
                (
                    "Packit failed on creating pull-requests in dist-git"
                    " (https://src.fedoraproject.org/rpms/python-teamcity-messages):"
                ),
                "| dist-git branch | error |",
                "| --------------- | ----- |",
                "| `f37` | `` |",
                "| `f38` | `` |",
                (
                    "You can retrigger the update by adding a comment"
                    " (`/packit propose-downstream`) into this issue."
                ),
            ),
        ),
        get_comments=list,
        comment=lambda message: None,
    )
    project = flexmock(GithubProject).should_receive("get_issue").and_return(issue).mock()
    flexmock(PackageConfigGetter).should_call("get_package_config_from_repo").with_args(
        project=PagureProject,
        fail_when_missing=False,
    ).and_return(PackageConfig)

    project.should_receive("get_releases").and_return([flexmock(tag_name="123")])
    project.should_receive("get_sha_from_tag").and_return("abcdef")
    project.should_receive("has_write_access").and_return(True)
    db_project_object = flexmock(
        id=123,
        job_config_trigger_type=JobConfigTriggerType.release,
        project_event_model_type=ProjectEventModelType.issue,
    )
    flexmock(github.issue.Comment).should_receive("db_project_object").and_return(
        db_project_object,
    )

    db_project_event = (
        flexmock().should_receive("get_project_event_object").and_return(db_project_object).mock()
    )

    flexmock(IssueModel).should_receive("get_or_create").and_return(db_project_object)
    flexmock(ProjectEventModel).should_receive("get_or_create").and_return(
        db_project_event,
    )
    flexmock(PipelineModel).should_receive("create")

    nvr_f37 = "package-1.2.3-1.fc37"

    koji_build_f37 = flexmock(
        id=12,
        target="f37",
        status="queued",
        sidetag=None,
        nvr=nvr_f37,
        set_status=lambda x: None,
        set_task_id=lambda x: None,
        set_web_url=lambda x: None,
        set_build_logs_urls=lambda x: None,
        set_data=lambda x: None,
    )

    nvr_f38 = "package-1.2.3-1.fc38"

    koji_build_f38 = flexmock(
        id=13,
        target="f38",
        status="queued",
        sidetag=None,
        nvr=nvr_f38,
        set_status=lambda x: None,
        set_task_id=lambda x: None,
        set_web_url=lambda x: None,
        set_build_logs_urls=lambda x: None,
        set_data=lambda x: None,
    )

    flexmock(KojiBuildTargetModel).should_receive("create")
    flexmock(KojiBuildTargetModel).should_receive(
        "get_all_successful_or_in_progress_by_nvr",
    ).with_args(nvr_f37).and_return({koji_build_f37})
    flexmock(KojiBuildTargetModel).should_receive(
        "get_all_successful_or_in_progress_by_nvr",
    ).with_args(nvr_f38).and_return({koji_build_f38})
    flexmock(KojiBuildGroupModel).should_receive("create").and_return(
        flexmock(grouped_targets=[koji_build_f38, koji_build_f37]),
    )

    comment = flexmock()
    flexmock(issue).should_receive("get_comment").and_return(comment)
    flexmock(comment).should_receive("add_reaction").with_args(COMMENT_REACTION).once()
    flexmock(Allowlist, check_and_report=True)
    flexmock(group).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").and_return()


@pytest.fixture()
def github_repository_issue_comment_retrigger_bodhi_update():
    return json.loads(
        (
            DATA_DIR
            / "webhooks"
            / "github"
            / "repository_issue_comment_retrigger_bodhi_update.json"
        ).read_text(),
    )


def test_issue_comment_retrigger_bodhi_update_handler(
    mock_repository_issue_retriggering,
    github_repository_issue_comment_retrigger_bodhi_update,
):
    processing_results = SteveJobs().process_message(
        github_repository_issue_comment_retrigger_bodhi_update,
    )
    event_dict, _, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    flexmock(PackitAPI).should_receive("create_update").with_args(
        dist_git_branch="f38",
        update_type="enhancement",
        koji_builds=["python-teamcity-messages.fc38"],
        sidetag=None,
        alias=None,
    ).and_return(("alias", "url"))
    flexmock(KojiHelper).should_receive("get_latest_candidate_build").with_args(
        "python-teamcity-messages",
        "f38",
    ).and_return(
        {
            "nvr": "python-teamcity-messages.fc38",
            "build_id": 2,
            "state": 1,
            "task_id": 123,
        },
    )
    flexmock(PackitAPI).should_receive("create_update").with_args(
        dist_git_branch="f37",
        update_type="enhancement",
        koji_builds=["python-teamcity-messages.fc37"],
        sidetag=None,
        alias=None,
    ).and_return(("alias", "url"))
    flexmock(KojiHelper).should_receive("get_latest_candidate_build").with_args(
        "python-teamcity-messages",
        "f37",
    ).and_return(
        {
            "nvr": "python-teamcity-messages.fc37",
            "build_id": 1,
            "state": 1,
            "task_id": 123,
        },
    )

    run_model_flexmock = flexmock()
    flexmock(PipelineModel).should_receive("create").and_return(run_model_flexmock)
    group_model = flexmock(
        id=23,
        grouped_targets=[
            flexmock(
                target="f37",
                koji_nvrs="python-teamcity-messages.fc37",
                sidetag=None,
                set_status=lambda x: None,
                set_data=lambda x: None,
                set_web_url=lambda x: None,
                set_alias=lambda x: None,
                set_update_creation_time=lambda x: None,
            ),
            flexmock(
                target="f38",
                koji_nvrs="python-teamcity-messages.fc38",
                sidetag=None,
                set_status=lambda x: None,
                set_data=lambda x: None,
                set_web_url=lambda x: None,
                set_alias=lambda x: None,
                set_update_creation_time=lambda x: None,
            ),
        ],
    )
    flexmock(BodhiUpdateGroupModel).should_receive("create").and_return(group_model)
    flexmock(BodhiUpdateTargetModel).should_receive(
        "get_all_successful_or_in_progress_by_nvrs",
    ).with_args("python-teamcity-messages.fc38").and_return(set())
    flexmock(BodhiUpdateTargetModel).should_receive("create").with_args(
        target="f38",
        koji_nvrs="python-teamcity-messages.fc38",
        sidetag=None,
        status="queued",
        bodhi_update_group=group_model,
    ).and_return().once()
    flexmock(BodhiUpdateTargetModel).should_receive(
        "get_all_successful_or_in_progress_by_nvrs",
    ).with_args("python-teamcity-messages.fc37").and_return(set())
    flexmock(BodhiUpdateTargetModel).should_receive("create").with_args(
        target="f37",
        koji_nvrs="python-teamcity-messages.fc37",
        sidetag=None,
        status="queued",
        bodhi_update_group=group_model,
    ).and_return().once()

    results = run_issue_comment_retrigger_bodhi_update(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]


@pytest.fixture()
def github_repository_issue_comment_retrigger_koji_build():
    return json.loads(
        (
            DATA_DIR / "webhooks" / "github" / "repository_issue_comment_retrigger_koji_build.json"
        ).read_text(),
    )


def test_issue_comment_retrigger_koji_build_handler(
    mock_repository_issue_retriggering,
    github_repository_issue_comment_retrigger_koji_build,
):
    processing_results = SteveJobs().process_message(
        github_repository_issue_comment_retrigger_koji_build,
    )
    event_dict, _, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    dg = flexmock(local_project=flexmock(git_url="an url"))
    dg.should_receive("get_nvr").with_args("f37").and_return("package-1.2.3-1.fc37")
    dg.should_receive("get_nvr").with_args("f38").and_return("package-1.2.3-1.fc38")
    packit_api = flexmock(dg=dg)
    packit_api.should_receive("build").with_args(
        dist_git_branch="f37",
        scratch=False,
        nowait=True,
        from_upstream=False,
        koji_target=None,
    ).and_return("")
    packit_api.should_receive("build").with_args(
        dist_git_branch="f38",
        scratch=False,
        nowait=True,
        from_upstream=False,
        koji_target=None,
    ).and_return("")
    flexmock(RetriggerDownstreamKojiBuildHandler).should_receive(
        "packit_api",
    ).and_return(packit_api)
    flexmock(RetriggerDownstreamKojiBuildHandler).should_receive(
        "local_project",
    ).and_return(flexmock())

    results = run_retrigger_downstream_koji_build(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]


def test_issue_comment_retrigger_koji_build_error_msg(
    mock_repository_issue_retriggering,
    github_repository_issue_comment_retrigger_koji_build,
):
    processing_results = SteveJobs().process_message(
        github_repository_issue_comment_retrigger_koji_build,
    )
    event_dict, _, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    flexmock(CeleryTask).should_receive("is_last_try").and_return(True)
    error_msg = "error abc"
    dg = flexmock(local_project=flexmock(git_url="an url"))
    dg.should_receive("get_nvr").with_args("f37").and_return("package-1.2.3-1.fc37")
    dg.should_receive("get_nvr").with_args("f38").and_return("package-1.2.3-1.fc38")
    packit_api = flexmock(dg=dg)
    packit_api.should_receive("build").with_args(
        dist_git_branch="f38",
        scratch=False,
        nowait=True,
        from_upstream=False,
        koji_target=None,
    ).and_return("")
    packit_api.should_receive("build").with_args(
        dist_git_branch="f37",
        scratch=False,
        nowait=True,
        from_upstream=False,
        koji_target=None,
    ).and_raise(PackitException, error_msg)
    # flexmock(JobConfig).should_receive("issue_repository").and_return(
    #  "a repo"
    # )
    flexmock(RetriggerDownstreamKojiBuildHandler).should_receive(
        "packit_api",
    ).and_return(packit_api)
    msg = (
        "Packit failed on creating Koji build in dist-git (an url):"
        "\n\n<table><tr>"
        "<th>dist-git branch</th><th>error</th></tr>"
        "<tr><td><code>f37</code></td>"
        '<td>See <a href="https://localhost/jobs/koji/12">https://localhost/jobs/koji/12</a></td>'
        "</tr>\n</table>\n\n"
        "Fedora Koji build was re-triggered by comment in issue 1.\n\n"
        "You can retrigger the build by adding a comment "
        "(`/packit koji-build`) into this issue.\n\n"
        "---\n\n*Get in [touch with us](https://packit.dev/#contact) if you need some help.*\n"
    )
    flexmock(distgit).should_receive("report_in_issue_repository").with_args(
        issue_repository=None,
        service_config=ServiceConfig,
        title=("Fedora Koji build failed to be triggered"),
        message=msg,
        comment_to_existing=msg,
    ).once()

    run_retrigger_downstream_koji_build(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
