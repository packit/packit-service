# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
We love you, Steve Jobs.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from functools import cached_property
from re import match
from typing import Callable, Optional, Union

import celery
from ogr.exceptions import GithubAppNotInstalledError
from packit.config import JobConfig, JobConfigTriggerType, JobConfigView, JobType, PackageConfig
from packit.utils import nested_get

from packit_service.config import ServiceConfig
from packit_service.constants import (
    COMMENT_REACTION,
    HELP_COMMENT_DESCRIPTION,
    HELP_COMMENT_EPILOG,
    HELP_COMMENT_PROG,
    HELP_COMMENT_PROG_FEDORA_CI,
    PACKIT_HELP_COMMAND,
    PACKIT_VERIFY_FAS_COMMAND,
    TASK_ACCEPTED,
)
from packit_service.events import (
    abstract,
    github,
    gitlab,
    koji,
    logdetective,
    pagure,
    testing_farm,
)
from packit_service.events.event import Event
from packit_service.events.event_data import EventData
from packit_service.package_config_getter import PackageConfigGetter
from packit_service.utils import (
    elapsed_seconds,
    get_packit_commands_from_comment,
    get_pr_comment_parser,
    get_pr_comment_parser_fedora_ci,
    pr_labels_match_configuration,
)
from packit_service.worker.allowlist import Allowlist
from packit_service.worker.handlers import (
    CoprBuildHandler,
    GithubAppInstallationHandler,
    GithubFasVerificationHandler,
    GitPullRequestHelpHandler,
    KojiBuildHandler,
    ProposeDownstreamHandler,
    TestingFarmHandler,
)
from packit_service.worker.handlers.abstract import (
    MAP_CHECK_PREFIX_TO_HANDLER,
    MAP_COMMENT_TO_HANDLER,
    MAP_COMMENT_TO_HANDLER_FEDORA_CI,
    MAP_JOB_TYPE_TO_HANDLER,
    MAP_REQUIRED_JOB_TYPE_TO_HANDLER,
    SUPPORTED_EVENTS_FOR_HANDLER,
    SUPPORTED_EVENTS_FOR_HANDLER_FEDORA_CI,
    FedoraCIJobHandler,
    JobHandler,
)
from packit_service.worker.handlers.bodhi import (
    BodhiUpdateHandler,
    RetriggerBodhiUpdateHandler,
)
from packit_service.worker.handlers.distgit import (
    DownstreamKojiBuildHandler,
    PullFromUpstreamHandler,
    RetriggerDownstreamKojiBuildHandler,
    TagIntoSidetagHandler,
)
from packit_service.worker.helpers.build import (
    BaseBuildJobHelper,
    CoprBuildJobHelper,
    KojiBuildJobHelper,
)
from packit_service.worker.helpers.fedora_ci import FedoraCIHelper
from packit_service.worker.helpers.sync_release.propose_downstream import (
    ProposeDownstreamJobHelper,
)
from packit_service.worker.helpers.testing_farm import TestingFarmJobHelper
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.parser import Parser
from packit_service.worker.reporting import BaseCommitStatus
from packit_service.worker.result import TaskResults

logger = logging.getLogger(__name__)


MANUAL_OR_RESULT_EVENTS = [abstract.comment.CommentEvent, abstract.base.Result, github.check.Rerun]


@dataclass
class ParsedComment:
    command: Optional[str] = None
    package: Optional[str] = None


def parse_comment(
    comment: str,
    packit_comment_command_prefix: str,
) -> ParsedComment:
    """
    Get arguments from the given comment respecting `packit_comment_command_prefix`.

    Args:
        comment: comment we are reacting to
        packit_comment_command_prefix: `/packit` for packit-prod or `/packit-stg` for stg

    Returns:
        `ParsedComment` storing command and a monorepo package, if specified.

        For example: If the comment is `/packit build --commit 123 --package best-package-ever`,
        it would return `ParsedComment(command="build", package="best-package-ever")`
        Other arguments are ignored because they are handled separately by job handlers
    """
    commands = get_packit_commands_from_comment(comment, packit_comment_command_prefix)
    if not commands:
        return ParsedComment()

    if comment.startswith("/packit-ci"):
        parser = get_pr_comment_parser_fedora_ci(
            prog=HELP_COMMENT_PROG_FEDORA_CI,
            description=HELP_COMMENT_DESCRIPTION,
            epilog=HELP_COMMENT_EPILOG,
        )
    else:
        parser = get_pr_comment_parser(
            prog=HELP_COMMENT_PROG,
            description=HELP_COMMENT_DESCRIPTION,
            epilog=HELP_COMMENT_EPILOG,
        )

    try:
        args = parser.parse_args(commands)
        return ParsedComment(command=args.command, package=args.package)
    except SystemExit:
        # tests expect invalid syntax comments be ignored
        logger.debug(
            f"Comment {comment} uses unexpected syntax or contains unsupported commands. "
            "It will be ignored.",
        )
        return ParsedComment()


