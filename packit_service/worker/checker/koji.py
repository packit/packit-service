# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging

from ogr.services.pagure import PagureProject

from packit_service.constants import (
    KOJI_PRODUCTION_BUILDS_ISSUE,
    PERMISSIONS_ERROR_WRITE_OR_ADMIN,
)
from packit_service.events import github, gitlab
from packit_service.models import SidetagModel
from packit_service.worker.checker.abstract import Checker
from packit_service.worker.handlers.mixin import GetKojiBuildJobHelperMixin
from packit_service.worker.reporting import BaseCommitStatus

logger = logging.getLogger(__name__)


class IsJobConfigTriggerMatching(Checker, GetKojiBuildJobHelperMixin):
    def pre_check(self) -> bool:
        return self.koji_build_helper.is_job_config_trigger_matching(self.job_config)


class IsUpstreamKojiScratchBuild(Checker, GetKojiBuildJobHelperMixin):
    def pre_check(self) -> bool:
        return not isinstance(self.koji_build_helper.project, PagureProject)


class PermissionOnKoji(Checker, GetKojiBuildJobHelperMixin):
    def pre_check(self) -> bool:
        if (
            self.data.event_type == gitlab.mr.Action.event_type()
            and self.data.event_dict["action"] == gitlab.enums.Action.closed.value
        ):
            # Not interested in closed merge requests
            return False

        if self.data.event_type in (
            github.pr.Action.event_type(),
            gitlab.mr.Action.event_type(),
        ):
            user_can_merge_pr = self.project.can_merge_pr(self.data.actor)
            if not (user_can_merge_pr or self.data.actor in self.service_config.admins):
                self.koji_build_helper.report_status_to_all(
                    description=PERMISSIONS_ERROR_WRITE_OR_ADMIN,
                    state=BaseCommitStatus.neutral,
                )
                return False

        if not self.koji_build_helper.is_scratch:
            msg = "Non-scratch builds not possible from upstream."
            self.koji_build_helper.report_status_to_all(
                description=msg,
                state=BaseCommitStatus.neutral,
                url=KOJI_PRODUCTION_BUILDS_ISSUE,
            )
            return False

        return True


class SidetagExists(Checker):
    def pre_check(self) -> bool:
        return SidetagModel.get_by_koji_name(self.data.tag_name) is not None
