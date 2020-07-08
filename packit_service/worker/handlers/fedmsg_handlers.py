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

from celery import signature

from ogr.abstract import CommitStatus
from ogr.services.github import GithubProject
from packit.api import PackitAPI
from packit.config import (
    JobType,
    JobConfig,
    JobConfigTriggerType,
)
from packit.config.package_config import PackageConfig
from packit.distgit import DistGit
from packit.local_project import LocalProject
from packit.utils import get_namespace_and_repo_name

from packit_service.constants import (
    PG_COPR_BUILD_STATUS_FAILURE,
    PG_COPR_BUILD_STATUS_SUCCESS,
    COPR_API_SUCC_STATE,
    KojiBuildState,
)
from packit_service.models import CoprBuildModel, KojiBuildModel, AbstractTriggerDbType
from packit_service.service.events import (
    TheJobTriggerType,
    CoprBuildEvent,
    KojiBuildEvent,
    EventData,
)
from packit_service.service.urls import (
    get_copr_build_info_url_from_flask,
    get_koji_build_info_url_from_flask,
)
from packit_service.worker.build.copr_build import CoprBuildJobHelper
from packit_service.worker.build.koji_build import KojiBuildJobHelper
from packit_service.worker.handlers.abstract import (
    JobHandler,
    use_for,
    required_by,
    TaskName,
)
from packit_service.worker.result import TaskResults
from packit_service.utils import dump_package_config, dump_job_config

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
        self, package_config: PackageConfig, job_config: JobConfig, data: EventData,
    ):
        super().__init__(
            package_config=package_config, job_config=job_config, data=data,
        )
        self._pagure_service = None

    def run(self) -> TaskResults:
        raise NotImplementedError("This should have been implemented.")


@add_topic
@use_for(job_type=JobType.sync_from_downstream)
class NewDistGitCommitHandler(FedmsgHandler):
    """Sync new changes to upstream after a new git push in the dist-git."""

    topic = "org.fedoraproject.prod.git.receive"
    triggers = [TheJobTriggerType.commit]
    task_name = TaskName.distgit_commit

    def __init__(
        self, package_config: PackageConfig, job_config: JobConfig, data: EventData,
    ):
        super().__init__(
            package_config=package_config, job_config=job_config, data=data,
        )
        self.branch = data.event_dict.get("branch")

    def run(self) -> TaskResults:
        # self.project is dist-git, we need to get upstream
        dg = DistGit(self.service_config, self.job_config)
        self.job_config.upstream_project_url = dg.get_project_url_from_distgit_spec()
        if not self.job_config.upstream_project_url:
            return TaskResults(
                success=False,
                details={
                    "msg": "URL in specfile is not set. "
                    "We don't know where the upstream project lives."
                },
            )

        n, r = get_namespace_and_repo_name(self.job_config.upstream_project_url)
        up = self.project.service.get_project(repo=r, namespace=n)
        self.local_project = LocalProject(
            git_project=up, working_dir=self.service_config.command_handler_work_dir
        )

        self.api = PackitAPI(self.service_config, self.job_config, self.local_project)
        self.api.sync_from_downstream(
            # rev is a commit
            # we use branch on purpose so we get the latest thing
            # TODO: check if rev is HEAD on {branch}, warn then?
            dist_git_branch=self.branch,
            upstream_branch="master",  # TODO: this should be configurable
        )
        return TaskResults(success=True, details={})


class AbstractCoprBuildReportHandler(FedmsgHandler):
    def __init__(
        self,
        package_config: PackageConfig,
        job_config: JobConfig,
        data: EventData,
        copr_event: CoprBuildEvent,
    ):
        super().__init__(
            package_config=package_config, job_config=job_config, data=data,
        )
        self.copr_event = copr_event
        self._build = None
        self._db_trigger = None

    @property
    def build(self):
        if not self._build:
            self._build = CoprBuildModel.get_by_build_id(
                str(self.copr_event.build_id), self.copr_event.chroot
            )
        return self._build

    @property
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        if not self._db_trigger:
            self._db_trigger = self.build.job_trigger.get_trigger_object()
        return self._db_trigger


