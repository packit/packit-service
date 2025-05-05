# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json
import os
from datetime import datetime
from pathlib import Path

import pytest
from deepdiff import DeepDiff
from flexmock import flexmock
from ogr import GithubService, GitlabService, PagureService
from packit.config import JobConfig, JobConfigTriggerType, PackageConfig
from packit.config.common_package_config import Deployment

from packit_service import events
from packit_service.config import ServiceConfig
from packit_service.models import (
    BuildStatus,
    ProjectEventModel,
    ProjectEventModelType,
    PullRequestModel,
)
from packit_service.worker.parser import Parser
from tests.spellbook import DATA_DIR, SAVED_HTTPD_REQS, load_the_message_from_file


@pytest.fixture(autouse=True)
def global_service_config():
    """
    This config will be used instead of the one loaded from the local config file.

    You can still mock/overwrite the service config content in your tests
    but this one will be used by default.

    You can also (re)define some values like this:
    ServiceConfig.get_service_config().attribute = "value"
    """
    service_config = ServiceConfig()
    service_config.fas_user = "packit"
    service_config.services = {
        GithubService(token="token"),
        GitlabService(token="token"),
        PagureService(instance_url="https://src.fedoraproject.org", token="token"),
        PagureService(instance_url="https://git.stg.centos.org", token="6789"),
    }
    service_config.server_name = "localhost"
    service_config.github_requests_log_path = "/path"
    # By default, [Deployment.prod] is used as packit_instances config option.
    # So just prod reacts to configs without packit_instances defined.
    service_config.deployment = Deployment.prod
    ServiceConfig.service_config = service_config


@pytest.fixture()
def dump_http_com():
    """
    This fixture is able to dump whole http traffic of a single test case
    so that no http comm is happening while testing

    Usage:
    1. add it to your test case and pass the test path
      def test_something(dump_http_com):
        service_config = dump_http_com(f"{Path(__file__).name}/pr_handle.yaml")
    2. Run your test
      GITHUB_TOKEN=asdqwe pytest-3 -k test_something
    3. Your http communication should now be stored in tests/data/http-requests/{path}
    4. Once you rerun the tests WITHOUT the token, the offline communication should be picked up
    """

    def f(path: str):
        """path points to a file where the http communication will be saved"""
        conf = ServiceConfig()
        # TODO: add pagure support
        # conf._pagure_user_token = os.environ.get("PAGURE_TOKEN", "test")
        # conf._pagure_fork_token = os.environ.get("PAGURE_FORK_TOKEN", "test")
        conf._github_token = os.getenv("GITHUB_TOKEN", None)
        target_path: Path = SAVED_HTTPD_REQS / path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        conf.github_requests_log_path = str(target_path)
        return conf

    return f


@pytest.fixture()
def srpm_build_model(
    repo_name="bar",
    repo_namespace="foo",
    forge_instance="github.com",
    job_config_trigger_type=JobConfigTriggerType.pull_request,
    project_event_model_type=ProjectEventModelType.pull_request,
    **trigger_model_kwargs,
):
    project_model = flexmock(
        repo_name=repo_name,
        namespace=repo_namespace,
        project_url=f"https://{forge_instance}/{repo_namespace}/{repo_name}",
    )
    pr_model = flexmock(
        id=1,
        pr_id=123,
        project=project_model,
        job_config_trigger_type=job_config_trigger_type,
        project_event_model_type=ProjectEventModelType.pull_request,
        commit_sha="0011223344",
        **trigger_model_kwargs,
    )
    project_event_model = flexmock(
        id=2,
        type=project_event_model_type,
        event_id=1,
        get_project_event_object=lambda: pr_model,
    )

    runs = []
    srpm_build = flexmock(
        id=1,
        build_id="1",
        commit_sha="0011223344",
        status=BuildStatus.pending,
        runs=runs,
        set_status=lambda x: None,
        set_end_time=lambda x: None,
        set_start_time=lambda x: None,
        set_build_logs_url=lambda x: None,
        url=None,
        build_start_time=None,
        logs_url=None,
        copr_web_url=None,
    )

    flexmock(ProjectEventModel).should_receive("get_or_create").with_args(
        type=pr_model.project_event_model_type,
        event_id=pr_model.id,
        commit_sha="0011223344",
    ).and_return(project_event_model)

    def mock_set_status(status):
        srpm_build.status = status

    def mock_set_url(url):
        srpm_build.url = url

    srpm_build.set_status = mock_set_status
    srpm_build.set_url = mock_set_url
    srpm_build.get_project_event_object = lambda: pr_model
    srpm_build.should_receive("get_project_event_model").and_return(project_event_model)

    run_model = flexmock(
        id=3,
        job_project_event=project_event_model,
        srpm_build=srpm_build,
    )
    runs.append(run_model)

    return srpm_build


