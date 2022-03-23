# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json
from datetime import datetime

import pytest
import shutil
from celery.canvas import Signature
from flexmock import flexmock

from ogr.abstract import GitTag
from ogr.abstract import PRStatus
from ogr.read_only import PullRequestReadOnly
from ogr.services.github import GithubProject, GithubRelease
from ogr.services.gitlab import GitlabProject, GitlabRelease

from packit.api import PackitAPI
from packit.distgit import DistGit
from packit.config import JobConfigTriggerType
from packit.local_project import LocalProject

from packit_service.config import ServiceConfig
from packit_service.constants import SANDCASTLE_WORK_DIR, COMMENT_REACTION
from packit_service.models import (
    IssueModel,
    JobTriggerModelType,
    PipelineModel,
    ProposeDownstreamModel,
    ProposeDownstreamTargetModel,
    ProposeDownstreamStatus,
    ProposeDownstreamTargetStatus,
)
from packit_service.worker.events import IssueCommentEvent, IssueCommentGitlabEvent
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.tasks import run_propose_downstream_handler
from packit_service.worker.allowlist import Allowlist
from tests.spellbook import DATA_DIR, first_dict_value, get_parameters_from_results


def issue_comment_propose_downstream_event(forge):
    return json.loads(
        (DATA_DIR / "webhooks" / forge / "issue_propose_downstream.json").read_text()
    )


def mock_release_class(release_class, project_class, **kwargs):
    release = release_class(raw_release=flexmock(), project=flexmock(project_class))

    for prop, value in kwargs.items():
        flexmock(release).should_receive(prop).and_return(value)

    return release


@pytest.fixture(scope="module")
def mock_comment(request):
    project_class, release_class, forge, author = request.param

    packit_yaml = (
        "{'specfile_path': 'packit.spec', 'synced_files': [],"
        "'jobs': [{'trigger': 'release', 'job': 'propose_downstream',"
        "'metadata': {'dist-git-branch': 'main'}}],"
        "'downstream_package_name': 'packit'}"
    )
    flexmock(
        project_class,
        get_file_content=lambda path, ref: packit_yaml,
        full_repo_name="packit-service/packit",
        get_web_url=lambda: f"https://{forge}.com/packit-service/packit",
        default_branch="main",
    )
    (
        flexmock(project_class)
        .should_receive("can_merge_pr")
        .with_args(author)
        .and_return(True)
    )
    issue = flexmock()
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
    flexmock(project_class).should_receive("get_latest_release").and_return(gr)
    config = ServiceConfig()
    config.command_handler_work_dir = SANDCASTLE_WORK_DIR
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(config)
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
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
            IssueCommentEvent,
        ),
        (
            (GitlabProject, GitlabRelease, "gitlab", "shreyaspapi"),
            IssueCommentGitlabEvent,
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

    flexmock(PackitAPI).should_receive("sync_release").and_return(
        PullRequestReadOnly(
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
    )
    flexmock(
        project_class,
        get_files=lambda ref, filter_regex: [],
        is_private=lambda: False,
    )

    flexmock(LocalProject).should_receive("reset").with_args("HEAD").once()

    flexmock(IssueCommentGitlabEvent).should_receive("db_trigger").and_return(
        flexmock(id=123, job_config_trigger_type=JobConfigTriggerType.release)
    )
    trigger = flexmock(
        id=123,
        job_config_trigger_type=JobConfigTriggerType.release,
        job_trigger_model_type=JobTriggerModelType.issue,
    )
    flexmock(IssueModel).should_receive("get_or_create").and_return(trigger)

    run_model = flexmock(PipelineModel)
    propose_downstream_model = flexmock(propose_downstream_targets=[])
    flexmock(ProposeDownstreamModel).should_receive("create_with_new_run").with_args(
        status=ProposeDownstreamStatus.running,
        trigger_model=trigger,
    ).and_return(propose_downstream_model, run_model).once()

    model = flexmock(status="queued")
    flexmock(ProposeDownstreamTargetModel).should_receive("create").with_args(
        status=ProposeDownstreamTargetStatus.queued
    ).and_return(model).once()
    flexmock(model).should_receive("set_status").with_args(
        status=ProposeDownstreamTargetStatus.running
    ).once()
    flexmock(model).should_receive("set_branch").with_args(branch="main").once()
    flexmock(model).should_receive("set_downstream_pr_url").with_args(
        downstream_pr_url="https://xyz"
    ).once()
    flexmock(model).should_receive("set_status").with_args(
        status=ProposeDownstreamTargetStatus.submitted
    ).once()
    flexmock(model).should_receive("set_start_time").once()
    flexmock(model).should_receive("set_finished_time").once()
    flexmock(model).should_receive("set_logs").once()
    flexmock(propose_downstream_model).should_receive("set_status").with_args(
        status=ProposeDownstreamStatus.finished
    ).once()

    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").once().and_return()
    flexmock(shutil).should_receive("rmtree").with_args("")

    processing_results = SteveJobs().process_message(comment_event)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)

    results = run_propose_downstream_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]
