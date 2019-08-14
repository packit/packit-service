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
from pathlib import Path
from typing import Dict, Any, Optional, Type, List

from ogr.abstract import GitProject
from packit.api import PackitAPI
from packit.config import JobConfig, JobTriggerType, JobType
from packit_service.config import Config, Deployment


from packit_service.constants import PACKIT_PROD_CHECK, PACKIT_STG_CHECK

from packit_service.service.models import CoprBuild

logger = logging.getLogger(__name__)


JOB_NAME_HANDLER_MAPPING: Dict[JobType, Type["JobHandler"]] = {}


def add_to_mapping(kls: Type["JobHandler"]):
    JOB_NAME_HANDLER_MAPPING[kls.name] = kls
    return kls


class BuildStatusReporter:
    def __init__(
        self,
        gh_proj: GitProject,
        commit_sha: str,
        copr_build_model: Optional[CoprBuild] = None,
    ):
        self.gh_proj = gh_proj
        self.commit_sha = commit_sha
        self.copr_build_model = copr_build_model

    def report(
        self,
        state: str,
        description: str,
        build_id: Optional[str] = None,
        url: str = "",
        check_name: str = PACKIT_PROD_CHECK,
    ):
        logger.debug(
            f"Reporting state of copr build ID={build_id},"
            f" state={state}, commit={self.commit_sha}"
        )
        if self.copr_build_model:
            self.copr_build_model.status = state
            self.copr_build_model.save()

        config = Config.get_service_config()
        if config.deployment == Deployment.stg:
            check_name = PACKIT_STG_CHECK

        self.gh_proj.set_commit_status(
            self.commit_sha, state, url, description, check_name
        )

    def set_status(self, state: str, description: str):
        logger.debug(description)
        self.gh_proj.set_commit_status(
            self.commit_sha, state, "", description, "packit/rpm-build"
        )


class HandlerResults(dict):
    """
    Job handler results.
    Inherit from dict to be JSON serializable.
    """

    def __init__(self, success: bool, details: Dict[str, Any] = None):
        """

        :param success: has the job handler succeeded
        :param details: more info from job handler
                        (optional) 'msg' key contains a message
                        more keys to be defined
        """
        super().__init__(self, success=success, details=details or {})


class JobHandler:
    """ Generic interface to handle different type of inputs """

    name: JobType
    triggers: List[JobTriggerType]

    def __init__(self, config: Config, job: JobConfig):
        self.config: Config = config
        self.job: JobConfig = job

        self.api: Optional[PackitAPI] = None
        self.local_project: Optional[PackitAPI] = None

        self._clean_workplace()

    def run(self) -> HandlerResults:
        raise NotImplementedError("This should have been implemented.")

    def _clean_workplace(self):
        logger.debug("removing contents of the PV")
        p = Path(self.config.command_handler_work_dir)
        # remove everything in the volume, but not the volume dir
        dir_items = list(p.iterdir())
        if dir_items:
            logger.info("volume is not empty")
            logger.debug("content: %s" % [g.name for g in dir_items])
        for item in dir_items:
            if item.is_file() or item.is_symlink():
                item.unlink()
            else:
                shutil.rmtree(item)

    def clean(self):
        """ clean up the mess once we're done """
        logger.info("cleaning up the mess")
        if self.api:
            self.api.clean()
        self._clean_workplace()
