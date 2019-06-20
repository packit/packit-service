import json

import pytest
from flexmock import flexmock
from packit.api import PackitAPI

from packit_service.jobs import SteveJobs
from tests.spellbook import DATA_DIR


@pytest.fixture()
def pr_event():
    with open(DATA_DIR / "webhooks" / "github_pr_event.json", "r") as outfile:
        return json.load(outfile)


@pytest.fixture()
def release_event():
    with open(DATA_DIR / "webhooks" / "release_event.json", "r") as outfile:
        return json.load(outfile)


def test_copr_pr_handle(pr_event, dump_http_com):
    config = dump_http_com(f"{__file__.rsplit('/', 1)[1]}/pr_handle.yaml")
    s = SteveJobs(config)
    # it would make sense to make LocalProject offline
    flexmock(PackitAPI).should_receive("run_copr_build").with_args(
        owner="packit",
        project="packit-service-packit-342",
        chroots=["fedora-29-x86_64", "fedora-rawhide-x86_64", "fedora-30-x86_64"],
    ).and_return("1", "asd").once()
    flexmock(PackitAPI).should_receive("watch_copr_build").and_return("failed").once()
    s.process_message(pr_event)


# We do not support this workflow officially
# def test_copr_release_handle(release_event):
#     packit_yaml = (
#         "{'specfile_path': '', 'synced_files': []"
#         ", jobs: [{trigger: release, job: copr_build, metadata: {targets:[]}}]}"
#     )
#     flexmock(Github, get_repo=lambda full_name_or_id: None)
#     flexmock(
#         GithubProject,
#         get_file_content=lambda path, ref: packit_yaml,
#         full_repo_name="foo/bar",
#     )
#     flexmock(LocalProject, refresh_the_arguments=lambda: None)
#     flexmock(PackitAPI, sync_release=lambda dist_git_branch, version: None)
#
#     flexmock(PackitAPI).should_receive("run_copr_build").with_args(
#         owner="packit",
#         project="Codertocat-Hello-World",
#         committish="0.0.1",
#         clone_url="https://github.com/Codertocat/Hello-World.git",
#         chroots=[],
#     ).and_return(1, "http://shire").once()
#     flexmock(GithubProject).should_receive("commit_comment").with_args(
#         pr_event["number"], "Triggered copr build (ID:1).\nMore info: http://shire"
#     ).and_return().once()
#
#     c = Config()
#     s = SteveJobs(c)
#     s.process_message(release_event)
