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
This file defines classes for issue/pr comments which are sent by a git forge.
"""

import enum
import logging
from typing import Dict, Type

from packit.config import JobConfig
from packit.config.package_config import PackageConfig

from packit_service.worker.handlers import JobHandler
from packit_service.worker.result import TaskResults
from packit_service.service.events import EventData

logger = logging.getLogger(__name__)


class CommentAction(enum.Enum):
    copr_build = "copr-build"
    propose_update = "propose-update"
    test = "test"
    build = "build"


MAP_COMMENT_ACTION_TO_HANDLER: Dict[CommentAction, Type["CommentActionHandler"]] = {}


def add_to_comment_action_mapping(kls: Type["CommentActionHandler"]):
    """
    [class decorator]
    Add a comment handler to the mapping.
    """
    MAP_COMMENT_ACTION_TO_HANDLER[kls.type] = kls
    return kls


def add_to_comment_action_mapping_with_name(name: CommentAction):
    """
    [class decorator]
    Use this handler for the given comment action.
    """

    def add_to_comment_action_mapping_with_name_inner(
        kls: Type["CommentActionHandler"],
    ):
        MAP_COMMENT_ACTION_TO_HANDLER[name] = kls
        return kls

    return add_to_comment_action_mapping_with_name_inner


class CommentActionHandler(JobHandler):
    type: CommentAction

    def __init__(
        self,
        package_config: PackageConfig,
        job_config: JobConfig,
        data: EventData,
        **kwargs
    ):
        super().__init__(
            package_config=package_config, job_config=job_config, data=data,
        )

    def run(self) -> TaskResults:
        raise NotImplementedError("This should have been implemented.")
