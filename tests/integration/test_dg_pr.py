# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json
from pathlib import Path

import pytest
from celery.canvas import Signature
from flexmock import flexmock
from ogr.abstract import CommitStatus
from ogr.services.pagure import PagureProject
from packit.api import PackitAPI
from packit.config import Deployment, JobConfigTriggerType
from packit.local_project import LocalProjectBuilder
from packit.utils import commands

from packit_service.config import ServiceConfig
from packit_service.constants import SANDCASTLE_WORK_DIR
from packit_service.models import (
    KojiBuildGroupModel,
    KojiBuildTargetModel,
    PipelineModel,
    ProjectEventModel,
    ProjectEventModelType,
    PullRequestModel,
)
from packit_service.worker.handlers import distgit
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.tasks import (
    run_downstream_koji_scratch_build_handler,
)
from tests.spellbook import DATA_DIR, first_dict_value, get_parameters_from_results


@pytest.fixture()
def distgit_pr_event():
    return json.loads((DATA_DIR / "fedmsg" / "pagure_pr_new.json").read_text())


@pytest.mark.parametrize(
    "target_branch, uid, check_name",
    [
        pytest.param(
            "rawhide",
            "e0091d5fbcb20572cbf2e6442af9bed5",
            "Packit - scratch build - rawhide",
            id="rawhide target branch",
        ),
        pytest.param(
            "f42",
            "6f08c3bbb20660dc8c597bc7dbe4f056",
            "Packit - scratch build - f42",
            id="f42 target branch",
        ),
    ],
)
def test_downstream_koji_scratch_build(distgit_pr_event, target_branch, uid, check_name):
    distgit_pr_event["pullrequest"]["branch"] = target_branch
    pr_object = (
        flexmock()
        .should_receive("set_flag")
        .with_args(username=check_name, comment=str, url=str, status=CommitStatus, uid=uid)
        .mock()
    )
    dg_project = (
        flexmock(
            PagureProject(namespace="rpms", repo="optee_os", service=flexmock(read_only=False))
        )
        .should_receive("is_private")
        .and_return(False)
        .mock()
        .should_receive("get_pr")
        .and_return(pr_object)
        .mock()
    )
    service_config = (
        flexmock(
            enabled_projects_for_fedora_ci="https://src.fedoraproject.org/rpms/optee_os",
            command_handler_work_dir=SANDCASTLE_WORK_DIR,
            repository_cache="/tmp/repository-cache",
            add_repositories_to_repository_cache=False,
            deployment=Deployment.stg,
        )
        .should_receive("get_project")
        .and_return(dg_project)
        .mock()
    )
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(service_config)
    db_project_object = flexmock(
        id=9,
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        project_event_model_type=ProjectEventModelType.pull_request,
        project=flexmock(project_url="https://src.fedoraproject.org/rpms/optee_os"),
    )
    db_project_event = (
        flexmock().should_receive("get_project_event_object").and_return(db_project_object).mock()
    )
    flexmock(ProjectEventModel).should_receive("get_or_create").with_args(
        type=ProjectEventModelType.pull_request,
        event_id=9,
        commit_sha="abcd",
    ).and_return(flexmock())
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=2,
        namespace="rpms",
        repo_name="optee_os",
        project_url="https://src.fedoraproject.org/rpms/optee_os",
    ).and_return(db_project_object)
    flexmock(ProjectEventModel).should_receive("get_or_create").and_return(
        db_project_event,
    )
    flexmock(PipelineModel).should_receive("create")

    koji_build = flexmock(
        id=123,
        target="main",
        status="queued",
        set_status=lambda x: None,
        set_task_id=lambda x: None,
        set_web_url=lambda x: None,
        set_build_logs_urls=lambda x: None,
        set_data=lambda x: None,
        set_build_submission_stdout=lambda x: None,
    )

    flexmock(KojiBuildTargetModel).should_receive("create").and_return(koji_build)
    flexmock(KojiBuildGroupModel).should_receive("create").and_return(
        flexmock(grouped_targets=[koji_build]),
    )

    flexmock(LocalProjectBuilder, _refresh_the_state=lambda *args: None)
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    flexmock(commands).should_receive("run_command_remote").with_args(
        cmd=[
            "koji",
            "build",
            "--scratch",
            "--nowait",
            target_branch,
            "git+https://src.fedoraproject.org/forks/zbyszek/rpms/optee_os.git#889f07af35d27bbcaf9c535c17a63b974aa42ee3",
        ],
        cwd=Path,
        output=True,
        print_live=True,
    ).and_return(flexmock(stdout="some output"))
    flexmock(PackitAPI).should_receive("init_kerberos_ticket")

    flexmock(distgit).should_receive("get_koji_task_id_and_url_from_stdout").and_return(
        (123, "koji-web-url")
    ).once()

    processing_results = SteveJobs().process_message(distgit_pr_event)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)
    results = run_downstream_koji_scratch_build_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]
