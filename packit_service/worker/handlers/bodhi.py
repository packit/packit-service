# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
This file defines classes for job handlers related to Bodhi
"""

import abc
import logging
from datetime import datetime
from os import getenv
from typing import Optional

from celery import Task
from packit.config import Deployment, JobConfig, JobType, PackageConfig
from packit.exceptions import PackitException

from packit_service.config import ServiceConfig
from packit_service.constants import (
    DEFAULT_RETRY_BACKOFF,
    MSG_DOWNSTREAM_JOB_ERROR_HEADER,
    MSG_DOWNSTREAM_JOB_ERROR_ROW,
    MSG_GET_IN_TOUCH,
    MSG_RETRIGGER,
)
from packit_service.events import (
    github,
    gitlab,
    koji,
    pagure,
)
from packit_service.models import (
    BodhiUpdateGroupModel,
    BodhiUpdateTargetModel,
    KojiBuildTargetModel,
    PipelineModel,
)
from packit_service.service.urls import get_bodhi_update_info_url
from packit_service.worker.checker.abstract import Checker
from packit_service.worker.checker.bodhi import (
    HasIssueCommenterRetriggeringPermissions,
    IsAuthorAPackager,
    IsKojiBuildCompleteAndBranchConfiguredCheckEvent,
    IsKojiBuildCompleteAndBranchConfiguredCheckService,
    IsKojiBuildCompleteAndBranchConfiguredCheckSidetag,
    IsKojiBuildOwnerMatchingConfiguration,
)
from packit_service.worker.checker.run_condition import IsRunConditionSatisfied
from packit_service.worker.handlers.abstract import (
    RetriableJobHandler,
    TaskName,
    configured_as,
    reacts_to,
    run_for_comment,
)
from packit_service.worker.handlers.mixin import (
    GetKojiBuildData,
    GetKojiBuildDataFromKojiBuildEventMixin,
    GetKojiBuildDataFromKojiBuildTagEventMixin,
    GetKojiBuildDataFromKojiServiceMixin,
    GetKojiBuildDataFromKojiServiceMultipleBranches,
    GetKojiBuildEventMixin,
)
from packit_service.worker.helpers.sidetag import SidetagHelper
from packit_service.worker.mixin import (
    ConfigFromDistGitUrlMixin,
    GetBranchesFromIssueMixin,
    PackitAPIWithDownstreamMixin,
)
from packit_service.worker.reporting import (
    report_in_issue_repository,
    update_message_with_configured_failure_comment_message,
)
from packit_service.worker.reporting.news import DistgitAnnouncement
from packit_service.worker.result import TaskResults

logger = logging.getLogger(__name__)


class BodhiUpdateHandler(
    RetriableJobHandler,
    PackitAPIWithDownstreamMixin,
    GetKojiBuildData,
):
    topic = "org.fedoraproject.prod.buildsys.build.state.change"

    def __init__(
        self,
        package_config: PackageConfig,
        job_config: JobConfig,
        event: dict,
        celery_task: Task,
        bodhi_update_group_model_id: Optional[int] = None,
    ):
        super().__init__(
            package_config=package_config,
            job_config=job_config,
            event=event,
            celery_task=celery_task,
        )
        self._bodhi_update_group_model_id = bodhi_update_group_model_id

    def run(self) -> TaskResults:
        try:
            group = self._get_or_create_bodhi_update_group_model()
        except PackitException as ex:
            logger.debug(f"Bodhi update failed to be created: {ex}")
            return TaskResults(success=True, details={})

        errors = {}
        for target_model in group.grouped_targets:
            try:
                existing_alias = None
                # get update alias from previous run(s) from the same sidetag (if any)
                if target_model.sidetag and (
                    existing_model := BodhiUpdateTargetModel.get_last_successful_by_sidetag(
                        target_model.sidetag,
                    )
                ):
                    existing_alias = existing_model.alias
                    if set(target_model.koji_nvrs.split()) == set(
                        existing_model.koji_nvrs.split(),
                    ):
                        logger.info("No changes, skipping Bodhi update edit")
                        target_model.set_status("skipped")
                        continue

                if not existing_alias:
                    # avoid creating another update containing the same build - Bodhi shouldn't
                    # allow it anyway but there is a race condition that makes it possible
                    existing_models = (
                        BodhiUpdateTargetModel.get_all_successful_or_in_progress_by_nvrs(
                            target_model.koji_nvrs,
                        )
                    )
                    if existing_models - {target_model}:
                        logger.info(
                            "Bodhi update containing one or more builds from "
                            f"{{{target_model.koji_nvrs}}} already exists, skipping",
                        )
                        target_model.set_status("skipped")
                        continue

                logger.debug(
                    (f"Edit update {existing_alias} " if existing_alias else "Create update ")
                    + f"for dist-git branch: {target_model.target} "
                    f"and nvrs: {target_model.koji_nvrs}"
                    + (f" from sidetag: {target_model.sidetag}." if target_model.sidetag else "."),
                )
                result = self.packit_api.create_update(
                    dist_git_branch=target_model.target,
                    update_type="enhancement",
                    koji_builds=target_model.koji_nvrs.split(),  # it accepts NVRs, not build IDs
                    sidetag=target_model.sidetag,
                    alias=existing_alias,
                )
                if not result:
                    # update was already created
                    target_model.set_status("skipped")
                    continue

                alias, url = result
                target_model.set_status("success")
                target_model.set_alias(alias)
                target_model.set_web_url(url)
                target_model.set_update_creation_time(datetime.now())

            except PackitException as ex:
                logger.debug(f"Bodhi update failed to be created: {ex}")

                if self.celery_task and not self.celery_task.is_last_try():
                    kargs = self.celery_task.task.request.kwargs.copy()
                    kargs["bodhi_update_group_model_id"] = group.id
                    for model in group.grouped_targets:
                        model.set_status("retry")

                    logger.debug(
                        "Celery task will be retried. User will not be notified about the failure.",
                    )
                    retry_backoff = int(
                        getenv("CELERY_RETRY_BACKOFF", DEFAULT_RETRY_BACKOFF),
                    )
                    delay = retry_backoff * 2**self.celery_task.retries
                    self.celery_task.task.retry(exc=ex, countdown=delay, kwargs=kargs)
                    return TaskResults(
                        success=True,
                        details={
                            "msg": f"There was an error: {ex}. Task will be retried.",
                        },
                    )

                error = str(ex)
                errors[target_model.target] = get_bodhi_update_info_url(target_model.id)

                target_model.set_status("error")
                target_model.set_data({"error": error})

            except Exception as ex:
                if self.celery_task and not self.celery_task.is_last_try():
                    target_model.set_status("retry")
                    raise

                error = f"Internal error, please contact us: {ex}"
                errors[target_model.target] = get_bodhi_update_info_url(target_model.id)

                target_model.set_status("error")
                target_model.set_data({"error": error})
                raise

        if errors:
            self.report_in_issue_repository(errors=errors)

        # `success=True` for all known errors
        # (=The task was correctly processed.)
        # Sentry issue will be created otherwise.
        return TaskResults(success=True, details={})

    @abc.abstractmethod
    def get_trigger_type_description(self) -> str:
        """Describe the user's action which triggered the Bodhi update"""

    def _get_or_create_bodhi_update_group_model(self) -> BodhiUpdateGroupModel:
        """
        Get or create the group model with target models.

        For retriggering, use this method - create pipeline model with corresponding
        trigger (issue/dist-git PR comment), for updates triggered by
        completed Koji build, obtain the pipeline model from Koji build in our DB
        (subclass method).

        """
        if self._bodhi_update_group_model_id is not None:
            return BodhiUpdateGroupModel.get_by_id(self._bodhi_update_group_model_id)

        run_model = PipelineModel.create(
            self.data.db_project_event,
            package_name=self.get_package_name(),
        )
        group = BodhiUpdateGroupModel.create(run_model)

        for koji_build_data in self:
            sidetag = builds = None
            if self.job_config.sidetag_group:
                sidetag = SidetagHelper.get_sidetag(
                    self.job_config.sidetag_group,
                    koji_build_data.dist_git_branch,
                )
                # check if dependencies are satisfied within the sidetag
                dependencies = set(self.job_config.dependencies or [])
                dependencies.add(
                    self.job_config.downstream_package_name,  # include self
                )
                if missing_dependencies := sidetag.get_missing_dependencies(
                    dependencies,
                ):
                    raise PackitException(
                        f"Missing dependencies for Bodhi update: {missing_dependencies}",
                    )
                builds = " ".join(
                    str(b) for b in sidetag.get_builds_suitable_for_update(dependencies)
                )

            BodhiUpdateTargetModel.create(
                target=koji_build_data.dist_git_branch,
                koji_nvrs=builds if builds else koji_build_data.nvr,
                sidetag=sidetag.koji_name if sidetag else None,
                status="queued",
                bodhi_update_group=group,
            )

        return group

    @staticmethod
    def get_handler_specific_task_accepted_message(
        service_config: ServiceConfig,
    ) -> str:
        user = "packit" if service_config.deployment == Deployment.prod else "packit-stg"
        return (
            "You can check the recent Bodhi update submissions of Packit "
            f"in [Packit dashboard]({service_config.dashboard_url}/jobs/bodhi-updates). "
            f"You can also check the recent Bodhi update activity of `{user}` in "
            f"[the Bodhi interface](https://bodhi.fedoraproject.org/users/{user})."
            f"{DistgitAnnouncement.get_comment_footer_with_announcement_if_present()}"
        )

    def report_in_issue_repository(self, errors: dict[str, str]) -> None:
        body = MSG_DOWNSTREAM_JOB_ERROR_HEADER.format(
            object="Bodhi update",
            dist_git_url=self.packit_api.dg.local_project.git_url,
        )
        for branch, url in errors.items():
            body += MSG_DOWNSTREAM_JOB_ERROR_ROW.format(branch=branch, url=url)
        body += "</table>\n"

        msg_retrigger = MSG_RETRIGGER.format(
            job="update",
            command="create-update",
            place="issue",
            packit_comment_command_prefix=self.service_config.comment_command_prefix,
        )
        body_msg = (
            f"{body}\n{self.get_trigger_type_description()}\n\n{msg_retrigger}{MSG_GET_IN_TOUCH}\n"
        )

        body_msg = update_message_with_configured_failure_comment_message(
            body_msg,
            self.job_config,
        )

        report_in_issue_repository(
            issue_repository=self.job_config.issue_repository,
            service_config=self.service_config,
            title="Fedora Bodhi update failed to be created",
            message=body_msg,
            comment_to_existing=body_msg,
        )


