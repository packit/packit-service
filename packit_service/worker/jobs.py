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
from typing import Optional, Dict, Union, Type, Set

from ogr.abstract import GitProject
from ogr.services.github import GithubProject
from packit.config import JobType, PackageConfig, JobConfig

from packit_service.config import ServiceConfig
from packit_service.service.events import (
    PullRequestCommentEvent,
    IssueCommentEvent,
    Event,
    TheJobTriggerType,
)
from packit_service.trigger_mapping import is_trigger_matching_job_config
from packit_service.worker.handlers import (
    CoprBuildEndHandler,
    CoprBuildStartHandler,
    GithubAppInstallationHandler,
    CommentActionHandler,
    TestingFarmResultsHandler,
)
from packit_service.worker.handlers.abstract import (
    Handler,
    MAP_EVENT_TRIGGER_TO_HANDLER,
    MAP_HANDLER_TO_JOB_TYPES,
    JOB_REQUIRED_MAPPING,
    JobHandler,
)
from packit_service.worker.handlers.comment_action_handler import (
    COMMENT_ACTION_HANDLER_MAPPING,
    CommentAction,
)
from packit_service.worker.parser import Parser
from packit_service.worker.result import HandlerResults
from packit_service.worker.whitelist import Whitelist

REQUESTED_PULL_REQUEST_COMMENT = "/packit"

logger = logging.getLogger(__name__)


def get_handlers_for_event(
    event: Event, package_config: PackageConfig
) -> Set[Type[JobHandler]]:
    """
    Get all handlers that we need to run for the given event.

    :param event: event which we are reacting to
    :param package_config: for checking configured jobs
    :return: set of handler instances
    """
    handlers: Set[Type[JobHandler]] = set()
    classes_for_trigger = MAP_EVENT_TRIGGER_TO_HANDLER[event.trigger]

    for job in package_config.jobs:
        if is_trigger_matching_job_config(trigger=event.trigger, job_config=job):
            for pos_handler in classes_for_trigger:
                if job.type in MAP_HANDLER_TO_JOB_TYPES[pos_handler]:
                    handlers.add(pos_handler)
        required_handlers = JOB_REQUIRED_MAPPING[job.type]
        for pos_handler in required_handlers:
            for trigger in pos_handler.triggers:
                if trigger == event.trigger:
                    handlers.add(pos_handler)

    return handlers


def get_config_for_handler_kls(
    handler_kls: Type[JobHandler], event: Event, package_config: PackageConfig
) -> Optional[JobConfig]:
    for job in package_config.jobs:
        if job.type in MAP_HANDLER_TO_JOB_TYPES[
            handler_kls
        ] and is_trigger_matching_job_config(trigger=event.trigger, job_config=job):
            return job

        required_handlers = JOB_REQUIRED_MAPPING[job.type]
        for pos_handler in required_handlers:
            for trigger in pos_handler.triggers:
                if trigger == event.trigger:
                    return job
    return None


