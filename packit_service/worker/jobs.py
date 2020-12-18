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
from typing import Any
from typing import Dict, List, Optional, Set, Type, Union

from celery import group

from packit.config import JobConfig, PackageConfig
from packit_service.config import ServiceConfig
from packit_service.log_versions import log_job_versions
from packit_service.service.events import (
    Event,
    InstallationEvent,
    IssueCommentEvent,
    IssueCommentGitlabEvent,
    MergeRequestCommentGitlabEvent,
    PullRequestCommentGithubEvent,
    PullRequestCommentPagureEvent,
    PullRequestLabelPagureEvent,
    PushGitHubEvent,
    PushGitlabEvent,
    PushPagureEvent,
)
from packit_service.utils import dump_job_config, dump_package_config
from packit_service.worker.handlers import (
    CoprBuildEndHandler,
    CoprBuildStartHandler,
    GithubAppInstallationHandler,
    TestingFarmResultsHandler,
)
from packit_service.worker.handlers.abstract import (
    JobHandler,
    MAP_COMMENT_TO_HANDLER,
    MAP_JOB_TYPE_TO_HANDLER,
    MAP_REQUIRED_JOB_TYPE_TO_HANDLER,
    SUPPORTED_EVENTS_FOR_HANDLER,
)
from packit_service.worker.handlers.pagure_handlers import PagurePullRequestLabelHandler
from packit_service.worker.parser import CentosEventParser, Parser
from packit_service.worker.result import TaskResults
from packit_service.worker.whitelist import Whitelist

REQUESTED_PULL_REQUEST_COMMENT = "/packit"

logger = logging.getLogger(__name__)


def get_handlers_for_event(
    event: Event, package_config: PackageConfig
) -> Set[Type[JobHandler]]:
    """
    Get all handlers that we need to run for the given event.

    We need to return all handler classes that:
    - can react to the given event AND
    - are configured in the package_config (either directly or as a required job)

    Examples of the matching can be found in the tests:
    ./tests/unit/test_jobs.py:test_get_handlers_for_event

    :param event: event which we are reacting to
    :param package_config: for checking configured jobs
    :return: set of handler instances that we need to run for given event and user configuration
    """

    jobs_matching_trigger = []
    for job in package_config.jobs:
        if (
            job.trigger == event.db_trigger.job_config_trigger_type
            and job not in jobs_matching_trigger
        ):
            jobs_matching_trigger.append(job)

    if isinstance(
        event,
        (
            PullRequestCommentGithubEvent,
            PullRequestCommentPagureEvent,
            IssueCommentEvent,
            MergeRequestCommentGitlabEvent,
            IssueCommentGitlabEvent,
        ),
    ):
        handlers_triggered_by_comment = get_handlers_for_comment(event.comment)
    else:
        handlers_triggered_by_comment = None

    matching_handlers: Set[Type["JobHandler"]] = set()
    for job in jobs_matching_trigger:
        for handler in (
            MAP_JOB_TYPE_TO_HANDLER[job.type]
            | MAP_REQUIRED_JOB_TYPE_TO_HANDLER[job.type]
        ):
            if isinstance(event, tuple(SUPPORTED_EVENTS_FOR_HANDLER[handler])) and (
                handlers_triggered_by_comment is None
                or handler in handlers_triggered_by_comment
            ):
                matching_handlers.add(handler)

    if not matching_handlers:
        logger.debug(
            f"We did not find any handler for a following event:\n{event.__class__}"
        )

    return matching_handlers


def get_packit_commands_from_comment(comment: str) -> List[str]:
    comment_parts = comment.strip()

    if not comment_parts:
        logger.debug("Empty comment, nothing to do.")
        return []

    cmd_start_index = comment.find(REQUESTED_PULL_REQUEST_COMMENT)

    if cmd_start_index == -1:
        logger.debug(f"comment '{comment}' is not handled by packit-service.")
        return []

    (packit_mark, *packit_command) = comment[cmd_start_index:].split(maxsplit=3)
    # packit_command[0] has the first cmd and [1] has the second, if needed.

    if packit_mark != REQUESTED_PULL_REQUEST_COMMENT:
        logger.debug(f"comment '{comment}' is not handled by packit-service.")
        return []

    if not packit_command:
        logger.debug(f"comment '{comment}' does not contain a packit-service command.")
        return []

    return packit_command


def get_handlers_for_comment(comment: str) -> Set[Type[JobHandler]]:
    commands = get_packit_commands_from_comment(comment)
    if not commands:
        return set()

    return MAP_COMMENT_TO_HANDLER.get(commands[0])


def get_config_for_handler_kls(
    handler_kls: Type[JobHandler], event: Event, package_config: PackageConfig
) -> List[JobConfig]:
    """
    Get a list of JobConfigs relevant to event and the handler class.

    We need to find all job configurations that:
    - can be run by the given handler class AND
    - that matches the trigger of the event

    If there is no matching job-config found, we will pick the ones that are required.
    e.g.: For build handler, you can pick the test config since tests require the build.

    Examples of the matching can be found in the tests:
    ./tests/unit/test_jobs.py:test_get_config_for_handler_kls

    :param handler_kls: class that will use the JobConfig
    :param event: which we are reacting to
    :param package_config: we pick the JobConfig(s) from this package_config instance
    :return: list of JobConfigs relevant to the given handler and event
             preserving the order in the config
    """
    jobs_matching_trigger: List[JobConfig] = []
    for job in package_config.jobs:
        if job.trigger == event.db_trigger.job_config_trigger_type:
            jobs_matching_trigger.append(job)

    matching_jobs: List[JobConfig] = []
    for job in jobs_matching_trigger:
        if handler_kls in MAP_JOB_TYPE_TO_HANDLER[job.type]:
            matching_jobs.append(job)

    if not matching_jobs:
        logger.debug("No config found, let's see the jobs that requires this handler.")
        for job in jobs_matching_trigger:
            if handler_kls in MAP_REQUIRED_JOB_TYPE_TO_HANDLER[job.type]:
                matching_jobs.append(job)

    if not matching_jobs:
        logger.warning(
            f"We did not find any config for {handler_kls} and a following event:\n"
            f"{event.__class__}"
        )

    return matching_jobs


