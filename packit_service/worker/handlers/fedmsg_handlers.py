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
from datetime import datetime
from typing import Type, Optional

from ogr.abstract import CommitStatus
from ogr.services.github import GithubProject
from packit.api import PackitAPI
from packit.config import (
    JobType,
    JobConfig,
    JobConfigTriggerType,
    PackageConfig,
)
from packit.distgit import DistGit
from packit.local_project import LocalProject
from packit.utils import get_namespace_and_repo_name
from packit_service.constants import (
    PG_COPR_BUILD_STATUS_FAILURE,
    PG_COPR_BUILD_STATUS_SUCCESS,
    COPR_API_SUCC_STATE,
    KojiBuildState,
)
from packit_service.service.events import TheJobTriggerType
from packit_service.service.urls import (
    get_copr_build_info_url_from_flask,
    get_koji_build_info_url_from_flask,
)
from packit_service.models import CoprBuildModel, KojiBuildModel, AbstractTriggerDbType
from packit_service.worker.build.copr_build import CoprBuildJobHelper
from packit_service.worker.build.koji_build import KojiBuildJobHelper
from packit_service.worker.handlers.abstract import JobHandler, use_for, required_by
from packit_service.worker.handlers.github_handlers import GithubTestingFarmHandler
from packit_service.worker.result import HandlerResults

logger = logging.getLogger(__name__)

PROCESSED_FEDMSG_TOPICS = []


def add_topic(kls: Type["FedmsgHandler"]):
    if issubclass(kls, FedmsgHandler):
        PROCESSED_FEDMSG_TOPICS.append(kls.topic)
    return kls


class FedmsgHandler(JobHandler):
    """ Handlers for events from fedmsg """

    topic: str

    def __init__(
        self, package_config: PackageConfig, job_config: JobConfig, event: dict
    ):
        super().__init__(
            package_config=package_config, job_config=job_config, event=event
        )
        self._pagure_service = None

    def run(self) -> HandlerResults:
        raise NotImplementedError("This should have been implemented.")


@add_topic
@use_for(job_type=JobType.sync_from_downstream)
class NewDistGitCommitHandler(FedmsgHandler):
    """Sync new changes to upstream after a new git push in the dist-git."""

    topic = "org.fedoraproject.prod.git.receive"
    triggers = [TheJobTriggerType.commit]

    def __init__(
        self,
        package_config: PackageConfig,
        job_config: Optional[JobConfig],
        event: dict,
    ):
        super().__init__(
            package_config=package_config, job_config=job_config, event=event,
        )
        self.branch = event.get("branch")

        # TODO check these, whether they can be created from the future task info
        # self.project = event.get_project()
        # self.package_config = get_package_config_from_repo(self.project, event.git_ref)

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
            dist_git_branch=self.branch,
            upstream_branch="master",  # TODO: this should be configurable
        )
        return HandlerResults(success=True, details={})


class CoprBuildHandler(FedmsgHandler):
    def __init__(
        self, package_config: PackageConfig, job_config: JobConfig, event: dict,
    ):
        super().__init__(
            package_config=package_config, job_config=job_config, event=event
        )
        self.project_name = event.get("project_name")
        self.owner = event.get("owner")
        self.build_id = event.get("build_id")
        self.chroot = event.get("chroot")
        self.pr_id = event.get("pr_id")
        self.timestamp = event.get("timestamp")
        self.pkg = event.get("pkg")
        self.status = event.get("status")
        self._build = None

    def get_copr_build_url(self) -> str:
        return (
            "https://copr.fedorainfracloud.org/coprs/"
            f"{self.owner}/{self.project_name}/build/{self.build_id}/"
        )

    def get_copr_build_logs_url(self) -> str:
        return (
            f"https://copr-be.cloud.fedoraproject.org/results/{self.owner}/"
            f"{self.project_name}/{self.chroot}/"
            f"{self.build_id:08d}-{self.pkg}/builder-live.log.gz"
        )

    @property
    def build(self):
        if not self._build:
            self._build = CoprBuildModel.get_by_build_id(
                str(self.build_id), self.chroot
            )
        return self._build

    @property
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        return self.build.job_trigger.get_trigger_object()


