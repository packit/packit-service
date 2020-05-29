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
import os
from pathlib import Path

import pytest
from flexmock import flexmock

from ogr.services.github import GithubProject
from packit.config import JobConfig, JobType, JobConfigTriggerType, PackageConfig
from packit_service.config import ServiceConfig, PackageConfigGetter
from packit_service.service.events import Event, TheJobTriggerType
from packit_service.worker.handlers import JobHandler
from packit_service.worker.handlers.github_handlers import AbstractCoprBuildHandler


@pytest.fixture()
def trick_p_s_with_k8s():
    os.environ["KUBERNETES_SERVICE_HOST"] = "YEAH"  # trick p-s
    yield
    del os.environ["KUBERNETES_SERVICE_HOST"]


def test_handler_cleanup(tmpdir, trick_p_s_with_k8s):
    t = Path(tmpdir)
    t.joinpath("a").mkdir()
    t.joinpath("b").write_text("a")
    t.joinpath("c").symlink_to("b")
    t.joinpath("d").symlink_to("a", target_is_directory=True)
    t.joinpath("e").symlink_to("nope", target_is_directory=False)
    t.joinpath("f").symlink_to("nopez", target_is_directory=True)
    t.joinpath(".g").write_text("g")
    t.joinpath(".h").symlink_to(".g", target_is_directory=False)

    c = ServiceConfig()
    c.command_handler_work_dir = t
    jc = JobConfig(
        type=JobType.copr_build, trigger=JobConfigTriggerType.pull_request, metadata={}
    )
    j = JobHandler(
        config=c, job_config=jc, event=Event(trigger=TheJobTriggerType.pull_request)
    )

    j._clean_workplace()

    assert len(list(t.iterdir())) == 0


def test_precheck(github_pr_event):
    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo"
    ).and_return(
        PackageConfig(
            jobs=[
                JobConfig(
                    type=JobType.copr_build, trigger=JobConfigTriggerType.pull_request,
                ),
                JobConfig(
                    type=JobType.tests, trigger=JobConfigTriggerType.pull_request,
                ),
            ]
        )
    )
    copr_build_handler = AbstractCoprBuildHandler(
        config=flexmock(),
        job_config=JobConfig(
            type=JobType.copr_build, trigger=JobConfigTriggerType.pull_request,
        ),
        event=github_pr_event,
    )
    assert copr_build_handler.pre_check()


def test_precheck_skip_tests_when_build_defined(github_pr_event):
    flexmock(PackageConfigGetter).should_receive(
        "get_package_config_from_repo"
    ).and_return(
        PackageConfig(
            jobs=[
                JobConfig(
                    type=JobType.copr_build, trigger=JobConfigTriggerType.pull_request,
                ),
                JobConfig(
                    type=JobType.tests, trigger=JobConfigTriggerType.pull_request,
                ),
            ]
        )
    )
    flexmock(GithubProject).should_receive("get_web_url").and_return(
        "https://github.com/the-namespace/the-repo"
    )
    copr_build_handler = AbstractCoprBuildHandler(
        config=flexmock(),
        job_config=JobConfig(
            type=JobType.tests, trigger=JobConfigTriggerType.pull_request,
        ),
        event=github_pr_event,
    )
    assert not copr_build_handler.pre_check()
