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
from github import Github
from github.GitRelease import GitRelease as PyGithubRelease

from ogr.abstract import GitTag
from ogr.abstract import PullRequest, PRStatus
from ogr.services.github import GithubProject
from ogr.services.github import GithubRelease
from packit.api import PackitAPI
from packit.local_project import LocalProject
from packit_service.config import ServiceConfig
from packit_service.constants import SANDCASTLE_WORK_DIR
from packit_service.models import IssueModel
from packit_service.service.events import IssueCommentEvent
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.whitelist import Whitelist
from packit_service.worker.tasks import run_propose_update_comment_handler
from tests.spellbook import DATA_DIR, first_dict_value, get_parameters_from_results


@pytest.fixture(scope="module")
def issue_comment_propose_update_event():
    return json.loads(
        (DATA_DIR / "webhooks" / "github" / "issue_propose_update.json").read_text()
    )


@pytest.fixture(scope="module")
def mock_issue_comment_functionality():
    packit_yaml = (
        "{'specfile_path': 'packit.spec', 'synced_files': [],"
        "'jobs': [{'trigger': 'release', 'job': 'propose_downstream',"
        "'metadata': {'dist-git-branch': 'master'}}],"
        "'downstream_package_name': 'packit'}"
    )
    flexmock(
        GithubProject,
        get_file_content=lambda path, ref: packit_yaml,
        full_repo_name="packit-service/packit",
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)
    (
        flexmock(GithubProject)
        .should_receive("can_merge_pr")
        .with_args("phracek")
        .and_return(True)
    )
    flexmock(GithubProject).should_receive("issue_comment").and_return(None)
    flexmock(GithubProject).should_receive("issue_close").and_return(None)
    gr = GithubRelease(
        tag_name="0.5.1",
        url="packit-service/packit",
        created_at="",
        tarball_url="https://foo/bar",
        git_tag=flexmock(GitTag),
        project=flexmock(GithubProject),
        raw_release=flexmock(PyGithubRelease),
    )
    flexmock(GithubProject).should_receive("get_releases").and_return([gr])
    config = ServiceConfig()
    config.command_handler_work_dir = SANDCASTLE_WORK_DIR
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(config)
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Whitelist, check_and_report=True)


def test_issue_comment_propose_update_handler(
    mock_issue_comment_functionality, issue_comment_propose_update_event
):
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
        GithubProject,
        get_files=lambda ref, filter_regex: [],
        get_web_url=lambda: "https://github.com/the-namespace/the-repo",
        is_private=lambda: False,
    )

    flexmock(IssueCommentEvent, db_trigger=IssueModel(id=123))
    flexmock(IssueModel).should_receive("get_by_id").with_args(123).and_return(
        flexmock(issue_id=12345)
    )
    flexmock(Signature).should_receive("apply_async").once()

    processing_results = SteveJobs().process_message(issue_comment_propose_update_event)
    event_dict, package_config, job = get_parameters_from_results(processing_results)

    results = run_propose_update_comment_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job,
    )

    assert first_dict_value(results["job"])["success"]