@add_topic
@use_for(job_type=JobType.copr_build)
@use_for(job_type=JobType.build)
@required_by(job_type=JobType.tests)
class CoprBuildEndHandler(CoprBuildHandler):
    topic = "org.fedoraproject.prod.copr.build.end"
    triggers = [TheJobTriggerType.copr_end]

    def was_last_packit_comment_with_congratulation(self):
        """
        Check if the last comment by the packit app
        was about successful build to not duplicate it.

        :return: bool
        """
        comments = self.project.get_pr_comments(pr_id=self.pr_id, reverse=True)
        for comment in comments:
            if comment.author.startswith("packit-as-a-service"):
                if "Congratulations!" in comment.comment:
                    return True
                return False
        # if there is no comment from p-s
        return False

    def run(self):
        build_job_helper = CoprBuildJobHelper(
            config=self.config,
            package_config=self.package_config,
            project=self.project,
            event=self.event,
            db_trigger=self.db_trigger,
            job=self.job_config,
        )

        if self.chroot == "srpm-builds":
            # we don't want to set check for this
            msg = "SRPM build in copr has finished."
            logger.debug(msg)
            return HandlerResults(success=True, details={"msg": msg})

        if not self.build:
            # TODO: how could this happen?
            msg = f"Copr build {self.build_id} not in CoprBuildDB."
            logger.warning(msg)
            return HandlerResults(success=False, details={"msg": msg})
        if self.build.status in [
            PG_COPR_BUILD_STATUS_FAILURE,
            PG_COPR_BUILD_STATUS_SUCCESS,
        ]:
            msg = (
                f"Copr build {self.build_id} is already"
                f" processed (status={self.build.status})."
            )
            logger.info(msg)
            return HandlerResults(success=True, details={"msg": msg})

        end_time = datetime.utcfromtimestamp(self.timestamp) if self.timestamp else None
        self.build.set_end_time(end_time)
        url = get_copr_build_info_url_from_flask(self.build.id)

        # https://pagure.io/copr/copr/blob/master/f/common/copr_common/enums.py#_42
        if self.status != COPR_API_SUCC_STATE:
            failed_msg = "RPMs failed to be built."
            build_job_helper.report_status_to_all_for_chroot(
                state=CommitStatus.failure,
                description=failed_msg,
                url=url,
                chroot=self.chroot,
            )
            self.build.set_status(PG_COPR_BUILD_STATUS_FAILURE)
            return HandlerResults(success=False, details={"msg": failed_msg})

        if (
            build_job_helper.job_build
            and build_job_helper.job_build.trigger == JobConfigTriggerType.pull_request
            and self.pr_id
            and isinstance(self.project, GithubProject)
            and not self.was_last_packit_comment_with_congratulation()
            and self.package_config.notifications.pull_request.successful_build
        ):
            msg = (
                f"Congratulations! One of the builds has completed. :champagne:\n\n"
                "You can install the built RPMs by following these steps:\n\n"
                "* `sudo yum install -y dnf-plugins-core` on RHEL 8\n"
                "* `sudo dnf install -y dnf-plugins-core` on Fedora\n"
                f"* `dnf copr enable {self.owner}/{self.project_name}`\n"
                "* And now you can install the packages.\n"
                "\nPlease note that the RPMs should be used only in a testing environment."
            )
            self.project.pr_comment(pr_id=self.pr_id, body=msg)

        build_job_helper.report_status_to_build_for_chroot(
            state=CommitStatus.success,
            description="RPMs were built successfully.",
            url=url,
            chroot=self.chroot,
        )
        build_job_helper.report_status_to_test_for_chroot(
            state=CommitStatus.pending,
            description="RPMs were built successfully.",
            url=url,
            chroot=self.chroot,
        )
        self.build.set_status(PG_COPR_BUILD_STATUS_SUCCESS)

        if build_job_helper.job_tests and self.chroot in build_job_helper.tests_targets:
            testing_farm_handler = GithubTestingFarmHandler(
                package_config=self.package_config,
                job_config=build_job_helper.job_tests,
                event=self.event,
                chroot=self.chroot,
                db_trigger=self.db_trigger,
            )
            testing_farm_handler.run()
        else:
            logger.debug("Testing farm not in the job config.")

        return HandlerResults(success=True, details={})


@add_topic
@use_for(job_type=JobType.copr_build)
@use_for(job_type=JobType.build)
@required_by(job_type=JobType.tests)
class CoprBuildStartHandler(CoprBuildHandler):
    topic = "org.fedoraproject.prod.copr.build.start"
    triggers = [TheJobTriggerType.copr_start]

    def run(self):
        build_job_helper = CoprBuildJobHelper(
            config=self.config,
            package_config=self.package_config,
            project=self.project,
            event=self.event,
            db_trigger=self.db_trigger,
            job=self.job_config,
        )

        if self.chroot == "srpm-builds":
            # we don't want to set the check status for this
            msg = "SRPM build in copr has started."
            logger.debug(msg)
            return HandlerResults(success=True, details={"msg": msg})

        if not self.build:
            msg = f"Copr build {self.build_id} not in CoprBuildDB."
            logger.warning(msg)
            return HandlerResults(success=False, details={"msg": msg})

        start_time = (
            datetime.utcfromtimestamp(self.timestamp) if self.timestamp else None
        )
        self.build.set_start_time(start_time)
        url = get_copr_build_info_url_from_flask(self.build.id)
        self.build.set_status("pending")
        copr_build_logs = self.get_copr_build_logs_url()
        self.build.set_build_logs_url(copr_build_logs)

        build_job_helper.report_status_to_all_for_chroot(
            description="RPM build is in progress...",
            state=CommitStatus.pending,
            url=url,
            chroot=self.chroot,
        )
        msg = f"Build on {self.chroot} in copr has started..."
        return HandlerResults(success=True, details={"msg": msg})