@add_topic
@use_for(job_type=JobType.copr_build)
@use_for(job_type=JobType.build)
@required_by(job_type=JobType.tests)
class CoprBuildEndHandler(AbstractCoprBuildReportHandler):
    topic = "org.fedoraproject.prod.copr.build.end"
    triggers = [TheJobTriggerType.copr_end]
    task_name = TaskName.copr_build_end

    def was_last_packit_comment_with_congratulation(self):
        """
        Check if the last comment by the packit app
        was about successful build to not duplicate it.

        :return: bool
        """
        comments = self.project.get_pr_comments(
            pr_id=self.copr_event.pr_id, reverse=True
        )
        for comment in comments:
            if comment.author.startswith("packit-as-a-service"):
                return "Congratulations!" in comment.comment
        # if there is no comment from p-s
        return False

    def run(self):
        build_job_helper = CoprBuildJobHelper(
            service_config=self.service_config,
            package_config=self.package_config,
            project=self.project,
            metadata=self.data,
            db_trigger=self.db_trigger,
            job_config=self.job_config,
        )

        if self.copr_event.chroot == "srpm-builds":
            # we don't want to set check for this
            msg = "SRPM build in copr has finished."
            logger.debug(msg)
            return TaskResults(success=True, details={"msg": msg})

        if not self.build:
            # TODO: how could this happen?
            msg = f"Copr build {self.copr_event.build_id} not in CoprBuildDB."
            logger.warning(msg)
            return TaskResults(success=False, details={"msg": msg})
        if self.build.status in [
            PG_COPR_BUILD_STATUS_FAILURE,
            PG_COPR_BUILD_STATUS_SUCCESS,
        ]:
            msg = (
                f"Copr build {self.copr_event.build_id} is already"
                f" processed (status={self.copr_event.build.status})."
            )
            logger.info(msg)
            return TaskResults(success=True, details={"msg": msg})

        end_time = (
            datetime.utcfromtimestamp(self.copr_event.timestamp)
            if self.copr_event.timestamp
            else None
        )
        self.build.set_end_time(end_time)
        url = get_copr_build_info_url_from_flask(self.build.id)

        # https://pagure.io/copr/copr/blob/master/f/common/copr_common/enums.py#_42
        if self.copr_event.status != COPR_API_SUCC_STATE:
            failed_msg = "RPMs failed to be built."
            build_job_helper.report_status_to_all_for_chroot(
                state=CommitStatus.failure,
                description=failed_msg,
                url=url,
                chroot=self.copr_event.chroot,
            )
            self.build.set_status(PG_COPR_BUILD_STATUS_FAILURE)
            return TaskResults(success=False, details={"msg": failed_msg})

        if (
            build_job_helper.job_build
            and build_job_helper.job_build.trigger == JobConfigTriggerType.pull_request
            and self.copr_event.pr_id
            and isinstance(self.project, GithubProject)
            and not self.was_last_packit_comment_with_congratulation()
            and self.job_config.notifications.pull_request.successful_build
        ):
            msg = (
                f"Congratulations! One of the builds has completed. :champagne:\n\n"
                "You can install the built RPMs by following these steps:\n\n"
                "* `sudo yum install -y dnf-plugins-core` on RHEL 8\n"
                "* `sudo dnf install -y dnf-plugins-core` on Fedora\n"
                f"* `dnf copr enable {self.copr_event.owner}/{self.copr_event.project_name}`\n"
                "* And now you can install the packages.\n"
                "\nPlease note that the RPMs should be used only in a testing environment."
            )
            self.project.pr_comment(pr_id=self.copr_event.pr_id, body=msg)

        build_job_helper.report_status_to_build_for_chroot(
            state=CommitStatus.success,
            description="RPMs were built successfully.",
            url=url,
            chroot=self.copr_event.chroot,
        )
        build_job_helper.report_status_to_test_for_chroot(
            state=CommitStatus.pending,
            description="RPMs were built successfully.",
            url=url,
            chroot=self.copr_event.chroot,
        )
        self.build.set_status(PG_COPR_BUILD_STATUS_SUCCESS)

        if (
            build_job_helper.job_tests
            and self.copr_event.chroot in build_job_helper.tests_targets
        ):
            signature(
                TaskName.testing_farm.value,
                kwargs={
                    "package_config": dump_package_config(self.package_config),
                    "job_config": dump_job_config(build_job_helper.job_tests),
                    "event": self.data.get_dict(),
                    "chroot": self.copr_event.chroot,
                    "build_id": self.build.id,
                },
            ).apply_async()
        else:
            logger.debug("Testing farm not in the job config.")

        return TaskResults(success=True, details={})


