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

"""
This file defines classes for job handlers specific for Testing farm
"""

from ogr.abstract import GitProject
from packit.config import (
    JobType,
    JobTriggerType,
    PackageConfig,
    JobConfig,
    get_package_config_from_repo,
)
from packit.local_project import LocalProject

from packit_service.config import ServiceConfig
from packit_service.service.events import TestingFarmResultsEvent, TestingFarmResult
from packit_service.worker.github_handlers import AbstractGithubJobHandler
from packit_service.worker.handler import (
    add_to_mapping,
    HandlerResults,
    BuildStatusReporter,
    PRCheckName,
)


@add_to_mapping
class TestingFarmResultsHandler(AbstractGithubJobHandler):
    name = JobType.report_test_results
    triggers = [JobTriggerType.testing_farm_results]
    event: TestingFarmResultsEvent

    def __init__(
        self,
        config: ServiceConfig,
        job: JobConfig,
        test_results_event: TestingFarmResultsEvent,
    ):
        super().__init__(config=config, job=job, event=test_results_event)
        self.project: GitProject = test_results_event.get_project()
        self.package_config: PackageConfig = get_package_config_from_repo(
            self.project, test_results_event.ref
        )
        if not self.package_config:
            raise ValueError(f"No config file found in {self.project.full_repo_name}")
        self.package_config.upstream_project_url = test_results_event.project_url

    def run(self) -> HandlerResults:
        self.local_project = LocalProject(
            git_project=self.project, working_dir=self.config.command_handler_work_dir
        )

        r = BuildStatusReporter(self.project, self.event.commit_sha)
        if self.event.result == TestingFarmResult.passed:
            status = "success"
        else:
            status = "failure"

        r.report(
            status,
            self.event.message,
            None,
            self.event.log_url,
            check_names=PRCheckName.get_testing_farm_check(self.event.copr_chroot),
        )

        return HandlerResults(success=True, details={})
