# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json

import pytest
from celery.canvas import group
from flexmock import flexmock
from github.MainClass import Github
from ogr.services.github import GithubProject
from packit.config import JobConfigTriggerType
from packit.copr_helper import CoprHelper
from packit.local_project import LocalProject

from packit_service.constants import (
    TASK_ACCEPTED,
)
from packit_service.models import (
    BuildStatus,
    GitBranchModel,
    ProjectEventModel,
    ProjectReleaseModel,
    PullRequestModel,
    TestingFarmResult,
    TFTTestRunGroupModel,
    TFTTestRunTargetModel,
)
from packit_service.worker.handlers import ProposeDownstreamHandler
from packit_service.worker.helpers.build import (
    KojiBuildJobHelper,
    koji_build,
)
from packit_service.worker.helpers.sync_release.propose_downstream import (
    ProposeDownstreamJobHelper,
)
from packit_service.worker.helpers.testing_farm import TestingFarmJobHelper
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.reporting import BaseCommitStatus, StatusReporterGithubChecks
from packit_service.worker.result import TaskResults
from packit_service.worker.tasks import (
    run_koji_build_handler,
    run_testing_farm_handler,
)
from tests.spellbook import DATA_DIR, first_dict_value, get_parameters_from_results


@pytest.fixture(scope="module")
def check_rerun_event_testing_farm():
    return json.loads(
        (DATA_DIR / "webhooks" / "github" / "checkrun_rerequested.json").read_text(),
    )


@pytest.fixture(scope="module")
def check_rerun_event_copr_build():
    event = json.loads(
        (DATA_DIR / "webhooks" / "github" / "checkrun_rerequested.json").read_text(),
    )
    event["check_run"]["name"] = "rpm-build:fedora-rawhide-x86_64"
    return event


@pytest.fixture(scope="module")
def check_rerun_event_koji_build():
    event = json.loads(
        (DATA_DIR / "webhooks" / "github" / "checkrun_rerequested.json").read_text(),
    )
    event["check_run"]["name"] = "koji-build:f34"
    return event


@pytest.fixture(scope="module")
def check_rerun_event_koji_build_push():
    event = json.loads(
        (DATA_DIR / "webhooks" / "github" / "checkrun_rerequested.json").read_text(),
    )
    event["check_run"]["name"] = "koji-build:main:f34"
    return event


@pytest.fixture(scope="module")
def check_rerun_event_propose_downstream():
    event = json.loads(
        (DATA_DIR / "webhooks" / "github" / "checkrun_rerequested.json").read_text(),
    )
    event["check_run"]["name"] = "propose-downstream:f34"
    return event


@pytest.fixture
def mock_pr_functionality(request):
    packit_yaml = "{'specfile_path': 'the-specfile.spec', 'jobs':" + str(request.param) + "}"
    flexmock(
        GithubProject,
        full_repo_name="packit/hello-world",
        get_file_content=lambda path, ref: packit_yaml,
        get_files=lambda ref, filter_regex: ["the-specfile.spec"],
        get_web_url=lambda: "https://github.com/the-namespace/the-repo",
        get_pr=lambda pr_id: flexmock(head_commit="12345"),
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)

    pr_model = (
        flexmock(PullRequestModel(pr_id=123))
        .should_receive("get_project_event_models")
        .and_return([flexmock(commit_sha="12345")])
        .mock()
    )
    project_event = (
        flexmock(ProjectEventModel(type=JobConfigTriggerType.pull_request, id=123456))
        .should_receive("get_project_event_object")
        .and_return(pr_model)
        .mock()
        .should_receive("set_packages_config")
        .mock()
    )
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(ProjectEventModel).should_receive("get_or_create").and_return(
        project_event,
    )
    flexmock(ProjectEventModel).should_receive("get_by_id").with_args(
        123456,
    ).and_return(project_event)
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=123,
        namespace="packit",
        repo_name="hello-world",
        project_url="https://github.com/packit/hello-world",
    ).and_return(pr_model)
    flexmock(PullRequestModel).should_receive("get_by_id").with_args(123).and_return(
        pr_model,
    )