@add_topic
@use_for(job_type=JobType.copr_build)
@use_for(job_type=JobType.build)
@required_by(job_type=JobType.tests)
class CoprBuildStartHandler(AbstractCoprBuildReportHandler):
    topic = "org.fedoraproject.prod.copr.build.start"
    triggers = [TheJobTriggerType.copr_start]
    task_name = TaskName.copr_build_start

    def run(self):
        build_job_helper = CoprBuildJobHelper(
            service_config=self.service_config,
            package_config=self.package_config,
            project=self.project,
            metadata=self.data,
            db_trigger=self.db_trigger,
            job_config=self.job_config,
        )

        if self.copr_event.chroot == "srpm-builds":
            # we don't want to set the check status for this
            msg = "SRPM build in copr has started."
            logger.debug(msg)
            return TaskResults(success=True, details={"msg": msg})

        if not self.build:
            msg = f"Copr build {self.copr_event.build_id} not in CoprBuildDB."
            logger.warning(msg)
            return TaskResults(success=False, details={"msg": msg})

        start_time = (
            datetime.utcfromtimestamp(self.copr_event.timestamp)
            if self.copr_event.timestamp
            else None
        )
        self.build.set_start_time(start_time)
        url = get_copr_build_info_url_from_flask(self.build.id)
        self.build.set_status("pending")
        copr_build_logs = self.copr_event.get_copr_build_logs_url()
        self.build.set_build_logs_url(copr_build_logs)

        build_job_helper.report_status_to_all_for_chroot(
            description="RPM build is in progress...",
            state=CommitStatus.pending,
            url=url,
            chroot=self.copr_event.chroot,
        )
        msg = f"Build on {self.copr_event.chroot} in copr has started..."
        return TaskResults(success=True, details={"msg": msg})


@add_topic
@use_for(job_type=JobType.production_build)
class KojiBuildReportHandler(FedmsgHandler):
    topic = "org.fedoraproject.prod.buildsys.task.state.change"
    triggers = [TheJobTriggerType.koji_results]
    task_name = TaskName.koji_build_report

    def __init__(
        self,
        package_config: PackageConfig,
        job_config: JobConfig,
        data: EventData,
        koji_event: KojiBuildEvent,
    ):
        super().__init__(
            package_config=package_config, job_config=job_config, data=data,
        )
        self.koji_event = koji_event
        self._db_trigger: Optional[AbstractTriggerDbType] = None
        self._build: Optional[KojiBuildModel] = None

    @property
    def build(self) -> Optional[KojiBuildModel]:
        if not self._build:
            self._build = KojiBuildModel.get_by_build_id(
                build_id=str(self.koji_event.build_id)
            )
        return self._build

    @property
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        if not self._db_trigger and self.build:
            self._db_trigger = self.build.job_trigger.get_trigger_object()
        return self._db_trigger

    def run(self):
        build = KojiBuildModel.get_by_build_id(build_id=str(self.koji_event.build_id))

        if not build:
            msg = f"Koji build {self.koji_event.build_id} not found in the database."
            logger.warning(msg)
            return TaskResults(success=False, details={"msg": msg})

        logger.debug(
            f"Build on {build.target} in koji changed state "
            f"from {self.koji_event.old_state} to {self.koji_event.state}."
        )

        build.set_build_start_time(
            datetime.utcfromtimestamp(self.koji_event.start_time)
            if self.koji_event.start_time
            else None
        )

        build.set_build_finished_time(
            datetime.utcfromtimestamp(self.koji_event.completion_time)
            if self.koji_event.completion_time
            else None
        )

        url = get_koji_build_info_url_from_flask(build.id)
        build_job_helper = KojiBuildJobHelper(
            service_config=self.service_config,
            package_config=self.package_config,
            project=self.project,
            metadata=self.data,
            db_trigger=self.db_trigger,
            job_config=self.job_config,
        )

        if self.koji_event.state == KojiBuildState.open:
            build.set_status("pending")
            build_job_helper.report_status_to_all_for_chroot(
                description="RPM build is in progress...",
                state=CommitStatus.pending,
                url=url,
                chroot=build.target,
            )
        elif self.koji_event.state == KojiBuildState.closed:
            build.set_status("success")
            build_job_helper.report_status_to_all_for_chroot(
                description="RPMs were built successfully.",
                state=CommitStatus.success,
                url=url,
                chroot=build.target,
            )
        elif self.koji_event.state == KojiBuildState.failed:
            build.set_status("failed")
            build_job_helper.report_status_to_all_for_chroot(
                description="RPMs failed to be built.",
                state=CommitStatus.failure,
                url=url,
                chroot=build.target,
            )
        elif self.koji_event.state == KojiBuildState.canceled:
            build.set_status("error")
            build_job_helper.report_status_to_all_for_chroot(
                description="RPMs build was canceled.",
                state=CommitStatus.error,
                url=url,
                chroot=build.target,
            )
        else:
            logger.debug(
                f"We don't react to this koji build state change: {self.koji_event.state}"
            )

        koji_build_logs = self.koji_event.get_koji_build_logs_url()
        build.set_build_logs_url(koji_build_logs)
        koji_rpm_task_web_url = self.koji_event.get_koji_build_logs_url()
        build.set_web_url(koji_rpm_task_web_url)

        msg = (
            f"Build on {build.target} in koji changed state "
            f"from {self.koji_event.old_state} to {self.koji_event.state}."
        )
        return TaskResults(success=True, details={"msg": msg})