def copr_build_model(
    repo_name="hello-world",
    repo_namespace="packit-service",
    forge_instance="github.com",
    trigger_kls=PullRequestModel,
    job_config_trigger_type=JobConfigTriggerType.pull_request,
    project_event_model_type=ProjectEventModelType.pull_request,
    **trigger_model_kwargs,
):
    project_model = flexmock(
        repo_name=repo_name,
        namespace=repo_namespace,
        project_url=f"https://{forge_instance}/{repo_namespace}/{repo_name}",
    )

    # so that isinstance works
    class Trigger(trigger_kls):
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    trigger_object_model = Trigger(
        id=1,
        project=project_model,
        job_config_trigger_type=job_config_trigger_type,
        project_event_model_type=project_event_model_type,
        **trigger_model_kwargs,
    )

    project_event_model = flexmock(
        id=2,
        type=project_event_model_type,
        event_id=1,
        get_project_event_object=lambda: trigger_object_model,
        packages_config=None,
    )

    runs = []
    srpm_build = flexmock(
        logs="asdsdf",
        url=None,
        runs=runs,
        status=BuildStatus.success,
    )
    copr_group = flexmock(runs=runs)
    copr_build = flexmock(
        id=1,
        build_id="1",
        commit_sha="0011223344",
        project_name="some-project",
        owner="some-owner",
        web_url="https://some-url",
        target="some-target",
        status="some-status",
        group_of_targets=copr_group,
        set_status=lambda x: None,
        set_built_packages=lambda x: None,
        built_packages=[
            {
                "name": repo_name,
                "version": "0.1",
                "release": "1",
                "arch": "noarch",
                "epoch": 0,
            },
        ],
        task_accepted_time=datetime.now(),
        build_start_time=None,
        build_logs_url="https://log-url",
    )

    flexmock(ProjectEventModel).should_receive("get_or_create").with_args(
        type=trigger_object_model.project_event_model_type,
        event_id=trigger_object_model.id,
        commit_sha="0011223344",
    ).and_return(project_event_model)

    def mock_set_status(status):
        copr_build.status = status

    def mock_set_built_packages(built_packages):
        copr_build.built_packages = built_packages

    copr_build.set_status = mock_set_status
    copr_build._srpm_build_for_mocking = srpm_build
    copr_build.get_project_event_object = lambda: trigger_object_model
    copr_build.get_project_event_model = lambda: project_event_model
    copr_build.get_srpm_build = lambda: srpm_build

    run_model = flexmock(
        id=3,
        job_project_event=project_event_model,
        srpm_build=srpm_build,
        copr_build_group=copr_group,
        test_run_group=None,
    )
    runs.append(run_model)

    return copr_build


@pytest.fixture()
def copr_build_pr():
    return copr_build_model(
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        project_event_model_type=ProjectEventModelType.pull_request,
        pr_id=24,
        task_accepted_time=datetime.now(),
    )


@pytest.fixture()
def koji_build_pr():
    project_model = flexmock(
        repo_name="bar",
        namespace="foo",
        project_url="https://github.com/foo/bar",
    )
    pr_model = flexmock(
        id=1,
        pr_id=123,
        project=project_model,
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        project_event_model_type=ProjectEventModelType.pull_request,
        commit_sha="0011223344",
    )
    project_event_model = flexmock(
        id=2,
        type=ProjectEventModelType.pull_request,
        event_id=1,
        get_project_event_object=lambda: pr_model,
    )
    runs = []
    srpm_build = flexmock(logs="asdsdf", url=None, runs=runs)
    koji_build_model = flexmock(
        id=1,
        task_id="1",
        commit_sha="0011223344",
        project_name="some-project",
        owner="some-owner",
        web_url="https://some-url",
        target="some-target",
        status="some-status",
        runs=runs,
    )
    koji_build_model._srpm_build_for_mocking = srpm_build
    koji_build_model.get_project_event_object = lambda: pr_model
    koji_build_model.get_srpm_build = lambda: srpm_build
    koji_build_model.should_receive("get_project_event_model").and_return(
        project_event_model,
    )

    flexmock(ProjectEventModel).should_receive("get_or_create").with_args(
        type=pr_model.project_event_model_type,
        event_id=pr_model.id,
        commit_sha="0011223344",
    ).and_return(project_event_model)

    run_model = flexmock(
        id=3,
        job_project_event=project_event_model,
        srpm_build=srpm_build,
        copr_build=koji_build_model,
    )
    runs.append(run_model)

    return koji_build_model


