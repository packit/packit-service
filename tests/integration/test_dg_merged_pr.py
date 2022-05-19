# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json
import pytest

from celery.canvas import Signature
from flexmock import flexmock
from ogr.services.pagure import PagureProject

from packit.api import PackitAPI
from packit.config import JobConfigTriggerType, JobConfig, PackageConfig, JobType
from packit.local_project import LocalProject
from packit_service.models import GitBranchModel, JobTriggerModelType, GitProjectModel
from packit_service.worker.handlers.distgit import DownstreamKojiBuildHandler
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.tasks import (
    run_downstream_koji_build,
)
from tests.spellbook import DATA_DIR, first_dict_value, get_parameters_from_results


def distgit_merged_pr_event():
    return json.loads((DATA_DIR / "fedmsg" / "distgit_merged_pr.json").read_text())


def test_downstream_koji_build_pull_request_merged_event():

    packit_yaml = (
        "{'specfile_path': 'buildah.spec', 'synced_files': [],"
        "'jobs': [{'trigger': 'commit', 'job': 'koji_build', 'dist_git_branches': "
        "['epel-8'], 'allowed_pr_authors': ['packit-stg']}],"
        "'downstream_package_name': 'buildah'}"
    )
    pagure_project = flexmock(
        PagureProject,
        full_repo_name="rpms/python-specfile",
        get_web_url=lambda: "https://src.fedoraproject.org/rpms/python-specfile",
        default_branch="main",
    )
    pagure_project.should_receive("get_files").with_args(
        ref="0ba51c0f420befcd8fe76742fa3f2bd0e24b3740", filter_regex=r".+\.spec$"
    ).and_return(["buildah.spec"])
    pagure_project.should_receive("get_file_content").with_args(
        path=".distro/source-git.yaml", ref="0ba51c0f420befcd8fe76742fa3f2bd0e24b3740"
    ).and_raise(FileNotFoundError, "Not found.")
    pagure_project.should_receive("get_file_content").with_args(
        path=".packit.yaml", ref="0ba51c0f420befcd8fe76742fa3f2bd0e24b3740"
    ).and_return(packit_yaml)

    flexmock(GitBranchModel).should_receive("get_or_create").with_args(
        branch_name="epel8",
        namespace="rpms",
        repo_name="python-specfile",
        project_url="https://src.fedoraproject.org/rpms/python-specfile",
    ).and_return(flexmock(id=9, job_config_trigger_type=JobConfigTriggerType.commit))

    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").once().and_return()
    flexmock(PackitAPI).should_receive("build").with_args(
        dist_git_branch="epel8",
        scratch=False,
        nowait=True,
        from_upstream=False,
    )
    processing_results = SteveJobs().process_message(distgit_merged_pr_event())
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)
    results = run_downstream_koji_build(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]


@pytest.mark.parametrize(
    "pr_author, committer, allowed_pr_authors, allowed_committers, should_pass",
    (
        ("packit", "sakamoto", ["packit"], [], True),
        ("packit", "sakamoto", ["packit"], ["sakamoto"], True),
        ("packit-stg", "sakamoto", [], ["sakamoto"], True),
        ("packit-stg", "sakamoto", ["packit"], [], False),
    ),
)
def test_precheck_koji_build_merged_pr(
    distgit_merged_pr_event,
    pr_author,
    committer,
    allowed_pr_authors,
    allowed_committers,
    should_pass,
):
    distgit_merged_pr_event.pr_author = pr_author
    distgit_merged_pr_event.committer = committer

    flexmock(GitProjectModel).should_receive("get_or_create").with_args(
        namespace="rpms",
        project_url="https://src.fedoraproject.org/rpms/packit",
        repo_name="packit",
    ).and_return(
        flexmock(
            id=342,
        )
    )
    flexmock(GitBranchModel).should_receive("get_or_create").with_args(
        branch_name="f36",
        namespace="rpms",
        project_url="https://src.fedoraproject.org/rpms/packit",
        repo_name="packit",
    ).and_return(
        flexmock(
            id=13,
            job_config_trigger_type=JobConfigTriggerType.commit,
            job_trigger_model_type=JobTriggerModelType.branch_push,
        )
    )

    # flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
    #     type=JobTriggerModelType.pull_request, trigger_id=342
    # ).and_return(flexmock(id=2, type=JobTriggerModelType.pull_request))
    # flexmock(GithubProject).should_receive("can_merge_pr").and_return(True)
    jobs = [
        JobConfig(
            type=JobType.koji_build,
            trigger=JobConfigTriggerType.commit,
            dist_git_branches=["epel-8"],
            allowed_pr_authors=allowed_pr_authors,
            allowed_committers=allowed_committers,
        ),
    ]
    koji_build_handler = DownstreamKojiBuildHandler(
        package_config=PackageConfig(
            jobs=jobs,
        ),
        job_config=jobs[0],
        event=distgit_merged_pr_event.get_dict(),
    )
    assert koji_build_handler.pre_check() == should_pass
