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
    PullRequestGithubCoprBuildHandler,
    ProposeDownstreamHandler,
    CoprBuildStartHandler,
    CoprBuildEndHandler,
)
from packit_service.worker.jobs import get_handlers_for_event


@pytest.mark.parametrize(
    "trigger,jobs,result",
    [
        pytest.param(
            TheJobTriggerType.pull_request, [], set(), id="nothing_configured"
        ),
        pytest.param(
            TheJobTriggerType.pull_request,
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata={},
                )
            ],
            {PullRequestGithubCoprBuildHandler},
            id="config=copr_build@trigger=pull_request",
        ),
        pytest.param(
            TheJobTriggerType.pull_request,
            [
                JobConfig(
                    type=JobType.build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata={},
                )
            ],
            {PullRequestGithubCoprBuildHandler},
            id="config=build@trigger=pull_request",
        ),
        pytest.param(
            TheJobTriggerType.release,
            [
                JobConfig(
                    type=JobType.propose_downstream,
                    trigger=JobConfigTriggerType.release,
                    metadata={},
                )
            ],
            {ProposeDownstreamHandler},
            id="propose_downstream",
        ),
        pytest.param(
            TheJobTriggerType.copr_start,
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata={},
                )
            ],
            {CoprBuildStartHandler},
            id="config=copr_build@trigger=copr_start",
        ),
        pytest.param(
            TheJobTriggerType.copr_end,
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata={},
                )
            ],
            {CoprBuildEndHandler},
            id="config=copr_build@trigger=copr_end",
        ),
        pytest.param(
            TheJobTriggerType.pull_request,
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata={},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    metadata={},
                ),
            ],
            {PullRequestGithubCoprBuildHandler},
            id="config=copr_build_on_pull_request_and_release@trigger=pull_request",
        ),
        pytest.param(
            TheJobTriggerType.copr_end,
            [
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata={},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.release,
                    metadata={},
                ),
            ],
            {CoprBuildEndHandler},
            id="config=copr_build_on_pull_request_and_release@trigger=copr_end",
        ),
        pytest.param(
            TheJobTriggerType.pull_request,
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata={},
                )
            ],
            {PullRequestGithubCoprBuildHandler},
            id="config=tests@trigger=pull_request",
        ),
        pytest.param(
            TheJobTriggerType.copr_start,
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata={},
                )
            ],
            {CoprBuildStartHandler},
            id="config=tests@trigger=copr_start",
        ),
        pytest.param(
            TheJobTriggerType.copr_end,
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata={},
                )
            ],
            {CoprBuildEndHandler},
            id="config=tests@trigger=copr_end",
        ),
        pytest.param(
            TheJobTriggerType.pull_request,
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata={},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata={},
                ),
            ],
            {PullRequestGithubCoprBuildHandler},
            id="config=tests_and_copr_build@trigger=pull_request",
        ),
        pytest.param(
            TheJobTriggerType.copr_start,
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata={},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata={},
                ),
            ],
            {CoprBuildStartHandler},
            id="config=tests_and_copr_build@trigger=copr_start",
        ),
        pytest.param(
            TheJobTriggerType.copr_start,
            [
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata={},
                ),
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata={},
                ),
                JobConfig(
                    type=JobType.propose_downstream,
                    trigger=JobConfigTriggerType.release,
                    metadata={},
                ),
                JobConfig(
                    type=JobType.sync_from_downstream,
                    trigger=JobConfigTriggerType.commit,
                    metadata={},
                ),
            ],
            {CoprBuildStartHandler},
            id="config=tests_and_copr_build_and_propose_and_sync@trigger=copr_start",
        ),
    ],
)
def test_get_handlers_for_event(trigger, jobs, result):
    assert (
        set(
            get_handlers_for_event(
                event=flexmock(trigger=trigger), package_config=flexmock(jobs=jobs)
            )
        )
        == result
    )