def get_handlers_for_command(
    command: str,
) -> set[type[JobHandler]]:
    """
    Get handlers for the given command.

    Args:
        command: command to get handler to

    Returns:
        Set of handlers that are triggered by command.
    """
    if not command:
        return set()

    handlers = MAP_COMMENT_TO_HANDLER[command]
    if not handlers:
        logger.debug(f"Command {command} not supported by packit.")
    return handlers


def get_handlers_for_command_fedora_ci(
    command: str,
) -> set[type[FedoraCIJobHandler]]:
    """
    Get handlers for the given command.

    Args:
        command: command to get handler to

    Returns:
        Set of handlers for Fecora CI that are triggered by command.
    """
    if not command:
        return set()

    handlers = MAP_COMMENT_TO_HANDLER_FEDORA_CI[command]
    if not handlers:
        logger.debug(f"Command {command} not supported by packit.")
    return handlers


def replace_packit_comment_command_prefix(
    packit_comment_command_prefix: str,
) -> str:
    # TODO: remove this once Fedora CI has its own instances and comment_command_prefixes
    # comment_command_prefixes for Fedora CI are /packit-ci and /packit-ci-stg
    if packit_comment_command_prefix.endswith("-stg"):
        return "/packit-ci-stg"
    return "/packit-ci"


def get_handlers_for_check_rerun(check_name_job: str) -> set[type[JobHandler]]:
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
            f"Rerun for check with {check_name_job} prefix not supported by packit.",
        )
    return handlers


