# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
We love you, Steve Jobs.
"""
import logging
from datetime import datetime, timezone
from typing import Any
from typing import List, Set, Type, Union

from celery import group
from packit.config import JobConfig, PackageConfig

from packit_service.celerizer import celery_app
from packit_service.config import ServiceConfig
from packit_service.constants import TASK_ACCEPTED
from packit_service.log_versions import log_job_versions
from packit_service.worker.events.comment import AbstractCommentEvent
from packit_service.worker.events import (
    Event,
    EventData,
    PullRequestCommentPagureEvent,
    MergeRequestGitlabEvent,
    InstallationEvent,
    CheckRerunEvent,
)
from packit_service.worker.allowlist import Allowlist
from packit_service.worker.build import CoprBuildJobHelper, KojiBuildJobHelper
from packit_service.worker.handlers import (
    BugzillaHandler,
    CoprBuildHandler,
    CoprBuildEndHandler,
    CoprBuildStartHandler,
    GithubAppInstallationHandler,
    KojiBuildHandler,
    TestingFarmHandler,
    TestingFarmResultsHandler,
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

from packit_service.worker.monitoring import Pushgateway, measure_time
from packit_service.worker.parser import CentosEventParser, Parser
from packit_service.worker.reporting import BaseCommitStatus
from packit_service.worker.result import TaskResults


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
            job.trigger == event.job_config_trigger_type
            and job not in jobs_matching_trigger
        ):
            jobs_matching_trigger.append(job)

    handlers_triggered_by_comment = None
    handlers_triggered_by_check_rerun = None

    if isinstance(event, AbstractCommentEvent):
        handlers_triggered_by_comment = get_handlers_for_comment(event.comment)

        if handlers_triggered_by_comment and not isinstance(
            event, PullRequestCommentPagureEvent
        ):
            event.comment_object.add_reaction("+1")

    if isinstance(event, CheckRerunEvent):
        handlers_triggered_by_check_rerun = get_handlers_for_check_rerun(
            event.check_name_job
        )

    matching_handlers: Set[Type["JobHandler"]] = set()
    for job in jobs_matching_trigger:
        for handler in (
            MAP_JOB_TYPE_TO_HANDLER[job.type]
            | MAP_REQUIRED_JOB_TYPE_TO_HANDLER[job.type]
        ):
            if (
                isinstance(event, tuple(SUPPORTED_EVENTS_FOR_HANDLER[handler]))
                and (
                    handlers_triggered_by_comment is None
                    or handler in handlers_triggered_by_comment
                )
                and (
                    handlers_triggered_by_check_rerun is None
                    or handler in handlers_triggered_by_check_rerun
                )
            ):
                matching_handlers.add(handler)

    if not matching_handlers:
        logger.debug(
            f"We did not find any handler for a following event:\n{event.__class__}"
        )

    return matching_handlers


def get_handlers_for_comment(comment: str) -> Set[Type[JobHandler]]:
    commands = get_packit_commands_from_comment(comment)
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
    jobs_matching_trigger: List[JobConfig] = []
    for job in package_config.jobs:
        if job.trigger == event.job_config_trigger_type:
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


def push_initial_metrics(event: Event, handler: JobHandler, build_targets_len: int):
    pushgateway = Pushgateway()

    task_accepted_time = datetime.now(timezone.utc)
    response_time = measure_time(end=task_accepted_time, begin=event.created_at)
    pushgateway.initial_status_time.observe(response_time)
    if response_time > 15:
        pushgateway.no_status_after_15_s.inc()

    # set the time when the accepted status was set so that we can use it later for measurements
    event.task_accepted_time = task_accepted_time

    if isinstance(handler, CoprBuildHandler):
        for _ in range(build_targets_len):
            pushgateway.copr_builds_queued.inc()

    # record # of tasks in the celery queue
    i = celery_app.control.inspect()
    # >>> i.scheduled()
    # {'celery@packit-worker-0': [{'eta': '2022-02-11T16:22:53.954247+00:00',...
    pushgateway.celery_tasks_count = len(
        [item for workers_tasks in i.scheduled().values() for item in workers_tasks]
    )

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

    def process_jobs(self, event: Event) -> List[TaskResults]:
        """
        Create a Celery task for a job handler (if trigger matches) for every job defined in config.
        """
        if isinstance(event, AbstractCommentEvent) and get_packit_commands_from_comment(
            event.comment
        ):
            # we require packit config file when event is triggered by /packit command
            event.fail_when_config_file_missing = True

        if not event.package_config:
            # this happens when service receives events for repos which don't have packit config
            # success=True - it's not an error that people don't have packit.yaml in their repo
            return [
                TaskResults.create_from(
                    success=True,
                    msg="No packit config found in the repository.",
                    job_config=None,
                    event=event,
                )
            ]

        handler_classes = get_handlers_for_event(event, event.package_config)

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

            signatures = []

            # we want to run handlers for all possible jobs, not just the first one
            for job_config in job_configs:
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
                        targets_override=event.targets_override,
                    )

                    reporting_method = (
                        job_helper.report_status_to_tests
                        if isinstance(handler, TestingFarmHandler)
                        else job_helper.report_status_to_build
                    )

                    reporting_method(
                        description=TASK_ACCEPTED,
                        state=BaseCommitStatus.pending,
                        url="",
                    )
                    push_initial_metrics(event, handler, len(job_helper.build_targets))

                signatures.append(
                    handler_kls.get_signature(event=event, job=job_config)
                )
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

    def process_message(
        self, event: dict, topic: str = None, source: str = None
    ) -> List[TaskResults]:
        """
        Entrypoint for message processing.

        :param event:  dict with webhook/fed-mes payload
        :param topic:  meant to be a topic provided by messaging subsystem (fedmsg, mqqt)
        :param source: source of message
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
        if source == "centosmsg":
            event_object = CentosEventParser().parse_event(event)
        else:
            event_object = Parser.parse_event(event)

        if not (event_object and event_object.pre_check()):
            return []

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
                return []
            logger.debug(
                f"Working in `{service_with_namespace}` namespace "
                f"which is private but enabled via configuration."
            )

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