@pytest.fixture
def mock_push_functionality(request):
    packit_yaml = "{'specfile_path': 'the-specfile.spec', 'jobs':" + str(request.param) + "}"
    flexmock(
        GithubProject,
        full_repo_name="packit/hello-world",
        get_file_content=lambda path, ref: packit_yaml,
        get_files=lambda ref, filter_regex: ["the-specfile.spec"],
        get_web_url=lambda: "https://github.com/the-namespace/the-repo",
        get_pr=lambda pr_id: flexmock(head_commit="12345"),
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)

    branch_model = (
        flexmock(GitBranchModel(name="main"))
        .should_receive("get_project_event_models")
        .and_return([flexmock(commit_sha="12345")])
        .mock()
    )
    project_event = (
        flexmock(ProjectEventModel(type=JobConfigTriggerType.commit, id=123456))
        .should_receive("get_project_event_object")
        .and_return(branch_model)
        .mock()
        .should_receive("set_packages_config")
        .mock()
    )

    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(ProjectEventModel).should_receive("get_or_create").and_return(
        project_event,
    )
    flexmock(ProjectEventModel).should_receive("get_by_id").with_args(
        123456,
    ).and_return(project_event)
    flexmock(GitBranchModel).should_receive("get_or_create").with_args(
        branch_name="main",
        namespace="packit",
        repo_name="hello-world",
        project_url="https://github.com/packit/hello-world",
    ).and_return(branch_model)
    flexmock(GitBranchModel).should_receive("get_by_id").with_args(123).and_return(
        branch_model,
    )


@pytest.fixture
def mock_release_functionality(request):
    packit_yaml = "{'specfile_path': 'the-specfile.spec', 'jobs':" + str(request.param) + "}"
    flexmock(
        GithubProject,
        full_repo_name="packit/hello-world",
        get_file_content=lambda path, ref: packit_yaml,
        get_files=lambda ref, filter_regex: ["the-specfile.spec"],
        get_web_url=lambda: "https://github.com/the-namespace/the-repo",
        get_pr=lambda pr_id: flexmock(head_commit="12345"),
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)

    release_model = (
        flexmock(ProjectReleaseModel(tag_name="0.1.0"))
        .should_receive("get_project_event_models")
        .and_return([flexmock(commit_sha="12345")])
        .mock()
    )
    project_event = (
        flexmock(ProjectEventModel(type=JobConfigTriggerType.release, id=123456))
        .should_receive("get_project_event_object")
        .and_return(release_model)
        .mock()
        .should_receive("set_packages_config")
        .mock()
    )
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(ProjectEventModel).should_receive("get_by_id").with_args(
        123456,
    ).and_return(project_event)
    flexmock(ProjectEventModel).should_receive("get_or_create").and_return(
        project_event,
    )
    flexmock(ProjectReleaseModel).should_receive("get_by_id").with_args(123).and_return(
        release_model,
    )
    flexmock(ProjectReleaseModel).should_receive("get_or_create").with_args(
        tag_name="0.1.0",
        namespace="packit",
        repo_name="hello-world",
        project_url="https://github.com/packit/hello-world",
        commit_hash="0e5d8b51fd5dfa460605e1497d22a76d65c6d7fd",
    ).and_return(release_model)


