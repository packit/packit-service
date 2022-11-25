# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
We love you, Steve Jobs.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Optional, Union
from typing import List, Set, Type

import celery

from ogr.exceptions import GithubAppNotInstalledError
from packit.config import JobConfig, JobType, JobConfigTriggerType
from packit.config.job_config import DEPRECATED_JOB_TYPES
from packit_service.config import ServiceConfig
from packit_service.constants import (
    DOCS_CONFIGURATION_URL,
    TASK_ACCEPTED,
    COMMENT_REACTION,
    PACKIT_VERIFY_FAS_COMMAND,
)
from packit_service.utils import get_packit_commands_from_comment, elapsed_seconds
from packit_service.worker.allowlist import Allowlist
from packit_service.worker.events import (
    Event,
    EventData,
    PullRequestCommentPagureEvent,
    InstallationEvent,
    CheckRerunEvent,
    IssueCommentEvent,
)
from packit_service.worker.events.comment import AbstractCommentEvent
from packit_service.worker.handlers import (
    CoprBuildHandler,
    GithubAppInstallationHandler,
    GithubFasVerificationHandler,
    KojiBuildHandler,
    TestingFarmHandler,
    ProposeDownstreamHandler,
)
from packit_service.worker.handlers.abstract import (
    JobHandler,
    MAP_COMMENT_TO_HANDLER,
    MAP_JOB_TYPE_TO_HANDLER,
    MAP_REQUIRED_JOB_TYPE_TO_HANDLER,
    SUPPORTED_EVENTS_FOR_HANDLER,
    MAP_CHECK_PREFIX_TO_HANDLER,
)
from packit_service.worker.helpers.build import (
    CoprBuildJobHelper,
    KojiBuildJobHelper,
    BaseBuildJobHelper,
)
from packit_service.worker.helpers.propose_downstream import ProposeDownstreamJobHelper
from packit_service.worker.helpers.testing_farm import TestingFarmJobHelper
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.parser import Parser
from packit_service.worker.reporting import BaseCommitStatus
from packit_service.worker.result import TaskResults

logger = logging.getLogger(__name__)


def get_handlers_for_comment(
    comment: str, packit_comment_command_prefix: str
) -> Set[Type[JobHandler]]:
    """
    Get handlers for the given command respecting packit_comment_command_prefix.

    Args:
        comment: comment we are reacting to
        packit_comment_command_prefix: `/packit` for packit-prod or `/packit-stg` for stg

    Returns:
        Set of handlers that are triggered by a comment.
    """
    commands = get_packit_commands_from_comment(comment, packit_comment_command_prefix)
    if not commands:
        return set()

    handlers = MAP_COMMENT_TO_HANDLER[commands[0]]
    if not handlers:
        logger.debug(f"Command {commands[0]} not supported by packit.")
    return handlers


def get_handlers_for_check_rerun(check_name_job: str) -> Set[Type[JobHandler]]:
    """
    Get handlers for the given check name.

    Args:
        check_name_job: check name we are reacting to

    Returns:
        Set of handlers that are triggered by a check rerun.
    """
    handlers = MAP_CHECK_PREFIX_TO_HANDLER[check_name_job]
    if not handlers:
        logger.debug(
            f"Rerun for check with {check_name_job} prefix not supported by packit."
        )
    return handlers


