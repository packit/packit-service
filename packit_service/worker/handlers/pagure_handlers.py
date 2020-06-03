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
from typing import Optional, Union, Any

from packit.config import JobType, JobConfig

from packit_service.config import ServiceConfig
from packit_service.service.events import (
    TheJobTriggerType,
    PullRequestCommentPagureEvent,
    PullRequestLabelPagureEvent,
)
from packit_service.worker.build import CoprBuildJobHelper
from packit_service.worker.handlers import (
    CommentActionHandler,
    AbstractGitForgeJobHandler,
)
from packit_service.worker.handlers.abstract import use_for
from packit_service.worker.handlers.comment_action_handler import CommentAction
from packit_service.worker.result import HandlerResults

logger = logging.getLogger(__name__)


@use_for(JobType.build)
@use_for(JobType.copr_build)
class PagurePullRequestCommentCoprBuildHandler(CommentActionHandler):
    """ Handler for PR comment `/packit copr-build` """

    type = CommentAction.copr_build
    triggers = [TheJobTriggerType.pr_comment]
    event: PullRequestCommentPagureEvent

    def __init__(
        self,
        config: ServiceConfig,
        event: PullRequestCommentPagureEvent,
        job: JobConfig,
    ):
        super().__init__(config=config, event=event, job=job)

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
                job=self.job,
            )
        return self._copr_build_helper

    def run(self) -> HandlerResults:
        return self.copr_build_helper.run_copr_build()


class PagurePullRequestLabelHandler(AbstractGitForgeJobHandler):
    type = JobType.create_bugzilla
    triggers = [TheJobTriggerType.pr_label]
    event: PullRequestLabelPagureEvent

    def __init__(
        self,
        config: ServiceConfig,
        job_config: Optional[JobConfig],
        event: Union[PullRequestLabelPagureEvent, Any],
    ):
        super().__init__(config=config, job_config=job_config, event=event)

        self.event = event
        self.project = self.config.get_project(event.project_url)

    def run(self) -> HandlerResults:
        e = self.event
        logger.debug(
            f"Handling labels/tags {e.labels} {e.action.value} to Pagure PR "
            f"{e.base_repo_owner}/{e.base_repo_namespace}/{e.base_repo_name}/{e.identifier}"
        )
        if e.labels.intersection(self.config.pr_accepted_labels):
            logger.debug(f"About to create a bug @ {self.config.bugzilla_url}")
        else:
            logger.debug(f"We accept only {self.config.pr_accepted_labels} labels/tags")
        return HandlerResults(success=True)
