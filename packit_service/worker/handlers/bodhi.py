# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
This file defines classes for job handlers related to Bodhi
"""
import abc
import logging
from datetime import datetime
from os import getenv
from typing import List, Tuple, Type, Optional

from celery import Task

from packit.config import JobConfig, JobType, PackageConfig, Deployment
from packit.exceptions import PackitException
from packit.utils.koji_helper import KojiHelper
from specfile.utils import NEVR
from packit_service.config import ServiceConfig
from packit_service.constants import (
    MSG_RETRIGGER,
    MSG_GET_IN_TOUCH,
    MSG_DOWNSTREAM_JOB_ERROR_HEADER,
    DEFAULT_RETRY_BACKOFF,
)
from packit_service.models import (
    BodhiUpdateGroupModel,
    PipelineModel,
    BodhiUpdateTargetModel,
    KojiBuildTargetModel,
    SidetagGroupModel,
    SidetagModel,
)
from packit_service.worker.checker.abstract import Checker
from packit_service.worker.checker.bodhi import (
    IsAuthorAPackager,
    HasIssueCommenterRetriggeringPermissions,
    IsKojiBuildCompleteAndBranchConfiguredCheckEvent,
    IsKojiBuildCompleteAndBranchConfiguredCheckSidetag,
    IsKojiBuildCompleteAndBranchConfiguredCheckService,
    IsKojiBuildOwnerMatchingConfiguration,
)
from packit_service.worker.events import (
    PullRequestCommentPagureEvent,
    IssueCommentEvent,
    IssueCommentGitlabEvent,
)
from packit_service.worker.events.koji import KojiBuildEvent
from packit_service.worker.handlers.abstract import (
    TaskName,
    configured_as,
    reacts_to,
    RetriableJobHandler,
    run_for_comment,
)
from packit_service.worker.handlers.mixin import (
    GetKojiBuildDataFromKojiServiceMultipleBranches,
    GetKojiBuildDataFromKojiBuildEventMixin,
    GetKojiBuildDataFromKojiBuildTagEventMixin,
    GetKojiBuildDataFromKojiServiceMixin,
    GetKojiBuildEventMixin,
    GetKojiBuildData,
)
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


class SidetagHelper:
    _koji_helper: Optional[KojiHelper] = None
    _sidetag_group: Optional[SidetagGroupModel] = None

    def __init__(self, job_config: JobConfig) -> None:
        self.job_config = job_config

    @property
    def koji_helper(self) -> KojiHelper:
        if not self._koji_helper:
            self._koji_helper = KojiHelper()
        return self._koji_helper

    @property
    def sidetag_group(self) -> SidetagGroupModel:
        if not self._sidetag_group:
            self._sidetag_group = SidetagGroupModel.get_by_name(
                self.job_config.sidetag_group
            )
        return self._sidetag_group

    def get_builds_in_sidetag(self, dist_git_branch: str) -> Tuple[str, List[str]]:
        """
        Gets the latest builds of required packages from a sidetag identified
        by the configured sidetag group and the specified dist-git branch.

        Args:
            dist_git_branch: dist-git branch.

        Returns:
            A tuple where the first element is the name of the sidetag
            and the second element is a list of NVRs.

        Raises:
            PackitException if a sidetag was not found or if job dependencies
            are not satisfied.
        """
        sidetag = self.sidetag_group.get_sidetag_by_target(dist_git_branch)
        if not sidetag:
            raise PackitException(
                f"No sidetag found for {self.sidetag_group.name} and {dist_git_branch}"
            )

        builds = self.koji_helper.get_builds_in_tag(sidetag.koji_name)
        tagged_packages = {b["package_name"] for b in builds}

        # check if dependencies are satisfied within the sidetag
        dependencies = set(self.job_config.dependencies or [])
        dependencies.add(self.job_config.downstream_package_name)  # include self
        if not dependencies <= tagged_packages:
            missing = dependencies - tagged_packages
            raise PackitException(f"Missing dependencies for Bodhi update: {missing}")

        if not (candidate_tag := self.koji_helper.get_candidate_tag(dist_git_branch)):
            raise PackitException(f"Failed to get candidate tag for {dist_git_branch}")

        stable_tags = self.koji_helper.get_stable_tags(candidate_tag)

        nvrs = []
        for package in dependencies:
            latest_stable_nvr = max(
                (
                    self.koji_helper.get_latest_nvr_in_tag(package=package, tag=t)
                    for t in stable_tags + [candidate_tag]
                ),
                key=lambda nvr: NEVR.from_string(nvr),
            )
            latest_build_in_sidetag = max(
                (b for b in builds if b["package_name"] == package),
                key=lambda b: NEVR.from_string(b["nvr"]),
            )
            # exclude NVRs that are already in stable or candidate tags - if a build
            # has been manually tagged into the sidetag to satisfy dependencies,
            # we don't want it in the update
            if NEVR.from_string(latest_build_in_sidetag["nvr"]) > NEVR.from_string(
                latest_stable_nvr
            ):
                nvrs.append(latest_build_in_sidetag["nvr"])

        return sidetag.koji_name, nvrs

    def remove_sidetag(self, koji_name: str) -> None:
        """Removes the specified sidetag."""
        self.koji_helper.remove_sidetag(koji_name)
        if sidetag := SidetagModel.get_by_koji_name(koji_name):
            sidetag.delete()


class BodhiUpdateHandler(
    RetriableJobHandler, PackitAPIWithDownstreamMixin, GetKojiBuildData
):
    topic = "org.fedoraproject.prod.buildsys.build.state.change"

    _sidetag_helper: Optional[SidetagHelper] = None

    @property
    def sidetag_helper(self) -> SidetagHelper:
        if not self._sidetag_helper:
            self._sidetag_helper = SidetagHelper(self.job_config)
        return self._sidetag_helper

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
                logger.debug(
                    f"Create update for dist-git branch: {target_model.target} "
                    f"and nvrs: {target_model.koji_nvrs}"
                    + (
                        f" from sidetag: {target_model.sidetag}."
                        if target_model.sidetag
                        else "."
                    )
                )
                result = self.packit_api.create_update(
                    dist_git_branch=target_model.target,
                    update_type="enhancement",
                    koji_builds=target_model.koji_nvrs.split(),  # it accepts NVRs, not build IDs
                    sidetag=target_model.sidetag,
                )
                if not result:
                    # update was already created
                    target_model.set_status("skipped")
                    continue

                if target_model.sidetag:
                    # remove the sidetag now; Bodhi would remove it for us
                    # when the update hits stable, but we would be blocked
                    # from creating new updates until that happens
                    logger.debug(f"Removing sidetag {target_model.sidetag}")
                    # we need Kerberos ticket to remove a sidetag
                    self.packit_api.init_kerberos_ticket()
                    self.sidetag_helper.remove_sidetag(target_model.sidetag)

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
                        "Celery task will be retried. User will not be notified about the failure."
                    )
                    retry_backoff = int(
                        getenv("CELERY_RETRY_BACKOFF", DEFAULT_RETRY_BACKOFF)
                    )
                    delay = retry_backoff * 2**self.celery_task.retries
                    self.celery_task.task.retry(exc=ex, countdown=delay, kwargs=kargs)
                    return TaskResults(
                        success=True,
                        details={
                            "msg": f"There was an error: {ex}. Task will be retried."
                        },
                    )
                else:
                    error = str(ex)
                    errors[target_model.target] = error

                    target_model.set_status("error")
                    target_model.set_data({"error": error})

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
            self.data.db_project_event, package_name=self.get_package_name()
        )
        group = BodhiUpdateGroupModel.create(run_model)

        for koji_build_data in self:
            if self.job_config.sidetag_group:
                sidetag, builds = self.sidetag_helper.get_builds_in_sidetag(
                    koji_build_data.dist_git_branch
                )
            else:
                sidetag = builds = None

            BodhiUpdateTargetModel.create(
                target=koji_build_data.dist_git_branch,
                koji_nvrs=koji_build_data.nvr if not builds else " ".join(builds),
                sidetag=sidetag,
                status="queued",
                bodhi_update_group=group,
            )

        return group

    @staticmethod
    def get_handler_specific_task_accepted_message(
        service_config: ServiceConfig,
    ) -> str:
        user = (
            "packit" if service_config.deployment == Deployment.prod else "packit-stg"
        )
        return (
            "You can check the recent Bodhi update submissions of Packit "
            f"in [Packit dashboard]({service_config.dashboard_url}/jobs/bodhi-updates). "
            f"You can also check the recent Bodhi update activity of `{user}` in "
            f"[the Bodhi interface](https://bodhi.fedoraproject.org/users/{user})."
            f"{DistgitAnnouncement.get_comment_footer_with_announcement_if_present()}"
        )

    def report_in_issue_repository(self, errors: dict[str, str]) -> None:
        body = MSG_DOWNSTREAM_JOB_ERROR_HEADER.format(
            object="Bodhi update", dist_git_url=self.packit_api.dg.local_project.git_url
        )
        for branch, ex in errors.items():
            body += f"| `{branch}` | ```{ex}``` |\n"

        msg_retrigger = MSG_RETRIGGER.format(
            job="update",
            command="create-update",
            place="issue",
            packit_comment_command_prefix=self.service_config.comment_command_prefix,
        )
        body_msg = (
            f"{body}\n{self.get_trigger_type_description()}\n\n"
            f"{msg_retrigger}{MSG_GET_IN_TOUCH}\n"
        )

        body_msg = update_message_with_configured_failure_comment_message(
            body_msg, self.job_config
        )

        report_in_issue_repository(
            issue_repository=self.job_config.issue_repository,
            service_config=self.service_config,
            title="Fedora Bodhi update failed to be created",
            message=body_msg,
            comment_to_existing=body_msg,
        )


@configured_as(job_type=JobType.bodhi_update)
@reacts_to(event=KojiBuildEvent)
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
    def get_checkers() -> Tuple[Type[Checker], ...]:
        """We react only on finished builds (=KojiBuildState.complete)
        and configured branches.
        """
        logger.debug("Bodhi update will be re-triggered via dist-git PR comment.")
        return (
            IsKojiBuildCompleteAndBranchConfiguredCheckEvent,
            IsKojiBuildOwnerMatchingConfiguration,
        )

    def get_trigger_type_description(self) -> str:
        for koji_build_data in self:
            return (
                f"Fedora Bodhi update was triggered by "
                f"Koji build {koji_build_data.nvr}."
            )
        return ""

    def _get_or_create_bodhi_update_group_model(self) -> BodhiUpdateGroupModel:
        if self._bodhi_update_group_model_id is not None:
            return BodhiUpdateGroupModel.get_by_id(self._bodhi_update_group_model_id)

        group = None
        for koji_build_data in self:
            koji_build_target = KojiBuildTargetModel.get_by_task_id(
                koji_build_data.task_id
            )
            if koji_build_target:
                run_model = koji_build_target.group_of_targets.runs[-1]
            # this should not happen as we react only to Koji builds done by us,
            # but let's cover the case
            else:
                run_model = PipelineModel.create(
                    self.data.db_project_event, package_name=self.get_package_name()
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
    def get_checkers() -> Tuple[Type[Checker], ...]:
        """We react only on finished builds (=KojiBuildState.complete)
        and configured branches.
        """
        logger.debug("Bodhi update will be re-triggered via dist-git PR comment.")
        return (IsKojiBuildCompleteAndBranchConfiguredCheckSidetag,)

    def get_trigger_type_description(self) -> str:
        for koji_build_data in self:
            return (
                f"Fedora Bodhi update was triggered by "
                f"Koji build {koji_build_data.nvr}."
            )
        return ""


@configured_as(job_type=JobType.bodhi_update)
@reacts_to(event=PullRequestCommentPagureEvent)
@run_for_comment(command="create-update")
class RetriggerBodhiUpdateHandler(
    BodhiUpdateHandler, GetKojiBuildDataFromKojiServiceMixin
):
    """
    This handler can re-trigger a bodhi update if any successful Koji build.
    """

    task_name = TaskName.retrigger_bodhi_update

    @staticmethod
    def get_checkers() -> Tuple[Type[Checker], ...]:
        """We react only on finished builds (=KojiBuildState.complete)
        and configured branches.
        """
        logger.debug("Bodhi update will be re-triggered via dist-git PR comment.")
        return (
            IsAuthorAPackager,
            HasIssueCommenterRetriggeringPermissions,
            IsKojiBuildCompleteAndBranchConfiguredCheckService,
        )

    def get_trigger_type_description(self) -> str:
        return (
            f"Fedora Bodhi update was re-triggered "
            f"by comment in dist-git PR with id {self.data.pr_id}."
        )


@configured_as(job_type=JobType.bodhi_update)
@reacts_to(event=IssueCommentEvent)
@reacts_to(event=IssueCommentGitlabEvent)
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
    def get_checkers() -> Tuple[Type[Checker], ...]:
        """We react only on finished builds (=KojiBuildState.complete)
        and configured branches.
        """
        logger.debug("Bodhi update will be re-triggered via dist-git PR comment.")
        return (HasIssueCommenterRetriggeringPermissions,)

    def get_trigger_type_description(self) -> str:
        return (
            f"Fedora Bodhi update was re-triggered by "
            f"comment in issue {self.data.issue_id}."
        )
