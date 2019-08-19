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
This file defines classes for issue comments which are sent by GitHub.
"""

import enum
import logging
import shutil

from typing import Dict, Type, Optional
from pathlib import Path

from ogr import GithubService
from packit.api import PackitAPI
from packit.config import Config

from packit_service.service.events import PullRequestCommentEvent
from packit_service.worker.handler import HandlerResults

logger = logging.getLogger(__name__)


class PullRequestCommentAction(enum.Enum):
    copr_build = "copr-build"
    build = "build"


PULL_REQUEST_COMMENT_HANDLER_MAPPING: Dict[
    PullRequestCommentAction, Type["PullRequestCommentHandler"]
] = {}


def add_to_pr_comment_mapping(kls: Type["PullRequestCommentHandler"]):
    PULL_REQUEST_COMMENT_HANDLER_MAPPING[kls.name] = kls
    return kls


class PullRequestCommentHandler:
    name: PullRequestCommentAction

    def __init__(self, config: Config, event: PullRequestCommentEvent):
        self.config: Config = config
        self.event: PullRequestCommentEvent = event
        self.api: Optional[PackitAPI] = None
        self.local_project: Optional[PackitAPI] = None

    def __get_private_key(self):
        if self.config.github_app_cert_path:
            return Path(self.config.github_app_cert_path).read_text()
        return None

    @property
    def github_service(self) -> GithubService:
        return GithubService(
            token=self.config.github_token,
            github_app_id=self.config.github_app_id,
            github_app_private_key=self.__get_private_key(),
        )

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
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()

    def clean(self):
        """ clean up the mess once we're done """
        logger.info("cleaning up the mess")
        if self.api:
            self.api.clean()
        self._clean_workplace()