class SteveJobs:
    """
    Steve makes sure all the jobs are done with precision.
    """

    def __init__(self):
        self._config = None

    @property
    def config(self):
        if self._config is None:
            self._config = ServiceConfig.get_service_config()
        return self._config

    @staticmethod
    def _is_private(project: GitProject) -> bool:
        github_project = GithubProject(
            repo=project.repo, service=project.service, namespace=project.namespace
        )
        return github_project.github_repo.private

    def process_jobs(self, event: Event) -> Dict[str, HandlerResults]:
        """
        Run a job handler (if trigger matches) for every job defined in config.
        """

        handlers_results = {}
        package_config = event.get_package_config()

        if not package_config:
            # this happens when service receives events for repos which
            # don't have packit config, this is not an error
            # success=True - it's not an error that people don't have packit.yaml in their repo
            handlers_results[event.trigger.value] = HandlerResults(
                success=True, details={"msg": "No packit config in repo"}
            )
            return handlers_results

        handler_classes = get_handlers_for_event(event, package_config)

        if not handler_classes:
            logger.warning(f"There is no handler for {event.trigger} event.")
            return handlers_results

        for handler_kls in handler_classes:
            job = get_config_for_handler_kls(
                handler_kls=handler_kls, event=event, package_config=package_config
            )
            # check whitelist approval for every job to be able to track down which jobs
            # failed because of missing whitelist approval
            whitelist = Whitelist()
            github_login = getattr(event, "github_login", None)
            if github_login and github_login in self.config.admins:
                logger.info(f"{github_login} is admin, you shall pass")
            elif not whitelist.check_and_report(
                event, event.get_project(), config=self.config
            ):
                handlers_results[job.type.value] = HandlerResults(
                    success=False, details={"msg": "Account is not whitelisted!"}
                )
                return handlers_results

            logger.debug(f"Running handler: {str(handler_kls)}")
            handler = handler_kls(config=self.config, job_config=job, event=event)
            if handler.pre_check():
                handlers_results[job.type.value] = handler.run_n_clean()
            # don't break here, other handlers may react to the same event

        return handlers_results

    def find_packit_command(self, comment):
        packit_command = []
        pr_comment_error_msg = ""

        comment = comment.strip()

        if not comment:
            pr_comment_error_msg = f"comment '{comment}' is empty."
            return packit_command, pr_comment_error_msg

        cmd_start_index = comment.find(REQUESTED_PULL_REQUEST_COMMENT)
        (packit_mark, *packit_command) = comment[cmd_start_index:].split(maxsplit=3)
        # packit_command[0] has the first cmd and [1] has the second, if needed.

        if packit_mark != REQUESTED_PULL_REQUEST_COMMENT:
            pr_comment_error_msg = (
                f"comment '{comment}' is not handled by packit-service."
            )
            return packit_command, pr_comment_error_msg

        if not packit_command:
            pr_comment_error_msg = (
                f"comment '{comment}' does not contain a packit-service command."
            )
            return packit_command, pr_comment_error_msg

        # Returns a list of commands, after /packit
        return packit_command, pr_comment_error_msg

    def process_comment_jobs(
        self, event: Union[PullRequestCommentEvent, IssueCommentEvent]
    ) -> HandlerResults:

        msg = f"comment '{event.comment}'"
        packit_command, pr_comment_error_msg = self.find_packit_command(
            str(event.comment)
        )

        if pr_comment_error_msg:
            return HandlerResults(success=True, details={"msg": pr_comment_error_msg},)

        # packit has command `copr-build`. But PullRequestCommentAction has enum `copr_build`.
        try:
            packit_action = CommentAction[packit_command[0].replace("-", "_")]
        except KeyError:
            return HandlerResults(
                success=True,
                details={
                    "msg": f"{msg} does not contain a valid packit-service command."
                },
            )
        handler_kls: Type[CommentActionHandler] = COMMENT_ACTION_HANDLER_MAPPING.get(
            packit_action, None
        )
        if not handler_kls:
            return HandlerResults(
                success=True, details={"msg": f"{msg} is not a packit-service command."}
            )

        # check whitelist approval for every job to be able to track down which jobs
        # failed because of missing whitelist approval
        whitelist = Whitelist()
        github_login = getattr(event, "github_login", None)
        if github_login and github_login in self.config.admins:
            logger.info(f"{github_login} is admin, you shall pass")
        elif not whitelist.check_and_report(
            event, event.get_project(), config=self.config
        ):
            return HandlerResults(
                success=True, details={"msg": "Account is not whitelisted!"}
            )

        handler_instance: Handler = handler_kls(config=self.config, event=event)
        return handler_instance.run_n_clean()

    def process_message(self, event: dict, topic: str = None) -> Optional[dict]:
        """
        Entrypoint to processing messages.

        topic is meant to be a fedmsg topic for the message
        """
        if topic:
            # let's pre-filter messages: we don't need to get debug logs from processing
            # messages when we know beforehand that we are not interested in messages for such topic
            topics = [
                getattr(h, "topic", None) for h in MAP_HANDLER_TO_JOB_TYPES.keys()
            ]

            if topic not in topics:
                logger.debug(f"{topic} not in {topics}")
                return None

        event_object = Parser.parse_event(event)
        if not event_object or not event_object.pre_check():
            return None

        is_private_repository = False
        try:
            project = event_object.get_project()
            # CoprBuildEvent.get_project returns None when the build id is not in redis
            if project:
                is_private_repository = self._is_private(project)
        except NotImplementedError:
            logger.warning("Cannot obtain project from this event!")
            logger.warning("Skipping private repository check!")
        if is_private_repository:
            logger.info("We do not interact with private repositories!")
            return None

        handler: Union[
            GithubAppInstallationHandler,
            TestingFarmResultsHandler,
            CoprBuildStartHandler,
            CoprBuildEndHandler,
        ]
        jobs_results: Dict[str, HandlerResults] = {}
        # installation is handled differently b/c app is installed to GitHub account
        # not repository, so package config with jobs is missing
        if event_object.trigger == TheJobTriggerType.installation:
            handler = GithubAppInstallationHandler(
                self.config, job_config=None, installation_event=event_object
            )
            job_type = JobType.add_to_whitelist.value
            jobs_results[job_type] = handler.run_n_clean()
        elif event_object.trigger in {
            TheJobTriggerType.issue_comment,
            TheJobTriggerType.pr_comment,
        } and (isinstance(event_object, (PullRequestCommentEvent, IssueCommentEvent))):
            job_type = JobType.pull_request_action.value
            jobs_results[job_type] = self.process_comment_jobs(event_object)
        else:
            # Processing the jobs from the config.
            jobs_results = self.process_jobs(event_object)

        logger.debug("All jobs finished!")

        task_results = {"jobs": jobs_results, "event": event_object.get_dict()}

        for v in jobs_results.values():
            if not (v and v["success"]):
                logger.warning(task_results)
                logger.error(v["details"]["msg"])
        return task_results
