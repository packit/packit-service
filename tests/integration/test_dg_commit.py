# MIT License
#
# Copyright (c) 2018-2019 Red Hat, Inc.

# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json

from celery.canvas import Signature
from flexmock import flexmock

from ogr.services.github import GithubProject
from packit.api import PackitAPI
from packit.config import JobConfigTriggerType
from packit.local_project import LocalProject
from packit_service.config import ServiceConfig, ProjectToSync
from packit_service.constants import SANDCASTLE_WORK_DIR
from packit_service.models import GitBranchModel
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.tasks import run_distgit_commit_handler
from tests.spellbook import DATA_DIR, first_dict_value, get_parameters_from_results


def distgit_commit_event():
    return json.loads((DATA_DIR / "fedmsg" / "distgit_commit.json").read_text())


def test_distgit_commit_handler():

    packit_yaml = (
        "{'specfile_path': 'buildah.spec', 'synced_files': [],"
        "'jobs': [{'trigger': 'commit', 'job': 'sync_from_downstream'}],"
        "'downstream_package_name': 'buildah'}"
    )
    flexmock(
        GithubProject,
        get_file_content=lambda path, ref: packit_yaml,
        full_repo_name="example-namespace/buildah",
        get_web_url=lambda: "https://github.com/example-namespace/buildah",
        default_branch="main",
    )
    flexmock(
        GithubProject,
        get_files=lambda ref, filter_regex: [],
        is_private=lambda: False,
    )

    flexmock(GitBranchModel).should_receive("get_or_create").with_args(
        branch_name="abcd",
        namespace="rpms",
        repo_name="buildah",
        project_url="https://src.fedoraproject.org/rpms/buildah",
    ).and_return(flexmock(id=9, job_config_trigger_type=JobConfigTriggerType.commit))

    flexmock(ServiceConfig).should_receive("get_service_config").and_return(
        ServiceConfig(
            projects_to_sync=[
                ProjectToSync(
                    forge="https://github.com",
                    repo_namespace="example-namespace",
                    repo_name="buildah",
                    branch="aaa",
                    dg_branch="master",
                    dg_repo_name="buildah",
                )
            ],
            command_handler_work_dir=SANDCASTLE_WORK_DIR,
        )
    )

    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(PackitAPI).should_receive("sync_from_downstream").with_args(
        dist_git_branch="master", upstream_branch="aaa"
    )

    processing_results = SteveJobs().process_message(distgit_commit_event())
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    results = run_distgit_commit_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]
