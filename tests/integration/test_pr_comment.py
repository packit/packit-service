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

import pytest
from celery.canvas import Signature
from flexmock import flexmock
from github import Github
from ogr.services.github import GithubProject
from packit.config import JobConfigTriggerType
from packit.local_project import LocalProject

from packit_service.config import ServiceConfig
from packit_service.constants import SANDCASTLE_WORK_DIR
from packit_service.models import PullRequestModel
from packit_service.service.db_triggers import AddPullRequestDbTrigger
from packit_service.worker.build.copr_build import CoprBuildJobHelper
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.result import TaskResults
from packit_service.worker.whitelist import Whitelist
from packit_service.worker.tasks import run_pr_comment_copr_build_handler
from tests.spellbook import DATA_DIR, first_dict_value, get_parameters_from_results


@pytest.fixture(scope="module")
def pr_copr_build_comment_event():
    return json.loads(
        (DATA_DIR / "webhooks" / "github" / "pr_comment_copr_build.json").read_text()
    )


@pytest.fixture(scope="module")
def pr_build_comment_event():
    return json.loads(
        (DATA_DIR / "webhooks" / "github" / "pr_comment_build.json").read_text()
    )


@pytest.fixture(scope="module")
def pr_embedded_command_comment_event():
    return json.loads(
        (
            DATA_DIR / "webhooks" / "github" / "pr_comment_embedded_command.json"
        ).read_text()
    )


@pytest.fixture(scope="module")
def pr_empty_comment_event():
    return json.loads(
        (DATA_DIR / "webhooks" / "github" / "pr_comment_empty.json").read_text()
    )


@pytest.fixture(scope="module")
def pr_packit_only_comment_event():
    return json.loads(
        (
            DATA_DIR / "webhooks" / "github" / "issue_comment_packit_only.json"
        ).read_text()
    )


@pytest.fixture(scope="module")
def pr_wrong_packit_comment_event():
    return json.loads(
        (
            DATA_DIR / "webhooks" / "github" / "issue_comment_wrong_packit_command.json"
        ).read_text()
    )


@pytest.fixture(
    params=[
        [
            {
                "trigger": "pull_request",
                "job": "copr_build",
                "metadata": {"targets": "fedora-rawhide-x86_64"},
            }
        ],
        [
            {
                "trigger": "pull_request",
                "job": "tests",
                "metadata": {"targets": "fedora-rawhide-x86_64"},
            }
        ],
        [
            {
                "trigger": "pull_request",
                "job": "copr_build",
                "metadata": {"targets": "fedora-rawhide-x86_64"},
            },
            {
                "trigger": "pull_request",
                "job": "tests",
                "metadata": {"targets": "fedora-rawhide-x86_64"},
            },
        ],
    ]
)
def mock_pr_comment_functionality(request):
    packit_yaml = (
        "{'specfile_path': '', 'synced_files': [], 'jobs': " + str(request.param) + "}"
    )
    flexmock(
        GithubProject,
        full_repo_name="packit-service/hello-world",
        get_file_content=lambda path, ref: packit_yaml,
        get_web_url=lambda: "https://github.com/the-namespace/the-repo",
        get_pr=lambda pr_id: flexmock(head_commit="12345"),
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)

    config = ServiceConfig()
    config.command_handler_work_dir = SANDCASTLE_WORK_DIR
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(config)
    trigger = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request, id=123
    )
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    flexmock(PullRequestModel).should_receive("get_by_id").with_args(123).and_return(
        trigger
    )
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Whitelist, check_and_report=True)


def one_job_finished_with_msg(results: dict, msg: str):
    for value in results.values():
        assert value["success"]
        if value["details"]["msg"] == msg:
            break
    else:
        raise AssertionError(f"None of the jobs finished with {msg!r}")


