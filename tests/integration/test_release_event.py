import json

import pytest
from flexmock import flexmock
from github import Github

from packit.api import PackitAPI
from packit.config import JobConfigTriggerType
from packit.fedpkg import FedPKG
from packit.local_project import LocalProject
from packit_service import sentry_integration
from packit_service.config import ServiceConfig
from packit_service.constants import SANDCASTLE_WORK_DIR
from packit_service.service.db_triggers import AddReleaseDbTrigger
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.whitelist import Whitelist
from tests.spellbook import DATA_DIR


@pytest.fixture()
def release_event():
    return json.loads((DATA_DIR / "webhooks" / "github_release_event.json").read_text())


def test_dist_git_push_release_handle(release_event):
    packit_yaml = (
        "{'specfile_path': 'hello-world.spec', 'synced_files': []"
        ", jobs: [{trigger: release, job: propose_downstream, metadata: {targets:[]}}]}"
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)
    project = flexmock(
        get_file_content=lambda path, ref: packit_yaml,
        full_repo_name="packit-service/hello-world",
        repo="hello-world",
        get_files=lambda ref, filter_regex: [],
        get_sha_from_tag=lambda tag_name: "123456",
        get_web_url=lambda: "https://github.com/packit-service/hello-world",
        is_private=lambda: False,
    )
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Whitelist, check_and_report=True)
    config = ServiceConfig()
    config.command_handler_work_dir = SANDCASTLE_WORK_DIR
    config.get_project = lambda url: project
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(config)
    # it would make sense to make LocalProject offline
    flexmock(PackitAPI).should_receive("sync_release").with_args(
        dist_git_branch="master", version="0.3.0"
    ).once()

    flexmock(AddReleaseDbTrigger).should_receive("db_trigger").and_return(
        flexmock(job_config_trigger_type=JobConfigTriggerType.release)
    )

    results = SteveJobs().process_message(release_event)
    assert results["jobs"]["propose_downstream"]["success"]
    assert results["event"]["trigger"] == "release"


def test_dist_git_push_release_handle_multiple_branches(release_event):
    packit_yaml = (
        "{'specfile_path': 'hello-world.spec', 'synced_files': []"
        ", jobs: [{trigger: release, job: propose_downstream, "
        "metadata: {targets:[], dist-git-branch: fedora-all}}]}"
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)
    project = flexmock(
        get_file_content=lambda path, ref: packit_yaml,
        full_repo_name="packit-service/hello-world",
        repo="hello-world",
        get_files=lambda ref, filter_regex: [],
        get_sha_from_tag=lambda tag_name: "123456",
        get_web_url=lambda: "https://github.com/packit-service/hello-world",
        is_private=lambda: False,
    )
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Whitelist, check_and_report=True)
    config = ServiceConfig()
    config.command_handler_work_dir = SANDCASTLE_WORK_DIR
    config.get_project = lambda url: project
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(config)
    # it would make sense to make LocalProject offline
    flexmock(PackitAPI).should_receive("sync_release").with_args(
        dist_git_branch="master", version="0.3.0"
    ).once()

    flexmock(PackitAPI).should_receive("sync_release").with_args(
        dist_git_branch="f32", version="0.3.0"
    ).once()
    flexmock(PackitAPI).should_receive("sync_release").with_args(
        dist_git_branch="f31", version="0.3.0"
    ).once()
    flexmock(PackitAPI).should_receive("sync_release").with_args(
        dist_git_branch="f30", version="0.3.0"
    ).once()

    flexmock(FedPKG).should_receive("clone").and_return(None)

    flexmock(AddReleaseDbTrigger).should_receive("db_trigger").and_return(
        flexmock(job_config_trigger_type=JobConfigTriggerType.release)
    )

    results = SteveJobs().process_message(release_event)
    assert results["jobs"]["propose_downstream"]["success"]
    assert results["event"]["trigger"] == "release"