@configured_as(job_type=JobType.bodhi_update)
@reacts_to(event=koji.result.Build)
class CreateBodhiUpdateHandler(
    BodhiUpdateHandler,
    RetriableJobHandler,
    GetKojiBuildEventMixin,
    GetKojiBuildDataFromKojiBuildEventMixin,
):
    """
    This handler can create a bodhi update for successful Koji builds.
    """

    task_name = TaskName.bodhi_update

    @staticmethod
    def get_checkers() -> tuple[type[Checker], ...]:
        """We react only on finished builds (=KojiBuildState.complete)
        and configured branches.
        """
        logger.debug("Bodhi update will be re-triggered via dist-git PR comment.")
        return (
            IsKojiBuildCompleteAndBranchConfiguredCheckEvent,
            IsKojiBuildOwnerMatchingConfiguration,
            IsRunConditionSatisfied,
        )

    def get_trigger_type_description(self) -> str:
        for koji_build_data in self:
            return f"Fedora Bodhi update was triggered by Koji build {koji_build_data.nvr}."
        return ""

    def _get_or_create_bodhi_update_group_model(self) -> BodhiUpdateGroupModel:
        if self._bodhi_update_group_model_id is not None:
            return BodhiUpdateGroupModel.get_by_id(self._bodhi_update_group_model_id)

        group = None
        for koji_build_data in self:
            koji_build_target = KojiBuildTargetModel.get_by_task_id(
                koji_build_data.task_id,
            )
            if koji_build_target:
                run_model = koji_build_target.group_of_targets.runs[-1]
            # this should not happen as we react only to Koji builds done by us,
            # but let's cover the case
            else:
                run_model = PipelineModel.create(
                    self.data.db_project_event,
                    package_name=self.get_package_name(),
                )

            group = BodhiUpdateGroupModel.create(run_model)
            BodhiUpdateTargetModel.create(
                target=koji_build_data.dist_git_branch,
                koji_nvrs=koji_build_data.nvr,
                status="queued",
                bodhi_update_group=group,
            )

        return group


