# MIT License
#
# Copyright (c) 2018-2019 Red Hat, Inc.

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

import json
from datetime import datetime

import pytest
from celery.canvas import Signature
from flexmock import flexmock

from ogr.abstract import GitTag
from ogr.abstract import PullRequest, PRStatus
from ogr.services.github import GithubProject, GithubRelease
from ogr.services.gitlab import GitlabProject, GitlabRelease
from packit.api import PackitAPI
from packit.distgit import DistGit
from packit.local_project import LocalProject
from packit_service.config import ServiceConfig
from packit_service.constants import SANDCASTLE_WORK_DIR
from packit_service.models import IssueModel
from packit_service.service.events import IssueCommentEvent, IssueCommentGitlabEvent
from packit_service.worker.jobs import SteveJobs
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
    flexmock(DistGit).should_receive("local_project").and_return(
        flexmock(git_project=flexmock(default_branch="main"))
    )
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

    flexmock(event_type, db_trigger=IssueModel(id=123))
    flexmock(IssueModel).should_receive("get_by_id").with_args(123).and_return(
        flexmock(issue_id=12345)
    )
    flexmock(Signature).should_receive("apply_async").once()

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
