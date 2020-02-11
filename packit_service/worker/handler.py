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
from os import getenv
from pathlib import Path
from typing import Dict, Any, Optional, Type, List, Union

from ogr.abstract import GitProject
from packit.api import PackitAPI
from packit.config import JobConfig, JobTriggerType, JobType

from packit_service.config import Deployment, ServiceConfig
from packit_service.constants import (
    PACKIT_PROD_CHECK,
    PACKIT_STG_CHECK,
    PACKIT_PROD_TESTING_FARM_CHECK,
    PACKIT_STG_TESTING_FARM_CHECK,
)
from packit_service.service.events import Event
from packit_service.service.models import CoprBuild

logger = logging.getLogger(__name__)

JOB_NAME_HANDLER_MAPPING: Dict[JobType, Type["JobHandler"]] = {}


class PRCheckName:
    """
    This is class providing static methods for getting check names according to deployment
    """

    @staticmethod
    def get_build_check(chroot: str = None) -> str:
        config = ServiceConfig.get_service_config()
        if config.deployment == Deployment.prod:
            if chroot:
                return f"{PACKIT_PROD_CHECK}-{chroot}"
            return PACKIT_PROD_CHECK

        if chroot:
            return f"{PACKIT_STG_CHECK}-{chroot}"
        return PACKIT_STG_CHECK

    @staticmethod
    def get_testing_farm_check(chroot: str = None) -> str:
        config = ServiceConfig.get_service_config()
        if config.deployment == Deployment.prod:
            if chroot:
                return f"{PACKIT_PROD_TESTING_FARM_CHECK}-{chroot}"
            return PACKIT_PROD_TESTING_FARM_CHECK

        if chroot:
            return f"{PACKIT_STG_TESTING_FARM_CHECK}-{chroot}"
        return PACKIT_STG_TESTING_FARM_CHECK


def add_to_mapping(kls: Type["JobHandler"]):
    JOB_NAME_HANDLER_MAPPING[kls.name] = kls
    return kls


def add_to_mapping_for_job(job_type: JobType):
    def _add_to_mapping(kls: Type["JobHandler"]):
        JOB_NAME_HANDLER_MAPPING[job_type] = kls
        return kls

    return _add_to_mapping


class BuildStatusReporter:
    def __init__(
        self,
        project: GitProject,
        commit_sha: str,
        copr_build_model: Optional[CoprBuild] = None,
    ):
        self.project = project
        self.commit_sha = commit_sha
        self.copr_build_model = copr_build_model

    def report(
        self,
        state: str,
        description: str,
        build_id: Optional[str] = None,
        url: str = "",
        check_names: Union[str, list, None] = None,
    ):

        logger.debug(
            f"Reporting state of copr build ID={build_id}"
            f" state={state}, commit={self.commit_sha}"
        )
        if self.copr_build_model:
            self.copr_build_model.status = state
            self.copr_build_model.save()

        if not check_names:
            check_names = [PRCheckName.get_build_check()]
        elif isinstance(check_names, str):
            check_names = [check_names]

        for check in check_names:
            self.set_status(
                state=state, description=description, check_name=check, url=url
            )

    def set_status(self, state: str, description: str, check_name: str, url: str = ""):
        logger.debug(f"Setting status for check '{check_name}': {description}")
        self.project.set_commit_status(
            self.commit_sha, state, url, description, check_name, trim=True
        )

    def get_statuses(self):
        self.project.get_commit_statuses(commit=self.commit_sha)


class HandlerResults(dict):
    """
    Job handler results.
    Inherit from dict to be JSON serializable.
    """

    def __init__(self, success: bool, details: Dict[str, Any] = None):
        """

        :param success: has the job handler succeeded:
                          True - we processed the event
                          False - there was an error while processing it -
                                  usually an exception
        :param details: more info from job handler
                        (optional) 'msg' key contains a message
                        more keys to be defined
        """
        super().__init__(self, success=success, details=details or {})


class Handler:
    def __init__(self, config: ServiceConfig):
        self.config: ServiceConfig = config
        self.api: Optional[PackitAPI] = None
        self.local_project: Optional[PackitAPI] = None

    def run(self) -> HandlerResults:
        raise NotImplementedError("This should have been implemented.")

    def run_n_clean(self) -> HandlerResults:
        try:
            return self.run()
        finally:
            self.clean()

    def _clean_workplace(self):
        # clean only when we are in k8s for sure
        if not getenv("KUBERNETES_SERVICE_HOST"):
            logger.debug("this is not a kubernetes pod, won't clean")
            return
        logger.debug("removing contents of the PV")
        p = Path(self.config.command_handler_work_dir)
        # Do not clean dir if does not exist
        if not p.is_dir():
            logger.debug(
                f"Directory {self.config.command_handler_work_dir} does not exist."
            )
            return

        # remove everything in the volume, but not the volume dir
        dir_items = list(p.iterdir())
        if dir_items:
            logger.info("volume is not empty")
            logger.debug("content: %s" % [g.name for g in dir_items])
        for item in dir_items:
            # symlink pointing to a dir is also a dir and a symlink
            if item.is_symlink() or item.is_file():
                item.unlink()
            else:
                shutil.rmtree(item)

    def clean(self):
        """ clean up the mess once we're done """
        logger.info("cleaning up the mess")
        if self.api:
            self.api.clean()
        self._clean_workplace()


class JobHandler(Handler):
    """ Generic interface to handle different type of inputs """

    name: JobType
    triggers: List[JobTriggerType]

    def __init__(self, config: ServiceConfig, job: JobConfig, event: Event):
        super().__init__(config)
        self.job: JobConfig = job
        self.event = event
        self._clean_workplace()

    def run(self) -> HandlerResults:
        raise NotImplementedError("This should have been implemented.")