@configured_as(job_type=JobType.bodhi_update)
class BodhiUpdateFromSidetagHandler(
    BodhiUpdateHandler,
    RetriableJobHandler,
    GetKojiBuildDataFromKojiBuildTagEventMixin,
):
    """
    This handler can create a bodhi update from a sidetag.
    """

    task_name = TaskName.bodhi_update_from_sidetag

    @staticmethod
    def get_checkers() -> tuple[type[Checker], ...]:
        """We react only on finished builds (=KojiBuildState.complete)
        and configured branches.
        """
        logger.debug("Bodhi update will be re-triggered via dist-git PR comment.")
        return (
            IsKojiBuildCompleteAndBranchConfiguredCheckSidetag,
            IsRunConditionSatisfied,
        )

    def get_trigger_type_description(self) -> str:
        for koji_build_data in self:
            return f"Fedora Bodhi update was triggered by Koji build {koji_build_data.nvr}."
        return ""


@configured_as(job_type=JobType.bodhi_update)
@reacts_to(event=pagure.pr.Comment)
@run_for_comment(command="create-update")
class RetriggerBodhiUpdateHandler(
    BodhiUpdateHandler,
    GetKojiBuildDataFromKojiServiceMixin,
):
    """
    This handler can re-trigger a bodhi update if any successful Koji build.
    """

    task_name = TaskName.retrigger_bodhi_update

    @staticmethod
    def get_checkers() -> tuple[type[Checker], ...]:
        """We react only on finished builds (=KojiBuildState.complete)
        and configured branches.
        """
        logger.debug("Bodhi update will be re-triggered via dist-git PR comment.")
        return (
            IsAuthorAPackager,
            HasIssueCommenterRetriggeringPermissions,
            IsKojiBuildCompleteAndBranchConfiguredCheckService,
            IsRunConditionSatisfied,
        )

    def get_trigger_type_description(self) -> str:
        return (
            f"Fedora Bodhi update was re-triggered "
            f"by comment in dist-git PR with id {self.data.pr_id}."
        )


@configured_as(job_type=JobType.bodhi_update)
@reacts_to(event=github.issue.Comment)
@reacts_to(event=gitlab.issue.Comment)
@run_for_comment(command="create-update")
class IssueCommentRetriggerBodhiUpdateHandler(
    BodhiUpdateHandler,
    ConfigFromDistGitUrlMixin,
    GetBranchesFromIssueMixin,
    GetKojiBuildDataFromKojiServiceMultipleBranches,
):
    """
    This handler can re-trigger a bodhi update if any successful Koji build.
    """

    task_name = TaskName.issue_comment_retrigger_bodhi_update

    @staticmethod
    def get_checkers() -> tuple[type[Checker], ...]:
        """We react only on finished builds (=KojiBuildState.complete)
        and configured branches.
        """
        logger.debug("Bodhi update will be re-triggered via dist-git PR comment.")
        return (
            HasIssueCommenterRetriggeringPermissions,
            IsRunConditionSatisfied,
        )

    def get_trigger_type_description(self) -> str:
        return f"Fedora Bodhi update was re-triggered by comment in issue {self.data.issue_id}."
