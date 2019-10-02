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
This file defines classes for job handlers specific for Fedmsg events
"""

import logging
from typing import Type, Optional

from packit.api import PackitAPI
from packit.config import (
    JobType,
    JobTriggerType,
    JobConfig,
    get_package_config_from_repo,
)
from packit.distgit import DistGit
from packit.local_project import LocalProject
from packit.utils import get_namespace_and_repo_name

from packit_service.service.events import Event, DistGitEvent, CoprBuildEvent
from packit_service.worker.copr_db import CoprBuildDB
from packit_service.worker.github_handlers import GithubTestingFarmHandler
from packit_service.worker.handler import (
    JobHandler,
    HandlerResults,
    add_to_mapping,
    BuildStatusReporter,
    PRCheckName,
)
from packit_service.config import ServiceConfig

logger = logging.getLogger(__name__)

PROCESSED_FEDMSG_TOPICS = []


def add_topic(kls: Type["FedmsgHandler"]):
    if issubclass(kls, FedmsgHandler):
        PROCESSED_FEDMSG_TOPICS.append(kls.topic)
    return kls


def do_we_process_fedmsg_topic(topic: str) -> bool:
    """ do we process selected fedmsg topic? """
    return topic in PROCESSED_FEDMSG_TOPICS


class FedmsgHandler(JobHandler):
    """ Handlers for events from fedmsg """

    topic: str

    def __init__(self, config: ServiceConfig, job: JobConfig, event: Event):
        super().__init__(config=config, job=job, event=event)
        self._pagure_service = None

    def run(self) -> HandlerResults:
        raise NotImplementedError("This should have been implemented.")


@add_topic
@add_to_mapping
class NewDistGitCommit(FedmsgHandler):
    """ A new flag was added to a dist-git pull request """

    topic = "org.fedoraproject.prod.git.receive"
    name = JobType.sync_from_downstream
    triggers = [JobTriggerType.commit]

    def __init__(
        self, config: ServiceConfig, job: JobConfig, distgit_event: DistGitEvent
    ):
        super().__init__(config=config, job=job, event=distgit_event)
        self.distgit_event = distgit_event
        self.project = distgit_event.get_project()
        self.package_config = get_package_config_from_repo(
            self.project, distgit_event.ref
        )

    def run(self) -> HandlerResults:
        # self.project is dist-git, we need to get upstream
        dg = DistGit(self.config, self.package_config)
        self.package_config.upstream_project_url = (
            dg.get_project_url_from_distgit_spec()
        )
        if not self.package_config.upstream_project_url:
            return HandlerResults(
                success=False,
                details={
                    "msg": "URL in specfile is not set. "
                    "We don't know where the upstream project lives."
                },
            )

        n, r = get_namespace_and_repo_name(self.package_config.upstream_project_url)
        up = self.project.service.get_project(repo=r, namespace=n)
        self.local_project = LocalProject(
            git_project=up, working_dir=self.config.command_handler_work_dir
        )

        self.api = PackitAPI(self.config, self.package_config, self.local_project)
        self.api.sync_from_downstream(
            # rev is a commit
            # we use branch on purpose so we get the latest thing
            # TODO: check if rev is HEAD on {branch}, warn then?
            dist_git_branch=self.distgit_event.branch,
            upstream_branch="master",  # TODO: this should be configurable
        )
        return HandlerResults(success=True, details={})


@add_topic
@add_to_mapping
class CoprBuildEndHandler(FedmsgHandler):
    topic = "org.fedoraproject.prod.copr.build.end"
    name = JobType.copr_build_finished

    def __init__(self, config: Config, job: JobConfig, event: CoprBuildEvent):
        super().__init__(config=config, job=job, event=event)
        self.project = self.event.get_project()
        self.package_config = self.event.get_package_config()

    def run(self):
        # get copr build from db
        db = CoprBuildDB()
        build = db.get_build(self.event.build_id)

        if not build:
            logger.warning(
                f"Build: {self.event.build_id} is not handled by packit service!"
            )
            return

        r = BuildStatusReporter(self.event.get_project(), build["commit_sha"])
        url = (
            "https://copr.fedorainfracloud.org/coprs/"
            f"{self.event.owner}/{self.event.project_name}/build/{self.event.build_id}/"
        )

        msg = "RPMs failed to be built."
        gh_state = "failure"

        if self.event.status == 1:

            if self.event.chroot == "srpm-builds":
                # we don't want to set check for this
                msg = "SRPM build in copr has finished"
                logger.debug(msg)
                return HandlerResults(success=True, details={"msg": msg})

            check_msg = "RPMs were built successfully."
            gh_state = "success"

            msg = (
                f"Congratulations! The build [has finished]({url})"
                " successfully. :champagne:\n\n"
                "You can install the built RPMs by following these steps:\n\n"
                "* `sudo yum install -y dnf-plugins-core` on RHEL 8\n"
                "* `sudo dnf install -y dnf-plugins-core` on Fedora\n"
                f"* `dnf copr enable {self.event.owner}/{self.event.project_name}`\n"
                "* And now you can install the packages.\n"
                "\nPlease note that the RPMs should be used only in a testing environment."
            )
            self.project.pr_comment(self.event.pr_id, msg)
            r.report(
                gh_state, check_msg, url=url, check_name=PRCheckName.get_build_check()
            )

            test_job_config = self.get_tests_for_build()
            if test_job_config:
                testing_farm_handler = GithubTestingFarmHandler(
                    self.config, test_job_config, self.event
                )
                testing_farm_handler.run()
            else:
                logger.debug("Testing farm not in the job config.")

            return HandlerResults(success=True, details={})

        r.report(gh_state, msg, url=url, check_name=PRCheckName.get_build_check())
        return HandlerResults(success=False, details={"msg": msg})

    def get_tests_for_build(self) -> Optional[JobConfig]:
        """
        Check if there are tests defined
        :return: JobConfig or None
        """
        for job in self.package_config.jobs:
            if job.job == JobType.tests:
                return job
        return None


@add_topic
@add_to_mapping
class CoprBuildStartHandler(FedmsgHandler):
    topic = "org.fedoraproject.prod.copr.build.start"
    name = JobType.copr_build_started

    def __init__(self, config: Config, job: JobConfig, event: CoprBuildEvent):
        super().__init__(config=config, job=job, event=event)
        self.project = self.event.get_project()
        self.package_config = self.event.get_package_config()

    def run(self):
        # get copr build from db
        db = CoprBuildDB()
        build = db.get_build(self.event.build_id)

        if not build:
            logger.warning(
                f"Build: {self.event.build_id} is not handled by packit service!"
            )
            return

        r = BuildStatusReporter(self.event.get_project(), build["commit_sha"])
        url = (
            "https://copr.fedorainfracloud.org/coprs/"
            f"{self.event.owner}/{self.event.project_name}/build/{self.event.build_id}/"
        )

        if self.event.chroot == "srpm-builds":
            # we don't want to set check for this
            msg = "SRPM build in copr has started"
            logger.debug(msg)
            return HandlerResults(success=True, details={"msg": msg})

        r.report(
            "pending",
            "RPM build has started...",
            url=url,
            check_name=PRCheckName.get_build_check(),
        )