@pytest.fixture()
def koji_build_pr_downstream():
    project_model = flexmock(
        repo_name="packit",
        namespace="rpms",
        project_url="https://src.fedoraproject.org/rpms/packit",
    )
    pr_model = flexmock(
        id=1,
        pr_id=123,
        project=project_model,
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        project_event_model_type=ProjectEventModelType.pull_request,
        commit_sha="0011223344",
    )
    project_event_model = flexmock(
        id=2,
        type=ProjectEventModelType.pull_request,
        event_id=1,
        get_project_event_object=lambda: pr_model,
    )
    runs = []
    srpm_build = flexmock(logs="asdsdf", url=None, runs=runs)
    koji_group = flexmock(runs=runs)
    koji_build_model = flexmock(
        id=1,
        task_id="1",
        commit_sha="0011223344",
        project_name="some-project",
        owner="some-owner",
        web_url="https://some-url",
        target="some-target",
        status="some-status",
        group_of_targets=koji_group,
        runs=runs,
    )
    koji_build_model._srpm_build_for_mocking = srpm_build
    koji_build_model.get_project_event_object = lambda: pr_model
    koji_build_model.get_srpm_build = lambda: srpm_build
    koji_build_model.should_receive("get_project_event_model").and_return(
        project_event_model,
    )

    flexmock(ProjectEventModel).should_receive("get_or_create").with_args(
        type=pr_model.project_event_model_type,
        event_id=pr_model.id,
        commit_sha="0011223344",
    ).and_return(project_event_model)

    run_model = flexmock(
        id=3,
        job_project_event=project_event_model,
        srpm_build=srpm_build,
        koji_build=koji_build_model,
        test_run_group=None,
    )
    runs.append(run_model)

    return koji_build_model


@pytest.fixture()
def add_pull_request_event_with_sha_123456():
    db_project_object = flexmock(
        project=flexmock(
            repo_name="repo_name",
            namespace="the-namespace",
            project_url="https://github.com/the-namespace/repo_name",
        ),
        pr_id=5,
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        project_event_model_type=ProjectEventModelType.pull_request,
        id=123,
    )
    db_project_event = (
        flexmock(type=ProjectEventModelType.pull_request, commit_sha="123456")
        .should_receive("get_project_event_object")
        .and_return(db_project_object)
        .mock()
    )
    yield db_project_object, db_project_event


@pytest.fixture()
def add_pull_request_event_with_pr_id_9():
    db_project_object = flexmock(
        id=9,
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        project_event_model_type=ProjectEventModelType.pull_request,
    )
    db_project_event = (
        flexmock()
        .should_receive("get_project_event_object")
        .and_return(db_project_object)
        .mock()
        .should_receive("set_packages_config")
        .mock()
    )
    flexmock(PullRequestModel).should_receive("get_by_id").with_args(9).and_return(
        db_project_object,
    )
    flexmock(ProjectEventModel).should_receive("get_or_create").with_args(
        type=ProjectEventModelType.pull_request,
        event_id=9,
        commit_sha="12345",
    ).and_return(db_project_event)
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=9,
        namespace="packit-service",
        repo_name="hello-world",
        project_url="https://github.com/packit-service/hello-world",
    ).and_return(db_project_object)
    yield db_project_object, db_project_event


@pytest.fixture()
def add_pull_request_event_with_sha_0011223344():
    db_project_object = flexmock(
        id=9,
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        project_event_model_type=ProjectEventModelType.pull_request,
    )
    db_project_event = (
        flexmock(id=123)
        .should_receive("get_project_event_object")
        .and_return(db_project_object)
        .mock()
        .should_receive("set_packages_config")
        .mock()
    )
    flexmock(PullRequestModel).should_receive("get_by_id").with_args(9).and_return(
        db_project_object,
    )
    flexmock(ProjectEventModel).should_receive("get_or_create").with_args(
        type=ProjectEventModelType.pull_request,
        event_id=9,
        commit_sha="0011223344",
    ).and_return(db_project_event)
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=9,
        namespace="packit-service",
        repo_name="hello-world",
        project_url="https://github.com/packit-service/hello-world",
    ).and_return(db_project_object)
    yield db_project_object, db_project_event


@pytest.fixture(scope="module")
def github_release_webhook() -> dict:
    with open(DATA_DIR / "webhooks" / "github" / "release.json") as outfile:
        return json.load(outfile)


@pytest.fixture(scope="module")
def release_event(github_release_webhook) -> events.github.release.Release:
    return Parser.parse_release_event(github_release_webhook)


@pytest.fixture(scope="module")
def github_pr_webhook():
    with open(DATA_DIR / "webhooks" / "github" / "pr.json") as outfile:
        return json.load(outfile)


@pytest.fixture(scope="module")
def github_push_webhook():
    with open(DATA_DIR / "webhooks" / "github" / "push_branch.json") as outfile:
        return json.load(outfile)


@pytest.fixture(scope="module")
def github_vm_image_build_comment():
    with open(
        DATA_DIR / "webhooks" / "github" / "vm_image_build_comment.json",
    ) as outfile:
        return json.load(outfile)