@pytest.mark.parametrize(
    "mock_pr_functionality",
    (
        [
            [
                {
                    "trigger": "pull_request",
                    "job": "tests",
                    "metadata": {"targets": "fedora-all"},
                },
                {
                    "trigger": "pull_request",
                    "job": "copr_build",
                    "metadata": {"targets": "fedora-all"},
                },
            ],
        ]
    ),
    indirect=True,
)
def test_check_rerun_pr_testing_farm_handler(
    mock_pr_functionality,
    check_rerun_event_testing_farm,
):
    run = flexmock(test_run_group=None)
    build = flexmock(status=BuildStatus.success, group_of_targets=flexmock(runs=[run]))
    test = flexmock(
        copr_builds=[build],
        status=TestingFarmResult.new,
        target="fedora-rawhide-x86_64",
    )
    flexmock(TFTTestRunGroupModel).should_receive("create").with_args(
        [run], ranch="public"
    ).and_return(
        flexmock(grouped_targets=[test]),
    )
    flexmock(TFTTestRunTargetModel).should_receive("create").and_return(test)
    flexmock(TestingFarmJobHelper).should_receive("run_testing_farm").once().and_return(
        TaskResults(success=True, details={}),
    )
    flexmock(GithubProject).should_receive("get_files").and_return(["foo.spec"])
    flexmock(GithubProject).should_receive("get_files").and_return(
        ["foo.spec", ".packit.yaml"],
    )
    flexmock(GithubProject).should_receive("get_web_url").and_return(
        "https://github.com/the-namespace/the-repo",
    )
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(TestingFarmJobHelper).should_receive("get_latest_copr_build").and_return(
        build,
    )
    flexmock(CoprHelper).should_receive("get_valid_build_targets").and_return(
        {"fedora-rawhide-x86_64", "fedora-34-x86_64"},
    )
    flexmock(StatusReporterGithubChecks).should_receive("set_status").with_args(
        state=BaseCommitStatus.pending,
        description=TASK_ACCEPTED,
        check_name="testing-farm:fedora-rawhide-x86_64",
        url="",
        links_to_external_services=None,
        markdown_content=None,
    ).once()
    flexmock(group).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    processing_results = SteveJobs().process_message(check_rerun_event_testing_farm)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)
    assert event_dict["tests_targets_override"] == [("fedora-rawhide-x86_64", None)]
    results = run_testing_farm_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert first_dict_value(results["job"])["success"]


@pytest.mark.parametrize(
    "mock_pr_functionality",
    (
        [
            [
                {
                    "trigger": "pull_request",
                    "job": "upstream_koji_build",
                    "metadata": {"targets": "fedora-all", "scratch": "true"},
                },
            ],
        ]
    ),
    indirect=True,
)
def test_check_rerun_pr_koji_build_handler(
    mock_pr_functionality,
    check_rerun_event_koji_build,
):
    flexmock(KojiBuildJobHelper).should_receive("run_koji_build").and_return(
        TaskResults(success=True, details={}),
    )
    flexmock(GithubProject).should_receive("get_files").and_return(["foo.spec"])
    flexmock(GithubProject).should_receive("get_files").and_return(
        ["foo.spec", ".packit.yaml"],
    )
    flexmock(GithubProject).should_receive("get_web_url").and_return(
        "https://github.com/the-namespace/the-repo",
    )
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(koji_build).should_receive("get_koji_targets").and_return(
        {"rawhide", "f34"},
    )
    flexmock(StatusReporterGithubChecks).should_receive("set_status").with_args(
        state=BaseCommitStatus.pending,
        description=TASK_ACCEPTED,
        check_name="koji-build:f34",
        url="",
        links_to_external_services=None,
        markdown_content=None,
    ).once()
    flexmock(group).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    processing_results = SteveJobs().process_message(check_rerun_event_koji_build)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)
    assert event_dict["build_targets_override"] == [("f34", None)]

    results = run_koji_build_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert first_dict_value(results["job"])["success"]


@pytest.mark.parametrize(
    "mock_pr_functionality",
    (
        [
            [
                {
                    "trigger": "pull_request",
                    "job": "upstream_koji_build",
                    "metadata": {"targets": "fedora-all", "scratch": "true"},
                },
            ],
        ]
    ),
    indirect=True,
)
def test_check_rerun_pr_koji_build_handler_old_job_name(
    mock_pr_functionality,
    check_rerun_event_koji_build,
):
    flexmock(KojiBuildJobHelper).should_receive("run_koji_build").and_return(
        TaskResults(success=True, details={}),
    )
    flexmock(GithubProject).should_receive("get_files").and_return(["foo.spec"])
    flexmock(GithubProject).should_receive("get_files").and_return(
        ["foo.spec", ".packit.yaml"],
    )
    flexmock(GithubProject).should_receive("get_web_url").and_return(
        "https://github.com/the-namespace/the-repo",
    )
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(koji_build).should_receive("get_koji_targets").and_return(
        {"rawhide", "f34"},
    )
    flexmock(StatusReporterGithubChecks).should_receive("set_status").with_args(
        state=BaseCommitStatus.pending,
        description=TASK_ACCEPTED,
        check_name="koji-build:f34",
        url="",
        links_to_external_services=None,
        markdown_content=None,
    ).once()
    flexmock(group).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    processing_results = SteveJobs().process_message(check_rerun_event_koji_build)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)
    assert event_dict["build_targets_override"] == [("f34", None)]

    results = run_koji_build_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert first_dict_value(results["job"])["success"]


