# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import copy

import celery
import pytest
from flexmock import flexmock
from ogr.exceptions import GithubAppNotInstalledError
from packit.config import (
    CommonPackageConfig,
    JobConfig,
    JobConfigTriggerType,
    JobType,
    PackageConfig,
)

from packit_service.config import ServiceConfig
from packit_service.constants import COMMENT_REACTION
from packit_service.events import (
    abstract,
    copr,
    github,
    gitlab,
    koji,
    pagure,
    testing_farm,
    vm_image,
)
from packit_service.worker.allowlist import Allowlist
from packit_service.worker.handlers import (
    CoprBuildEndHandler,
    CoprBuildHandler,
    CoprBuildStartHandler,
    JobHandler,
    KojiBuildHandler,
    KojiTaskReportHandler,
    ProposeDownstreamHandler,
    TestingFarmHandler,
    TestingFarmResultsHandler,
)
from packit_service.worker.handlers.bodhi import CreateBodhiUpdateHandler
from packit_service.worker.handlers.distgit import DownstreamKojiBuildHandler
from packit_service.worker.handlers.koji import (
    KojiBuildReportHandler,
    KojiBuildTagHandler,
)
from packit_service.worker.jobs import SteveJobs, get_handlers_for_check_rerun
from packit_service.worker.result import TaskResults


