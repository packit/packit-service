# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
We love you, Steve Jobs.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from typing import List, Set, Type, Union

from celery import group

from packit.config import JobConfig, PackageConfig
from packit_service.config import ServiceConfig
from packit_service.constants import (
    TASK_ACCEPTED,
    COMMENT_REACTION,
    PACKIT_VERIFY_FAS_COMMAND,
)
from packit_service.log_versions import log_job_versions
from packit_service.worker.allowlist import Allowlist
from packit_service.worker.events import (
    Event,
    EventData,
    PullRequestCommentPagureEvent,
    MergeRequestGitlabEvent,
    InstallationEvent,
    CheckRerunEvent,
    IssueCommentEvent,
)
from packit_service.worker.events.comment import AbstractCommentEvent
from packit_service.worker.handlers import (
    BugzillaHandler,
    CoprBuildHandler,
    CoprBuildEndHandler,
    CoprBuildStartHandler,
    GithubAppInstallationHandler,
    GithubFasVerificationHandler,
    KojiBuildHandler,
    TestingFarmHandler,
    TestingFarmResultsHandler,
    ProposeDownstreamHandler,
)
from packit_service.worker.handlers.abstract import (
    JobHandler,
    MAP_COMMENT_TO_HANDLER,
    MAP_JOB_TYPE_TO_HANDLER,
    MAP_REQUIRED_JOB_TYPE_TO_HANDLER,
    SUPPORTED_EVENTS_FOR_HANDLER,
    MAP_CHECK_PREFIX_TO_HANDLER,
    get_packit_commands_from_comment,
)
from packit_service.worker.helpers.build import CoprBuildJobHelper, KojiBuildJobHelper
from packit_service.worker.helpers.propose_downstream import ProposeDownstreamJobHelper
from packit_service.worker.monitoring import Pushgateway, measure_time
from packit_service.worker.parser import Parser
from packit_service.worker.reporting import BaseCommitStatus
from packit_service.worker.result import TaskResults

logger = logging.getLogger(__name__)


def get_handlers_for_comment_and_rerun_event(
    event: Event, packit_comment_command_prefix: str
) -> Set[Type[JobHandler]]:
    """
    Get all handlers that can be triggered by comment (e.g. `/packit build`) or check rerun.

    For comment events we want to get handlers mapped to comment commands. For check rerun event we
     want to get handlers mapped to check name job.
     These two sets of handlers are mutually exclusive.

    Args:
        event: Event which we are reacting to.
        packit_comment_command_prefix: `/packit` for packit-prod or `/packit-stg` for stg.

    Returns:
        Set of handlers that are triggered by a comment or check rerun job.
    """
    handlers_triggered_by_job = None

    if isinstance(event, AbstractCommentEvent):
        handlers_triggered_by_job = get_handlers_for_comment(
            event.comment, packit_comment_command_prefix
        )

        if handlers_triggered_by_job and not isinstance(
            event, PullRequestCommentPagureEvent
        ):
            event.comment_object.add_reaction(COMMENT_REACTION)

    if isinstance(event, CheckRerunEvent):
        handlers_triggered_by_job = get_handlers_for_check_rerun(event.check_name_job)

    return handlers_triggered_by_job


def is_handler_matching_the_event(
    event: Event,
    handler: Type[JobHandler],
    allowed_handlers: Set[Type[JobHandler]],
) -> bool:
    """
    Decides whether handler matches to comment or check rerun job and given event supports handler.

    Args:
        event: Event which we are reacting to.
        handler: Handler which we are observing whether it is matching to job.
        allowed_handlers: Set of handlers that are triggered by a comment or check rerun
         job.
    """
    handler_matches_to_comment_or_check_rerun_job = (
        allowed_handlers is None or handler in allowed_handlers
    )

    return (
        isinstance(event, tuple(SUPPORTED_EVENTS_FOR_HANDLER[handler]))
        and handler_matches_to_comment_or_check_rerun_job
    )


def get_jobs_matching_event(
    event: Event, package_config: PackageConfig
) -> List[JobConfig]:
    """
    Get list of non-duplicated all jobs that matches with event's trigger.

    Args:
        event: Event which we are reacting to.
        package_config: Config object for upstream/downstream packages
    """
    jobs_matching_trigger = []
    for job in package_config.jobs:
        if (
            job.trigger == event.job_config_trigger_type
            and job not in jobs_matching_trigger
        ):
            jobs_matching_trigger.append(job)

    return jobs_matching_trigger


