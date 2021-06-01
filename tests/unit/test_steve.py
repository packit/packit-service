# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
Let's test that Steve's as awesome as we think he is.
"""
from json import dumps, load

import pytest
from celery.canvas import Signature
from flexmock import flexmock
from github import Github
from ogr.services.github import GithubProject
from packit.api import PackitAPI
from packit.config import JobConfigTriggerType
from packit.distgit import DistGit
from packit.local_project import LocalProject

from packit_service.config import ServiceConfig
from packit_service.constants import SANDCASTLE_WORK_DIR
from packit_service.service.db_triggers import AddReleaseDbTrigger
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.tasks import run_propose_downstream_handler
from packit_service.worker.allowlist import Allowlist
from tests.spellbook import first_dict_value, get_parameters_from_results, DATA_DIR

EVENT = {
    "action": "published",
    "release": {"tag_name": "1.2.3"},
    "repository": {
        "name": "bar",
        "html_url": "https://github.com/the-namespace/the-repo",
        "owner": {"login": "foo"},
    },
}


@pytest.mark.parametrize(
    "event,private,enabled_private_namespaces,success",
    (
        (EVENT, False, set(), True),
        (EVENT, True, {"github.com/the-namespace"}, True),
        (EVENT, True, set(), False),
    ),
)
def test_process_message(event, private, enabled_private_namespaces, success):
    packit_yaml = {
        "specfile_path": "bar.spec",
        "synced_files": [],
        "jobs": [{"trigger": "release", "job": "propose_downstream"}],
    }
    flexmock(Github, get_repo=lambda full_name_or_id: None)
    gh_project = flexmock(
        GithubProject,
        get_file_content=lambda path, ref: dumps(packit_yaml),
        full_repo_name="the-namespace/the-repo",
        get_files=lambda ref, filter_regex: [],
        get_sha_from_tag=lambda tag_name: "12345",
        get_web_url=lambda: "https://github.com/the-namespace/the-repo",
        is_private=lambda: private,
    )
    gh_project.default_branch = "main"

    lp = flexmock(LocalProject, refresh_the_arguments=lambda: None)
    lp.git_project = gh_project
    flexmock(DistGit).should_receive("local_project").and_return(lp)
    # reset of the upstream repo
    flexmock(LocalProject).should_receive("reset").with_args("HEAD").times(
        1 if success else 0
    )

    config = ServiceConfig(enabled_private_namespaces=enabled_private_namespaces)
    config.command_handler_work_dir = SANDCASTLE_WORK_DIR
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(config)

    flexmock(PackitAPI).should_receive("sync_release").with_args(
        dist_git_branch="main", tag="1.2.3"
    ).times(1 if success else 0)

    flexmock(AddReleaseDbTrigger).should_receive("db_trigger").and_return(
        flexmock(job_config_trigger_type=JobConfigTriggerType.release, id=1)
    )
    flexmock(Allowlist, check_and_report=True)
    flexmock(Signature).should_receive("apply_async").times(1 if success else 0)

    processing_results = SteveJobs().process_message(event)
    if not success:
        assert processing_results == []
        return

    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )

    results = run_propose_downstream_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert "propose_downstream" in next(iter(results["job"]))
    assert first_dict_value(results["job"])["success"]


@pytest.fixture()
def github_push():
    with open(DATA_DIR / "webhooks" / "github" / "push.json") as outfile:
        return load(outfile)


def test_ignore_delete_branch(github_push):
    flexmock(
        GithubProject,
        is_private=lambda: False,
    )

    processing_results = SteveJobs().process_message(github_push)

    assert processing_results == []
