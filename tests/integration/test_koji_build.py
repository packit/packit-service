# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json

import pytest
from celery.canvas import group
from flexmock import flexmock
from ogr.services.github import GithubProject
from ogr.services.pagure import PagureProject
from packit.config import JobConfigTriggerType
from packit.exceptions import PackitException
from packit.utils.koji_helper import KojiHelper

from packit_service.events import pagure
from packit_service.models import (
    GitBranchModel,
    KojiBuildGroupModel,
    KojiBuildTargetModel,
    PipelineModel,
    ProjectEventModel,
    ProjectEventModelType,
)
from packit_service.worker.celery_task import CeleryTask
from packit_service.worker.handlers.distgit import (
    DownstreamKojiBuildHandler,
)
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.reporting import utils
from packit_service.worker.tasks import (
    run_downstream_koji_build,
    run_downstream_koji_build_report,
)
from tests.conftest import koji_build_completed_rawhide, koji_build_start_rawhide
from tests.spellbook import first_dict_value, get_parameters_from_results


@pytest.mark.parametrize(
    "koji_build_fixture",
    [koji_build_start_rawhide, koji_build_completed_rawhide],
)
def test_downstream_koji_build_report_known_build(koji_build_fixture, request):
    koji_build_event = request.getfixturevalue(koji_build_fixture.__name__)
    packit_yaml = (
        "{'specfile_path': 'python-ogr.spec',"
        "'jobs': [{'trigger': 'commit', 'job': 'koji_build'}],"
        "'downstream_package_name': 'python-ogr'}"
    )
    pagure_project = flexmock(
        PagureProject,
        full_repo_name="rpms/python-ogr",
        get_web_url=lambda: "https://src.fedoraproject.org/rpms/python-ogr",
        default_branch="main",
    )
    pagure_project.should_receive("get_files").with_args(
        ref="main",
        filter_regex=r".+\.spec$",
    ).and_return(["python-ogr.spec"])
    pagure_project.should_receive("get_file_content").with_args(
        path=".packit.yaml",
        ref="main",
    ).and_return(packit_yaml)
    pagure_project.should_receive("get_files").with_args(
        ref="main",
        recursive=False,
    ).and_return(["python-ogr.spec", ".packit.yaml"])

    flexmock(GitBranchModel).should_receive("get_or_create").with_args(
        branch_name="main",
        namespace="rpms",
        repo_name="python-ogr",
        project_url="https://src.fedoraproject.org/rpms/python-ogr",
    ).and_return(flexmock(id=9, job_config_trigger_type=JobConfigTriggerType.commit))

    # 1*KojiBuildReportHandler
    flexmock(group).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").times(2).and_return()

    # Database
    flexmock(KojiBuildTargetModel).should_receive("get_by_task_id").with_args(
        task_id=80860894,
    ).and_return(
        flexmock(
            target="target",
            status="pending",
            web_url="some-url",
            build_logs_url=None,
            get_project_event_object=lambda: flexmock(
                id=1,
                job_config_trigger_type=JobConfigTriggerType.commit,
            ),
            set_status=lambda x: None,
            set_build_start_time=lambda x: None,
            set_build_finished_time=lambda x: None,
        )
        .should_receive("set_build_logs_urls")
        .with_args({})
        .and_return()
        .mock(),
    ).once()  # only when running a handler

    processing_results = SteveJobs().process_message(koji_build_event)
    # 1*KojiBuildReportHandler
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)
    results = run_downstream_koji_build_report(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )

    assert first_dict_value(results["job"])["success"]


