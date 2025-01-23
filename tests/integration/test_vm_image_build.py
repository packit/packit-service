# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json

from celery import Celery
from celery.canvas import group
from flexmock import flexmock
from ogr.services.github import GithubProject
from packit.copr_helper import CoprHelper

from packit_service.models import (
    CoprBuildTargetModel,
    JobConfigTriggerType,
    PipelineModel,
    ProjectEventModel,
    ProjectEventModelType,
    PullRequestModel,
    VMImageBuildTargetModel,
)
from packit_service.worker.allowlist import Allowlist
from packit_service.worker.handlers.vm_image import VMImageBuildHandler
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.tasks import (
    run_vm_image_build,
)
from tests.spellbook import first_dict_value, get_parameters_from_results


def test_vm_image_build(github_vm_image_build_comment):
    packit_yaml = (
        "{'specfile_path': 'python-knx-stack.spec',"
        " 'jobs': [{"
        "   'job': 'vm_image_build',"
        "   'trigger': 'pull_request',"
        "   'copr_chroot': 'fedora-36-x86_64',"
        "   'owner': 'mmassari',"
        "   'project': 'knx-stack',"
        "   'image_customizations': {'packages': ['python-knx-stack']},"
        "   'image_distribution': 'fedora-36',"
        "   'image_request': {"
        "     'architecture': 'x86_64',"
        "     'image_type': 'aws',"
        "     'upload_request': {'type': 'aws', 'options': {}}"
        "    }"
        "}]}"
    )
    project = flexmock(
        GithubProject,
        full_repo_name="mmassari/knx-stack",
        default_branch="main",
    )
    project.should_receive("is_private").and_return(False)
    project.should_receive("get_pr").and_return(
        flexmock(
            head_commit="123456",
            comment=lambda _: None,
            get_comment=lambda _: flexmock(add_reaction=lambda _: None),
        ),
    )
    project.should_receive("has_write_access").with_args(
        user="majamassarini",
    ).and_return(True)
    project.should_receive("get_files").with_args(
        ref="123456",
        filter_regex=r".+\.spec$",
    ).and_return(["packit.spec"])
    project.should_receive("get_file_content").with_args(
        path=".packit.yaml",
        ref="123456",
    ).and_return(packit_yaml)
    project.should_receive("get_files").with_args(
        ref="123456",
        recursive=False,
    ).and_return(["packit.spec", ".packit.yaml"])

    flexmock(ProjectEventModel).should_receive("get_or_create").with_args(
        type=ProjectEventModelType.pull_request,
        event_id=1,
        commit_sha="123456",
    ).and_return(flexmock())
    flexmock(PullRequestModel).should_receive("get_or_create").and_return(
        flexmock(
            job_config_trigger_type=JobConfigTriggerType.pull_request,
            project_event_model_type=ProjectEventModelType.pull_request,
            id=1,
            commit_sha="123456",
        ),
    )
    flexmock(Allowlist).should_receive("check_and_report").and_return(True)

    flexmock(CoprBuildTargetModel).should_receive("get_all_by").and_return(
        [
            flexmock(
                owner="mmassari",
                project_name="knx-stack",
                target="fedora-36-x86_64",
                status="success",
                get_project_event_object=lambda: flexmock(id=1),
            ),
        ],
    )
    flexmock(group).should_receive("apply_async").times(1)
    repo_download_url = (
        "https://download.copr.fedorainfracloud.org/results/mmassari/knx-stack/fedora-36-x86_64/"
    )
    flexmock(CoprHelper).should_receive("get_repo_download_url").once().and_return(
        repo_download_url,
    )
    flexmock(VMImageBuildHandler).should_receive("vm_image_builder").and_return(
        flexmock()
        .should_receive("create_image")
        .with_args(
            "fedora-36",
            "mmassari/knx-stack/21",
            {
                "architecture": "x86_64",
                "image_type": "aws",
                "upload_request": {"type": "aws", "options": {}},
            },
            {"packages": ["python-knx-stack"]},
            repo_download_url,
        )
        .mock(),
    )
    flexmock(VMImageBuildHandler).should_receive("report_status")
    flexmock(PipelineModel).should_receive("create").and_return(flexmock())
    flexmock(VMImageBuildTargetModel).should_receive("create").and_return(flexmock())
    flexmock(Celery).should_receive("send_task")
    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    processing_results = SteveJobs().process_message(github_vm_image_build_comment)
    event_dict, _, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)
    results = run_vm_image_build(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]
