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
from typing import Optional, Dict, Any

from packit.config import JobTriggerType, JobType
from packit.exceptions import PackitException

from packit_service.config import Config
from packit_service.worker.github_handlers import GithubAppInstallationHandler
from packit_service.worker.handler import HandlerResults, JOB_NAME_HANDLER_MAPPING
from packit_service.worker.parser import Parser
from packit_service.worker.whitelist import Whitelist


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

                    handlers_results[job.job.value] = handler.run()
                    # don't break here, other handlers may react to the same event
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
            handler = GithubAppInstallationHandler(self.config, None, event_object)
            jobs_results[JobType.add_to_whitelist.value] = handler.run()
        else:
            jobs_results = self.process_jobs(event_object)

        task_results = {
            "jobs": jobs_results,
            "event": event_object.get_dict(),
            "trigger": str(event_object.trigger),
        }

        if any(not v["success"] for v in jobs_results.values()):
            # Any job handler failed, mark task state as FAILURE
            raise PackitException(task_results)
        # Task state SUCCESS
        return task_results
