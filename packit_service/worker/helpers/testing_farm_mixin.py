# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
Mixins for Testing Farm job helpers.
Separated from handlers/mixin.py to avoid circular imports.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol

from packit.config import JobConfig, PackageConfig

from packit_service.worker.celery_task import CeleryTask
from packit_service.worker.handlers.mixin import (
    GetCoprBuildMixin,
    GetKojiBuildFromTaskOrPullRequestMixin,
)
from packit_service.worker.helpers.testing_farm import (
    DownstreamTestingFarmJobHelper,
    TestingFarmJobHelper,
)
from packit_service.worker.mixin import ConfigFromEventMixin


class GetTestingFarmJobHelper(Protocol):
    package_config: PackageConfig
    job_config: JobConfig
    celery_task: CeleryTask | None = None

    @property
    @abstractmethod
    def testing_farm_job_helper(self) -> TestingFarmJobHelper: ...


class GetTestingFarmJobHelperMixin(
    GetTestingFarmJobHelper,
    GetCoprBuildMixin,
    ConfigFromEventMixin,
):
    _testing_farm_job_helper: TestingFarmJobHelper | None = None

    @property
    def testing_farm_job_helper(self) -> TestingFarmJobHelper:
        if not self._testing_farm_job_helper:
            self._testing_farm_job_helper = TestingFarmJobHelper(
                service_config=self.service_config,
                package_config=self.package_config,
                project=self.project,
                metadata=self.data,
                db_project_event=self.db_project_event,
                job_config=self.job_config,
                build_targets_override=self.data.build_targets_override,
                tests_targets_override=self.data.tests_targets_override,
                celery_task=self.celery_task,
            )
        return self._testing_farm_job_helper


class GetDownstreamTestingFarmJobHelper(Protocol):
    celery_task: CeleryTask | None = None

    @property
    @abstractmethod
    def downstream_testing_farm_job_helper(self) -> DownstreamTestingFarmJobHelper: ...


class GetDownstreamTestingFarmJobHelperMixin(
    GetDownstreamTestingFarmJobHelper,
    GetKojiBuildFromTaskOrPullRequestMixin,
    ConfigFromEventMixin,
):
    _downstream_testing_farm_job_helper: DownstreamTestingFarmJobHelper | None = None

    @property
    def downstream_testing_farm_job_helper(self) -> DownstreamTestingFarmJobHelper:
        if not self._downstream_testing_farm_job_helper:
            self._downstream_testing_farm_job_helper = DownstreamTestingFarmJobHelper(
                service_config=self.service_config,
                project=self.project,
                metadata=self.data,
                koji_build=self.koji_build,
                celery_task=self.celery_task,
            )
        return self._downstream_testing_farm_job_helper
