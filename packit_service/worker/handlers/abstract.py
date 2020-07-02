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
This file defines generic job handler
"""
import logging
import shutil
from collections import defaultdict
from os import getenv
from pathlib import Path
from typing import Dict, Optional, Type, List, Set

from ogr.abstract import GitProject
from packit.api import PackitAPI
from packit.config import JobConfig, JobType
from packit.config.package_config import PackageConfig
from packit.local_project import LocalProject

from packit_service.config import ServiceConfig
from packit_service.models import (
    AbstractTriggerDbType,
    PullRequestModel,
    IssueModel,
    ProjectReleaseModel,
    GitBranchModel,
)
from packit_service.sentry_integration import push_scope_to_sentry
from packit_service.service.events import TheJobTriggerType, EventData
from packit_service.worker.result import HandlerResults

logger = logging.getLogger(__name__)

MAP_REQUIRED_JOB_TO_HANDLERS: Dict[JobType, Set[Type["JobHandler"]]] = defaultdict(set)

MAP_EVENT_TRIGGER_TO_HANDLERS: Dict[
    TheJobTriggerType, Set[Type["JobHandler"]]
] = defaultdict(set)

MAP_HANDLER_TO_JOB_TYPES: Dict[Type["Handler"], Set[JobType]] = defaultdict(set)


def required_by(job_type: JobType):
    """
    [class decorator]
    Set when you need to run for some job even if this one is not configured.

    (e.g. we want to run build for test even if only the test is defined)
    """

    def _add_to_mapping(kls: Type["JobHandler"]):
        MAP_REQUIRED_JOB_TO_HANDLERS[job_type].add(kls)
        return kls

    return _add_to_mapping


def use_for(job_type: JobType):
    """
    [class decorator]
    Specify a job type for which we want to use this handler.
    """

    def _add_to_mapping(kls: Type["JobHandler"]):
        for trigger in kls.triggers:
            MAP_EVENT_TRIGGER_TO_HANDLERS[trigger].add(kls)
        MAP_HANDLER_TO_JOB_TYPES[kls].add(job_type)
        return kls

    return _add_to_mapping


class Handler:
    triggers: List[TheJobTriggerType]
    api: Optional[PackitAPI] = None
    local_project: Optional[LocalProject] = None
    _service_config: Optional[ServiceConfig] = None

    @property
    def service_config(self) -> ServiceConfig:
        if not self._service_config:
            self._service_config = ServiceConfig.get_service_config()
        return self._service_config

    def run(self) -> HandlerResults:
        raise NotImplementedError("This should have been implemented.")

    def get_tag_info(self) -> dict:
        tags = {"handler": getattr(self, "name", "generic-handler")}
        # repository info for easier filtering events that were grouped based on event type
        if self.local_project:
            tags.update(
                {
                    "repository": self.local_project.repo_name,
                    "namespace": self.local_project.namespace,
                }
            )
        return tags

    def run_n_clean(self) -> HandlerResults:
        try:
            with push_scope_to_sentry() as scope:
                for k, v in self.get_tag_info().items():
                    scope.set_tag(k, v)
                return self.run()
        finally:
            self.clean()

    def _clean_workplace(self):
        # clean only when we are in k8s for sure
        if not getenv("KUBERNETES_SERVICE_HOST"):
            logger.debug("This is not a kubernetes pod, won't clean.")
            return
        logger.debug("Removing contents of the PV.")
        p = Path(self.service_config.command_handler_work_dir)
        # Do not clean dir if does not exist
        if not p.is_dir():
            logger.debug(
                f"Directory {self.service_config.command_handler_work_dir!r} does not exist."
            )
            return

        # remove everything in the volume, but not the volume dir
        dir_items = list(p.iterdir())
        if dir_items:
            logger.info("Volume is not empty.")
            logger.debug(f"Content: {[g.name for g in dir_items]}")
        for item in dir_items:
            # symlink pointing to a dir is also a dir and a symlink
            if item.is_symlink() or item.is_file():
                item.unlink()
            else:
                shutil.rmtree(item)

    def pre_check(self) -> bool:
        """
        Implement this method for those handlers, where you want to check if the properties are
        correct. If this method returns False during runtime, execution of service code is skipped.

        :return: False if we can skip the job execution.
        """
        return True

    def clean(self):
        """ clean up the mess once we're done """
        logger.info("Cleaning up the mess.")
        if self.api:
            self.api.clean()
        self._clean_workplace()


class JobHandler(Handler):
    """ Generic interface to handle different type of inputs """

    type: JobType
    triggers: List[TheJobTriggerType]

    def __init__(
        self, package_config: PackageConfig, job_config: JobConfig, data: EventData,
    ):
        # build helper needs package_config to resolve dependencies b/w tests and build jobs
        self.package_config = package_config
        # always use job_config to pick up values, use package_config only for package_config.jobs
        self.job_config = job_config
        self.data = data

        self._db_trigger: Optional[AbstractTriggerDbType] = None
        self._project: Optional[GitProject] = None
        self._clean_workplace()

    @property
    def db_trigger(self):
        if not self._db_trigger and self.data.trigger_id is not None:
            if self.data.trigger in (
                TheJobTriggerType.pull_request,
                TheJobTriggerType.pr_comment,
                TheJobTriggerType.pr_label,
            ):
                self._db_trigger = PullRequestModel.get_by_id(self.data.trigger_id)
            elif self.data.trigger == TheJobTriggerType.issue_comment:
                self._db_trigger = IssueModel.get_by_id(self.data.trigger_id)
            elif self.data.trigger == TheJobTriggerType.release:
                self._db_trigger = ProjectReleaseModel.get_by_id(self.data.trigger_id)
            elif self.data.trigger in TheJobTriggerType.push:
                self._db_trigger = GitBranchModel.get_by_id(self.data.trigger_id)
        return self._db_trigger

    @property
    def project(self) -> Optional[GitProject]:
        if not self._project and self.data.project_url:
            self._project = self.service_config.get_project(url=self.data.project_url)
        return self._project

    def run(self) -> HandlerResults:
        raise NotImplementedError("This should have been implemented.")