def test_pr_comment_copr_build_handler(
    mock_pr_comment_functionality, pr_copr_build_comment_event
):
    flexmock(CoprBuildJobHelper).should_receive("run_copr_build").and_return(
        TaskResults(success=True, details={})
    ).once()
    (
        flexmock(GithubProject)
        .should_receive("can_merge_pr")
        .with_args("phracek")
        .and_return(True)
        .once()
    )
    flexmock(GithubProject).should_receive("get_files").and_return(["foo.spec"])
    flexmock(GithubProject).should_receive("get_web_url").and_return(
        "https://github.com/the-namespace/the-repo"
    )
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(Signature).should_receive("apply_async").once()

    processing_results = SteveJobs().process_message(pr_copr_build_comment_event)
    event_dict, package_config, job = get_parameters_from_results(processing_results)

    results = run_pr_comment_copr_build_handler(
        package_config=package_config, event=event_dict, job_config=job,
    )
    assert first_dict_value(results["job"])["success"]


def test_pr_comment_build_handler(
    mock_pr_comment_functionality, pr_build_comment_event
):
    flexmock(CoprBuildJobHelper).should_receive("run_copr_build").and_return(
        TaskResults(success=True, details={})
    )
    (
        flexmock(GithubProject)
        .should_receive("can_merge_pr")
        .with_args("phracek")
        .and_return(True)
        .once()
    )
    flexmock(GithubProject, get_files="foo.spec")
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(Signature).should_receive("apply_async").once()

    processing_results = SteveJobs().process_message(pr_build_comment_event)
    event_dict, package_config, job = get_parameters_from_results(processing_results)

    results = run_pr_comment_copr_build_handler(
        package_config=package_config, event=event_dict, job_config=job,
    )
    assert first_dict_value(results["job"])["success"]


@pytest.mark.parametrize(
    "comment",
    (
        "",
        " ",
        "   ",
        "some unrelated",
        "some\nmore\nunrelated\ntext",
        "even\nsome → unicode",
        " stuff",
        " \n ",
        "x ",
    ),
)
def test_pr_comment_invalid(comment):
    s = SteveJobs()
    command, err_msg = s.find_packit_command(comment)
    assert len(command) == 0
    assert err_msg


@pytest.mark.parametrize(
    "comments_list",
    (
        "/packit build",
        "/packit build ",
        "/packit  build ",
        " /packit build",
        " /packit build ",
        "asd\n/packit build\n",
        "asd\n /packit build \n",
        "Should be fixed now, lets /packit build it.",
    ),
)
def test_pr_embedded_command_handler(
    mock_pr_comment_functionality, pr_embedded_command_comment_event, comments_list
):
    pr_embedded_command_comment_event["comment"]["body"] = comments_list
    flexmock(CoprBuildJobHelper).should_receive("run_copr_build").and_return(
        TaskResults(success=True, details={})
    )
    (
        flexmock(GithubProject)
        .should_receive("can_merge_pr")
        .with_args("phracek")
        .and_return(True)
        .once()
    )
    flexmock(GithubProject, get_files="foo.spec")
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(Signature).should_receive("apply_async").once()

    processing_results = SteveJobs().process_message(pr_embedded_command_comment_event)
    event_dict, package_config, job = get_parameters_from_results(processing_results)

    results = run_pr_comment_copr_build_handler(
        package_config=package_config, event=event_dict, job_config=job,
    )

    assert first_dict_value(results["job"])["success"]


def test_pr_comment_empty_handler(
    mock_pr_comment_functionality, pr_empty_comment_event
):
    flexmock(GithubProject).should_receive("is_private").and_return(False)

    results = SteveJobs().process_message(pr_empty_comment_event)
    msg = "comment '' is empty."
    one_job_finished_with_msg(results, msg)


def test_pr_comment_packit_only_handler(
    mock_pr_comment_functionality, pr_packit_only_comment_event
):
    flexmock(GithubProject).should_receive("is_private").and_return(False)

    results = SteveJobs().process_message(pr_packit_only_comment_event)
    msg = "comment '/packit' does not contain a packit-service command."
    one_job_finished_with_msg(results, msg)


def test_pr_comment_wrong_packit_command_handler(
    mock_pr_comment_functionality, pr_wrong_packit_comment_event
):
    flexmock(GithubProject).should_receive("is_private").and_return(False)

    results = SteveJobs().process_message(pr_wrong_packit_comment_event)
    msg = "comment '/packit foobar' does not contain a valid packit-service command."
    one_job_finished_with_msg(results, msg)
