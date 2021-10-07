# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json
from datetime import datetime

import pytest
import shutil
from celery.canvas import Signature
from flexmock import flexmock

from ogr.abstract import GitTag
from ogr.abstract import PullRequest, PRStatus
from ogr.services.github import GithubProject, GithubRelease
from ogr.services.gitlab import GitlabProject, GitlabRelease
from packit.api import PackitAPI
from packit.distgit import DistGit
from packit.config import JobConfigTriggerType
from packit.local_project import LocalProject
from packit_service.config import ServiceConfig
from packit_service.constants import SANDCASTLE_WORK_DIR
from packit_service.models import IssueModel
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
    flexmock(project_class).should_receive("issue_comment").and_return(None)
    issue = flexmock()
    flexmock(project_class).should_receive("get_issue").and_return(issue)
    comment = flexmock()
    flexmock(issue).should_receive("get_comment").and_return(comment)
    flexmock(comment).should_receive("add_reaction").with_args("+1").once()
    flexmock(project_class).should_receive("issue_close").and_return(None)
    gr = release_class(
        tag_name="0.5.1",
        url="packit-service/packit",
        created_at="",
        tarball_url="https://foo/bar",
        git_tag=flexmock(GitTag),
        project=flexmock(project_class),
        raw_release=flexmock(),
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
        PullRequest(
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
    flexmock(IssueModel).should_receive("get_or_create").and_return(
        flexmock(id=123, job_config_trigger_type=JobConfigTriggerType.release)
    )
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").once().and_return()
    flexmock(shutil).should_receive("rmtree").with_args("")

    processing_results = SteveJobs().process_message(comment_event)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )

    results = run_propose_downstream_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]