def get_handlers_for_event(
    event: Event, package_config: PackageConfig, packit_comment_command_prefix: str
) -> Set[Type[JobHandler]]:
    """
    Get all handlers that we need to run for the given event.

    We need to return all handler classes that:
    - can react to the given event AND
    - are configured in the package_config (either directly or as a required job)

    Examples of the matching can be found in the tests:
    ./tests/unit/test_jobs.py:test_get_handlers_for_event

    Args:
        event: Event which we are reacting to.
        package_config: For checking configured jobs.
        packit_comment_command_prefix: `/packit` for packit-prod or `/packit-stg` for stg

    Returns:
        Set of handler instances that we need to run for given event and user configuration.
    """

    jobs_matching_trigger = get_jobs_matching_event(event, package_config)

    handlers_triggered_by_job = get_handlers_for_comment_and_rerun_event(
        event=event, packit_comment_command_prefix=packit_comment_command_prefix
    )

    matching_handlers: Set[Type["JobHandler"]] = set()
    for job in jobs_matching_trigger:
        if (
            isinstance(event, CheckRerunEvent)
            and event.job_identifier != job.identifier
        ):
            continue

        for handler in (
            MAP_JOB_TYPE_TO_HANDLER[job.type]
            | MAP_REQUIRED_JOB_TYPE_TO_HANDLER[job.type]
        ):
            if is_handler_matching_the_event(
                event=event,
                handler=handler,
                allowed_handlers=handlers_triggered_by_job,
            ):
                matching_handlers.add(handler)

    if not matching_handlers:
        logger.debug(
            f"We did not find any handler for a following event:\n{event.__class__}"
        )

    return matching_handlers


def get_handlers_for_comment(
    comment: str, packit_comment_command_prefix: str
) -> Set[Type[JobHandler]]:
    commands = get_packit_commands_from_comment(comment, packit_comment_command_prefix)
    if not commands:
        return set()

    handlers = MAP_COMMENT_TO_HANDLER[commands[0]]
    if not handlers:
        logger.debug(f"Command {commands[0]} not supported by packit.")
    return handlers


def get_handlers_for_check_rerun(check_name_job: str) -> Set[Type[JobHandler]]:
    handlers = MAP_CHECK_PREFIX_TO_HANDLER[check_name_job]
    if not handlers:
        logger.debug(
            f"Rerun for check with {check_name_job} prefix not supported by packit."
        )
    return handlers


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
    jobs_matching_trigger: List[JobConfig] = get_jobs_matching_event(
        event=event, package_config=package_config
    )

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


