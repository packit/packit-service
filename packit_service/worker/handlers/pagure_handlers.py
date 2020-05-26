# MIT License
#
# Copyright (c) 2020 Red Hat, Inc.
#
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

import logging
from typing import Optional

from packit_service.config import ServiceConfig
from packit_service.service.events import (
    TheJobTriggerType,
    PullRequestCommentPagureEvent,
)
from packit_service.worker.build import CoprBuildJobHelper
from packit_service.worker.handlers import CommentActionHandler
from packit_service.worker.handlers.comment_action_handler import CommentAction
from packit_service.worker.result import HandlerResults

logger = logging.getLogger(__name__)


class PagurePullRequestCommentCoprBuildHandler(CommentActionHandler):
    """ Handler for PR comment `/packit copr-build` """

    type = CommentAction.copr_build
    triggers = [TheJobTriggerType.pr_comment]
    event: PullRequestCommentPagureEvent

    def __init__(
        self, config: ServiceConfig, event: PullRequestCommentPagureEvent,
    ):
        super().__init__(config=config, event=event)

        # lazy property
        self._copr_build_helper: Optional[CoprBuildJobHelper] = None

    @property
    def copr_build_helper(self) -> CoprBuildJobHelper:
        if not self._copr_build_helper:
            self._copr_build_helper = CoprBuildJobHelper(
                config=self.config,
                package_config=self.event.package_config,
                project=self.event.project,
                event=self.event,
            )
        return self._copr_build_helper

    def run(self) -> HandlerResults:
        return self.copr_build_helper.run_copr_build()