@add_topic
@use_for(job_type=JobType.production_build)
class KojiBuildReportHandler(FedmsgHandler):
    topic = "org.fedoraproject.prod.buildsys.task.state.change"
    triggers = [TheJobTriggerType.koji_results]

    def __init__(
        self, package_config: PackageConfig, job_config: JobConfig, event: dict,
    ):
        super().__init__(
            package_config=package_config, job_config=job_config, event=event
        )
        self.build_id = event.get("build_id")
        self.state = KojiBuildState(event.get("state")) if event.get("state") else None
        self.old_state = (
            KojiBuildState(event.get("old_state")) if event.get("old_state") else None
        )
        self.start_time = event.get("start_time")
        self.rpm_build_task_id = event.get("rpm_build_task_id")
        self._db_trigger: Optional[AbstractTriggerDbType] = None
        self._build: Optional[KojiBuildModel] = None

    @property
    def build(self) -> Optional[KojiBuildModel]:
        if not self._build:
            self._build = KojiBuildModel.get_by_build_id(build_id=str(self.build_id))
        return self._build

    @property
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        if not self._db_trigger:
            if self.build:
                self._db_trigger = self.build.job_trigger.get_trigger_object()
        return self._db_trigger

    def get_koji_build_logs_url(self) -> Optional[str]:
        if not self.rpm_build_task_id:
            return None

        return (
            f"https://kojipkgs.fedoraproject.org//work/tasks/"
            f"{self.rpm_build_task_id % 10000}/{self.rpm_build_task_id}/build.log"
        )

    def get_koji_rpm_build_web_url(self) -> Optional[str]:
        if not self.rpm_build_task_id:
            return None

        return f"https://koji.fedoraproject.org/koji/taskinfo?taskID={self.rpm_build_task_id}"

    def run(self):
        if not self.build:
            msg = f"Koji build {self.build_id} not found in the database."
            logger.warning(msg)
            return HandlerResults(success=False, details={"msg": msg})

        logger.debug(
            f"Build on {self.build.target} in koji changed state "
            f"from {self.old_state} to {self.state}."
        )

        self.build.set_build_start_time(
            datetime.utcfromtimestamp(self.start_time)
            if self.start_time
            else None
        )

        self.build.set_build_finished_time(
            datetime.utcfromtimestamp(self.completion_time)
            if self.completion_time
            else None
        )

        url = get_koji_build_info_url_from_flask(self.build.id)
        build_job_helper = KojiBuildJobHelper(
            config=self.config,
            package_config=self.package_config,
            project=self.project,
            event=self.event,
            db_trigger=self.db_trigger,
        )

        if self.state == KojiBuildState.open:
            self.build.set_status("pending")
            build_job_helper.report_status_to_all_for_chroot(
                description="RPM build is in progress...",
                state=CommitStatus.pending,
                url=url,
                chroot=self.build.target,
            )
        elif self.state == KojiBuildState.closed:
            self.build.set_status("success")
            build_job_helper.report_status_to_all_for_chroot(
                description="RPMs were built successfully.",
                state=CommitStatus.success,
                url=url,
                chroot=self.build.target,
            )
        elif self.state == KojiBuildState.failed:
            self.build.set_status("failed")
            build_job_helper.report_status_to_all_for_chroot(
                description="RPMs failed to be built.",
                state=CommitStatus.failure,
                url=url,
                chroot=self.build.target,
            )
        elif self.state == KojiBuildState.canceled:
            self.build.set_status("error")
            build_job_helper.report_status_to_all_for_chroot(
                description="RPMs build was canceled.",
                state=CommitStatus.error,
                url=url,
                chroot=self.build.target,
            )
        else:
            logger.debug(
                f"We don't react to this koji build state change: {self.state}"
            )

        koji_build_logs = self.get_koji_build_logs_url()
        self.build.set_build_logs_url(koji_build_logs)
        koji_rpm_task_web_url = self.get_koji_build_logs_url()
        self.build.set_web_url(koji_rpm_task_web_url)

        msg = (
            f"Build on {self.build.target} in koji changed state "
            f"from {self.old_state} to {self.state}."
        )
        return HandlerResults(success=True, details={"msg": msg})
