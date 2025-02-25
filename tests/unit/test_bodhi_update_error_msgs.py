# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import pytest
from flexmock import flexmock
from packit.config import (
    CommonPackageConfig,
    JobConfig,
    JobConfigTriggerType,
    JobType,
    PackageConfig,
)
from packit.exceptions import PackitException

from packit_service.config import ServiceConfig
from packit_service.events import (
    pagure,
)
from packit_service.events.enums import PullRequestAction
from packit_service.models import BodhiUpdateTargetModel
from packit_service.worker.celery_task import CeleryTask
from packit_service.worker.handlers import bodhi
from packit_service.worker.handlers.bodhi import (
    RetriggerBodhiUpdateHandler,
)
from packit_service.worker.handlers.mixin import KojiBuildData


@pytest.fixture(scope="module")
def package_config__job_config():
    package_config = PackageConfig(
        packages={
            "package": CommonPackageConfig(
                identifier="first",
            ),
        },
        jobs=[
            JobConfig(
                type=JobType.bodhi_update,
                trigger=JobConfigTriggerType.pull_request,
                packages={
                    "package": CommonPackageConfig(
                        identifier="first",
                    ),
                },
            ),
        ],
    )
    job_config = JobConfig(
        type=JobType.bodhi_update,
        trigger=JobConfigTriggerType.pull_request,
        packages={
            "package": CommonPackageConfig(
                identifier="first",
            ),
        },
    )
    return package_config, job_config


@pytest.fixture(scope="module")
def package_config__job_config__pull_request_event(package_config__job_config):
    package_config, job_config = package_config__job_config
    flexmock(pagure.pr.Comment).should_receive("commit_sha").and_return(
        "abcdef",
    )
    flexmock(pagure.pr.Comment).should_receive(
        "get_packages_config",
    ).and_return(package_config)
    data = pagure.pr.Comment(
        pr_id=123,
        action=PullRequestAction.opened,
        base_repo_namespace="a_namespace",
        base_repo_name="a_repo_name",
        base_repo_owner="a_owner",
        target_repo="a_target",
        project_url="projec_url",
        source_project_url="source_project_url",
        user_login="usr_login",
        comment="/packit creat-update",
        comment_id=321,
        base_ref="abcdef",
    ).get_dict()
    return package_config, job_config, data


def test_pull_request_retrigger_bodhi_update_with_koji_data(
    package_config__job_config__pull_request_event,
):
    package_config, job_config, data = package_config__job_config__pull_request_event

    msg = (
        "Packit failed on creating Bodhi update "
        "in dist-git (an url):\n\n"
        "<table>"
        "<tr><th>dist-git branch</th><th>error</th></tr>"
        "<tr><td><code>f36</code></td>"
        '<td>See <a href="/jobs/bodhi/12">/jobs/bodhi/12</a></td></tr>\n'
        "</table>\n\n"
        "Fedora Bodhi update was re-triggered by comment in dist-git PR with id 123.\n\n"
        "You can retrigger the update by adding a comment (`/packit create-update`) "
        "into this issue.\n\n---\n\n"
        "*Get in [touch with us](https://packit.dev/#contact) if you need some help.*\n"
    )
    flexmock(bodhi).should_receive("report_in_issue_repository").with_args(
        issue_repository=None,
        service_config=ServiceConfig,
        title=("Fedora Bodhi update failed to be created"),
        message=msg,
        comment_to_existing=msg,
    ).once()

    error_msg = "error abc"
    dg = flexmock(local_project=flexmock(git_url="an url"))
    packit_api = (
        flexmock(dg=dg).should_receive("create_update").and_raise(PackitException, error_msg).mock()
    )
    flexmock(RetriggerBodhiUpdateHandler).should_receive("packit_api").and_return(
        packit_api,
    )
    flexmock(RetriggerBodhiUpdateHandler).should_receive(
        "_get_or_create_bodhi_update_group_model",
    ).and_return(
        flexmock(
            grouped_targets=[
                flexmock(
                    id=12,
                    target="f36",
                    koji_nvrs="a_package_1.f36",
                    sidetag=None,
                    set_status=lambda x: None,
                    set_data=lambda x: None,
                ),
            ],
        ),
    )
    flexmock(RetriggerBodhiUpdateHandler).should_receive("__next__").and_return(
        KojiBuildData(
            dist_git_branch="f36",
            build_id=1,
            nvr="a_package_1.f36",
            state=1,
            task_id=123,
        ),
    )
    flexmock(BodhiUpdateTargetModel).should_receive(
        "get_all_successful_or_in_progress_by_nvrs",
    ).and_return(set())
    flexmock(CeleryTask).should_receive("is_last_try").and_return(True)
    handler = RetriggerBodhiUpdateHandler(package_config, job_config, data, flexmock())
    handler.run()
