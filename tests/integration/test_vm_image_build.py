# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json

from flexmock import flexmock

from celery import Celery
from celery.canvas import Signature

from ogr.services.github import GithubProject

from packit_service.worker.jobs import SteveJobs
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.tasks import (
    run_vm_image_build,
)
from packit_service.models import (
    PullRequestModel,
    JobConfigTriggerType,
    CoprBuildTargetModel,
    PipelineModel,
    VMImageBuildTargetModel,
    JobTriggerModelType,
)
from packit_service.worker.allowlist import Allowlist
from tests.spellbook import first_dict_value, get_parameters_from_results
from packit_service.worker.handlers.vm_image import VMImageBuildHandler


def test_vm_image_build(github_vm_image_build_comment):

    packit_yaml = (
        "{'specfile_path': 'python-knx-stack.spec',"
        " 'synced_files': [],"
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
    project = GithubProject(
        namespace="mmassari",
        repo="knx-stack",
        default_branch="main",
        service=flexmock(),
    )
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("get_pr").and_return(
        flexmock(
            head_commit="123456",
            comment=lambda _: None,
            get_comment=lambda _: flexmock(add_reaction=lambda _: None),
        )
    )
    flexmock(GithubProject).should_receive("get_file_content").with_args(
        path=".distro/source-git.yaml", ref="123456"
    ).and_raise(FileNotFoundError, "Not found.")
    flexmock(GithubProject).should_receive("get_file_content").with_args(
        path=".packit.yaml", ref="123456"
    ).and_return(packit_yaml)
    flexmock(project).should_receive("get_files").with_args(
        ref="0eb3e12005cb18f15d3054020f7ac934c01eae08", filter_regex=r".+\.spec$"
    ).and_return(["packit.spec"])

    flexmock(PullRequestModel).should_receive("get_or_create").and_return(
        flexmock(
            job_config_trigger_type=JobConfigTriggerType.pull_request,
            job_trigger_model_type=JobTriggerModelType.pull_request,
            id=1,
        )
    )
    flexmock(Allowlist).should_receive("check_and_report").and_return(True)

    flexmock(CoprBuildTargetModel).should_receive("get_all_by_commit").and_return(
        flexmock(project_name="knx-stack", target="fedora-36-x86_64", status="success"),
    )
    flexmock(Signature).should_receive("apply_async").times(1)
    flexmock(VMImageBuildHandler).should_receive("vm_image_builder").and_return(
        flexmock().should_receive("create_image").mock()
    )
    flexmock(VMImageBuildHandler).should_receive("report_status")
    flexmock(PipelineModel).should_receive("create").and_return(flexmock())
    flexmock(VMImageBuildTargetModel).should_receive("create").and_return(flexmock())
    flexmock(Celery).should_receive("send_task")
    flexmock(Pushgateway).should_receive("push").once().and_return()

    processing_results = SteveJobs().process_message(github_vm_image_build_comment)
    event_dict, _, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)
    results = run_vm_image_build(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]