def test_koji_build_error_msg(distgit_push_packit):
    packit_yaml = (
        "{'specfile_path': 'packit.spec',"
        "'jobs': [{'trigger': 'commit', 'job': 'koji_build'}],"
        "'downstream_package_name': 'python-ogr', 'issue_repository': "
        "'https://github.com/packit/packit'}"
    )
    pagure_project = flexmock(
        PagureProject,
        full_repo_name="rpms/packit",
        get_web_url=lambda: "https://src.fedoraproject.org/rpms/packit",
        default_branch="main",
    )
    pagure_project.should_receive("get_files").with_args(
        ref="main",
        filter_regex=r".+\.spec$",
    ).and_return(["packit.spec"])
    pagure_project.should_receive("get_file_content").with_args(
        path=".packit.yaml",
        ref="main",
    ).and_return(packit_yaml)
    pagure_project.should_receive("get_files").with_args(
        ref="main",
        recursive=False,
    ).and_return(["packit.spec", ".packit.yaml"])

    db_project_object = flexmock(
        id=123,
        job_config_trigger_type=JobConfigTriggerType.commit,
        project_event_model_type=ProjectEventModelType.branch_push,
    )
    db_project_event = (
        flexmock().should_receive("get_project_event_object").and_return(db_project_object).mock()
    )
    flexmock(pagure.push.Commit).should_receive("db_project_object").and_return(
        db_project_object,
    )
    flexmock(GitBranchModel).should_receive("get_or_create").and_return(
        db_project_object,
    )
    flexmock(ProjectEventModel).should_receive("get_or_create").and_return(
        db_project_event,
    )
    flexmock(PipelineModel).should_receive("create")

    nvr = "package-1.2.3-1.fc40"

    koji_build = flexmock(
        id=12,
        target="f36",
        status="queued",
        sidetag=None,
        nvr=nvr,
        set_status=lambda x: None,
        set_task_id=lambda x: None,
        set_web_url=lambda x: None,
        set_build_logs_urls=lambda x: None,
        set_data=lambda x: None,
    )

    flexmock(KojiBuildTargetModel).should_receive("create").and_return(koji_build)
    flexmock(KojiBuildTargetModel).should_receive(
        "get_all_successful_or_in_progress_by_nvr",
    ).and_return({koji_build})
    flexmock(KojiBuildGroupModel).should_receive("create").and_return(
        flexmock(grouped_targets=[koji_build]),
    )

    flexmock(DownstreamKojiBuildHandler).should_receive("pre_check").and_return(True)
    flexmock(Pushgateway).should_receive("push").times(2).and_return()
    flexmock(group).should_receive("apply_async").once()

    processing_results = SteveJobs().process_message(distgit_push_packit)
    event_dict, _, job_config, package_config = get_parameters_from_results(
        processing_results,
    )
    assert json.dumps(event_dict)

    flexmock(CeleryTask).should_receive("is_last_try").and_return(True)
    error_msg = "error abc"
    dg = flexmock(local_project=flexmock(git_url="an url"))
    dg.should_receive("get_nvr").and_return(nvr)
    packit_api = (
        flexmock(dg=dg).should_receive("build").and_raise(PackitException, error_msg).mock()
    )
    flexmock(DownstreamKojiBuildHandler).should_receive("packit_api").and_return(
        packit_api,
    )
    msg = (
        "Packit failed on creating Koji build in dist-git (an url):\n\n"
        "<table>"
        "<tr>"
        "<th>dist-git branch</th>"
        "<th>error</th>"
        "</tr>"
        "<tr><td><code>f36</code></td>"
        '<td>See <a href="https://localhost/jobs/koji/12">https://localhost/jobs/koji/12</a></td>'
        "</tr>\n"
        "</table>\n\n"
        "Fedora Koji build was triggered by push "
        "with sha ad0c308af91da45cf40b253cd82f07f63ea9cbbf."
        "\n\nYou can retrigger the build by adding a comment "
        "(`/packit koji-build`) into this issue."
        "\n\n---\n\n*Get in [touch with us]"
        "(https://packit.dev/#contact) if you need some help.*\n"
    )
    flexmock(utils).should_receive("create_issue_if_needed").with_args(
        project=GithubProject,
        title=("Fedora Koji build failed to be triggered"),
        message=msg,
        comment_to_existing=msg,
    ).once()

    run_downstream_koji_build(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )


@pytest.mark.parametrize(
    "build_info, result",
    [
        (None, False),
        ({"state": 0}, True),
        ({"state": 1}, True),
        ({"state": 2}, False),
    ],
)
def test_is_already_triggered(build_info, result):
    flexmock(KojiHelper).should_receive("get_build_info").and_return(build_info)

    assert (
        DownstreamKojiBuildHandler(
            package_config=flexmock(),
            job_config=flexmock(),
            event={},
            celery_task=flexmock(),
        ).is_already_triggered("rawhide")
        is result
    )
