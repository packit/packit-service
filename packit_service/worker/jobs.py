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
We love you, Steve Jobs.
"""

import logging
from typing import Optional, Dict, Any, Union

from packit_service.config import Config
from packit.config import JobTriggerType, JobType
from packit_service.worker.github_handlers import GithubAppInstallationHandler
from packit_service.worker.handler import HandlerResults, JOB_NAME_HANDLER_MAPPING
from packit_service.worker.parser import Parser
from packit_service.worker.testing_farm_handlers import TestingFarmResultsHandler
from packit_service.worker.whitelist import Whitelist

from packit_service.worker.pr_comment_handler import (
    PULL_REQUEST_COMMENT_HANDLER_MAPPING,
    PullRequestCommentAction,
)

REQUESTED_PULL_REQUEST_COMMENT = "/packit"

logger = logging.getLogger(__name__)


class SteveJobs:
    """
    Steve makes sure all the jobs are done with precision.
    """

    def __init__(self):
        self._config = None

    @property
    def config(self):
        if self._config is None:
            self._config = Config.get_service_config()
        return self._config

    def process_jobs(self, event: Optional[Any]) -> Dict[str, HandlerResults]:
        """
        Run a job handler (if trigger matches) for every job defined in config.
        """
        handlers_results = {}
        package_config = event.get_package_config()

        if not package_config:
            # this happens when service receives events for repos which
            # don't have packit config, this is not an error
            msg = "Failed to obtain package config!"
            logger.info(msg)
            handlers_results[event.trigger.value] = HandlerResults(
                success=False, details={"msg": msg}
            )

            return handlers_results

        for job in package_config.jobs:
            if event.trigger == job.trigger:
                handler_kls: Any = JOB_NAME_HANDLER_MAPPING.get(job.job, None)
                if not handler_kls:
                    logger.warning(f"There is no handler for job {job}")
                    continue
                handler = handler_kls(self.config, job, event)
                try:
                    # check whitelist approval for every job to be able to track down which jobs
                    # failed because of missing whitelist approval
                    whitelist = Whitelist()
                    if not whitelist.check_and_report(event, event.get_project()):
                        handlers_results[job.job.value] = HandlerResults(
                            success=False,
                            details={"msg": "Account is not whitelisted!"},
                        )
                        return handlers_results

                    logger.debug(f"Running handler: {str(handler_kls)}")
                    handlers_results[job.job.value] = handler.run()
                    # don't break here, other handlers may react to the same event
                finally:
                    handler.clean()
        return handlers_results

    def process_comment_jobs(self, event: Optional[Any]):
        handlers_results = {}
        # packit_command can be `/packit build` or `/packit build <with_args>`
        (packit_mark, *packit_command) = event.comment.split(maxsplit=3)

        if REQUESTED_PULL_REQUEST_COMMENT != packit_mark:
            logger.debug(
                f"This PR comment '{packit_mark}' is not handled by packit-service"
            )
            return HandlerResults(success=False, details={})

        # packit has command `copr-build`. But PullRequestCommentAction has enum `copr_build`.
        packit_action = PullRequestCommentAction[packit_command[0].replace("-", "_")]
        handler_kls: Any = PULL_REQUEST_COMMENT_HANDLER_MAPPING.get(packit_action, None)
        if not handler_kls:
            return HandlerResults(
                success=False,
                details={
                    "msg": (
                        f"This PR trigger command '{REQUESTED_PULL_REQUEST_COMMENT} "
                        f"{packit_command[0]}' is not handled by packit-service."
                    )
                },
            )

        handler = handler_kls(self.config, event)

        try:
            # check whitelist approval for every job to be able to track down which jobs
            # failed because of missing whitelist approval
            whitelist = Whitelist()
            if not whitelist.check_and_report(event, event.get_project()):
                handlers_results[packit_action] = HandlerResults(
                    success=False, details={"msg": "Account is not whitelisted!"}
                )
                return handlers_results
            handlers_results[packit_action] = handler.run()
        finally:
            handler.clean()
        return handlers_results

    def process_message(self, event: dict, topic: str = None) -> Optional[dict]:
        """
        Entrypoint to processing messages.

        topic is meant to be a fedmsg topic for the message
        """
        if topic:
            # let's pre-filter messages: we don't need to get debug logs from processing
            # messages when we know beforehand that we are not interested in messages for such topic
            topics = [
                getattr(h, "topic", None) for h in JOB_NAME_HANDLER_MAPPING.values()
            ]
            if topic not in topics:
                return None

        event_object = Parser.parse_event(event)
        if not event_object:
            logger.debug("We don't process this event")
            return None

        jobs_results = {}

        # installation is handled differently b/c app is installed to GitHub account
        # not repository, so package config with jobs is missing
        if event_object.trigger == JobTriggerType.installation:
            handler: Union[
                GithubAppInstallationHandler, TestingFarmResultsHandler
            ] = GithubAppInstallationHandler(self.config, None, event_object)
            jobs_results[JobType.add_to_whitelist.value] = handler.run()
        elif event_object.trigger == JobTriggerType.comment:
            jobs_results = self.process_comment_jobs(event_object)
        # Results from testing farm is another job which is not defined in packit.yaml so
        # it needs to be handled outside process_jobs method
        elif event_object.trigger == JobTriggerType.testing_farm_results:
            handler = TestingFarmResultsHandler(self.config, None, event_object)
            jobs_results[JobType.report_test_results.value] = handler.run()
        else:
            jobs_results = self.process_jobs(event_object)

        logger.debug("All jobs finished!")

        task_results = {
            "jobs": jobs_results,
            "event": event_object.get_dict(),
            "trigger": str(event_object.trigger),
        }

        # no jobs results, prevent from traceback on accessing v["success"]
        if not jobs_results.values():
            logger.error(task_results)
            return task_results

        if any(not v["success"] for v in jobs_results.values()):
            # Any job handler failed, mark task state as FAILURE
            logger.error(task_results)
        # Task state SUCCESS
        return task_results