def push_initial_metrics(
    task_accepted_time: datetime,
    event: Event,
    handler: JobHandler,
    number_of_build_targets: Optional[int] = None,
):
    pushgateway = Pushgateway()
    response_time = measure_time(end=task_accepted_time, begin=event.created_at)
    logger.debug(f"Reporting initial status time: {response_time} seconds.")
    pushgateway.initial_status_time.observe(response_time)
    if response_time > 15:
        pushgateway.no_status_after_15_s.inc()

    # set the time when the accepted status was set so that we can use it later for measurements
    event.task_accepted_time = task_accepted_time

    if isinstance(handler, CoprBuildHandler) and number_of_build_targets:
        for _ in range(number_of_build_targets):
            pushgateway.copr_builds_queued.inc()

    pushgateway.push()


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

    def report_task_accepted(
        self, event: Event, handler: JobHandler, job_config: JobConfig
    ):
        """
        For the upstream events report the initial status "Task was accepted" to
        inform user we are working on the request. Measure the time how much did it
        take to set the status from the time when the event was triggered.

        Args:

        """
        number_of_build_targets = None
        if isinstance(
            handler, (CoprBuildHandler, KojiBuildHandler, TestingFarmHandler)
        ):
            helper = (
                CoprBuildJobHelper
                if isinstance(handler, (CoprBuildHandler, TestingFarmHandler))
                else KojiBuildJobHelper
            )

            job_helper = helper(
                service_config=self.service_config,
                package_config=event.package_config,
                project=event.project,
                metadata=EventData.from_event_dict(event.get_dict()),
                db_trigger=event.db_trigger,
                job_config=job_config,
                build_targets_override=event.build_targets_override,
                tests_targets_override=event.tests_targets_override,
            )

            reporting_method = (
                job_helper.report_status_to_tests
                if isinstance(handler, TestingFarmHandler)
                else job_helper.report_status_to_build
            )

            task_accepted_time = datetime.now(timezone.utc)

            reporting_method(
                description=TASK_ACCEPTED,
                state=BaseCommitStatus.pending,
                url="",
            )
            number_of_build_targets = len(job_helper.build_targets)

        elif isinstance(handler, ProposeDownstreamHandler):
            job_helper = ProposeDownstreamJobHelper(
                service_config=self.service_config,
                package_config=event.package_config,
                project=event.project,
                metadata=EventData.from_event_dict(event.get_dict()),
                db_trigger=event.db_trigger,
                job_config=job_config,
                branches_override=event.branches_override,
            )
            task_accepted_time = datetime.now(timezone.utc)
            job_helper.report_status_to_all(
                description=TASK_ACCEPTED,
                state=BaseCommitStatus.pending,
                url="",
            )

        else:
            # no reporting, no metrics
            return

        push_initial_metrics(
            task_accepted_time, event, handler, number_of_build_targets
        )

    def is_packit_config_present(self, event: Event):
        """
        Set fail_when_config_file_missing if we handle comment events so that
        we notify user about not present config and check whether the config
        is present.

        Returns:
            whether the Packit configuration is present in the repo
        """
        if isinstance(event, AbstractCommentEvent) and get_packit_commands_from_comment(
            event.comment,
            packit_comment_command_prefix=self.service_config.comment_command_prefix,
        ):
            # we require packit config file when event is triggered by /packit command
            event.fail_when_config_file_missing = True

        if not event.package_config:
            # this happens when service receives events for repos which don't have packit config
            # success=True - it's not an error that people don't have packit.yaml in their repo
            return False

        return True

    def process_jobs(self, event: Event) -> List[TaskResults]:
        """
        Create Celery tasks for a job handler (if trigger matches) for every job defined in config.
        """
        if not self.is_packit_config_present(event):
            return [
                TaskResults.create_from(
                    success=True,
                    msg="No packit config found in the repository.",
                    job_config=None,
                    event=event,
                )
            ]

        handler_classes = get_handlers_for_event(
            event,
            event.package_config,
            packit_comment_command_prefix=self.service_config.comment_command_prefix,
        )

        if not handler_classes:
            logger.debug(
                f"There is no handler for {event} event suitable for the configuration."
            )
            return []

        allowlist = Allowlist()
        processing_results: List[TaskResults] = []

        for handler_kls in handler_classes:
            # TODO: merge to to get_handlers_for_event so
            # so we don't need to go through the similar process twice.
            job_configs = get_config_for_handler_kls(
                handler_kls=handler_kls,
                event=event,
                package_config=event.package_config,
            )

            # check allowlist approval for every job to be able to track down which jobs
            # failed because of missing allowlist approval
            if not allowlist.check_and_report(
                event,
                event.project,
                service_config=self.service_config,
                job_configs=job_configs,
            ):
                processing_results = []
                for job_config in job_configs:
                    processing_results.append(
                        TaskResults.create_from(
                            success=False,
                            msg="Account is not allowlisted!",
                            job_config=job_config,
                            event=event,
                        )
                    )
                return processing_results

            processing_results.extend(
                self.create_tasks(event, job_configs, handler_kls)
            )

        return processing_results

    def create_tasks(
        self, event: Event, job_configs: List[JobConfig], handler_kls: Type[JobHandler]
    ) -> List[TaskResults]:
        """ """
        processing_results: List[TaskResults] = []
        signatures = []
        # we want to run handlers for all possible jobs, not just the first one
        for job_config in job_configs:
            if self.service_config.deployment not in job_config.packit_instances:
                logger.debug(
                    f"Current deployment ({self.service_config.deployment}) "
                    f"does not match the job configuration ({job_config.packit_instances}). "
                    "The job will not be run."
                )
                continue

            handler = handler_kls(
                package_config=event.package_config,
                job_config=job_config,
                event=event.get_dict(),
            )
            if not handler.pre_check():
                continue

            if event.actor and not handler.check_if_actor_can_run_job_and_report(
                actor=event.actor
            ):
                # For external contributors, we need to be more careful when running jobs.
                # This is a handler-specific permission check
                # for a user who trigger the action on a PR.
                # e.g. We don't allow using internal TF for external contributors.
                continue

            self.report_task_accepted(
                event=event, handler=handler, job_config=job_config
            )

            signatures.append(handler_kls.get_signature(event=event, job=job_config))
            processing_results.append(
                TaskResults.create_from(
                    success=True,
                    msg="Job created.",
                    job_config=job_config,
                    event=event,
                )
            )
        # https://docs.celeryproject.org/en/stable/userguide/canvas.html#groups
        group(signatures).apply_async()
        return processing_results

    def is_project_public_or_enabled_private(self, event: Event) -> bool:
        """
        Checks whether the project is public or if it is private, explicitly enabled
        in our service configuration.

        Args:
            event: Event which we are reacting to.

        Returns:
            True if the project is public or enabled in our service config,
            False otherwise.
        """
        # CoprBuildEvent.get_project returns None when the build id is not known
        if not event.project:
            logger.warning(
                "Cannot obtain project from this event! "
                "Skipping private repository check!"
            )
        elif event.project.is_private():
            service_with_namespace = (
                f"{event.project.service.hostname}/" f"{event.project.namespace}"
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
                return False
            logger.debug(
                f"Working in `{service_with_namespace}` namespace "
                f"which is private but enabled via configuration."
            )

        return True

    def process_message(
        self, event: dict, topic: str = None, source: str = None
    ) -> List[TaskResults]:
        """
        Entrypoint for message processing.

        Args:
            event:  dict with webhook/fed-mes payload
            topic:  meant to be a topic provided by messaging subsystem (fedmsg, mqqt)
            source: source of message

        Returns:

        """

        if topic:
            # TODO: Check if we really use it.
            #  Ideally, we don't want to mix implementation and events
            #  (topics are related to events).
            # let's pre-filter messages: we don't need to get debug logs from processing
            # messages when we know beforehand that we are not interested in messages for such topic
            topics = [
                getattr(handler, "topic", None)
                for handler in JobHandler.get_all_subclasses()
            ]

            if topic not in topics:
                logger.debug(f"{topic} not in {topics}")
                return []

        event_object: Any
        event_object = Parser.parse_event(event)

        if not (event_object and event_object.pre_check()):
            return []

        if not self.is_project_public_or_enabled_private(event_object):
            return []

        handler: Union[
            GithubAppInstallationHandler,
            TestingFarmResultsHandler,
            CoprBuildStartHandler,
            CoprBuildEndHandler,
        ]
        processing_results = None

        # Bugzilla handler is run even the job is not configured in a package.
        # This's not in the condition below because we want to run process_jobs() as well.
        if isinstance(event_object, MergeRequestGitlabEvent):
            BugzillaHandler.get_signature(
                event=event_object,
                job=None,
            ).apply_async()

        # installation is handled differently b/c app is installed to GitHub account
        # not repository, so package config with jobs is missing
        if isinstance(event_object, InstallationEvent):
            GithubAppInstallationHandler.get_signature(
                event=event_object, job=None
            ).apply_async()
        elif isinstance(
            event_object, IssueCommentEvent
        ) and self.is_fas_verification_comment(event_object.comment):
            if GithubFasVerificationHandler(
                package_config=None, job_config=None, event=event_object.get_dict()
            ).pre_check():
                event_object.comment_object.add_reaction(COMMENT_REACTION)
                GithubFasVerificationHandler.get_signature(
                    event=event_object, job=None
                ).apply_async()
            # should we comment about not processing if the comment is not
            # on the issue created by us or not in packit/notifications?
        else:
            # Processing the jobs from the config.
            processing_results = self.process_jobs(event_object)

        if processing_results is None:
            processing_results = [
                TaskResults.create_from(
                    success=True,
                    msg="Job created.",
                    job_config=None,
                    event=event_object,
                )
            ]

        return processing_results

    def is_fas_verification_comment(self, comment: str) -> bool:
        """
        Checks whether the comment contains Packit verification command
        /packit(-stg) verify-fas
        """
        command = get_packit_commands_from_comment(
            comment, self.service_config.comment_command_prefix
        )
        if command and command[0] == PACKIT_VERIFY_FAS_COMMAND:
            return True

        return False
