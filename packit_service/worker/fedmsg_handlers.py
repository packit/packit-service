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
from typing import Type

import requests
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

from packit_service.config import ServiceConfig
from packit_service.service.events import Event, DistGitEvent, CoprBuildEvent
from packit_service.worker.copr_build import CoprBuildJobHelper
from packit_service.worker.copr_db import CoprBuildDB
from packit_service.worker.github_handlers import GithubTestingFarmHandler
from packit_service.worker.handler import (
    JobHandler,
    HandlerResults,
    add_to_mapping,
    PRCheckName,
)

logger = logging.getLogger(__name__)

PROCESSED_FEDMSG_TOPICS = []


def add_topic(kls: Type["FedmsgHandler"]):
    if issubclass(kls, FedmsgHandler):
        PROCESSED_FEDMSG_TOPICS.append(kls.topic)
    return kls


def do_we_process_fedmsg_topic(topic: str) -> bool:
    """ do we process selected fedmsg topic? """
    return topic in PROCESSED_FEDMSG_TOPICS


def get_copr_build_url(event: CoprBuildEvent) -> str:
    return (
        "https://copr.fedorainfracloud.org/coprs/"
        f"{event.owner}/{event.project_name}/build/{event.build_id}/"
    )


def copr_url_from_event(event: CoprBuildEvent):
    """
    Get url to builder-live.log.gz bound to single event
    :param event: fedora messaging event from topic copr.build.start or copr.build.end
    :return: reachable url
    """
    url = (
        f"https://copr-be.cloud.fedoraproject.org/results/{event.owner}/"
        f"{event.project_name}/{event.chroot}/"
        f"{event.build_id:08d}-{event.pkg}/builder-live.log.gz"
    )
    # make sure we provide valid url in status, let sentry handle if not
    try:
        logger.debug(f"Reaching url {url}")
        r = requests.head(url)
        r.raise_for_status()
    except requests.RequestException:
        # we might want sentry to know but don't want to start handling things?
        logger.error(f"Failed to reach url with copr chroot build result.")
        url = (
            "https://copr.fedorainfracloud.org/coprs/"
            f"{event.owner}/{event.project_name}/build/{event.build_id}/"
        )
    # return the frontend URL no matter what
    # we don't want to fail on this step; the error log is just enough
    return url


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
        if not self.package_config:
            raise ValueError(f"No config file found in {self.project.full_repo_name}")

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

    def __init__(self, config: ServiceConfig, job: JobConfig, event: CoprBuildEvent):
        super().__init__(config=config, job=job, event=event)
        self.project = self.event.get_project()
        self.package_config = self.event.get_package_config()
        self.build_job_helper = CoprBuildJobHelper(
            config=self.config,
            package_config=self.package_config,
            project=self.project,
            event=event,
        )

    def was_last_build_successful(self):
        """
        Check if the last copr build of the PR was successful
        :return: bool
        """
        comments = self.project.get_pr_comments(pr_id=self.event.pr_id, reverse=True)
        for comment in comments:
            if comment.author.startswith("packit-as-a-service"):
                if "Congratulations!" in comment.comment:
                    return True
                return False
        # if there is no comment from p-s
        return False

    def run(self):
        build = CoprBuildDB().get_build(self.event.build_id)
        if not build:
            # TODO: how could this happen?
            msg = f"Copr build {self.event.build_id} not in CoprBuildDB"
            logger.warning(msg)
            return HandlerResults(success=False, details={"msg": msg})

        url = copr_url_from_event(self.event)
        check_name = PRCheckName.get_build_check(self.event.chroot)

        # https://pagure.io/copr/copr/blob/master/f/common/copr_common/enums.py#_42
        if self.event.status == 1:

            if self.event.chroot == "srpm-builds":
                # we don't want to set check for this
                msg = "SRPM build in copr has finished"
                logger.debug(msg)
                return HandlerResults(success=True, details={"msg": msg})

            if (
                self.build_job_helper.job_copr_build
                and not self.was_last_build_successful()
            ):
                msg = (
                    f"Congratulations! One of the builds has completed. :champagne:\n\n"
                    "You can install the built RPMs by following these steps:\n\n"
                    "* `sudo yum install -y dnf-plugins-core` on RHEL 8\n"
                    "* `sudo dnf install -y dnf-plugins-core` on Fedora\n"
                    f"* `dnf copr enable {self.event.owner}/{self.event.project_name}`\n"
                    "* And now you can install the packages.\n"
                    "\nPlease note that the RPMs should be used only in a testing environment."
                )
                self.project.pr_comment(pr_id=self.event.pr_id, body=msg)

            self.build_job_helper.status_reporter.report(
                state="success",
                description="RPMs were built successfully.",
                url=url,
                check_names=check_name,
            )

            if (
                self.build_job_helper.job_tests
                and self.event.chroot in self.build_job_helper.tests_chroots
            ):
                testing_farm_handler = GithubTestingFarmHandler(
                    config=self.config,
                    job=self.build_job_helper.job_tests,
                    event=self.event,
                    chroot=self.event.chroot,
                )
                testing_farm_handler.run()
            else:
                logger.debug("Testing farm not in the job config.")

            return HandlerResults(success=True, details={})

        failed_msg = "RPMs failed to be built."
        self.build_job_helper.status_reporter(
            state="failure", description=failed_msg, url=url, check_names=check_name,
        )
        return HandlerResults(success=False, details={"msg": failed_msg})


@add_topic
@add_to_mapping
class CoprBuildStartHandler(FedmsgHandler):
    topic = "org.fedoraproject.prod.copr.build.start"
    name = JobType.copr_build_started

    def __init__(self, config: ServiceConfig, job: JobConfig, event: CoprBuildEvent):
        super().__init__(config=config, job=job, event=event)
        self.project = self.event.get_project()
        self.package_config = self.event.get_package_config()
        self.build_job_helper = CoprBuildJobHelper(
            config=self.config,
            package_config=self.package_config,
            project=self.project,
            event=event,
        )

    def run(self):
        build = CoprBuildDB().get_build(self.event.build_id)
        if not build:
            # TODO: how could this happen?
            msg = f"Copr build {self.event.build_id} not in CoprBuildDB"
            logger.warning(msg)
            return HandlerResults(success=False, details={"msg": msg})

        if self.event.chroot == "srpm-builds":
            # we don't want to set check for this
            msg = "SRPM build in copr has started"
            logger.debug(msg)
            return HandlerResults(success=True, details={"msg": msg})

        self.build_job_helper.status_reporter(
            state="pending",
            description="RPM build has started...",
            url=copr_url_from_event(self.event),
            check_names=PRCheckName.get_build_check(self.event.chroot),
        )
        msg = f"Build on {self.event.chroot} in copr has started..."
        return HandlerResults(success=True, details={"msg": msg})