@pytest.mark.parametrize(
    "event_cls,db_project_object,jobs,result",
    [
        # Single job defined:
        pytest.param(
            github.pr.Action,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler},
            id="config=build_for_pr&pull_request&github.pr.Action",
        ),
        pytest.param(
            github.pr.Action,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler},
            id="config=copr_build_for_pr&pull_request&github.pr.Action",
        ),
        pytest.param(
            github.pr.Action,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {TestingFarmHandler},
            id="config=test_for_pr&pull_request&github.pr.Action",
        ),
        # Not matching event:
        pytest.param(
            github.pr.Action,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            set(),
            id="config=build_for_commit&pull_request&github.pr.Action",
        ),
        # Matching events:
        pytest.param(
            gitlab.mr.Action,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler},
            id="config=build_for_pr&pull_request&gitlab.mr.Action",
        ),
        pytest.param(
            github.push.Commit,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler},
            id="config=build_for_push&commit&github.push.Commit",
        ),
        pytest.param(
            gitlab.push.Commit,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler},
            id="config=build_for_push&commit&gitlab.push.Commit",
        ),
        pytest.param(
            github.release.Release,
            flexmock(job_config_trigger_type=JobConfigTriggerType.release),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler},
            id="config=build_for_release&release&github.release.Release",
        ),
        pytest.param(
            github.release.Release,
            flexmock(job_config_trigger_type=JobConfigTriggerType.release),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {TestingFarmHandler},
            id="config=test_for_release&release&github.release.Release",
        ),
        pytest.param(
            gitlab.release.Release,
            flexmock(job_config_trigger_type=JobConfigTriggerType.release),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {TestingFarmHandler},
            id="config=test_for_release&release&gitlab.release.Release",
        ),
        # Copr results for build:
        pytest.param(
            copr.Start,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildStartHandler},
            id="config=build_for_pr&pull_request&copr.Start",
        ),
        pytest.param(
            copr.End,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildEndHandler},
            id="config=build_for_pr&pull_request&copr.End",
        ),
        # Test results:
        pytest.param(
            testing_farm.Result,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {TestingFarmResultsHandler},
            id="config=test_for_pr&pull_request&testing_farm.Result",
        ),
        # Koji:
        pytest.param(
            github.pr.Action,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.upstream_koji_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {KojiBuildHandler},
            id="config=upstream_koji_build_for_pr&pull_request&github.pr.Action",
        ),
        pytest.param(
            gitlab.mr.Action,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.upstream_koji_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {KojiBuildHandler},
            id="config=upstream_koji_build_for_pr&pull_request&gitlab.mr.Action",
        ),
        pytest.param(
            github.push.Commit,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.upstream_koji_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {KojiBuildHandler},
            id="config=upstream_koji_build_for_commit&commit&github.push.Commit",
        ),
        pytest.param(
            gitlab.push.Commit,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.upstream_koji_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {KojiBuildHandler},
            id="config=upstream_koji_build_for_commit&commit&gitlab.push.Commit",
        ),
        pytest.param(
            github.release.Release,
            flexmock(job_config_trigger_type=JobConfigTriggerType.release),
            [
                JobConfig(
                    type=JobType.upstream_koji_build,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {KojiBuildHandler},
            id="config=upstream_koji_build_for_release&commit&github.release.Release",
        ),
        pytest.param(
            koji.result.Task,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.upstream_koji_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {KojiTaskReportHandler},
            id="config=upstream_koji_build_for_pr&pull_request&koji.result.Build",
        ),
        # Build and test:
        pytest.param(
            github.pr.Action,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler, TestingFarmHandler},
            id="config=build_for_pr+test_for_pr&pull_request&github.pr.Action",
        ),
        pytest.param(
            copr.Start,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildStartHandler},
            id="config=build_for_pr+test_for_pr&pull_request&copr.Start",
        ),
        pytest.param(
            copr.End,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildEndHandler},
            id="config=build_for_pr+test_for_pr&pull_request&copr.End",
        ),
        pytest.param(
            testing_farm.Result,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {TestingFarmResultsHandler},
            id="config=build_for_pr+test_for_pr&pull_request&testing_farm.Result",
        ),
        # Multiple triggers for copr-build:
        pytest.param(
            github.pr.Action,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler},
            id="config=build_for_pr+build_for_commit+build_for_release"
            "&pull_request&github.pr.Action",
        ),
        pytest.param(
            github.push.Commit,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler},
            id="config=build_for_pr+build_for_commit+build_for_release&commit&github.push.Commit",
        ),
        pytest.param(
            github.release.Release,
            flexmock(job_config_trigger_type=JobConfigTriggerType.release),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler},
            id="config=build_for_pr+build_for_commit+build_for_release&release&github.release.Release",
        ),
        # No matching job for multiple triggers:
        pytest.param(
            github.release.Release,
            flexmock(job_config_trigger_type=JobConfigTriggerType.release),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            set(),
            id="config=build_for_pr+build_for_commit&release&github.release.Release",
        ),
        # multiple events for build but test only for push:
        pytest.param(
            github.pr.Action,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler, TestingFarmHandler},
            id="config=build_for_pr+test_for_pr+build_for_commit+build_for_release"
            "&pull_request&github.pr.Action",
        ),
        pytest.param(
            github.pr.Action,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            skip_build=True,
                        ),
                    },
                ),
            ],
            {TestingFarmHandler},
            id="config=test_for_pr_skip_build&pull_request&github.pr.Action",
        ),
        pytest.param(
            github.pr.Action,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            skip_build=True,
                        ),
                    },
                ),
            ],
            {CoprBuildHandler, TestingFarmHandler},
            id="config=copr_build_for_pr+test_for_pr_skip_build&pull_request&github.pr.Action",
        ),
        pytest.param(
            github.push.Commit,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler},
            id="config=build_for_pr+test_for_pr+build_for_commit+build_for_release"
            "&commit&github.push.Commit",
        ),
        pytest.param(
            copr.Start,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildStartHandler},
            id="config=build_for_pr+test_for_pr+build_for_commit+build_for_release"
            "&pull_request&copr.Start",
        ),
        pytest.param(
            copr.End,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildEndHandler},
            id="config=build_for_pr+test_for_pr+build_for_commit+build_for_release"
            "&pull_request&copr.End",
        ),
        pytest.param(
            testing_farm.Result,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {TestingFarmResultsHandler},
            id="config=build_for_pr+test_for_pr+build_for_commit+build_for_release"
            "&pull_request&testing_farm.Result",
        ),
        pytest.param(
            testing_farm.Result,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            set(),
            id="config=build_for_pr+test_for_pr+build_for_commit+build_for_release"
            "&commit&testing_farm.Result",
        ),
        # build for commit and release, test only for push:
        pytest.param(
            github.pr.Action,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {TestingFarmHandler},
            id="config=test_for_pr+build_for_commit+build_for_release"
            "&pull_request&github.pr.Action",
        ),
        pytest.param(
            github.push.Commit,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler},
            id="config=test_for_pr+build_for_commit+build_for_release&commit&github.push.Commit",
        ),
        pytest.param(
            testing_farm.Result,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {TestingFarmResultsHandler},
            id="config=test_for_pr+build_for_commit+build_for_release"
            "&pull_request&testing_farm.Result",
        ),
        pytest.param(
            testing_farm.Result,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            set(),
            id="config=test_for_pr+build_for_commit+build_for_release&commit&testing_farm.Result",
        ),
        # copr and koji build combination
        pytest.param(
            github.pr.Action,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.upstream_koji_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler, KojiBuildHandler},
            id="config=build_for_pr+upstream_koji_build_for_pr&pull_request&github.pr.Action",
        ),
        pytest.param(
            copr.Start,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.upstream_koji_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildStartHandler},
            id="config=build_for_pr+upstream_koji_build_for_pr&pull_request&copr.Start",
        ),
        pytest.param(
            koji.result.Task,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.upstream_koji_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {KojiTaskReportHandler},
            id="config=build_for_pr+upstream_koji_build_for_pr&pull_request&koji.result.Build",
        ),
        pytest.param(
            pagure.push.Commit,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.koji_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {DownstreamKojiBuildHandler},
            id="config=koji_build_for_commit&commit&DownstreamKojiBuildHandler",
        ),
        pytest.param(
            koji.result.Build,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.koji_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {KojiBuildReportHandler},
            id="config=koji_build_for_commit&build&DownstreamKojiBuildHandler",
        ),
        pytest.param(
            koji.result.Build,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.bodhi_update,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CreateBodhiUpdateHandler, KojiBuildReportHandler},
            id="config=bodhi_update_for_commit&commit&CreateBodhiUpdateHandler",
        ),
        pytest.param(
            gitlab.release.Release,
            flexmock(job_config_trigger_type=JobConfigTriggerType.release),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler},
            id="Copr build on release on GitLab",
        ),
        pytest.param(
            gitlab.release.Release,
            flexmock(job_config_trigger_type=JobConfigTriggerType.release),
            [
                JobConfig(
                    type=JobType.upstream_koji_build,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {KojiBuildHandler},
            id="Upstream Koji build on release on GitLab",
        ),
        pytest.param(
            koji.tag.Build,
            flexmock(job_config_trigger_type=JobConfigTriggerType.koji_build),
            [
                JobConfig(
                    type=JobType.koji_build_tag,
                    trigger=JobConfigTriggerType.koji_build,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {KojiBuildTagHandler},
            id="Koji build tagged",
        ),
    ],
)
def test_get_handlers_for_event(event_cls, db_project_object, jobs, result):
    # We are using isinstance for matching event to handlers
    # and flexmock can't do this for us so we need a subclass to test it.
    # (And real event classes have a lot of __init__ arguments.)
    class Event(event_cls):
        def __init__(self):
            self.package_name = "test"

        @property
        def db_project_object(self):
            return db_project_object

        @property
        def packages_config(self):
            return flexmock(
                get_job_views=lambda: jobs,
                packages={"package": CommonPackageConfig()},
            )

    event = Event()
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(
        ServiceConfig(packit_comment_command_prefix="/packit"),
    )

    event_handlers = set(SteveJobs(event).get_handlers_for_event())
    assert event_handlers == result


@pytest.mark.parametrize(
    "event_cls, comment, packit_comment_command_prefix, db_project_object, jobs, result",
    [
        pytest.param(
            github.pr.Comment,
            "",
            "/packit",
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            set(),
            id="config=build_for_pr&pull_request&github.pr.Comment&empty_comment",
        ),
        pytest.param(
            github.pr.Comment,
            "",
            "/packit-stg",
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            set(),
            id="config=build_for_pr&pull_request&github.pr.Comment&empty_comment&stg",
        ),
        pytest.param(
            github.pr.Comment,
            "/packit build",
            "/packit",
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler},
            id="config=build_for_pr&pull_request&github.pr.Comment&packit_build",
        ),
        pytest.param(
            github.pr.Comment,
            "/packit build",
            "/packit",
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler},
            id="config=copr_build_for_pr&pull_request&github.pr.Comment&packit_build",
        ),
        pytest.param(
            github.pr.Comment,
            "/packit copr-build",
            "/packit",
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler},
            id="config=build_for_pr&pull_request&github.pr.Comment&packit_copr-build",
        ),
        pytest.param(
            github.pr.Comment,
            "/packit build",
            "/packit",
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {TestingFarmHandler},
            id="config=test_for_pr&pull_request&github.pr.Comment&packit_build",
        ),
        pytest.param(
            github.pr.Comment,
            "/packit test",
            "/packit",
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {TestingFarmHandler},
            id="config=test_for_pr&pull_request&github.pr.Comment&packit_test",
        ),
        pytest.param(
            github.pr.Comment,
            "/packit upstream-koji-build",
            "/packit",
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.upstream_koji_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {KojiBuildHandler},
            id="config=upstream_koji_build_for_pr&pull_request&github.pr.Comment"
            "&packit_production-build",
        ),
        pytest.param(
            github.pr.Comment,
            "/packit build",
            "/packit",
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            skip_build=True,
                        ),
                    },
                ),
            ],
            {TestingFarmHandler},
            id="config=test_for_pr_skip_build&pull_request&github.pr.Comment&packit_build",
        ),
        pytest.param(
            github.pr.Comment,
            "/packit test",
            "/packit",
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            skip_build=True,
                        ),
                    },
                ),
            ],
            {TestingFarmHandler},
            id="config=test_for_pr_skip_build&pull_request&github.pr.Comment&packit_test",
        ),
        pytest.param(
            github.pr.Comment,
            "/packit build",
            "/packit-stg",
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            set(),
            id="config=build_for_pr&pull_request&github.pr.Comment&packit_build&stg",
        ),
        pytest.param(
            github.pr.Comment,
            "/packit-stg build",
            "/packit-stg",
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler},
            id="config=build_for_pr&pull_request&github.pr.Comment&packit_stg_build&stg",
        ),
    ],
)
def test_get_handlers_for_comment_event(
    event_cls,
    comment,
    packit_comment_command_prefix,
    db_project_object,
    jobs,
    result,
):
    # We are using isinstance for matching event to handlers
    # and flexmock can't do this for us so we need a subclass to test it.
    # (And real event classes have a lot of __init__ arguments.)
    class Event(event_cls):
        def __init__(self):
            self.comment = comment

        @property
        def db_project_object(self):
            return db_project_object

        @property
        def packages_config(self):
            return flexmock(get_job_views=lambda: jobs)

    flexmock(ServiceConfig).should_receive("get_service_config").and_return(
        ServiceConfig(comment_command_prefix=packit_comment_command_prefix),
    )

    event = Event()
    if result:
        comment_object = flexmock()
        event._comment_object = comment_object
        flexmock(comment_object).should_receive("add_reaction").with_args(
            COMMENT_REACTION,
        ).once()

    event_handlers = set(SteveJobs(event).get_handlers_for_event())
    assert event_handlers == result


