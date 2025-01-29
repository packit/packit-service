# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT


import logging
import re
from typing import Union

from flexmock import flexmock
from ogr.abstract import GitProject
from packit.api import PackitAPI
from packit.config import (
    CommonPackageConfig,
    JobConfig,
    JobConfigTriggerType,
    JobType,
    PackageConfig,
)

from packit_service.config import ServiceConfig
from packit_service.events.github.pr import (
    Action as PullRequestGithubEvent,
)
from packit_service.events.github.pr import (
    Comment as PullRequestCommentGithubEvent,
)
from packit_service.events.github.push import Commit as PushGitHubEvent
from packit_service.events.github.release import Release as ReleaseEvent
from packit_service.models import SRPMBuildModel
from packit_service.worker.helpers.build.koji_build import KojiBuildJobHelper

logger = logging.getLogger(__name__)


def build_helper(
    event: Union[
        PullRequestGithubEvent,
        PullRequestCommentGithubEvent,
        PushGitHubEvent,
        ReleaseEvent,
    ],
    _targets=None,
    scratch=None,
    trigger=None,
    jobs=None,
    db_project_event=None,
):
    jobs = jobs or []
    jobs.append(
        JobConfig(
            type=JobType.upstream_koji_build,
            trigger=trigger or JobConfigTriggerType.pull_request,
            packages={
                "package": CommonPackageConfig(
                    _targets=_targets,
                    owner="nobody",
                    scratch=scratch,
                ),
            },
        ),
    )

    pkg_conf = PackageConfig(
        jobs=jobs,
        packages={"package": CommonPackageConfig(downstream_package_name="dummy")},
    )
    handler = KojiBuildJobHelper(
        service_config=ServiceConfig(),
        package_config=pkg_conf,
        job_config=pkg_conf.jobs[0],
        project=GitProject(repo=flexmock(), service=flexmock(), namespace=flexmock()),
        metadata=flexmock(
            pr_id=event.pr_id,
            git_ref=event.git_ref,
            commit_sha=event.commit_sha,
            identifier=event.identifier,
        ),
        db_project_event=db_project_event,
    )
    handler._api = PackitAPI(config=ServiceConfig(), package_config=pkg_conf)
    return handler


def test_build_srpm_log_format(github_pr_event):
    def mock_packit_log(*args, **kwargs):
        packit_logger = logging.getLogger("packit")
        packit_logger.debug("try debug")
        packit_logger.info("try info")
        return "my.srpm"

    def inspect_log_date_format(logs=None, **_):
        timestamp_reg = re.compile(
            r"[0-9]+-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]+\s.*",
        )

        log_lines = 0
        for line in logs.split("\n"):
            logger.debug(line)
            if len(line) == 0:
                continue
            log_lines += 1
            assert timestamp_reg.match(line)

        # Check if both test logs were recorded
        assert log_lines == 2

        return (None, None)

    db_project_object = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        pr_id=123,
    )
    helper = build_helper(
        event=github_pr_event,
        _targets=["bright-future"],
        scratch=True,
        db_project_event=flexmock()
        .should_receive("get_project_event_object")
        .and_return(db_project_object)
        .mock(),
    )

    flexmock(GitProject).should_receive("get_pr").and_return(
        flexmock(target_branch="main"),
    )
    flexmock(GitProject).should_receive("set_commit_status").and_return().never()
    local_project = flexmock()
    local_project.working_dir = ""
    up = flexmock()
    up.local_project = local_project
    flexmock(PackitAPI).should_receive("up").and_return(up)

    # flexmock(PackitAPI).should_receive("up").and_return()
    flexmock(PackitAPI).should_receive("create_srpm").replace_with(mock_packit_log)
    srpm_model_mock = (
        flexmock(SRPMBuildModel)
        .should_receive("set_start_time")
        .mock()
        .should_receive("set_logs")
        .replace_with(inspect_log_date_format)
        .mock()
        .should_receive("set_status")
        .mock()
        .should_receive("set_end_time")
        .mock()
    )
    flexmock(SRPMBuildModel).should_receive("create_with_new_run").and_return(
        (srpm_model_mock(), None),
    )
    helper._create_srpm()