class SteveJobs:
    """
    Steve makes sure all the jobs are done with precision.
    """

    def __init__(self):
        self._service_config = None
        log_job_versions()

    @property
    def service_config(self):
        if self._service_config is None:
            self._service_config = ServiceConfig.get_service_config()
        return self._service_config

    def process_jobs(self, event: Event) -> Dict[str, TaskResults]:
        """
        Create a Celery task for a job handler (if trigger matches) for every job defined in config.
        """

        processing_results = {}

        if isinstance(
            event, (PushGitHubEvent, PushGitlabEvent, PushPagureEvent)
        ) and event.commit_sha.startswith("0000000"):
            processing_results[str(event)] = TaskResults(
                success=True, details={"msg": "Triggered by deleting a branch"}
            )
            return processing_results

        if not event.package_config:
            # this happens when service receives events for repos which don't have packit config
            # success=True - it's not an error that people don't have packit.yaml in their repo
            processing_results[str(event)] = TaskResults(
                success=True, details={"msg": "No packit config in repo"}
            )
            return processing_results

        handler_classes = get_handlers_for_event(event, event.package_config)

        if not handler_classes:
            logger.warning(f"There is no handler for {event} event.")
            return processing_results

        job_configs = []
        for handler_kls in handler_classes:
            # TODO: merge to to get_handlers_for_event so
            # so we don't need to go through the similar process twice.
            job_configs = get_config_for_handler_kls(
                handler_kls=handler_kls,
                event=event,
                package_config=event.package_config,
            )
            # check whitelist approval for every job to be able to track down which jobs
            # failed because of missing whitelist approval
            whitelist = Whitelist()
            user_login = getattr(event, "user_login", None)
            if user_login and user_login in self.service_config.admins:
                logger.info(f"{user_login} is admin, you shall pass.")
            elif not whitelist.check_and_report(
                event,
                event.project,
                service_config=self.service_config,
                job_configs=job_configs,
            ):
                for job_config in job_configs:
                    processing_results[job_config.type.value] = TaskResults(
                        success=False, details={"msg": "Account is not whitelisted!"}
                    )
                return processing_results

            # we want to run handlers for all possible jobs, not just the first one
            signatures = [
                handler_kls.get_signature(event=event, job=job_config)
                for job_config in job_configs
            ]
            # https://docs.celeryproject.org/en/stable/userguide/canvas.html#groups
            group(signatures).apply_async()
        return get_processing_results(event=event, jobs=job_configs)

    def process_message(
        self, event: dict, topic: str = None, source: str = None
    ) -> Optional[dict]:
        """
        Entrypoint for message processing.

        :param event:  dict with webhook/fed-mes payload
        :param topic:  meant to be a topic provided by messaging subsystem (fedmsg, mqqt)
        :param source: source of message
        """

        if topic:
            # let's pre-filter messages: we don't need to get debug logs from processing
            # messages when we know beforehand that we are not interested in messages for such topic
            topics = [
                getattr(handler, "topic", None)
                for handler in JobHandler.get_all_subclasses()
            ]

            if topic not in topics:
                logger.debug(f"{topic} not in {topics}")
                return None

        event_object: Any
        if source == "centosmsg":
            event_object = CentosEventParser().parse_event(event)
        else:
            event_object = Parser.parse_event(event)

        if not (event_object and event_object.pre_check()):
            return None

        # CoprBuildEvent.get_project returns None when the build id is not known
        if not event_object.project:
            logger.warning(
                "Cannot obtain project from this event! "
                "Skipping private repository check!"
            )
        elif event_object.project.is_private():
            service_with_namespace = (
                f"{event_object.project.service.hostname}/"
                f"{event_object.project.namespace}"
            )
            if (
                service_with_namespace
                not in self.service_config.enabled_private_namespaces
            ):
                logger.info(
                    f"We do not interact with private repositories by default. "
                    f"Add `{service_with_namespace}` to the `enabled_private_namespaces` "
                    f"in the service configuration."
                )
                return None
            logger.debug(
                f"Working in `{service_with_namespace}` namespace "
                f"which is private but enabled via configuration."
            )

        handler: Union[
            GithubAppInstallationHandler,
            TestingFarmResultsHandler,
            CoprBuildStartHandler,
            CoprBuildEndHandler,
            PagurePullRequestLabelHandler,
        ]
        processing_results = None

        # installation is handled differently b/c app is installed to GitHub account
        # not repository, so package config with jobs is missing
        if isinstance(event, InstallationEvent):
            GithubAppInstallationHandler.get_signature(
                event=event_object, job=None
            ).apply_async()
        # Label/Tag added event handler is run even when the job is not configured in package
        elif isinstance(event, PullRequestLabelPagureEvent):
            PagurePullRequestLabelHandler.get_signature(
                event=event_object,
                job=None,
            ).apply_async()
        else:
            # Processing the jobs from the config.
            processing_results = self.process_jobs(event_object)

        return processing_results or get_processing_results(event=event_object, jobs=[])


def get_processing_results(
    event: Event, jobs: List[JobConfig], success: bool = True
) -> TaskResults:
    return TaskResults(
        success=success,
        details={
            "event": event.get_dict(),
            "package_config": dump_package_config(event.package_config),
            "matching_jobs": [dump_job_config(job) for job in jobs],
        },
    )
