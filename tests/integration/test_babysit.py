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
from copr.v3 import Client
from flexmock import flexmock

from packit.config import PackageConfig, JobConfig, JobType, JobConfigTriggerType
from packit_service.models import CoprBuildModel, JobTriggerModelType
from packit_service.service.events import AbstractCoprBuildEvent
from packit_service.worker.build.babysit import check_copr_build
from packit_service.worker.handlers import CoprBuildEndHandler


def test_check_copr_build_no_build():
    flexmock(CoprBuildModel).should_receive("get_all_by_build_id").with_args(
        1
    ).and_return([])
    assert check_copr_build(build_id=1)


def test_check_copr_build_not_ended():
    flexmock(CoprBuildModel).should_receive("get_all_by_build_id").with_args(
        1
    ).and_return([flexmock()])
    flexmock(Client).should_receive("create_from_config_file").and_return(
        flexmock(
            build_proxy=flexmock()
            .should_receive("get")
            .with_args(1)
            .and_return(flexmock(ended_on=False))
            .mock()
        )
    )
    assert not check_copr_build(build_id=1)


def test_check_copr_build_already_successful():
    flexmock(CoprBuildModel).should_receive("get_all_by_build_id").with_args(
        1
    ).and_return([flexmock(status="success")])
    flexmock(Client).should_receive("create_from_config_file").and_return(
        flexmock(
            build_proxy=flexmock()
            .should_receive("get")
            .with_args(1)
            .and_return(flexmock(ended_on="timestamp", state="completed"))
            .mock()
        )
    )
    assert check_copr_build(build_id=1)


def test_check_copr_build_updated():
    flexmock(CoprBuildModel).should_receive("get_by_build_id").and_return()
    flexmock(CoprBuildModel).should_receive("get_all_by_build_id").with_args(
        1
    ).and_return(
        [
            flexmock(
                status="pending",
                target="the-target",
                owner="the-owner",
                project_name="the-project-name",
                commit_sha="123456",
                job_trigger=flexmock(type=JobTriggerModelType.pull_request),
                srpm_build=flexmock(url=None)
                .should_receive("set_url")
                .with_args("https://some.host/my.srpm")
                .mock(),
            )
            .should_receive("get_trigger_object")
            .and_return(
                flexmock(
                    project=flexmock(
                        repo_name="repo_name",
                        namespace="the-namespace",
                        project_url="https://github.com/the-namespace/repo_name",
                    ),
                    pr_id=5,
                    job_config_trigger_type=JobConfigTriggerType.pull_request,
                    id=123,
                )
            )
            .mock()
        ]
    )
    flexmock(Client).should_receive("create_from_config_file").and_return(
        flexmock(
            build_proxy=flexmock()
            .should_receive("get")
            .with_args(1)
            .and_return(
                flexmock(
                    ended_on=True,
                    state="completed",
                    source_package={
                        "name": "source_package_name",
                        "url": "https://some.host/my.srpm",
                    },
                )
            )
            .mock(),
            build_chroot_proxy=flexmock()
            .should_receive("get")
            .with_args(1, "the-target")
            .and_return(flexmock(ended_on="timestamp", state="completed"))
            .mock(),
        )
    )
    flexmock(AbstractCoprBuildEvent).should_receive("get_package_config").and_return(
        PackageConfig(
            jobs=[
                JobConfig(type=JobType.build, trigger=JobConfigTriggerType.pull_request)
            ]
        )
    )
    flexmock(CoprBuildEndHandler).should_receive("run").and_return().once()
    assert check_copr_build(build_id=1)