class SteveJobs:
    """
    Steve makes sure all the jobs are done with precision.
    """

    def __init__(self, event: Optional[Event] = None):
        self.event = event
        self._service_config = None

    @property
    def service_config(self):
        if self._service_config is None:
            self._service_config = ServiceConfig.get_service_config()
        return self._service_config

    @staticmethod
    def process_message(event: dict) -> List[TaskResults]:
        """
        Entrypoint for message processing.

        Args:
            event:  dict with webhook/fed-mes payload

        Returns:
            List of results of the processing tasks.
        """
        event_object: Any = Parser.parse_event(event)

        if not (event_object and event_object.pre_check()):
            return []

        return SteveJobs(event_object).process()

    def process(self) -> List[TaskResults]:
        """
        Processes the event object attribute of SteveJobs - runs the checks for
        the given event and creates tasks that match the event,
        example usage: SteveJobs(event_object).process()

        Returns:
            list of processing task results
        """
        try:
            if not self.is_project_public_or_enabled_private():
                return []
        except GithubAppNotInstalledError:
            host, namespace, repo = (
                self.event.project.service.hostname,
                self.event.project.namespace,
                self.event.project.repo,
            )
            logger.info(
                "Packit is not installed on %s/%s/%s, skipping.", host, namespace, repo
            )
            return []

        processing_results = None

        # installation is handled differently b/c app is installed to GitHub account
        # not repository, so package config with jobs is missing
        if isinstance(self.event, InstallationEvent):
            GithubAppInstallationHandler.get_signature(
                event=self.event, job=None
            ).apply_async()
        elif isinstance(
            self.event, IssueCommentEvent
        ) and self.is_fas_verification_comment(self.event.comment):
            if GithubFasVerificationHandler.pre_check(
                package_config=None, job_config=None, event=self.event.get_dict()
            ):
                self.event.comment_object.add_reaction(COMMENT_REACTION)
                GithubFasVerificationHandler.get_signature(
                    event=self.event, job=None
                ).apply_async()
            # should we comment about not processing if the comment is not
            # on the issue created by us or not in packit/notifications?
        else:
            # Processing the jobs from the config.
            processing_results = self.process_jobs()

        if processing_results is None:
            processing_results = [
                TaskResults.create_from(
                    success=True,
                    msg="Job created.",
                    job_config=None,
                    event=self.event,
                )
            ]

        return processing_results

    def initialize_job_helper(
        self, handler_kls: Type[JobHandler], job_config: JobConfig
    ) -> Union[ProposeDownstreamJobHelper, BaseBuildJobHelper]:
        """
        Initialize job helper with arguments
        based on what type of handler is used.

        Args:
            handler_kls: The class for the Handler that will handle the job.
            job_config: Corresponding job config.

        Returns:
            the correct job helper
        """
        params = {
            "service_config": self.service_config,
            "package_config": self.event.package_config,
            "project": self.event.project,
            "metadata": EventData.from_event_dict(self.event.get_dict()),
            "db_trigger": self.event.db_trigger,
            "job_config": job_config,
        }

        if handler_kls == ProposeDownstreamHandler:
            propose_downstream_helper = ProposeDownstreamJobHelper
            params.update({"branches_override": self.event.branches_override})
            return propose_downstream_helper(**params)

        helper_kls: Type[
            Union[TestingFarmJobHelper, CoprBuildJobHelper, KojiBuildJobHelper]
        ]

        if handler_kls == TestingFarmHandler:
            helper_kls = TestingFarmJobHelper
        elif handler_kls == CoprBuildHandler:
            helper_kls = CoprBuildJobHelper
        else:
            helper_kls = KojiBuildJobHelper

        params.update(
            {
                "build_targets_override": self.event.build_targets_override,
                "tests_targets_override": self.event.tests_targets_override,
            }
        )
        return helper_kls(**params)

    def report_task_accepted(
        self, handler_kls: Type[JobHandler], job_config: JobConfig
    ):
        """
        For the upstream events report the initial status "Task was accepted" to
        inform user we are working on the request. Measure the time how much did it
        take to set the status from the time when the event was triggered.

        Args:
            handler_kls: The class for the Handler that will be used.
            job_config: Job config that is being used.
        """
        number_of_build_targets = None
        if handler_kls not in (
            CoprBuildHandler,
            KojiBuildHandler,
            TestingFarmHandler,
            ProposeDownstreamHandler,
        ):
            # no reporting, no metrics
            return

        job_helper = self.initialize_job_helper(handler_kls, job_config)

        if isinstance(job_helper, CoprBuildJobHelper):
            number_of_build_targets = len(job_helper.build_targets)

        task_accepted_time = datetime.now(timezone.utc)
        job_helper.report_status_to_configured_job(
            description=TASK_ACCEPTED,
            state=BaseCommitStatus.pending,
            url="",
        )

        self.push_initial_metrics(
            task_accepted_time, handler_kls, number_of_build_targets
        )

    def is_packit_config_present(self) -> bool:
        """
        Set fail_when_config_file_missing if we handle comment events so that
        we notify user about not present config and check whether the config
        is present.

        Returns:
            Whether the Packit configuration is present in the repo.
        """
        if isinstance(self.event, AbstractCommentEvent) and get_handlers_for_comment(
            self.event.comment,
            packit_comment_command_prefix=self.service_config.comment_command_prefix,
        ):
            # we require packit config file when event is triggered by /packit command
            self.event.fail_when_config_file_missing = True

        if not self.event.package_config:
            # this happens when service receives events for repos which don't have packit config
            # success=True - it's not an error that people don't have packit.yaml in their repo
            return False

        return True

    def process_jobs(self) -> List[TaskResults]:
        """
        Create Celery tasks for a job handler (if trigger matches) for every job defined in config.
        """
        if not self.is_packit_config_present():
            return [
                TaskResults.create_from(
                    success=True,
                    msg="No packit config found in the repository.",
                    job_config=None,
                    event=self.event,
                )
            ]

        handler_classes = self.get_handlers_for_event()

        if not handler_classes:
            logger.debug(
                f"There is no handler for {self.event} event suitable for the configuration."
            )
            return []

        allowlist = Allowlist(service_config=self.service_config)
        processing_results: List[TaskResults] = []

        for handler_kls in handler_classes:
            # TODO: merge to to get_handlers_for_event so
            # so we don't need to go through the similar process twice.
            job_configs = self.get_config_for_handler_kls(
                handler_kls=handler_kls,
            )

            # check allowlist approval for every job to be able to track down which jobs
            # failed because of missing allowlist approval
            if not allowlist.check_and_report(
                self.event,
                self.event.project,
                job_configs=job_configs,
            ):
                return [
                    TaskResults.create_from(
                        success=False,
                        msg="Account is not allowlisted!",
                        job_config=job_config,
                        event=self.event,
                    )
                    for job_config in job_configs
                ]

            processing_results.extend(self.create_tasks(job_configs, handler_kls))

        return processing_results

    def create_tasks(
        self, job_configs: List[JobConfig], handler_kls: Type[JobHandler]
    ) -> List[TaskResults]:
        """
        Create handler tasks for handler and job configs.

        Args:
            job_configs: Matching job configs.
            handler_kls: Handler class that will be used.
        """
        processing_results: List[TaskResults] = []
        signatures = []
        # we want to run handlers for all possible jobs, not just the first one
        for job_config in job_configs:
            if self.should_task_be_created_for_job_config_and_handler(
                job_config, handler_kls
            ):
                signatures.append(
                    handler_kls.get_signature(event=self.event, job=job_config)
                )
                processing_results.append(
                    TaskResults.create_from(
                        success=True,
                        msg="Job created.",
                        job_config=job_config,
                        event=self.event,
                    )
                )
        # https://docs.celeryq.dev/en/stable/userguide/canvas.html#groups
        celery.group(signatures).apply_async()
        return processing_results

    def should_task_be_created_for_job_config_and_handler(
        self, job_config: JobConfig, handler_kls: Type[JobHandler]
    ) -> bool:
        """
        Check whether a new task should be created for job config and handler.

        Args:
            job_config: job config to check
            handler_kls: type of handler class to check

        Returns:
            Whether the task should be created.
        """
        if self.service_config.deployment not in job_config.packit_instances:
            logger.debug(
                f"Current deployment ({self.service_config.deployment}) "
                f"does not match the job configuration ({job_config.packit_instances}). "
                "The job will not be run."
            )
            return False

        if not handler_kls.pre_check(
            package_config=self.event.package_config,
            job_config=job_config,
            event=self.event.get_dict(),
        ):
            return False

        if deprecation_msg := DEPRECATED_JOB_TYPES.get(job_config.type):
            job_helper = self.initialize_job_helper(handler_kls, job_config)
            job_helper.status_reporter.report(
                state=BaseCommitStatus.error,
                description=f"Job name `{job_config.type.name}` deprecated.",
                url=f"{DOCS_CONFIGURATION_URL}/#supported-jobs",
                check_names=f"config-deprecation-{job_config.type.name}",
                markdown_content=f"{deprecation_msg}\n\n"
                "The support for the old name will be removed "
                "by the end of the year.",
            )

        self.report_task_accepted(handler_kls=handler_kls, job_config=job_config)
        return True

    def is_project_public_or_enabled_private(self) -> bool:
        """
        Checks whether the project is public or if it is private, explicitly enabled
        in our service configuration.

        Returns:
            True if the project is public or enabled in our service config,
            False otherwise.
        """
        # CoprBuildEvent.get_project returns None when the build id is not known
        if not self.event.project:
            logger.warning(
                "Cannot obtain project from this event! "
                "Skipping private repository check!"
            )
        elif self.event.project.is_private():
            service_with_namespace = (
                f"{self.event.project.service.hostname}/"
                f"{self.event.project.namespace}"
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

    def check_explicit_matching(self) -> List[JobConfig]:
        """Force explicit event/jobs matching for triggers

        Returns:
            list of jobs
        """
        matching_jobs = []
        if isinstance(self.event, PullRequestCommentPagureEvent):
            for job in self.event.package_config.jobs:
                if (
                    job.type in [JobType.koji_build, JobType.bodhi_update]
                    and job.trigger == JobConfigTriggerType.commit
                    and self.event.job_config_trigger_type
                    == JobConfigTriggerType.pull_request
                ):
                    # A koji_build job with comment trigger
                    # can be re-triggered by a Pagure comment in a PR
                    matching_jobs.append(job)

        return matching_jobs

    def get_jobs_matching_event(self) -> List[JobConfig]:
        """
        Get list of non-duplicated all jobs that matches with event's trigger.
        """
        jobs_matching_trigger = []
        for job in self.event.package_config.jobs:
            if (
                job.trigger == self.event.job_config_trigger_type
                and (
                    not isinstance(self.event, CheckRerunEvent)
                    or self.event.job_identifier == job.identifier
                )
                and job not in jobs_matching_trigger
            ):
                jobs_matching_trigger.append(job)

        jobs_matching_trigger.extend(self.check_explicit_matching())

        return jobs_matching_trigger

    def get_handlers_for_comment_and_rerun_event(self) -> Set[Type[JobHandler]]:
        """
        Get all handlers that can be triggered by comment (e.g. `/packit build`) or check rerun.

        For comment events we want to get handlers mapped to comment commands. For check rerun
        event we want to get handlers mapped to check name job.
        These two sets of handlers are mutually exclusive.

        Returns:
            Set of handlers that are triggered by a comment or check rerun job.
        """
        handlers_triggered_by_job = None

        if isinstance(self.event, AbstractCommentEvent):
            handlers_triggered_by_job = get_handlers_for_comment(
                self.event.comment, self.service_config.comment_command_prefix
            )

            if handlers_triggered_by_job and not isinstance(
                self.event, PullRequestCommentPagureEvent
            ):
                self.event.comment_object.add_reaction(COMMENT_REACTION)

        if isinstance(self.event, CheckRerunEvent):
            handlers_triggered_by_job = get_handlers_for_check_rerun(
                self.event.check_name_job
            )

        return handlers_triggered_by_job

    def get_handlers_for_event(self) -> Set[Type[JobHandler]]:
        """
        Get all handlers that we need to run for the given event.

        We need to return all handler classes that:
        - can react to the given event AND
        - are configured in the package_config (either directly or as a required job)

        Examples of the matching can be found in the tests:
        ./tests/unit/test_jobs.py:test_get_handlers_for_event

        Returns:
            Set of handler instances that we need to run for given event and user configuration.
        """

        jobs_matching_trigger = self.get_jobs_matching_event()

        handlers_triggered_by_job = self.get_handlers_for_comment_and_rerun_event()

        matching_handlers: Set[Type["JobHandler"]] = set()
        for job in jobs_matching_trigger:
            for handler in (
                MAP_JOB_TYPE_TO_HANDLER[job.type]
                | MAP_REQUIRED_JOB_TYPE_TO_HANDLER[job.type]
            ):
                if self.is_handler_matching_the_event(
                    handler=handler,
                    allowed_handlers=handlers_triggered_by_job,
                ):
                    matching_handlers.add(handler)

        if not matching_handlers:
            logger.debug(
                f"We did not find any handler for a following event:\n{self.event.__class__}"
            )

        return matching_handlers

    def is_handler_matching_the_event(
        self,
        handler: Type[JobHandler],
        allowed_handlers: Set[Type[JobHandler]],
    ) -> bool:
        """
        Decides whether handler matches to comment or check rerun job and given event
        supports handler.

        Args:
            handler: Handler which we are observing whether it is matching to job.
            allowed_handlers: Set of handlers that are triggered by a comment or check rerun
             job.
        """
        handler_matches_to_comment_or_check_rerun_job = (
            allowed_handlers is None or handler in allowed_handlers
        )

        return (
            isinstance(self.event, tuple(SUPPORTED_EVENTS_FOR_HANDLER[handler]))
            and handler_matches_to_comment_or_check_rerun_job
        )

    def get_config_for_handler_kls(
        self, handler_kls: Type[JobHandler]
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

        Args:
            handler_kls: class that will use the JobConfig

        Returns:
             list of JobConfigs relevant to the given handler and event
                 preserving the order in the config
        """
        jobs_matching_trigger: List[JobConfig] = self.get_jobs_matching_event()

        matching_jobs: List[JobConfig] = []
        for job in jobs_matching_trigger:
            if handler_kls in MAP_JOB_TYPE_TO_HANDLER[job.type]:
                matching_jobs.append(job)

        if not matching_jobs:
            logger.debug(
                "No config found, let's see the jobs that requires this handler."
            )
            for job in jobs_matching_trigger:
                if handler_kls in MAP_REQUIRED_JOB_TYPE_TO_HANDLER[job.type]:
                    matching_jobs.append(job)

        if not matching_jobs:
            logger.warning(
                f"We did not find any config for {handler_kls} and a following event:\n"
                f"{self.event.__class__}"
            )

        return matching_jobs

    def push_initial_metrics(
        self,
        task_accepted_time: datetime,
        handler_kls: Type[JobHandler],
        number_of_build_targets: Optional[int] = None,
    ):
        """
        Push the metrics about the time of setting initial status and possibly number
        of queued Copr builds.

        Args:
            task_accepted_time: Time when we put the initial status.
            handler_kls: The class for the Handler that will handle the job.
            number_of_build_targets: Number of build targets in case of CoprBuildHandler.
        """
        pushgateway = Pushgateway()
        response_time = elapsed_seconds(
            begin=self.event.created_at, end=task_accepted_time
        )
        logger.debug(f"Reporting initial status time: {response_time} seconds.")
        pushgateway.initial_status_time.observe(response_time)
        if response_time > 15:
            pushgateway.no_status_after_15_s.inc()
            # https://github.com/packit/packit-service/issues/1728
            # we need more info why this has happened
            logger.debug(f"Event dict: {self.event}.")
            logger.error(
                f"Event {self.event.__class__.__name__} took ({response_time}s) to process."
            )

        # set the time when the accepted status was set so that we can use it later for measurements
        self.event.task_accepted_time = task_accepted_time

        if handler_kls == CoprBuildHandler and number_of_build_targets:
            for _ in range(number_of_build_targets):
                pushgateway.copr_builds_queued.inc()

        pushgateway.push()

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
