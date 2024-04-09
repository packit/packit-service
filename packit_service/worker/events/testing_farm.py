# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
from datetime import datetime
from typing import Optional, Dict

from ogr.abstract import GitProject
from ogr.services.pagure import PagureProject
from packit.config import PackageConfig

from packit_service.models import (
    TestingFarmResult,
    AbstractProjectObjectDbType,
    PullRequestModel,
    TFTTestRunTargetModel,
    ProjectEventModel,
)
from packit_service.utils import load_package_config
from packit_service.worker.events.event import AbstractResultEvent


class TestingFarmResultsEvent(AbstractResultEvent):
    __test__ = False

    def __init__(
        self,
        pipeline_id: str,
        result: TestingFarmResult,
        compose: str,
        summary: str,
        log_url: str,
        copr_build_id: str,
        copr_chroot: str,
        commit_sha: str,
        project_url: str,
        created: datetime,
        identifier: Optional[str] = None,
    ):
        super().__init__(project_url=project_url)
        self.pipeline_id: str = pipeline_id
        self.result: TestingFarmResult = result
        self.compose: str = compose
        self.summary: str = summary
        self.log_url: str = log_url
        self.copr_build_id: str = copr_build_id
        self.copr_chroot: str = copr_chroot
        self.commit_sha: str = commit_sha
        self.created: datetime = created
        self.identifier: Optional[str] = identifier

        # Lazy properties
        self._pr_id: Optional[int] = None
        self._db_project_object: Optional[AbstractProjectObjectDbType] = None
        self._db_project_event: Optional[ProjectEventModel] = None

    @property
    def pr_id(self) -> Optional[int]:
        if not self._pr_id and isinstance(self.db_project_object, PullRequestModel):
            self._pr_id = self.db_project_object.pr_id
        return self._pr_id

    def get_dict(
        self, default_dict: Optional[Dict] = None, store_event: bool = False
    ) -> dict:
        result = super().get_dict()
        result["result"] = result["result"].value
        result["pr_id"] = self.pr_id
        return result

    @property
    def tf_run_model(self):
        return TFTTestRunTargetModel.get_by_pipeline_id(pipeline_id=self.pipeline_id)

    def get_db_project_object(self) -> Optional[AbstractProjectObjectDbType]:
        run_model = TFTTestRunTargetModel.get_by_pipeline_id(
            pipeline_id=self.pipeline_id
        )
        return run_model.get_project_event_object() if run_model else None

    def get_db_project_event(self) -> Optional[ProjectEventModel]:
        run_model = TFTTestRunTargetModel.get_by_pipeline_id(
            pipeline_id=self.pipeline_id
        )
        return run_model.get_project_event_model() if run_model else None

    def get_packages_config(self) -> Optional[PackageConfig]:
        if self.tf_run_model and self.tf_run_model.packages_config:
            return load_package_config(self.tf_run_model.packages_config)
        return super().get_packages_config()

    def get_base_project(self) -> Optional[GitProject]:
        if self.pr_id is not None:
            if isinstance(self.project, PagureProject):
                pull_request = self.project.get_pr(pr_id=self.pr_id)
                return self.project.service.get_project(
                    namespace=self.project.namespace,
                    username=pull_request.author,
                    repo=self.project.repo,
                    is_fork=True,
                )
            else:
                return None  # With Github app, we cannot work with fork repo
        return self.project