@pytest.mark.parametrize(
    "mock_push_functionality",
    (
        [
            [
                {
                    "trigger": "commit",
                    "job": "tests",
                    "branch": "main",
                    "targets": [
                        "fedora-all",
                    ],
                },
                {
                    "trigger": "commit",
                    "job": "copr_build",
                    "branch": "main",
                    "targets": [
                        "fedora-all",
                    ],
                },
            ],
        ]
    ),
    indirect=True,
)
def test_check_rerun_push_testing_farm_handler(
    mock_push_functionality,
    check_rerun_event_testing_farm,
):
    run = flexmock(test_run_group=None)
    build = flexmock(status=BuildStatus.success, group_of_targets=flexmock(runs=[run]))
    test = flexmock(
        copr_builds=[build],
        status=TestingFarmResult.new,
        target="fedora-rawhide-x86_64",
    )
    flexmock(TFTTestRunGroupModel).should_receive("create").with_args(
        [run], ranch="public"
    ).and_return(
        flexmock(grouped_targets=[test]),
    )
    flexmock(TFTTestRunTargetModel).should_receive("create").and_return(test)
    flexmock(TestingFarmJobHelper).should_receive("run_testing_farm").once().and_return(
        TaskResults(success=True, details={}),
    )
    flexmock(GithubProject).should_receive("get_files").and_return(["foo.spec"])
    flexmock(GithubProject).should_receive("get_files").and_return(
        ["foo.spec", ".packit.yaml"],
    )
    flexmock(GithubProject).should_receive("get_web_url").and_return(
        "https://github.com/the-namespace/the-repo",
    )
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(TestingFarmJobHelper).should_receive("get_latest_copr_build").and_return(
        flexmock(status=BuildStatus.success, group_of_targets=flexmock(runs=[run])),
    )
    flexmock(CoprHelper).should_receive("get_valid_build_targets").and_return(
        {"fedora-rawhide-x86_64", "fedora-34-x86_64"},
    )
    flexmock(StatusReporterGithubChecks).should_receive("set_status").with_args(
        state=BaseCommitStatus.pending,
        description=TASK_ACCEPTED,
        check_name="testing-farm:main:fedora-rawhide-x86_64",
        url="",
        links_to_external_services=None,
        markdown_content=None,
    ).once()
    flexmock(group).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    processing_results = SteveJobs().process_message(check_rerun_event_testing_farm)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert event_dict["tests_targets_override"] == [("fedora-rawhide-x86_64", None)]
    assert json.dumps(event_dict)
    results = run_testing_farm_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert first_dict_value(results["job"])["success"]