class SteveJobs:
    """
    Steve makes sure all the jobs are done with precision.
    """

    def __init__(self, event: Optional[Event] = None) -> None:
        self.event = event
        self.pushgateway = Pushgateway()

    @cached_property
    def service_config(self) -> ServiceConfig:
        return ServiceConfig.get_service_config()

    @classmethod
    def process_message(
        cls,
        event: dict,
        source: Optional[str] = None,
        event_type: Optional[str] = None,
    ) -> list[TaskResults]:
        """
        Entrypoint for message processing.

        For values of 'source' and 'event_type' see Parser.MAPPING.

        Args:
            event: Dict with webhook/fed-msg payload.
            source: Source of the event, for example: "github".
            event_type: Type of the event.

        Returns:
            List of results of the processing tasks.
        """
        parser = nested_get(
            Parser.MAPPING,
            source,
            event_type,
            default=Parser.parse_event,
        )
        event_object: Optional[Event] = parser(event)
        steve = cls(event_object)
        steve.pushgateway.events_processed.inc()
        if event_not_handled := not event_object:
            steve.pushgateway.events_not_handled.inc()
        elif pre_check_failed := not event_object.pre_check():
            steve.pushgateway.events_pre_check_failed.inc()

        result = [] if (event_not_handled or pre_check_failed) else steve.process()

        steve.pushgateway.push()
        return result

    def process(self) -> list[TaskResults]:
        """
        Processes the event object attribute of SteveJobs - runs the checks for
        the given event and creates tasks that match the event,
        example usage: SteveJobs(event_object).process()

        Returns:
            List of processing task results.
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
                "Packit is not installed on %s/%s/%s, skipping.",
                host,
                namespace,
                repo,
            )
            return []

        processing_results = None

        # installation is handled differently b/c app is installed to GitHub account
        # not repository, so package config with jobs is missing
        if isinstance(self.event, github.installation.Installation):
            GithubAppInstallationHandler.get_signature(
                event=self.event,
                job=None,
            ).apply_async()
        elif isinstance(
            self.event,
            github.issue.Comment,
        ) and self.is_fas_verification_comment(self.event.comment):
            if GithubFasVerificationHandler.pre_check(
                package_config=None,
                job_config=None,
                event=self.event.get_dict(),
            ):
                self.event.comment_object.add_reaction(COMMENT_REACTION)
                GithubFasVerificationHandler.get_signature(
                    event=self.event,
                    job=None,
                ).apply_async()
            # should we comment about not processing if the comment is not
            # on the issue created by us or not in packit/notifications?
        elif isinstance(
            self.event,
            (github.pr.Comment, gitlab.mr.Comment, pagure.pr.Comment),
        ) and self.is_help_comment(self.event.comment):
            self.event.comment_object.add_reaction(COMMENT_REACTION)
            GitPullRequestHelpHandler.get_signature(
                event=self.event,
                job=None,
            ).apply_async()
        else:
            if (
                isinstance(
                    self.event,
                    (
                        pagure.pr.Action,
                        pagure.pr.Comment,
                        koji.result.Task,
                        testing_farm.Result,
                        logdetective.Result,
                    ),
                )
                and self.event.db_project_object
                and (url := self.event.db_project_object.project.project_url)
                and url in self.service_config.enabled_projects_for_fedora_ci
            ):
                # try to process Fedora CI jobs first
                processing_results = self.process_fedora_ci_jobs()

            if not processing_results:
                # processing the jobs from the config
                processing_results = self.process_jobs()

        if processing_results is None:
            processing_results = [
                TaskResults.create_from(
                    success=True,
                    msg="Job created.",
                    job_config=None,
                    event=self.event,
                ),
            ]

        return processing_results

    def initialize_job_helper(
        self,
        handler_kls: type[JobHandler],
        job_config: JobConfig,
    ) -> Union[ProposeDownstreamJobHelper, BaseBuildJobHelper]:
        """
        Initialize job helper with arguments
        based on what type of handler is used.

        Args:
            handler_kls: The class for the Handler that will handle the job.
            job_config: Corresponding job config.

        Returns:
            The correct job helper.
        """
        params = {
            "service_config": self.service_config,
            "package_config": (
                self.event.packages_config.get_package_config_for(job_config)
                if self.event.packages_config
                else None
            ),
            "project": self.event.project,
            "metadata": EventData.from_event_dict(self.event.get_dict()),
            "db_project_event": self.event.db_project_event,
            "job_config": job_config,
        }

        if handler_kls == ProposeDownstreamHandler:
            propose_downstream_helper = ProposeDownstreamJobHelper
            params["branches_override"] = self.event.branches_override
            return propose_downstream_helper(**params)

        helper_kls: type[Union[TestingFarmJobHelper, CoprBuildJobHelper, KojiBuildJobHelper]]

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
            },
        )
        return helper_kls(**params)

    def report_task_accepted(
        self,
        handler_kls: type[JobHandler],
        job_config: JobConfig,
        update_feedback_time: Callable,
    ) -> None:
        """
        For the upstream events report the initial status "Task was accepted" to
        inform user we are working on the request. Measure the time how much did it
        take to set the status from the time when the event was triggered.

        Args:
            handler_kls: The class for the Handler that will be used.
            job_config: Job config that is being used.
            update_feedback_time: A callable which tells the caller when a check
                status has been updated.
        """
        number_of_build_targets = None
        if isinstance(self.event, abstract.comment.CommentEvent) and handler_kls in (
            PullFromUpstreamHandler,
            DownstreamKojiBuildHandler,
            BodhiUpdateHandler,
            RetriggerBodhiUpdateHandler,
            RetriggerDownstreamKojiBuildHandler,
            TagIntoSidetagHandler,
        ):
            self.report_task_accepted_for_downstream_retrigger_comments(handler_kls)
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

        job_helper.report_status_to_configured_job(
            description=TASK_ACCEPTED,
            state=BaseCommitStatus.pending,
            url="",
            update_feedback_time=update_feedback_time,
        )

        self.push_copr_metrics(handler_kls, number_of_build_targets)

    def report_task_accepted_for_fedora_ci(self, handler_kls: type[FedoraCIJobHandler]):
        """
        For CI-related dist-git PR comment events report the initial status
        "Task was accepted" to inform user we are working on the request.
        """

        if not isinstance(
            self.event,
            abstract.comment.PullRequest,
        ):
            logger.debug(
                "Not a comment event, not reporting task was accepted via commit status.",
            )
            return

        metadata = EventData.from_event_dict(self.event.get_dict())

        helper = FedoraCIHelper(
            project=self.event.project,
            metadata=metadata,
            target_branch=self.event.pull_request_object.target_branch,
        )

        for check_name in handler_kls.get_check_names(
            self.service_config, self.event.project, metadata
        ):
            helper.report(
                description=TASK_ACCEPTED,
                state=BaseCommitStatus.pending,
                url="",
                check_name=check_name,
            )

    def search_distgit_config_in_issue(self) -> Optional[tuple[str, PackageConfig]]:
        """Get a tuple (dist-git repo url, package config loaded from dist-git yaml file).
        Look up for a dist-git repo url inside
        the issue description for the issue comment event.
        The issue description should have a format like the following:
        ```
        Packit failed on creating pull-requests in dist-git
            (https://src.fedoraproject.org/rpms/python-teamcity-messages):
        | dist-git branch | error |
        | --------------- | ----- |
        | `f37`           | ``    |
        You can retrigger the update by adding a comment
            (`/packit propose-downstream`) into this issue.
        ```

        Returns:
            A tuple (`dist_git_repo_url`, `dist_git_package_config`) or `None`
        """
        if not isinstance(self.event, abstract.comment.Issue):
            # not a comment, doesn't matter
            return None

        issue = self.event.project.get_issue(self.event.issue_id)
        if m := match(r"[\w\s-]+dist-git \((\S+)\):", issue.description):
            url = m[1]
            project = self.service_config.get_project(url=url)
            package_config = PackageConfigGetter.get_package_config_from_repo(
                project=project,
                fail_when_missing=False,
            )
            return url, package_config

        return None

    def is_packit_config_present(self) -> bool:
        """
        Set fail_when_config_file_missing if we handle comment events so that
        we notify user about not present config and check whether the config
        is present.

        Returns:
            Whether the Packit configuration is present in the repo.
        """
        if isinstance(self.event, abstract.comment.CommentEvent):
            arguments = parse_comment(
                self.event.comment,
                self.service_config.comment_command_prefix,
            )
            command = arguments.command

            if handlers := get_handlers_for_command(command):
                # we require packit config file when event is triggered by /packit command
                # but not when it is triggered through an issue in the issues repository
                dist_git_package_config = None
                if (
                    isinstance(self.event, abstract.comment.Issue)
                    # for propose-downstream we want to load the package config
                    # from upstream repo
                    and ProposeDownstreamHandler not in handlers
                    and (dist_git_package_config := self.search_distgit_config_in_issue())
                ):
                    (
                        self.event.dist_git_project_url,
                        self.event._package_config,
                    ) = dist_git_package_config
                    return True

                if not dist_git_package_config:
                    self.event.fail_when_config_file_missing = True

        # False happens when service receives events for repos which don't have packit config
        # success=True - it's not an error that people don't have packit.yaml in their repo
        return self.event.packages_config

    def process_fedora_ci_jobs(self) -> list[TaskResults]:
        """
        Create Celery tasks for a job handler (if the trigger matches) for Fedora CI.

        Returns:
            A list of task results for each task created.
        """
        handlers_triggered_by_job = None
        # [XXX] if there are ever monorepos in Fedora CI…
        # monorepo_package = None

        if isinstance(self.event, abstract.comment.CommentEvent):
            arguments = parse_comment(
                self.event.comment,
                replace_packit_comment_command_prefix(self.service_config.comment_command_prefix),
            )

            # [XXX] if there are ever monorepos in Fedora CI…
            # monorepo_package = arguments.package
            command = arguments.command
            handlers_triggered_by_job = get_handlers_for_command_fedora_ci(command)

        matching_handlers = {
            handler
            for handler, supported_events in SUPPORTED_EVENTS_FOR_HANDLER_FEDORA_CI.items()
            if isinstance(self.event, tuple(supported_events))
            and (handlers_triggered_by_job is None or handler in handlers_triggered_by_job)
        }

        if not matching_handlers:
            logger.debug(f"No handler found for event {self.event} for Fedora CI.")
            return []

        # TODO: add allowlist checks here

        processing_results: list[TaskResults] = []

        for handler_kls in matching_handlers:
            if not handler_kls.pre_check(
                package_config=None,
                job_config=None,
                event=self.event.get_dict(),
            ):
                continue

            # [XXX] if there are ever monorepos in Fedora CI…
            # if monorepo_package and handler_kls.job_config.package == monorepo_package:
            #     continue

            self.report_task_accepted_for_fedora_ci(handler_kls)

            celery_signature = celery.signature(
                handler_kls.task_name.value,
                kwargs={
                    "package_config": None,
                    "job_config": None,
                    "event": self.event.get_dict(),
                },
            )

            celery_signature.apply_async()
            logger.debug(f"Celery signature sent for handler {handler_kls}.")

            processing_results.append(
                TaskResults(
                    success=True,
                    details={
                        "msg": "Job created.",
                        "event": self.event.get_dict(),
                    },
                )
            )

        return processing_results

    def process_jobs(self) -> list[TaskResults]:
        """
        Create Celery tasks for a job handler (if trigger matches) for every
        job defined in config.

        Returns:
            List of the results of each task.
        """
        monorepo_package = None
        if isinstance(
            self.event,
            abstract.comment.CommentEvent,
        ):
            arguments = parse_comment(
                self.event.comment,
                self.service_config.comment_command_prefix,
            )

            monorepo_package = arguments.package
            command = arguments.command

            if not get_handlers_for_command(command):
                return [
                    TaskResults(
                        success=True,
                        details={"msg": "No Packit command found in the comment."},
                    ),
                ]

        if not self.is_packit_config_present():
            return [
                TaskResults.create_from(
                    success=True,
                    msg="No packit config found in the repository.",
                    job_config=None,
                    event=self.event,
                ),
            ]

        handler_classes = self.get_handlers_for_event(monorepo_package)

        if not handler_classes:
            logger.debug(
                f"There is no handler for {self.event} event suitable for the configuration.",
            )
            return []

        allowlist = Allowlist(service_config=self.service_config)
        processing_results: list[TaskResults] = []

        statuses_check_feedback: list[datetime] = []
        for handler_kls in handler_classes:
            # TODO: merge to to get_handlers_for_event so
            # so we don't need to go through the similar process twice.
            job_configs = self.get_config_for_handler_kls(
                handler_kls=handler_kls,
                monorepo_package=monorepo_package,
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

            processing_results.extend(
                self.create_tasks(job_configs, handler_kls, statuses_check_feedback),
            )
        self.push_statuses_metrics(statuses_check_feedback)

        return processing_results

    def create_tasks(
        self,
        job_configs: list[JobConfig],
        handler_kls: type[JobHandler],
        statuses_check_feedback: list[datetime],
    ) -> list[TaskResults]:
        """
        Create handler tasks for handler and job configs.

        Args:
            job_configs: Matching job configs.
            handler_kls: Handler class that will be used.
        """
        processing_results: list[TaskResults] = []
        signatures = []
        # we want to run handlers for all possible jobs, not just the first one
        for job_config in job_configs:
            if self.should_task_be_created_for_job_config_and_handler(
                job_config,
                handler_kls,
            ):
                self.report_task_accepted(
                    handler_kls=handler_kls,
                    job_config=job_config,
                    update_feedback_time=lambda t: statuses_check_feedback.append(t),
                )

                # Set time when the task was accepted
                if not self.event.task_accepted_time and statuses_check_feedback:
                    self.event.task_accepted_time = statuses_check_feedback[0]

                if handler_kls in (
                    CoprBuildHandler,
                    TestingFarmHandler,
                    KojiBuildHandler,
                ):
                    self.event.store_packages_config()

                signatures.append(
                    handler_kls.get_signature(event=self.event, job=job_config),
                )
                logger.debug(
                    f"Got signature for handler={handler_kls} and job_config={job_config}.",
                )
                processing_results.append(
                    TaskResults.create_from(
                        success=True,
                        msg="Job created.",
                        job_config=job_config,
                        event=self.event,
                    ),
                )
        logger.debug("Signatures are going to be sent to Celery.")
        # https://docs.celeryq.dev/en/stable/userguide/canvas.html#groups
        celery.group(signatures).apply_async()
        logger.debug("Signatures were sent to Celery.")
        return processing_results

    def should_task_be_created_for_job_config_and_handler(
        self,
        job_config: JobConfig,
        handler_kls: type[JobHandler],
    ) -> bool:
        """
        Check whether a new task should be created for job config and handler.

        Args:
            job_config: Job config to check.
            handler_kls: Type of handler class to check.

        Returns:
            Whether the task should be created.
        """
        if self.service_config.deployment not in job_config.packit_instances:
            logger.debug(
                f"Current deployment ({self.service_config.deployment}) "
                f"does not match the job configuration ({job_config.packit_instances}). "
                "The job will not be run.",
            )
            return False

        return handler_kls.pre_check(
            package_config=(
                self.event.packages_config.get_package_config_for(job_config)
                if self.event.packages_config
                else None
            ),
            job_config=job_config,
            event=self.event.get_dict(),
        )

    def is_project_public_or_enabled_private(self) -> bool:
        """
        Checks whether the project is public or if it is private, explicitly enabled
        in our service configuration.

        Returns:
            `True`, if the project is public or enabled in our service config
            or the check is skipped,
            `False` otherwise.
        """
        # do the check only for events triggering the pipeline
        if isinstance(self.event, abstract.base.Result):
            logger.debug("Skipping private repository check for this type of event.")

        # CoprBuildEvent.get_project returns None when the build id is not known
        elif not self.event.project:
            logger.warning(
                "Cannot obtain project from this event! Skipping private repository check!",
            )
        elif self.event.project.is_private():
            service_with_namespace = (
                f"{self.event.project.service.hostname}/{self.event.project.namespace}"
            )
            if service_with_namespace not in self.service_config.enabled_private_namespaces:
                logger.info(
                    f"We do not interact with private repositories by default. "
                    f"Add `{service_with_namespace}` to the `enabled_private_namespaces` "
                    f"in the service configuration.",
                )
                return False
            logger.debug(
                f"Working in `{service_with_namespace}` namespace "
                f"which is private but enabled via configuration.",
            )

        return True

    def check_explicit_matching(self) -> list[JobConfig]:
        """Force explicit event/jobs matching for triggers

        Returns:
            List of job configs.
        """

        def compare_jobs_without_triggers(a, b):
            # check if two jobs are the same or differ only in trigger
            ad = dict(a.__dict__)
            ad.pop("trigger")
            bd = dict(b.__dict__)
            bd.pop("trigger")
            return ad == bd

        def event_is_koji_tag_command():
            commands = get_packit_commands_from_comment(
                self.event.comment, self.service_config.comment_command_prefix
            )
            if not commands:
                return False
            return commands[0] == "koji-tag"

        matching_jobs: list[JobConfig] = []
        if isinstance(self.event, pagure.pr.Comment):
            for job in self.event.packages_config.get_job_views():
                if (
                    job.type in [JobType.koji_build, JobType.bodhi_update]
                    and job.trigger
                    in (JobConfigTriggerType.commit, JobConfigTriggerType.koji_build)
                    and self.event.job_config_trigger_type == JobConfigTriggerType.pull_request
                ):
                    if job.type == JobType.koji_build:
                        # avoid having duplicate koji_build jobs
                        if any(j for j in matching_jobs if compare_jobs_without_triggers(job, j)):
                            continue
                        # in case of koji-tag command, match only koji_build jobs with sidetag group
                        if event_is_koji_tag_command() and not job.sidetag_group:
                            continue
                    # A koji_build or bodhi_update job with commit or koji_build trigger
                    # can be re-triggered by a Pagure comment in a PR
                    matching_jobs.append(job)
                elif (
                    job.type == JobType.pull_from_upstream
                    and job.trigger == JobConfigTriggerType.release
                    and self.event.job_config_trigger_type == JobConfigTriggerType.pull_request
                ):
                    # A pull_from_upstream job with release trigger
                    # can be re-triggered by a comment in a dist-git PR
                    matching_jobs.append(job)
        elif isinstance(self.event, abstract.comment.Issue):
            for job in self.event.packages_config.get_job_views():
                if (
                    job.type in (JobType.koji_build, JobType.bodhi_update)
                    and job.trigger
                    in (JobConfigTriggerType.commit, JobConfigTriggerType.koji_build)
                    and self.event.job_config_trigger_type == JobConfigTriggerType.release
                ):
                    # avoid having duplicate koji_build jobs
                    if job.type == JobType.koji_build and any(
                        j for j in matching_jobs if compare_jobs_without_triggers(job, j)
                    ):
                        continue
                    # A koji_build/bodhi_update can be re-triggered by a
                    # comment in a issue in the repository issues
                    # after a failed release event
                    # (which has created the issue)
                    matching_jobs.append(job)
        elif isinstance(self.event, koji.tag.Build):
            # create a virtual job config
            job_config = JobConfig(
                JobType.koji_build_tag,
                JobConfigTriggerType.koji_build,
                self.event.packages_config.packages,
            )
            for package, config in self.event.packages_config.packages.items():
                if config.downstream_package_name == self.event.package_name:
                    job = JobConfigView(job_config, package)
                    matching_jobs.append(job)
                    # if there are multiple packages with the same downstream_package_name,
                    # choose any of them (the handler should ignore the config anyway)
                    break

        return matching_jobs

    def get_jobs_matching_event(
        self,
        monorepo_package: Optional[str] = None,
    ) -> list[JobConfig]:
        """
        Get list of non-duplicated all jobs that matches with event's trigger.

        Returns:
            List of all jobs that match the event's trigger.
        """
        jobs_matching_trigger = []
        for job in self.event.packages_config.get_job_views():
            if (
                job.trigger == self.event.job_config_trigger_type
                and (
                    not isinstance(self.event, github.check.Rerun)
                    or self.event.job_identifier == job.identifier
                )
                and job not in jobs_matching_trigger
                # Manual trigger condition
                and (
                    not job.manual_trigger
                    or any(
                        isinstance(self.event, event_type) for event_type in MANUAL_OR_RESULT_EVENTS
                    )
                )
                and (
                    job.trigger != JobConfigTriggerType.pull_request
                    or not (job.require.label.present or job.require.label.absent)
                    or not isinstance(self.event, abstract.base.ForgeIndependent)
                    or pr_labels_match_configuration(
                        pull_request=self.event.pull_request_object,
                        configured_labels_absent=job.require.label.absent,
                        configured_labels_present=job.require.label.present,
                    )
                )
            ):
                jobs_matching_trigger.append(job)

        jobs_matching_trigger.extend(self.check_explicit_matching())

        if monorepo_package:
            jobs_matching_trigger = [
                job
                for job in jobs_matching_trigger
                if isinstance(job, JobConfigView) and job.package == monorepo_package
            ]

        return jobs_matching_trigger

    def get_handlers_for_comment_and_rerun_event(self) -> set[type[JobHandler]]:
        """
        Get all handlers that can be triggered by comment (e.g. `/packit build`) or check rerun.

        For comment events we want to get handlers mapped to comment commands. For check rerun
        event we want to get handlers mapped to check name job.
        These two sets of handlers are mutually exclusive.

        Returns:
            Set of handlers that are triggered by a comment or check rerun job.
        """
        handlers_triggered_by_job = None

        if isinstance(self.event, abstract.comment.CommentEvent):
            arguments = parse_comment(
                self.event.comment,
                self.service_config.comment_command_prefix,
            )

            command = arguments.command
            handlers_triggered_by_job = get_handlers_for_command(command)

            if handlers_triggered_by_job and not isinstance(
                self.event,
                (pagure.pr.Comment, abstract.comment.Commit),
            ):
                self.event.comment_object.add_reaction(COMMENT_REACTION)

        if isinstance(self.event, github.check.Rerun):
            handlers_triggered_by_job = get_handlers_for_check_rerun(
                self.event.check_name_job,
            )

        return handlers_triggered_by_job

    def get_handlers_for_event(
        self,
        monorepo_package: Optional[str] = None,
    ) -> set[type[JobHandler]]:
        """
        Get all handlers that we need to run for the given event.

        We need to return all handler classes that:
        - can react to the given event **and**
        - are configured in the package_config (either directly or as a required job)

        Examples of the matching can be found in the tests:
        ./tests/unit/test_jobs.py:test_get_handlers_for_event

        Returns:
            Set of handler instances that we need to run for given event and user configuration.
        """

        jobs_matching_trigger = self.get_jobs_matching_event(monorepo_package)

        handlers_triggered_by_job = self.get_handlers_for_comment_and_rerun_event()

        matching_handlers: set[type[JobHandler]] = set()
        for job in jobs_matching_trigger:
            for handler in (
                MAP_JOB_TYPE_TO_HANDLER[job.type] | MAP_REQUIRED_JOB_TYPE_TO_HANDLER[job.type]
            ):
                if self.is_handler_matching_the_event(
                    handler=handler,
                    allowed_handlers=handlers_triggered_by_job,
                ):
                    matching_handlers.add(handler)

        if not matching_handlers:
            logger.debug(
                f"We did not find any handler for a following event:\n{self.event.event_type()}",
            )

        logger.debug(f"Matching handlers: {matching_handlers}")

        return matching_handlers

    def is_handler_matching_the_event(
        self,
        handler: type[JobHandler],
        allowed_handlers: set[type[JobHandler]],
    ) -> bool:
        """
        Decides whether handler matches to comment or check rerun job and given event
        supports handler.

        Args:
            handler: Handler which we are observing whether it is matching to job.
            allowed_handlers: Set of handlers that are triggered by a comment or check rerun
             job.

        Returns:
            `True` if handler matches the event, `False` otherwise.
        """
        handler_matches_to_comment_or_check_rerun_job = (
            allowed_handlers is None or handler in allowed_handlers
        )

        return (
            isinstance(self.event, tuple(SUPPORTED_EVENTS_FOR_HANDLER[handler]))
            and handler_matches_to_comment_or_check_rerun_job
        )

    def get_config_for_handler_kls(
        self,
        handler_kls: type[JobHandler],
        monorepo_package: Optional[str] = None,
    ) -> list[JobConfig]:
        """
        Get a list of JobConfigs relevant to event and the handler class.

        We need to find all job configurations that:
        - can be run by the given handler class, **and**
        - that matches the trigger of the event

        If there is no matching job-config found, we will pick the ones that are required.
        e.g.: For build handler, you can pick the test config since tests require the build.

        Examples of the matching can be found in the tests:
        ./tests/unit/test_jobs.py:test_get_config_for_handler_kls

        Args:
            handler_kls: class that will use the JobConfig

        Returns:
            List of JobConfigs relevant to the given handler and event
            preserving the order in the config.
        """
        jobs_matching_trigger: list[JobConfig] = self.get_jobs_matching_event(monorepo_package)

        matching_jobs: list[JobConfig] = [
            job for job in jobs_matching_trigger if handler_kls in MAP_JOB_TYPE_TO_HANDLER[job.type]
        ]

        if not matching_jobs:
            logger.debug(
                "No config found, let's see the jobs that requires this handler.",
            )
            matching_jobs = [
                job
                for job in jobs_matching_trigger
                if handler_kls in MAP_REQUIRED_JOB_TYPE_TO_HANDLER[job.type]
            ]

        if not matching_jobs:
            logger.warning(
                f"We did not find any config for {handler_kls} and a following event:\n"
                f"{self.event.event_type()}",
            )

        logger.debug(
            "Jobs matching %s: %s",
            handler_kls.__qualname__,
            [str(j) for j in matching_jobs],
        )

        return matching_jobs

    def push_statuses_metrics(
        self,
        statuses_check_feedback: list[datetime],
    ) -> None:
        """
        Push the metrics about the time of setting initial statuses for the first and last check.

        Args:
            statuses_check_feedback: A list of times it takes to set every initial status check.
        """
        if not statuses_check_feedback:
            # no feedback, nothing to do
            return

        response_time = elapsed_seconds(
            begin=self.event.created_at,
            end=statuses_check_feedback[0],
        )
        logger.debug(
            f"Reporting first initial status check time: {response_time} seconds.",
        )
        self.pushgateway.first_initial_status_time.observe(response_time)
        if response_time > 25:
            self.pushgateway.no_status_after_25_s.inc()
        if response_time > 15:
            # https://github.com/packit/packit-service/issues/1728
            # we need more info why this has happened
            logger.debug(f"Event dict: {self.event}.")
            logger.error(
                f"Event {self.event.event_type()} took more than 15s to process.",
            )

        response_time = elapsed_seconds(
            begin=self.event.created_at,
            end=statuses_check_feedback[-1],
        )
        logger.debug(
            f"Reporting last initial status check time: {response_time} seconds.",
        )
        self.pushgateway.last_initial_status_time.observe(response_time)

    def push_copr_metrics(
        self,
        handler_kls: type[JobHandler],
        built_targets: int = 0,
    ) -> None:
        """
        Push metrics about queued Copr builds.

        Args:
            handler_kls: The class for the Handler that will handle the job.
            built_targets: Number of build targets in case of CoprBuildHandler.
        """
        # TODO(Friday): Do an early-return, but fix »all« **36** f-ing tests
        if handler_kls == CoprBuildHandler and built_targets:
            # handler wasn't matched or 0 targets were built
            self.pushgateway.copr_builds_queued.inc(built_targets)

    def is_fas_verification_comment(self, comment: str) -> bool:
        """
        Checks whether the comment contains Packit verification command:
        `/packit(-stg) verify-fas`

        Args:
            comment: Comment to be checked.

        Returns:
            `True`, if is verification comment, `False` otherwise.
        """
        command = get_packit_commands_from_comment(
            comment,
            self.service_config.comment_command_prefix,
        )

        return bool(command and command[0] == PACKIT_VERIFY_FAS_COMMAND)

    def is_help_comment(self, comment: str) -> bool:
        """
        Checks whether the comment contains Packit help command:
        `/packit(-stg) | /packit-ci(-stg) help`

        Args:
            comment: Comment to be checked.

        Returns:
            `True`, if is help comment, `False` otherwise.
        """
        command = get_packit_commands_from_comment(
            comment,
            self.service_config.comment_command_prefix,
        )

        return bool(command and command[0] == PACKIT_HELP_COMMAND)

    def report_task_accepted_for_downstream_retrigger_comments(
        self,
        handler_kls: type[JobHandler],
    ):
        """
        For dist-git PR comment events/ issue comment events in issue_repository,
        report that the task was accepted and provide handler specific info.
        """
        if not isinstance(
            self.event,
            (abstract.comment.Issue, abstract.comment.PullRequest),
        ):
            logger.debug(
                "Not a comment event, not reporting task was accepted via comment.",
            )
            return

        message = (
            f"{TASK_ACCEPTED} "
            f"{handler_kls.get_handler_specific_task_accepted_message(self.service_config)}"
        )

        if isinstance(self.event, abstract.comment.PullRequest):
            self.event.pull_request_object.comment(message)
        if isinstance(self.event, abstract.comment.Issue):
            self.event.issue_object.comment(message)
