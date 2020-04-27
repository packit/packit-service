# MIT License
#
# Copyright (c) 2018-2019 Red Hat, Inc.

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
import pytest
from flexmock import flexmock

from packit.config import JobConfig, JobType, JobConfigTriggerType
from packit_service.service.events import TheJobTriggerType
from packit_service.worker.handlers import (
    PullRequestCoprBuildHandler,
    ProposeDownstreamHandler,
    CoprBuildStartHandler,
    CoprBuildEndHandler,
    TestingFarmResultsHandler,
)
from packit_service.worker.handlers.github_handlers import (
    PullRequestGithubKojiBuildHandler,
    PushGithubKojiBuildHandler,
    PushCoprBuildHandler,
    ReleaseGithubKojiBuildHandler,
)
from packit_service.worker.jobs import get_handlers_for_event


@pytest.mark.parametrize(
    "trigger,db_trigger,jobs,result",
    [
        pytest.param(
            TheJobTriggerType.pull_request,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [],
            set(),
            id="nothing_configured",
        ),
        pytest.param(
            TheJobTriggerType.pull_request,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build, trigger=JobConfigTriggerType.pull_request,
                )
            ],
            {PullRequestCoprBuildHandler},
            id="config=copr_build@trigger=pull_request",
        ),
        pytest.param(
            TheJobTriggerType.pull_request,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [JobConfig(type=JobType.build, trigger=JobConfigTriggerType.pull_request,)],
            {PullRequestCoprBuildHandler},
            id="config=build@trigger=pull_request",
        ),
        pytest.param(
            TheJobTriggerType.push,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [JobConfig(type=JobType.copr_build, trigger=JobConfigTriggerType.commit,)],
            {PushCoprBuildHandler},
            id="config=copr_build_on_push@trigger=push",
        ),
        pytest.param(
            TheJobTriggerType.commit,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [JobConfig(type=JobType.copr_build, trigger=JobConfigTriggerType.commit,)],
            {PushCoprBuildHandler},
            id="config=copr_build_on_push@trigger=commit",
        ),
        pytest.param(
            TheJobTriggerType.release,
            flexmock(job_config_trigger_type=JobConfigTriggerType.release),
            [
                JobConfig(
                    type=JobType.propose_downstream,
                    trigger=JobConfigTriggerType.release,
                )
            ],
            {ProposeDownstreamHandler},
            id="propose_downstream",
        ),
        pytest.param(
            TheJobTriggerType.copr_start,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build, trigger=JobConfigTriggerType.pull_request,
                )
            ],
            {CoprBuildStartHandler},
            id="config=copr_build@trigger=copr_start",
        ),
        pytest.param(
            TheJobTriggerType.copr_end,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build, trigger=JobConfigTriggerType.pull_request,
                )
            ],
            {CoprBuildEndHandler},
            id="config=copr_build@trigger=copr_end",
        ),
        pytest.param(
            TheJobTriggerType.pull_request,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build, trigger=JobConfigTriggerType.pull_request,
                ),
                JobConfig(
                    type=JobType.copr_build, trigger=JobConfigTriggerType.release,
                ),
            ],
            {PullRequestCoprBuildHandler},
            id="config=copr_build_on_pull_request_and_release@trigger=pull_request",
        ),
        pytest.param(
            TheJobTriggerType.copr_end,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.copr_build, trigger=JobConfigTriggerType.pull_request,
                ),
                JobConfig(
                    type=JobType.copr_build, trigger=JobConfigTriggerType.release,
                ),
            ],
            {CoprBuildEndHandler},
            id="config=copr_build_on_pull_request_and_release@trigger=copr_end",
        ),
        pytest.param(
            TheJobTriggerType.pull_request,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [JobConfig(type=JobType.tests, trigger=JobConfigTriggerType.pull_request,)],
            {PullRequestCoprBuildHandler},
            id="config=tests@trigger=pull_request",
        ),
        pytest.param(
            TheJobTriggerType.copr_start,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [JobConfig(type=JobType.tests, trigger=JobConfigTriggerType.pull_request,)],
            {CoprBuildStartHandler},
            id="config=tests@trigger=copr_start",
        ),
        pytest.param(
            TheJobTriggerType.copr_end,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [JobConfig(type=JobType.tests, trigger=JobConfigTriggerType.pull_request,)],
            {CoprBuildEndHandler},
            id="config=tests@trigger=copr_end",
        ),
        pytest.param(
            TheJobTriggerType.testing_farm_results,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [JobConfig(type=JobType.tests, trigger=JobConfigTriggerType.pull_request,)],
            {TestingFarmResultsHandler},
            id="config=tests@trigger=testing_farm_results",
        ),
        pytest.param(
            TheJobTriggerType.testing_farm_results,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.tests, trigger=JobConfigTriggerType.pull_request,
                ),
                JobConfig(
                    type=JobType.copr_build, trigger=JobConfigTriggerType.pull_request,
                ),
            ],
            {TestingFarmResultsHandler},
            id="config=tests_and_copr_build@trigger=testing_farm_results",
        ),
        pytest.param(
            TheJobTriggerType.pull_request,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.tests, trigger=JobConfigTriggerType.pull_request,
                ),
                JobConfig(
                    type=JobType.copr_build, trigger=JobConfigTriggerType.pull_request,
                ),
            ],
            {PullRequestCoprBuildHandler},
            id="config=tests_and_copr_build@trigger=pull_request",
        ),
        pytest.param(
            TheJobTriggerType.copr_start,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.tests, trigger=JobConfigTriggerType.pull_request,
                ),
                JobConfig(
                    type=JobType.copr_build, trigger=JobConfigTriggerType.pull_request,
                ),
            ],
            {CoprBuildStartHandler},
            id="config=tests_and_copr_build@trigger=copr_start",
        ),
        pytest.param(
            TheJobTriggerType.copr_start,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.tests, trigger=JobConfigTriggerType.pull_request,
                ),
                JobConfig(
                    type=JobType.copr_build, trigger=JobConfigTriggerType.pull_request,
                ),
                JobConfig(
                    type=JobType.propose_downstream,
                    trigger=JobConfigTriggerType.release,
                ),
                JobConfig(
                    type=JobType.sync_from_downstream,
                    trigger=JobConfigTriggerType.commit,
                ),
            ],
            {CoprBuildStartHandler},
            id="config=tests_and_copr_build_and_propose_and_sync@trigger=copr_start",
        ),
        pytest.param(
            TheJobTriggerType.pull_request,
            flexmock(job_config_trigger_type=JobConfigTriggerType.pull_request),
            [
                JobConfig(
                    type=JobType.production_build,
                    trigger=JobConfigTriggerType.pull_request,
                ),
            ],
            {PullRequestGithubKojiBuildHandler},
            id="config=production_build@trigger=pull_request",
        ),
        pytest.param(
            TheJobTriggerType.push,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.production_build, trigger=JobConfigTriggerType.commit,
                ),
            ],
            {PushGithubKojiBuildHandler},
            id="config=production_build@trigger=push",
        ),
        pytest.param(
            TheJobTriggerType.commit,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.production_build, trigger=JobConfigTriggerType.commit,
                ),
            ],
            {PushGithubKojiBuildHandler},
            id="config=production_build@trigger=commit",
        ),
        pytest.param(
            TheJobTriggerType.release,
            flexmock(job_config_trigger_type=JobConfigTriggerType.release),
            [
                JobConfig(
                    type=JobType.production_build, trigger=JobConfigTriggerType.release,
                ),
            ],
            {ReleaseGithubKojiBuildHandler},
            id="config=production_build@trigger=release",
        ),
        pytest.param(
            TheJobTriggerType.push,
            flexmock(job_config_trigger_type=JobConfigTriggerType.commit),
            [
                JobConfig(
                    type=JobType.production_build, trigger=JobConfigTriggerType.commit,
                ),
                JobConfig(
                    type=JobType.production_build,
                    trigger=JobConfigTriggerType.pull_request,
                ),
            ],
            {PushGithubKojiBuildHandler},
            id="config=production_build_on_pull_request_and_commit@trigger=commit",
        ),
    ],
)
def test_get_handlers_for_event(trigger, db_trigger, jobs, result):
    event_handlers = set(
        get_handlers_for_event(
            event=flexmock(trigger=trigger, db_trigger=db_trigger),
            package_config=flexmock(jobs=jobs),
        )
    )
    assert event_handlers == result
