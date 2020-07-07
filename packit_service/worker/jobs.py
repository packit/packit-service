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
from celery import group
from typing import Any
from typing import Optional, Dict, Union, Type, Set, List

from packit.config import PackageConfig, JobConfig

from packit_service.config import ServiceConfig
from packit_service.log_versions import log_job_versions
from packit_service.models import PullRequestModel
from packit_service.service.events import (
    PullRequestCommentGithubEvent,
    IssueCommentEvent,
    Event,
    TheJobTriggerType,
    PullRequestCommentPagureEvent,
    MergeRequestCommentGitlabEvent,
    IssueCommentGitlabEvent,
)
from packit_service.trigger_mapping import (
    is_trigger_matching_job_config,
    are_job_types_same,
)
from packit_service.worker.handlers import (
    CoprBuildEndHandler,
    CoprBuildStartHandler,
    GithubAppInstallationHandler,
    CommentActionHandler,
    TestingFarmResultsHandler,
    GitHubPullRequestCommentCoprBuildHandler,
)
from packit_service.worker.handlers.abstract import (
    Handler,
    MAP_EVENT_TRIGGER_TO_HANDLERS,
    MAP_HANDLER_TO_JOB_TYPES,
    MAP_REQUIRED_JOB_TO_HANDLERS,
    JobHandler,
)
from packit_service.worker.handlers.comment_action_handler import (
    MAP_COMMENT_ACTION_TO_HANDLER,
    CommentAction,
)
from packit_service.worker.handlers.pagure_handlers import (
    PagurePullRequestCommentCoprBuildHandler,
)
from packit_service.worker.handlers.pagure_handlers import PagurePullRequestLabelHandler
from packit_service.worker.parser import Parser, CentosEventParser
from packit_service.worker.result import TaskResults
from packit_service.worker.whitelist import Whitelist
from packit_service.utils import dump_package_config, dump_job_config

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
    handlers: Set[Type[JobHandler]] = set()
    classes_for_trigger = MAP_EVENT_TRIGGER_TO_HANDLERS[event.trigger]

    for job in package_config.jobs:
        if (
            event.db_trigger and event.db_trigger.job_config_trigger_type == job.trigger
        ) or is_trigger_matching_job_config(trigger=event.trigger, job_config=job):
            for pos_handler in classes_for_trigger:
                if job.type in MAP_HANDLER_TO_JOB_TYPES[pos_handler]:
                    handlers.add(pos_handler)

        # We need to return also handlers that are required for the configured jobs.
        # e.g. we need to run `build` when only `test` is configured
        required_handlers = MAP_REQUIRED_JOB_TO_HANDLERS[job.type]
        for pos_handler in required_handlers:
            for trigger in pos_handler.triggers:
                if trigger == event.trigger:
                    handlers.add(pos_handler)

    return handlers