@pytest.mark.parametrize(
    "event_cls,check_name_job,db_project_object,job_identifier,jobs,result",
    [
        pytest.param(
            github.check.PullRequest,
            "rpm-build",
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            None,
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler},
            id="config=build_for_pr&pull_request&github.check.PullRequest",
        ),
        pytest.param(
            github.check.PullRequest,
            "rpm-build",
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            "the-identifier",
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            identifier="the-identifier",
                        ),
                    },
                ),
            ],
            {CoprBuildHandler},
            id="config=build_for_pr&pull_request&github.check.PullRequest&identifier_match",
        ),
        pytest.param(
            github.check.PullRequest,
            "rpm-build",
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            None,
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            identifier="the-identifier",
                        ),
                    },
                ),
            ],
            set(),
            id="config=build_for_pr&pull_request&github.check.PullRequest&identifier_not_in_event",
        ),
        pytest.param(
            github.check.PullRequest,
            "rpm-build",
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            "the-identifier",
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            set(),
            id="config=build_for_pr&pull_request&github.check.PullRequest&identifier_not_in_config",
        ),
        pytest.param(
            github.check.PullRequest,
            "testing-farm",
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            None,
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {TestingFarmHandler},
            id="config=tests_for_pr&pull_request&github.check.PullRequest",
        ),
        pytest.param(
            github.check.PullRequest,
            "koji-build",
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            None,
            [
                JobConfig(
                    type=JobType.upstream_koji_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {KojiBuildHandler},
            id="config=upstream_koji_build_for_pr&pull_request&github.check.PullRequest",
        ),
        pytest.param(
            github.check.Commit,
            "rpm-build",
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            None,
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {CoprBuildHandler},
            id="config=build_for_pr&pull_request&github.check.Commit",
        ),
        pytest.param(
            github.check.Commit,
            "testing-farm",
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            None,
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {TestingFarmHandler},
            id="config=tests_for_pr&pull_request&github.check.Commit",
        ),
        pytest.param(
            github.check.Release,
            "koji-build",
            flexmock(job_config_trigger_type=JobConfigTriggerType.release),
            None,
            [
                JobConfig(
                    type=JobType.upstream_koji_build,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {KojiBuildHandler},
            id="config=upstream_koji_build_for_release&pull_request&github.check.Commit",
        ),
        pytest.param(
            github.check.Release,
            "koji-build",
            flexmock(job_config_trigger_type=JobConfigTriggerType.release),
            None,
            [
                JobConfig(
                    type=JobType.upstream_koji_build,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {KojiBuildHandler},
            id="config=upstream_koji_build_for_release&pull_request&github.check.Commit",
        ),
    ],
)
def test_get_handlers_for_check_rerun_event(
    event_cls,
    check_name_job,
    job_identifier,
    db_project_object,
    jobs,
    result,
):
    # We are using isinstance for matching event to handlers
    # and flexmock can't do this for us so we need a subclass to test it.
    # (And real event classes have a lot of __init__ arguments.)
    class Event(event_cls):
        def __init__(self):
            self.check_name_job = check_name_job
            self.job_identifier = job_identifier

        @property
        def db_project_object(self):
            return db_project_object

        @property
        def packages_config(self):
            return flexmock(get_job_views=lambda: jobs)

    flexmock(ServiceConfig).should_receive("get_service_config").and_return(
        ServiceConfig(packit_comment_command_prefix="/packit"),
    )
    event = Event()

    event_handlers = set(SteveJobs(event).get_handlers_for_event())
    assert event_handlers == result


@pytest.mark.parametrize(
    "handler_kls,event_cls,db_project_object,jobs,result_job_config",
    [
        # Basic copr build:
        pytest.param(
            CoprBuildHandler,
            github.pr.Action,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            id="build_for_pr&CoprBuildHandler&github.pr.Action",
        ),
        pytest.param(
            CoprBuildStartHandler,
            copr.Start,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            id="build_for_pr&CoprBuildStartHandler&copr.Start",
        ),
        pytest.param(
            CoprBuildEndHandler,
            copr.End,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            id="build_for_pr&CoprBuildEndHandler&copr.End",
        ),
        # Test only for pr:
        pytest.param(
            CoprBuildHandler,
            github.pr.Action,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [],
            id="tests_for_pr&CoprBuildHandler&github.pr.Action",
        ),
        # Both test and build for pr:
        pytest.param(
            CoprBuildHandler,
            github.pr.Action,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            id="build_for_pr+tests_for_pr&CoprBuildHandler&github.pr.Action",
        ),
        pytest.param(
            CoprBuildHandler,
            github.pr.Action,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                # Reverse order:
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            id="test_for_pr+build_for_pr&CoprBuildHandler&github.pr.Action",
        ),
        # Multiple builds for pr:
        pytest.param(
            CoprBuildHandler,
            github.pr.Action,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project2",
                        ),
                    },
                ),
            ],
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project2",
                        ),
                    },
                ),
            ],
            id="build_for_pr_twice&CoprBuildHandler&github.pr.Action",
        ),
        # Multiple triggers for build:
        pytest.param(
            CoprBuildHandler,
            github.pr.Action,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            project="project2",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            project="project3",
                        ),
                    },
                ),
            ],
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        ),
                    },
                ),
            ],
            id="build_for_pr+build_for_commit+build_for_release&CoprBuildHandler&github.pr.Action",
        ),
        pytest.param(
            CoprBuildHandler,
            github.push.Commit,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            project="project2",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            project="project3",
                        ),
                    },
                ),
            ],
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            project="project2",
                        ),
                    },
                ),
            ],
            id="build_for_pr+build_for_commit+build_for_release&CoprBuildHandler&github.push.Commit",
        ),
        pytest.param(
            CoprBuildHandler,
            github.release.Release,
            flexmock(job_config_trigger_type=JobConfigTriggerType.release),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            project="project2",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            project="project3",
                        ),
                    },
                ),
            ],
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            project="project3",
                        ),
                    },
                ),
            ],
            id="build_for_pr+build_for_commit+build_for_release&CoprBuildHandler&github.release.Release",
        ),
        # Build for commit and release, test for pr
        pytest.param(
            CoprBuildHandler,
            github.pr.Action,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            project="project2",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            project="project3",
                        ),
                    },
                ),
            ],
            [],
            id="tests_for_pr+build_for_commit+build_for_release&CoprBuildHandler&github.pr.Action",
        ),
        pytest.param(
            CoprBuildHandler,
            github.push.Commit,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            project="project2",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            project="project3",
                        ),
                    },
                ),
            ],
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            project="project2",
                        ),
                    },
                ),
            ],
            id="tests_for_pr+build_for_commit+build_for_release&CoprBuildHandler&github.push.Commit",
        ),
        pytest.param(
            CoprBuildHandler,
            github.release.Release,
            flexmock(job_config_trigger_type=JobConfigTriggerType.release),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            project="project2",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            project="project3",
                        ),
                    },
                ),
            ],
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            project="project3",
                        ),
                    },
                ),
            ],
            id="tests_for_pr+build_for_commit+build_for_release&CoprBuildHandler&github.release.Release",
        ),
        pytest.param(
            TestingFarmResultsHandler,
            testing_farm.Result,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            project="project2",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            project="project3",
                        ),
                    },
                ),
            ],
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        ),
                    },
                ),
            ],
            id="tests_for_pr+build_for_commit+build_for_release"
            "&TestingFarmResultsHandler&testing_farm.Result",
        ),
        # Build for pr, commit and release, test for pr
        pytest.param(
            CoprBuildHandler,
            github.pr.Action,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project0",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            project="project2",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            project="project3",
                        ),
                    },
                ),
            ],
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project0",
                        ),
                    },
                ),
            ],
            id="build_for_pr+tests_for_pr+build_for_commit+build_for_release"
            "&CoprBuildHandler&github.pr.Action",
        ),
        pytest.param(
            CoprBuildHandler,
            github.push.Commit,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project0",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            project="project2",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            project="project3",
                        ),
                    },
                ),
            ],
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            project="project2",
                        ),
                    },
                ),
            ],
            id="build_for_pr+tests_for_pr+build_for_commit+build_for_release"
            "&CoprBuildHandler&github.push.Commit",
        ),
        pytest.param(
            CoprBuildHandler,
            github.release.Release,
            flexmock(job_config_trigger_type=JobConfigTriggerType.release),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project0",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            project="project2",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            project="project3",
                        ),
                    },
                ),
            ],
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            project="project3",
                        ),
                    },
                ),
            ],
            id="build_for_pr+tests_for_pr+build_for_commit+build_for_release"
            "&CoprBuildHandler&github.release.Release",
        ),
        pytest.param(
            CoprBuildStartHandler,
            copr.Start,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project0",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            project="project2",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            project="project3",
                        ),
                    },
                ),
            ],
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project0",
                        ),
                    },
                ),
            ],
            id="build_for_pr+tests_for_pr+build_for_commit+build_for_release"
            "&CoprBuildStartHandler&copr.Start",
        ),
        pytest.param(
            CoprBuildEndHandler,
            copr.End,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project0",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            project="project2",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            project="project3",
                        ),
                    },
                ),
            ],
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project0",
                        ),
                    },
                ),
            ],
            id="build_for_pr+tests_for_pr+build_for_commit+build_for_release"
            "&CoprBuildEndHandler&copr.End",
        ),
        pytest.param(
            TestingFarmResultsHandler,
            testing_farm.Result,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project0",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            project="project2",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    packages={
                        "package": CommonPackageConfig(
                            project="project3",
                        ),
                    },
                ),
            ],
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            project="project1",
                        ),
                    },
                ),
            ],
            id="build_for_pr+tests_for_pr+build_for_commit+build_for_release"
            "&TestingFarmResultsHandler&testing_farm.Result",
        ),
        # copr build and koji build:
        pytest.param(
            CoprBuildHandler,
            github.pr.Action,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.upstream_koji_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            id="build_for_pr+upstream_koji_build_for_pr&CoprBuildHandler&github.pr.Action",
        ),
        pytest.param(
            KojiBuildHandler,
            github.pr.Action,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.upstream_koji_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [
                JobConfig(
                    type=JobType.upstream_koji_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            id="build_for_pr+upstream_koji_build_for_pr&KojiBuildHandler&github.pr.Action",
        ),
        pytest.param(
            KojiTaskReportHandler,
            koji.result.Task,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.upstream_koji_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [
                JobConfig(
                    type=JobType.upstream_koji_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            id="build_for_pr+upstream_koji_build_for_pr&KojiBuildReportHandler&koji.result.Build",
        ),
        # comments:
        pytest.param(
            CoprBuildHandler,
            github.pr.Comment,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            id="build_for_pr&CoprBuildHandler&github.pr.Comment",
        ),
        pytest.param(
            CoprBuildHandler,
            gitlab.mr.Comment,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            id="build_for_pr&CoprBuildHandler&gitlab.mr.Comment",
        ),
        pytest.param(
            CoprBuildHandler,
            pagure.pr.Comment,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            id="build_for_pr&CoprBuildHandler&pagure.pr.Comment",
        ),
        # Build comment for test defined:
        pytest.param(
            CoprBuildHandler,
            github.pr.Comment,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [],
            id="tests_for_pr&CoprBuildHandler&github.pr.Comment",
        ),
        # Testing farm on comment:
        pytest.param(
            TestingFarmHandler,
            github.pr.Comment,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            id="tests_for_pr&TestingFarmHandler&github.pr.Comment",
        ),
        # Propose update retrigger:
        pytest.param(
            ProposeDownstreamHandler,
            github.issue.Comment,
            flexmock(job_config_trigger_type=JobConfigTriggerType.release),
            [
                JobConfig(
                    type=JobType.propose_downstream,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [
                JobConfig(
                    type=JobType.propose_downstream,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            id="propose_downstream_for_release&TestingFarmHandler&github.pr.Comment",
        ),
        pytest.param(
            DownstreamKojiBuildHandler,
            pagure.push.Commit,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.koji_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [
                JobConfig(
                    type=JobType.koji_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            id="koji_build_for_commit&DownstreamKojiBuildHandler&pagure.push.Commit",
        ),
        pytest.param(
            KojiBuildReportHandler,
            koji.result.Build,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.koji_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [
                JobConfig(
                    type=JobType.koji_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            id="koji_build_for_commit&KojiBuildReportHandler&koji.result.Build",
        ),
        pytest.param(
            CreateBodhiUpdateHandler,
            koji.result.Build,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.bodhi_update,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [
                JobConfig(
                    type=JobType.bodhi_update,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            id="bodhi_update_for_commit&CreateBodhiUpdateHandler&koji.result.Build",
        ),
        pytest.param(
            KojiBuildReportHandler,
            koji.result.Build,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.bodhi_update,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [
                JobConfig(
                    type=JobType.bodhi_update,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            id="bodhi_update_for_commit&KojiBuildReportHandler&koji.result.Build",
        ),
    ],
)
def test_get_config_for_handler_kls(
    handler_kls: type[JobHandler],
    event_cls,
    db_project_object,
    jobs,
    result_job_config,
):
    class Event(event_cls):  # type: ignore
        def __init__(self):
            pass

        @property
        def db_project_object(self):
            return db_project_object

        @property
        def packages_config(self):
            return flexmock(get_job_views=lambda: jobs)

    event = Event()

    job_config = SteveJobs(event).get_config_for_handler_kls(
        handler_kls=handler_kls,
    )
    assert job_config == result_job_config


@pytest.mark.parametrize(
    "event_kls,comment,packit_comment_command_prefix,result",
    [
        pytest.param(
            github.pr.Comment,
            "/packit build",
            "/packit",
            {CoprBuildHandler, TestingFarmHandler},
        ),
        pytest.param(
            github.pr.Comment,
            "/packit-stg build",
            "/packit-stg",
            {CoprBuildHandler, TestingFarmHandler},
        ),
        pytest.param(
            github.pr.Comment,
            "/packit test",
            "/packit",
            {TestingFarmHandler},
        ),
        pytest.param(
            github.pr.Comment,
            "/packit-stg test",
            "/packit-stg",
            {TestingFarmHandler},
        ),
        pytest.param(
            github.pr.Comment,
            "/packit propose-downstream",
            "/packit",
            {ProposeDownstreamHandler},
        ),
        pytest.param(
            github.pr.Comment,
            "/packit-stg propose-downstream",
            "/packit-stg",
            {ProposeDownstreamHandler},
        ),
        pytest.param(
            github.pr.Comment,
            "/packit upstream-koji-build",
            "/packit",
            {KojiBuildHandler},
        ),
        pytest.param(
            github.pr.Comment,
            "/packit-stg upstream-koji-build",
            "/packit-stg",
            {KojiBuildHandler},
        ),
    ],
)
def test_get_handlers_triggered_by_comment(
    event_kls,
    comment,
    packit_comment_command_prefix,
    result,
):
    class Event(event_kls):
        def __init__(self):
            self.comment = comment

    event = Event()
    comment_object = flexmock()
    event._comment_object = comment_object
    flexmock(comment_object).should_receive("add_reaction").with_args(
        COMMENT_REACTION,
    ).once()

    flexmock(ServiceConfig).should_receive("get_service_config").and_return(
        ServiceConfig(comment_command_prefix=packit_comment_command_prefix),
    )
    event_handlers = SteveJobs(event).get_handlers_for_comment_and_rerun_event()
    assert event_handlers == result


@pytest.mark.parametrize(
    "event_kls,check_name_job,result",
    [
        pytest.param(
            github.check.PullRequest,
            "rpm-build",
            {CoprBuildHandler},
        ),
        pytest.param(
            github.check.PullRequest,
            "testing-farm",
            {TestingFarmHandler},
        ),
        pytest.param(
            github.check.PullRequest,
            "koji-build",
            {KojiBuildHandler},
        ),
    ],
)
def test_get_handlers_triggered_by_check_rerun(event_kls, check_name_job, result):
    class Event(event_kls):
        def __init__(self):
            self.check_name_job = check_name_job

    flexmock(ServiceConfig).should_receive("get_service_config").and_return(
        ServiceConfig(packit_comment_command_prefix="/packit"),
    )
    event = Event()
    event_handlers = SteveJobs(event).get_handlers_for_comment_and_rerun_event()
    assert event_handlers == result


@pytest.mark.parametrize(
    "event_kls,handler,allowed_handlers",
    [
        pytest.param(
            github.pr.Comment,
            CoprBuildHandler,
            {CoprBuildHandler, KojiBuildHandler},
        ),
        pytest.param(
            github.check.PullRequest,
            KojiBuildHandler,
            {CoprBuildHandler, KojiBuildHandler},
        ),
        pytest.param(
            github.release.Release,
            ProposeDownstreamHandler,
            {KojiBuildHandler, ProposeDownstreamHandler},
        ),
    ],
)
def test_handler_matches_to_job(event_kls, handler: type[JobHandler], allowed_handlers):
    class Event(event_kls):  # type: ignore
        def __init__(self):
            pass

    event = Event()
    assert SteveJobs(event).is_handler_matching_the_event(handler, allowed_handlers)


@pytest.mark.parametrize(
    "event_kls,handler,allowed_handlers",
    [
        pytest.param(
            pagure.push.Commit,
            CoprBuildHandler,
            {DownstreamKojiBuildHandler},
        ),
        pytest.param(
            github.check.PullRequest,
            KojiBuildHandler,
            {CoprBuildHandler},
        ),
    ],
)
def test_handler_doesnt_match_to_job(
    event_kls,
    handler: type[JobHandler],
    allowed_handlers,
):
    class Event(event_kls):  # type: ignore
        def __init__(self):
            pass

    event = Event()
    assert not SteveJobs(event).is_handler_matching_the_event(handler, allowed_handlers)


@pytest.mark.parametrize(
    "event_kls,job_config_trigger_type,jobs,result,kwargs",
    [
        # GitHub comment event tests
        pytest.param(
            github.pr.Comment,
            JobConfigTriggerType.pull_request,
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.propose_downstream,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {},
        ),
        pytest.param(
            github.pr.Comment,
            JobConfigTriggerType.release,
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.propose_downstream,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [
                JobConfig(
                    type=JobType.propose_downstream,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {},
        ),
        pytest.param(
            github.pr.Comment,
            JobConfigTriggerType.release,
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.propose_downstream,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.propose_downstream,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [
                JobConfig(
                    type=JobType.propose_downstream,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {},
        ),
        pytest.param(
            github.pr.Comment,
            JobConfigTriggerType.release,
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [],
            {},
        ),
        pytest.param(
            github.pr.Comment,
            JobConfigTriggerType.pull_request,
            [
                JobConfig(
                    type=JobType.bodhi_update,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                    manual_trigger=False,
                ),
                JobConfig(
                    type=JobType.propose_downstream,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [
                JobConfig(
                    type=JobType.bodhi_update,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                    manual_trigger=False,
                ),
            ],
            {},
        ),
        pytest.param(
            github.pr.Comment,
            JobConfigTriggerType.pull_request,
            [
                JobConfig(
                    type=JobType.bodhi_update,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                    manual_trigger=False,
                ),
            ],
            [
                JobConfig(
                    type=JobType.bodhi_update,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                    manual_trigger=False,
                ),
            ],
            {},
        ),
        pytest.param(
            github.pr.Comment,
            JobConfigTriggerType.release,
            [
                JobConfig(
                    type=JobType.bodhi_update,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                    manual_trigger=True,
                ),
            ],
            [
                JobConfig(
                    type=JobType.bodhi_update,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                    manual_trigger=True,
                ),
            ],
            {},
        ),
        pytest.param(
            github.pr.Action,
            JobConfigTriggerType.pull_request,
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                    manual_trigger=True,
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                    manual_trigger=False,
                ),
            ],
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                    manual_trigger=False,
                ),
            ],
            {},
        ),
        pytest.param(
            github.release.Release,
            JobConfigTriggerType.release,
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                    manual_trigger=True,
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                    manual_trigger=False,
                ),
            ],
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                    manual_trigger=False,
                ),
            ],
            {},
        ),
        # GitLab comment event tests
        pytest.param(
            gitlab.mr.Comment,
            JobConfigTriggerType.pull_request,
            [
                JobConfig(
                    type=JobType.bodhi_update,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                    manual_trigger=False,
                ),
                JobConfig(
                    type=JobType.propose_downstream,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [
                JobConfig(
                    type=JobType.bodhi_update,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                    manual_trigger=False,
                ),
            ],
            {},
        ),
        pytest.param(
            gitlab.mr.Comment,
            JobConfigTriggerType.pull_request,
            [
                JobConfig(
                    type=JobType.bodhi_update,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                    manual_trigger=False,
                ),
            ],
            [
                JobConfig(
                    type=JobType.bodhi_update,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                    manual_trigger=False,
                ),
            ],
            {},
        ),
        pytest.param(
            gitlab.mr.Comment,
            JobConfigTriggerType.release,
            [
                JobConfig(
                    type=JobType.bodhi_update,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                    manual_trigger=True,
                ),
            ],
            [
                JobConfig(
                    type=JobType.bodhi_update,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                    manual_trigger=True,
                ),
            ],
            {},
        ),
        pytest.param(
            gitlab.mr.Action,
            JobConfigTriggerType.pull_request,
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                    manual_trigger=True,
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                    manual_trigger=False,
                ),
            ],
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                    manual_trigger=False,
                ),
            ],
            {},
        ),
        pytest.param(
            gitlab.release.Release,
            JobConfigTriggerType.release,
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                    manual_trigger=True,
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                    manual_trigger=False,
                ),
            ],
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.release,
                    packages={"package": CommonPackageConfig()},
                    manual_trigger=False,
                ),
            ],
            {},
        ),
        # Pagure comment event tests
        pytest.param(
            pagure.pr.Comment,
            JobConfigTriggerType.pull_request,
            [
                JobConfig(
                    type=JobType.koji_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [
                JobConfig(
                    type=JobType.koji_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {"comment": "test"},
        ),
        pytest.param(
            pagure.pr.Comment,
            JobConfigTriggerType.pull_request,
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.koji_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.koji_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {"comment": "test"},
        ),
        pytest.param(
            github.check.PullRequest,
            JobConfigTriggerType.pull_request,
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            identifier="first",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            identifier="second",
                        ),
                    },
                ),
            ],
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            identifier="first",
                        ),
                    },
                ),
            ],
            {"job_identifier": "first"},
        ),
        pytest.param(
            pagure.pr.Comment,
            JobConfigTriggerType.pull_request,
            [
                JobConfig(
                    type=JobType.bodhi_update,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            identifier="first",
                        ),
                    },
                    manual_trigger=True,
                ),
            ],
            [
                JobConfig(
                    type=JobType.bodhi_update,
                    trigger=JobConfigTriggerType.commit,
                    packages={
                        "package": CommonPackageConfig(
                            identifier="first",
                        ),
                    },
                    manual_trigger=True,
                ),
            ],
            {},
        ),
        pytest.param(
            github.issue.Comment,
            JobConfigTriggerType.release,
            [
                JobConfig(
                    type=JobType.koji_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [
                JobConfig(
                    type=JobType.koji_build,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {"issue_id": 1},
        ),
        pytest.param(
            github.issue.Comment,
            JobConfigTriggerType.release,
            [
                JobConfig(
                    type=JobType.bodhi_update,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [
                JobConfig(
                    type=JobType.bodhi_update,
                    trigger=JobConfigTriggerType.commit,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {"issue_id": 1},
        ),
        # Common re-run event
        pytest.param(
            github.check.Rerun,
            JobConfigTriggerType.pull_request,
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            identifier="first",
                        ),
                    },
                    manual_trigger=True,
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            identifier="first",
                        ),
                    },
                    manual_trigger=False,
                ),
            ],
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            identifier="first",
                        ),
                    },
                    manual_trigger=True,
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            identifier="first",
                        ),
                    },
                    manual_trigger=False,
                ),
            ],
            {"job_identifier": "first"},
        ),
        pytest.param(
            testing_farm.Result,
            JobConfigTriggerType.pull_request,
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                    manual_trigger=True,
                ),
            ],
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                    manual_trigger=True,
                ),
            ],
            {},
        ),
        pytest.param(
            vm_image.Result,
            JobConfigTriggerType.pull_request,
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                    manual_trigger=True,
                ),
            ],
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                    manual_trigger=True,
                ),
            ],
            {},
        ),
        pytest.param(
            copr.CoprBuild,
            JobConfigTriggerType.pull_request,
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                    manual_trigger=True,
                ),
            ],
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                    manual_trigger=True,
                ),
            ],
            {},
        ),
        pytest.param(
            koji.abstract.KojiEvent,
            JobConfigTriggerType.pull_request,
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                    manual_trigger=True,
                ),
            ],
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                    manual_trigger=True,
                ),
            ],
            {},
        ),
        pytest.param(
            pagure.pr.Comment,
            JobConfigTriggerType.pull_request,
            [
                JobConfig(
                    type=JobType.koji_build,
                    trigger=JobConfigTriggerType.commit,
                    sidetag_group="test",
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.koji_build,
                    trigger=JobConfigTriggerType.koji_build,
                    sidetag_group="test",
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.bodhi_update,
                    trigger=JobConfigTriggerType.koji_build,
                    sidetag_group="test",
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            [
                JobConfig(
                    type=JobType.koji_build,
                    trigger=JobConfigTriggerType.commit,
                    sidetag_group="test",
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.bodhi_update,
                    trigger=JobConfigTriggerType.koji_build,
                    sidetag_group="test",
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            {"comment": "test"},
        ),
    ],
)
def test_get_jobs_matching_trigger(
    event_kls,
    job_config_trigger_type,
    jobs,
    result,
    kwargs,
):
    class Event(event_kls):
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

        @property
        def job_config_trigger_type(self):
            return job_config_trigger_type

        @property
        def packages_config(self):
            return flexmock(get_job_views=lambda: jobs)

    event = Event(**kwargs)
    assert result == SteveJobs(event).get_jobs_matching_event()


@pytest.mark.parametrize(
    "event_kls,jobs,handler_kls,tasks_created,identifier",
    [
        (
            testing_farm.Result,
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            identifier="foo",
                        ),
                    },
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        "package": CommonPackageConfig(
                            identifier="bar",
                        ),
                    },
                ),
            ],
            TestingFarmResultsHandler,
            1,
            "foo",
        ),
        (
            testing_farm.Result,
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={"package": CommonPackageConfig()},
                ),
            ],
            TestingFarmResultsHandler,
            2,
            None,
        ),
    ],
)
def test_create_tasks_tf_identifier(
    event_kls,
    jobs,
    handler_kls,
    tasks_created,
    identifier,
):
    class Event(event_kls):
        def __init__(self):
            self._db_project_object = None

        @property
        def packages_config(self):
            return flexmock(
                jobs=jobs,
                get_package_config_for=lambda job_config: flexmock(),
            )

        def get_dict(self, *args, **kwargs):
            return {"identifier": identifier}

        @property
        def actor(self):
            return None

    event = Event()
    # Ignore remote reporting
    flexmock(
        SteveJobs,
        report_task_accepted=lambda handler_kls, job_config, update_feedback_time: None,
    )
    # We are testing the number of tasks, the exact signatures are not important
    flexmock(handler_kls).should_receive("get_signature").and_return(None)
    flexmock(TaskResults, create_from=lambda *args, **kwargs: object())
    flexmock(celery).should_receive("group").with_args(
        tasks_created * [None],
    ).and_return(flexmock().should_receive("apply_async").mock())
    statuses_check_feedback = flexmock()
    assert tasks_created == len(
        SteveJobs(event).create_tasks(jobs, handler_kls, statuses_check_feedback),
    )