def test_dist_git_push_release_handle_one_failed(release_event):
    packit_yaml = (
        "{'specfile_path': 'hello-world.spec', 'synced_files': []"
        ", jobs: [{trigger: release, job: propose_downstream, "
        "metadata: {targets:[], dist-git-branch: fedora-all}}]}"
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)
    project = (
        flexmock(
            get_file_content=lambda path, ref: packit_yaml,
            full_repo_name="packit-service/hello-world",
            repo="hello-world",
            get_files=lambda ref, filter_regex: [],
            get_sha_from_tag=lambda tag_name: "123456",
            get_web_url=lambda: "https://github.com/packit-service/hello-world",
            is_private=lambda: False,
        )
        .should_receive("create_issue")
        .once()
        .mock()
    )
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Whitelist, check_and_report=True)
    config = ServiceConfig()
    config.command_handler_work_dir = SANDCASTLE_WORK_DIR
    config.get_project = lambda url: project
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(config)
    # it would make sense to make LocalProject offline
    flexmock(PackitAPI).should_receive("sync_release").with_args(
        dist_git_branch="master", version="0.3.0"
    ).once()

    flexmock(PackitAPI).should_receive("sync_release").with_args(
        dist_git_branch="f32", version="0.3.0"
    ).and_raise(Exception, "Failed f30").once()
    flexmock(PackitAPI).should_receive("sync_release").with_args(
        dist_git_branch="f31", version="0.3.0"
    ).once()
    flexmock(PackitAPI).should_receive("sync_release").with_args(
        dist_git_branch="f30", version="0.3.0"
    ).once()

    flexmock(FedPKG).should_receive("clone").and_return(None)

    flexmock(sentry_integration).should_receive("send_to_sentry").and_return().once()
    flexmock(AddReleaseDbTrigger).should_receive("db_trigger").and_return(
        flexmock(job_config_trigger_type=JobConfigTriggerType.release)
    )

    results = SteveJobs().process_message(release_event)
    assert not results["jobs"]["propose_downstream"]["success"]
    assert results["event"]["trigger"] == "release"


def test_dist_git_push_release_handle_all_failed(release_event):
    packit_yaml = (
        "{'specfile_path': 'hello-world.spec', 'synced_files': []"
        ", jobs: [{trigger: release, job: propose_downstream, "
        "metadata: {targets:[], dist-git-branch: fedora-all}}]}"
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)
    project = (
        flexmock(
            get_file_content=lambda path, ref: packit_yaml,
            full_repo_name="packit-service/hello-world",
            repo="hello-world",
            get_files=lambda ref, filter_regex: [],
            get_sha_from_tag=lambda tag_name: "123456",
            get_web_url=lambda: "https://github.com/packit-service/hello-world",
            is_private=lambda: False,
        )
        .should_receive("create_issue")
        .with_args(
            title="[packit] Propose update failed for release 0.3.0",
            body="Packit failed on creating pull-requests in dist-git:\n\n"
            "| dist-git branch | error |\n"
            "| --------------- | ----- |\n"
            "| `f30` | `Failed` |\n"
            "| `f31` | `Failed` |\n"
            "| `f32` | `Failed` |\n"
            "| `master` | `Failed` |\n\n\n"
            "You can re-trigger the update by adding `/packit propose-update`"
            " to the issue comment.\n",
        )
        .once()
        .mock()
    )
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Whitelist, check_and_report=True)
    config = ServiceConfig()
    config.command_handler_work_dir = SANDCASTLE_WORK_DIR
    config.get_project = lambda url: project
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(config)
    # it would make sense to make LocalProject offline
    flexmock(PackitAPI).should_receive("sync_release").and_raise(
        Exception, "Failed"
    ).times(4)
    flexmock(AddReleaseDbTrigger).should_receive("db_trigger").and_return(
        flexmock(job_config_trigger_type=JobConfigTriggerType.release)
    )

    # 4 = master, f32, 31, 30
    flexmock(sentry_integration).should_receive("send_to_sentry").and_return().times(4)

    results = SteveJobs().process_message(release_event)
    assert not results["jobs"]["propose_downstream"]["success"]
    assert results["event"]["trigger"] == "release"