@pytest.mark.parametrize(
    "mock_push_functionality",
    (
        [
            [
                {
                    "trigger": "commit",
                    "job": "upstream_koji_build",
                    "targets": "fedora-all",
                    "scratch": "true",
                    "branch": "main",
                },
            ],
        ]
    ),
    indirect=True,
)
def test_check_rerun_push_koji_build_handler(
    mock_push_functionality,
    check_rerun_event_koji_build_push,
):
    flexmock(KojiBuildJobHelper).should_receive("run_koji_build").and_return(
        TaskResults(success=True, details={}),
    )
    flexmock(GithubProject).should_receive("get_files").and_return(["foo.spec"])
    flexmock(GithubProject).should_receive("get_files").and_return(
        ["foo.spec", ".packit.yaml"],
    )
    flexmock(GithubProject).should_receive("get_web_url").and_return(
        "https://github.com/the-namespace/the-repo",
    )
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(koji_build).should_receive("get_koji_targets").and_return(
        {"rawhide", "f34"},
    )
    flexmock(StatusReporterGithubChecks).should_receive("set_status").with_args(
        state=BaseCommitStatus.pending,
        description=TASK_ACCEPTED,
        check_name="koji-build:main:f34",
        url="",
        links_to_external_services=None,
        markdown_content=None,
    ).once()
    flexmock(group).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    processing_results = SteveJobs().process_message(check_rerun_event_koji_build_push)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert event_dict["build_targets_override"] == [("f34", None)]
    assert json.dumps(event_dict)

    results = run_koji_build_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert first_dict_value(results["job"])["success"]


@pytest.mark.parametrize(
    "mock_release_functionality",
    (
        [
            [
                {
                    "trigger": "release",
                    "job": "upstream_koji_build",
                    "metadata": {"targets": "fedora-all", "scratch": "true"},
                },
            ],
        ]
    ),
    indirect=True,
)
def test_check_rerun_release_koji_build_handler(
    mock_release_functionality,
    check_rerun_event_koji_build,
):
    flexmock(KojiBuildJobHelper).should_receive("run_koji_build").and_return(
        TaskResults(success=True, details={}),
    )
    flexmock(GithubProject).should_receive("get_files").and_return(["foo.spec"])
    flexmock(GithubProject).should_receive("get_files").and_return(
        ["foo.spec", ".packit.yaml"],
    )
    flexmock(GithubProject).should_receive("get_web_url").and_return(
        "https://github.com/the-namespace/the-repo",
    )
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(koji_build).should_receive("get_koji_targets").and_return(
        {"rawhide", "f34"},
    )
    flexmock(StatusReporterGithubChecks).should_receive("set_status").with_args(
        state=BaseCommitStatus.pending,
        description=TASK_ACCEPTED,
        check_name="koji-build:0.1.0:f34",
        url="",
        links_to_external_services=None,
        markdown_content=None,
    ).once()
    flexmock(group).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    processing_results = SteveJobs().process_message(check_rerun_event_koji_build)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert event_dict["build_targets_override"] == [("f34", None)]
    assert json.dumps(event_dict)

    results = run_koji_build_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert first_dict_value(results["job"])["success"]


@pytest.mark.parametrize(
    "mock_release_functionality",
    (
        [
            [
                {
                    "trigger": "release",
                    "job": "propose_downstream",
                    "metadata": {"targets": "fedora-all"},
                },
            ],
        ]
    ),
    indirect=True,
)
def test_check_rerun_release_propose_downstream_handler(
    mock_release_functionality,
    check_rerun_event_propose_downstream,
):
    flexmock(ProposeDownstreamHandler).should_receive("run_job").and_return(
        TaskResults(success=True, details={}),
    )
    flexmock(GithubProject).should_receive("get_files").and_return(["foo.spec"])
    flexmock(GithubProject).should_receive("get_files").and_return(
        ["foo.spec", ".packit.yaml"],
    )
    flexmock(GithubProject).should_receive("get_web_url").and_return(
        "https://github.com/the-namespace/the-repo",
    )
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(CoprHelper).should_receive("get_valid_build_targets").and_return(
        {"fedora-rawhide-x86_64", "fedora-34-x86_64"},
    )
    flexmock(ProposeDownstreamJobHelper).should_receive(
        "report_status_to_all",
    ).with_args(
        description=TASK_ACCEPTED,
        state=BaseCommitStatus.pending,
        url="",
        markdown_content=None,
        links_to_external_services=None,
        update_feedback_time=object,
    ).once()
    flexmock(group).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(1).and_return()

    processing_results = SteveJobs().process_message(
        check_rerun_event_propose_downstream,
    )
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert event_dict["branches_override"] == ["f34"]
    assert json.dumps(event_dict)