def test_monorepo_jobs_matching_event():
    python_teamcity_messages = CommonPackageConfig(
        patch_generation_ignore_paths=[],
        specfile_path="python-teamcity-messages.spec",
        upstream_ref=None,
        files_to_sync=[
            {
                "dest": "python-teamcity-messages.spec",
                "mkpath": False,
                "filters": [],
                "delete": False,
                "src": ["python-teamcity-messages.spec"],
            },
            {
                "dest": ".packit.yaml",
                "mkpath": False,
                "filters": [],
                "delete": False,
                "src": [".packit.yaml"],
            },
        ],
        paths=["."],
        dist_git_base_url="https://src.fedoraproject.org/",
        upstream_package_name="teamcity-messages",
        archive_root_dir_template="{upstream_pkg_name}-{version}",
        upstream_project_url="https://github.com/majamassarini/teamcity-messages",
        config_file_path=".packit.yaml",
        patch_generation_patch_id_digits=4,
        dist_git_namespace="rpms",
        downstream_package_name="python-teamcity-messages",
        upstream_tag_template="v{version}",
    )
    python_teamcity_messages_double = copy.deepcopy(python_teamcity_messages)
    python_teamcity_messages_double.downstream_package_name = "a double"

    packages = {
        "python-teamcity-messages": copy.deepcopy(python_teamcity_messages),
        "python-teamcity-messages-double": python_teamcity_messages_double,
    }
    jobs = [
        JobConfig(
            type=JobType.copr_build,
            trigger=JobConfigTriggerType.release,
            skip_build=False,
            packages={
                "python-teamcity-messages": python_teamcity_messages,
                "python-teamcity-messages-double": python_teamcity_messages_double,
            },
        ),
        JobConfig(
            type=JobType.propose_downstream,  # makes job different from the previous one
            trigger=JobConfigTriggerType.release,
            skip_build=False,
            packages={
                "python-teamcity-messages": python_teamcity_messages,
            },
        ),
        JobConfig(
            type=JobType.propose_downstream,  # makes job different from the previous one
            trigger=JobConfigTriggerType.release,
            skip_build=False,
            packages={
                "python-teamcity-messages-double": python_teamcity_messages_double,
            },
        ),
    ]
    packages_config = PackageConfig(packages=packages, jobs=jobs)

    class Event(github.release.Release):
        def __init__(self):
            self._package_config_searched = None
            self._package_config = packages_config
            self._project = flexmock()
            self._base_project = flexmock()
            self._pr_id = flexmock()
            self._commit_sha = flexmock()
            self.fail_when_config_file_missing = True

        @property
        def job_config_trigger_type(self):
            return JobConfigTriggerType.release

    event = Event()
    steve = SteveJobs(event)
    handlers = steve.get_handlers_for_event()
    assert handlers

    original_ref = 0
    double_ref = 0
    for handler in handlers:
        job_configs = steve.get_config_for_handler_kls(handler)
        assert job_configs

        for job_config in job_configs:
            if job_config.downstream_package_name == "a double":
                double_ref += 1
            else:
                original_ref += 1

    assert original_ref == 2
    assert double_ref == 2