@pytest.fixture(scope="module")
def github_pr_event(github_pr_webhook) -> events.github.pr.Action:
    return Parser.parse_pr_event(github_pr_webhook)


@pytest.fixture(scope="module")
def github_push_event(github_push_webhook) -> events.github.push.Commit:
    return Parser.parse_github_push_event(github_push_webhook)


@pytest.fixture(scope="module")
def gitlab_mr_webhook():
    with open(DATA_DIR / "webhooks" / "gitlab" / "mr_event.json") as outfile:
        return json.load(outfile)


@pytest.fixture(scope="module")
def distgit_push_packit():
    with open(DATA_DIR / "fedmsg" / "distgit_push_packit.json") as outfile:
        return json.load(outfile)


@pytest.fixture(scope="module")
def distgit_push_event(distgit_push_packit) -> events.pagure.push.Commit:
    return Parser.parse_pagure_push_event(distgit_push_packit)


@pytest.fixture(scope="module")
def gitlab_mr_event(gitlab_mr_webhook) -> events.gitlab.mr.Action:
    return Parser.parse_mr_event(gitlab_mr_webhook)


@pytest.fixture
def cache_clear(request):
    """
    Fixture which cleans lru_cache of functions defined in module variable CACHE_CLEAR.
    This allows reliable test results.

    :return:
    """

    if getattr(request.module, "CACHE_CLEAR", None):
        [f.cache_clear() for f in request.module.CACHE_CLEAR]


@pytest.fixture()
def koji_build_scratch_start():
    with open(DATA_DIR / "fedmsg" / "koji_build_scratch_start.json") as outfile:
        # We are using the final format used by parser.
        return json.load(outfile)


@pytest.fixture()
def koji_build_scratch_end():
    with open(DATA_DIR / "fedmsg" / "koji_build_scratch_end.json") as outfile:
        # We are using the final format used by parser.
        return json.load(outfile)


@pytest.fixture()
def koji_build_start_old_format():
    with open(DATA_DIR / "fedmsg" / "koji_build_start_old_format.json") as outfile:
        return load_the_message_from_file(outfile)


@pytest.fixture()
def koji_build_start_rawhide():
    with open(DATA_DIR / "fedmsg" / "koji_build_start_rawhide.json") as outfile:
        return load_the_message_from_file(outfile)


@pytest.fixture()
def koji_build_start_f36():
    with open(DATA_DIR / "fedmsg" / "koji_build_start_f36.json") as outfile:
        return load_the_message_from_file(outfile)


@pytest.fixture()
def koji_build_start_epel8():
    with open(DATA_DIR / "fedmsg" / "koji_build_start_epel8.json") as outfile:
        return load_the_message_from_file(outfile)


@pytest.fixture()
def koji_build_completed_old_format():
    with open(DATA_DIR / "fedmsg" / "koji_build_completed_old_format.json") as outfile:
        return load_the_message_from_file(outfile)


@pytest.fixture()
def koji_build_completed_rawhide():
    with open(DATA_DIR / "fedmsg" / "koji_build_completed_rawhide.json") as outfile:
        return load_the_message_from_file(outfile)


@pytest.fixture()
def koji_build_completed_event(koji_build_completed_rawhide) -> events.koji.result.Build:
    return Parser.parse_koji_build_event(koji_build_completed_rawhide)


@pytest.fixture()
def koji_build_completed_f36():
    with open(DATA_DIR / "fedmsg" / "koji_build_completed_f36.json") as outfile:
        return load_the_message_from_file(outfile)


@pytest.fixture()
def koji_build_completed_epel8():
    with open(DATA_DIR / "fedmsg" / "koji_build_completed_epel8.json") as outfile:
        return load_the_message_from_file(outfile)


@pytest.fixture()
def koji_build_tagged():
    with open(DATA_DIR / "fedmsg" / "koji_build_tagged.json") as outfile:
        return load_the_message_from_file(outfile)


def pytest_assertrepr_compare(op, left, right):
    if isinstance(left, JobConfig) and isinstance(right, JobConfig) and op == "==":
        from packit.schema import JobConfigSchema

        schema = JobConfigSchema()
        return [str(DeepDiff(schema.dump(left), schema.dump(right)))]

    if isinstance(left, PackageConfig) and isinstance(right, PackageConfig) and op == "==":
        from packit.schema import PackageConfigSchema

        schema = PackageConfigSchema()
        return [str(DeepDiff(schema.dump(left), schema.dump(right)))]
    return None


@pytest.fixture()
def pagure_pr_comment_added():
    with open(DATA_DIR / "fedmsg" / "pagure_pr_comment.json") as outfile:
        return json.load(outfile)


@pytest.fixture()
def new_hotness_update():
    with open(DATA_DIR / "fedmsg" / "new_hotness_update.json") as outfile:
        return json.load(outfile)