def get_config_for_handler_kls(
    handler_kls: Type[Handler], event: Event, package_config: PackageConfig
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
    matching_jobs = []
    jobs_that_can_be_triggered = []

    for job in package_config.jobs:
        if (
            event.db_trigger and event.db_trigger.job_config_trigger_type == job.trigger
        ) or is_trigger_matching_job_config(trigger=event.trigger, job_config=job):
            jobs_that_can_be_triggered.append(job)

    matching_job_types = MAP_HANDLER_TO_JOB_TYPES[handler_kls]
    for job in jobs_that_can_be_triggered:
        if (
            # Check if the job matches any job supported by the handler.
            # The function `are_job_types_same` is used
            # because of the `build` x `copr_build` aliasing.
            any(are_job_types_same(job.type, type) for type in matching_job_types)
            and job not in matching_jobs
        ):
            matching_jobs.append(job)

    if matching_jobs:
        return matching_jobs

    # The job was not configured but let's try required ones.
    # e.g. we can use `tests` configuration when running build
    for job in jobs_that_can_be_triggered:
        required_handlers = MAP_REQUIRED_JOB_TO_HANDLERS[job.type]
        if handler_kls in required_handlers:
            for trigger in handler_kls.triggers:
                if (
                    (trigger == event.db_trigger.job_config_trigger_type)
                    or is_trigger_matching_job_config(trigger=trigger, job_config=job)
                    and job not in matching_jobs
                ):
                    matching_jobs.append(job)

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

        if not event.package_config:
            # this happens when service receives events for repos which don't have packit config
            # success=True - it's not an error that people don't have packit.yaml in their repo
            processing_results[event.trigger.value] = TaskResults(
                success=True, details={"msg": "No packit config in repo"}
            )
            return processing_results

        handler_classes = get_handlers_for_event(event, event.package_config)

        if not handler_classes:
            logger.warning(f"There is no handler for {event.trigger} event.")
            return processing_results

        job_configs = []
        for handler_kls in handler_classes:
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
        self,
        event: Union[
            PullRequestCommentGithubEvent,
            PullRequestCommentPagureEvent,
            IssueCommentEvent,
            MergeRequestCommentGitlabEvent,
            IssueCommentGitlabEvent,
        ],
    ) -> Dict[str, TaskResults]:

        msg = f"comment '{event.comment}'"
        packit_command, pr_comment_error_msg = self.find_packit_command(
            str(event.comment)
        )

        if pr_comment_error_msg:
            return {
                event.trigger.value: TaskResults(
                    success=True, details={"msg": pr_comment_error_msg},
                )
            }

        # packit has command `copr-build`. But PullRequestCommentAction has enum `copr_build`.
        try:
            packit_action = CommentAction[packit_command[0].replace("-", "_")]
        except KeyError:
            return {
                event.trigger.value: TaskResults(
                    success=True,
                    details={
                        "msg": f"{msg} does not contain a valid packit-service command."
                    },
                )
            }

        if packit_action == CommentAction.test and isinstance(
            event.db_trigger, PullRequestModel
        ):
            if not event.db_trigger.get_copr_builds():
                packit_action = CommentAction.build

        handler_kls: Type[CommentActionHandler] = MAP_COMMENT_ACTION_TO_HANDLER.get(
            packit_action, None
        )
        if not handler_kls:
            return {
                event.trigger.value: TaskResults(
                    success=True,
                    details={"msg": f"{msg} is not a packit-service command."},
                )
            }

        # check whitelist approval for every job to be able to track down which jobs
        # failed because of missing whitelist approval
        whitelist = Whitelist()
        user_login = getattr(event, "user_login", None)
        jobs = get_config_for_handler_kls(
            handler_kls=handler_kls, event=event, package_config=event.package_config,
        )
        if user_login and user_login in self.service_config.admins:
            logger.info(f"{user_login} is admin, you shall pass.")
        elif not whitelist.check_and_report(
            event, event.project, service_config=self.service_config, job_configs=jobs
        ):
            return {
                event.trigger.value: TaskResults(
                    success=True, details={"msg": "Account is not whitelisted!"}
                )
            }

        # VERY UGLY
        # TODO: REFACTOR !!!
        if handler_kls == GitHubPullRequestCommentCoprBuildHandler and isinstance(
            event, PullRequestCommentPagureEvent
        ):
            handler_kls = PagurePullRequestCommentCoprBuildHandler

        jobs = get_config_for_handler_kls(
            handler_kls=handler_kls, event=event, package_config=event.package_config,
        )

        signatures = [handler_kls.get_signature(event=event, job=job) for job in jobs]
        # https://docs.celeryproject.org/en/stable/userguide/canvas.html#groups
        group(signatures).apply_async()
        return get_processing_results(event=event, jobs=jobs)

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
                getattr(h, "topic", None) for h in MAP_HANDLER_TO_JOB_TYPES.keys()
            ]

            if topic not in topics:
                logger.debug(f"{topic} not in {topics}")
                return None

        event_object: Any
        if source == "centosmsg":
            event_object = CentosEventParser().parse_event(event)
        else:
            event_object = Parser.parse_event(event)

        if not event_object or not event_object.pre_check():
            return None

        # CoprBuildEvent.get_project returns None when the build id is not known
        if not event_object.project:
            logger.warning(
                "Cannot obtain project from this event! "
                "Skipping private repository check!"
            )
        elif event_object.project.is_private():
            logger.info("We do not interact with private repositories!")
            return None

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
        if event_object.trigger == TheJobTriggerType.installation:
            GithubAppInstallationHandler.get_signature(
                event=event_object, job=None
            ).apply_async()
        # Label/Tag added event handler is run even when the job is not configured in package
        elif event_object.trigger == TheJobTriggerType.pr_label:
            PagurePullRequestLabelHandler.get_signature(
                event=event_object, job=None,
            ).apply_async()
        elif event_object.trigger in {
            TheJobTriggerType.issue_comment,
            TheJobTriggerType.pr_comment,
        } and (
            isinstance(
                event_object,
                (
                    PullRequestCommentGithubEvent,
                    PullRequestCommentPagureEvent,
                    IssueCommentEvent,
                    MergeRequestCommentGitlabEvent,
                    IssueCommentGitlabEvent,
                ),
            )
        ):
            processing_results = self.process_comment_jobs(event_object)
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