def test_no_handlers_for_rerun():
    result = get_handlers_for_check_rerun("whatever")
    assert not result, "There are no handlers for whatever"


def test_github_app_not_installed():
    service = flexmock(hostname="gitlab.com")
    project = flexmock(namespace="packit", repo="ogr", service=service)
    event = flexmock(project=project)
    jobs = SteveJobs(event)

    flexmock(jobs).should_receive("is_project_public_or_enabled_private").and_raise(
        GithubAppNotInstalledError,
    )

    assert not jobs.process()


def test_search_for_dg_config_in_issue_on_pr_comment():
    assert (
        SteveJobs(
            abstract.comment.PullRequest(None, None, None, None),
        ).search_distgit_config_in_issue()
        is None
    )


def test_search_for_dg_config_in_issue_no_url():
    issue = flexmock(description="Packit failed…")
    project = flexmock()
    project.should_receive("get_issue").with_args(42).and_return(issue)

    event = abstract.comment.Issue(42, None, None, None, None, None)
    flexmock(event).should_receive("project").and_return(project)

    assert SteveJobs(event).search_distgit_config_in_issue() is None


def test_invalid_packit_deployment():
    event = flexmock()
    jobs = SteveJobs(event)

    # inject service config
    jobs._service_config = flexmock(deployment="prod")

    # require ‹stg› & ‹dev› instance in the job config
    job_config = flexmock(packit_instances=["dev", "stg"])

    # handler class doesn't matter in this case
    assert not jobs.should_task_be_created_for_job_config_and_handler(job_config, None)


def test_unapproved_jobs():
    event = flexmock(project=None, packages_config=[])
    event.should_receive("get_dict").and_return(
        {"project": None, "packages_config": []},
    )
    jobs = SteveJobs(event)

    # inject service config
    jobs._service_config = flexmock()

    flexmock(jobs).should_receive("is_packit_config_present").and_return(True)
    flexmock(jobs).should_receive("get_handlers_for_event").and_return([None])
    flexmock(jobs).should_receive("get_config_for_handler_kls").and_return(
        [None, None, None],
    )
    # TODO: »do not« mock the ‹Allowlist› directly!!!
    flexmock(Allowlist).should_receive("check_and_report").and_return(False)

    results = jobs.process_jobs()
    assert results and len(results) == 3, "we have gotten exactly 3 results"
    assert all(not result["success"] for result in results), (
        "all of them must've failed the permission check"
    )
